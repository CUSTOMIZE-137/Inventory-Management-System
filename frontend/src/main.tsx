import { useCallback, useEffect, useState, type FormEvent } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  LogOut,
  Package,
  RefreshCw,
  Shield,
  WifiOff,
} from "lucide-react";
import {
  fetchCurrentUser,
  formatNetworkError,
  getCachedUser,
  getStoredToken,
  loginRequest,
  setStoredToken,
} from "./auth";
import type { UserRead, UserRole } from "./api/types";
import { db, requestPersistentStorage, type QueuedTransaction } from "./db";
import {
  clearRejectedTransactions,
  discardQueuedTransaction,
  syncTransactionQueue,
} from "./sync";
import { formatRole } from "./lib/utils";
import { AdminDashboard } from "./views/AdminDashboard";
import { RequestorDashboard } from "./views/RequestorDashboard";
import { StoreManagerDashboard } from "./views/StoreManagerDashboard";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Input, Label } from "./components/ui/primitives";
import "./style.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
    },
  },
});

function roleBadgeVariant(role: UserRole): "admin" | "manager" | "requestor" {
  if (role === "ADMIN") return "admin";
  if (role === "STORE_MANAGER") return "manager";
  return "requestor";
}

/**
 * Root application shell managing authentication state, online/offline connectivity
 * listeners, automatic IndexedDB outbox syncing on reconnect, and role-based routing
 * to the ADMIN, STORE_MANAGER, or REQUESTOR workspace.
 */
function AppShell() {
  const client = useQueryClient();
  const [token, setToken] = useState<string | null>(() => getStoredToken());
  const [user, setUser] = useState<UserRead | null>(() => getCachedUser());
  const [loadingProfile, setLoadingProfile] = useState<boolean>(Boolean(getStoredToken()));

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [signingIn, setSigningIn] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);

  const [online, setOnline] = useState<boolean>(() => navigator.onLine);
  const [queue, setQueue] = useState<QueuedTransaction[]>([]);
  const [syncing, setSyncing] = useState(false);

  const refreshQueue = useCallback(async () => {
    const items = await db.transactionQueue.orderBy("created_at").toArray();
    setQueue(items);
  }, []);

  const handleSignOut = useCallback(() => {
    setStoredToken(null);
    setToken(null);
    setUser(null);
    setAuthError(null);
    client.clear();
  }, [client]);

  const runSync = useCallback(async () => {
    if (!getStoredToken()) return;
    setSyncing(true);
    try {
      const result = await syncTransactionQueue();
      await refreshQueue();
      if (result.authExpired) {
        handleSignOut();
        setAuthError(result.lastError);
        return;
      }
      if (result.created > 0 || result.replayed > 0) {
        await Promise.all([
          client.invalidateQueries({ queryKey: ["items"] }),
          client.invalidateQueries({ queryKey: ["transactions"] }),
          client.invalidateQueries({ queryKey: ["admin-summary"] }),
        ]);
      }
    } finally {
      setSyncing(false);
    }
  }, [client, handleSignOut, refreshQueue]);

  useEffect(() => {
    void requestPersistentStorage();
    void refreshQueue();
  }, [refreshQueue]);

  // Watch browser connectivity transitions and auto-flush queued transactions on reconnect.
  useEffect(() => {
    const onOnline = () => {
      setOnline(true);
      void runSync();
    };
    const onOffline = () => setOnline(false);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    return () => {
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
    };
  }, [runSync]);

  // Verify the stored token on startup/login (falling back to cached profile when offline).
  useEffect(() => {
    if (!token) {
      setUser(null);
      setLoadingProfile(false);
      return;
    }
    let cancelled = false;
    setLoadingProfile(true);
    void fetchCurrentUser()
      .then((profile) => {
        if (!cancelled) {
          setUser(profile);
          if (navigator.onLine) {
            void runSync();
          }
        }
      })
      .catch((err) => {
        if (!cancelled) {
          handleSignOut();
          setAuthError(formatNetworkError(err, "Please sign in to continue."));
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingProfile(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token, handleSignOut, runSync]);

  const handleLogin = async (event: FormEvent) => {
    event.preventDefault();
    setAuthError(null);
    setSigningIn(true);
    try {
      const response = await loginRequest(email.trim(), password);
      setToken(response.access_token);
      setUser(response.user);
      setPassword("");
      await client.invalidateQueries();
      await refreshQueue();
      if (navigator.onLine) {
        await runSync();
      }
    } catch (err) {
      setAuthError(formatNetworkError(err, "Unable to sign in. Verify your credentials."));
    } finally {
      setSigningIn(false);
    }
  };

  const handleDiscardQueued = async (key: string) => {
    await discardQueuedTransaction(key);
    await refreshQueue();
    await client.invalidateQueries({ queryKey: ["items"] });
  };

  const handleClearRejected = async () => {
    await clearRejectedTransactions();
    await refreshQueue();
    await client.invalidateQueries({ queryKey: ["items"] });
  };

  const pendingCount = queue.length;

  const renderSyncPill = () => {
    if (!online) {
      return (
        <Badge variant="warning" className="gap-1.5 py-1">
          <WifiOff className="h-3.5 w-3.5" />
          Offline Mode{pendingCount > 0 ? ` · ${pendingCount} queued` : ""}
        </Badge>
      );
    }
    if (syncing || pendingCount > 0) {
      return (
        <button
          type="button"
          onClick={() => void runSync()}
          className="inline-flex items-center gap-1.5 rounded-full border border-sky-500/40 bg-sky-500/15 px-3 py-1 text-xs font-semibold text-sky-300 hover:bg-sky-500/25 cursor-pointer"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${syncing ? "animate-spin" : ""}`} />
          {syncing
            ? `Syncing ${pendingCount} transaction${pendingCount === 1 ? "" : "s"}`
            : `Syncing ${pendingCount} transaction${pendingCount === 1 ? "" : "s"}`}
        </button>
      );
    }
    return (
      <Badge variant="success" className="gap-1.5 py-1">
        <CheckCircle2 className="h-3.5 w-3.5" />
        Online · Synced
      </Badge>
    );
  };

  return (
    <div className="min-h-dvh bg-zinc-950 text-zinc-100 flex flex-col">
      {/* Persistent Top Status Bar */}
      <header className="sticky top-0 z-30 border-b border-zinc-800 bg-zinc-950/90 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-amber-400 text-zinc-950 shadow-sm">
              <Package className="h-5 w-5" />
            </div>
            <div>
              <span className="block text-sm font-bold leading-tight text-zinc-50">
                Stock Control PWA
              </span>
              <span className="block text-[11px] text-zinc-400">
                Real-Time RBAC Inventory Ledger
              </span>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            {renderSyncPill()}

            {user && (
              <>
                <div className="hidden items-center gap-2 rounded-full border border-zinc-800 bg-zinc-900 px-3 py-1 sm:flex">
                  <span className="text-xs font-medium text-zinc-200">{user.full_name}</span>
                  <Badge variant={roleBadgeVariant(user.role)}>
                    <Shield className="h-3 w-3" />
                    {user.role}
                  </Badge>
                </div>
                <Badge variant={roleBadgeVariant(user.role)} className="sm:hidden">
                  {formatRole(user.role)}
                </Badge>
                <Button type="button" size="sm" variant="ghost" onClick={handleSignOut}>
                  <LogOut className="h-4 w-4" />
                  <span className="hidden sm:inline">Sign Out</span>
                </Button>
              </>
            )}
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6">
        {!token || !user ? (
          loadingProfile ? (
            <div className="flex min-h-[50vh] items-center justify-center text-sm text-zinc-400">
              Loading your workspace...
            </div>
          ) : (
            <div className="mx-auto mt-6 max-w-md">
              <div className="mb-5 flex justify-center">
                <img
                  src="/brand-logo.png?v=2"
                  alt="Inventory Management System"
                  className="h-36 w-auto object-contain drop-shadow-lg"
                />
              </div>
              <Card>
                <CardHeader className="text-center">
                  <CardTitle className="text-xl">Sign in to Inventory</CardTitle>
                </CardHeader>
                <CardContent className="space-y-5">
                  {authError && (
                    <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3.5 py-2.5 text-sm text-red-200">
                      {authError}
                    </div>
                  )}

                  <form onSubmit={(e) => void handleLogin(e)} className="space-y-4">
                    <div className="space-y-1.5">
                      <Label htmlFor="login-email">Email Address</Label>
                      <Input
                        id="login-email"
                        type="email"
                        autoComplete="email"
                        required
                        placeholder="Enter your email"
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="login-password">Password</Label>
                      <Input
                        id="login-password"
                        type="password"
                        autoComplete="current-password"
                        required
                        placeholder="Enter password"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                      />
                    </div>
                    <Button type="submit" className="w-full" disabled={signingIn}>
                      {signingIn ? "Signing in..." : "Sign In"}
                    </Button>
                  </form>
                </CardContent>
              </Card>
            </div>
          )
        ) : user.role === "ADMIN" ? (
          <AdminDashboard currentUser={user} />
        ) : user.role === "STORE_MANAGER" ? (
          <StoreManagerDashboard
            online={online}
            syncing={syncing}
            queue={queue}
            onTriggerSync={runSync}
            onDiscardQueued={handleDiscardQueued}
            onClearRejected={handleClearRejected}
          />
        ) : (
          <RequestorDashboard
            online={online}
            syncing={syncing}
            queue={queue}
            onTriggerSync={runSync}
            onDiscardQueued={handleDiscardQueued}
            onClearRejected={handleClearRejected}
          />
        )}
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={queryClient}>
    <AppShell />
  </QueryClientProvider>
);
