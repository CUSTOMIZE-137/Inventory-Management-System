import { useMemo, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  KeyRound,
  Package,
  RefreshCw,
  Search,
  ShieldCheck,
  UserPlus,
  Users,
} from "lucide-react";
import { authHeaders, formatNetworkError, parseErrorDetail } from "../auth";
import type { AuditSummaryRead, TransactionRead, TransactionType, UserRead, UserRole } from "../api/types";
import { formatDateTime, formatQty, formatRole } from "../lib/utils";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Input,
  Label,
  Modal,
  Select,
} from "../components/ui/primitives";

async function apiGet<T>(url: string): Promise<T> {
  const response = await fetch(url, { headers: authHeaders() });
  if (!response.ok) throw new Error(await parseErrorDetail(response));
  return (await response.json()) as T;
}

interface AdminDashboardProps {
  currentUser: UserRead;
}

/**
 * System Administrator console for user lifecycle governance (provision, role assignment,
 * password resets, activation/deactivation) and read-only global ledger auditing.
 */
export function AdminDashboard({ currentUser }: AdminDashboardProps) {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<"users" | "audit">("users");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [addUserOpen, setAddUserOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newRole, setNewRole] = useState<UserRole>("REQUESTOR");
  const [creating, setCreating] = useState(false);

  const [resetTarget, setResetTarget] = useState<UserRead | null>(null);
  const [resetPassword, setResetPassword] = useState("");
  const [busyUserId, setBusyUserId] = useState<string | null>(null);

  const [auditSearch, setAuditSearch] = useState("");
  const [auditType, setAuditType] = useState<"ALL" | TransactionType>("ALL");

  const summaryQuery = useQuery({
    queryKey: ["admin-summary"],
    queryFn: () => apiGet<AuditSummaryRead>("/api/v1/audit/summary?recent_limit=50"),
    retry: false,
  });

  const usersQuery = useQuery({
    queryKey: ["admin-users"],
    queryFn: () => apiGet<UserRead[]>("/api/v1/users?limit=200"),
    retry: false,
  });

  const auditQuery = useQuery({
    queryKey: ["admin-transactions"],
    queryFn: () => apiGet<TransactionRead[]>("/api/v1/transactions?limit=250"),
    retry: false,
  });

  const refreshAll = async () => {
    setError(null);
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["admin-summary"] }),
      queryClient.invalidateQueries({ queryKey: ["admin-users"] }),
      queryClient.invalidateQueries({ queryKey: ["admin-transactions"] }),
    ]);
  };

  const handleCreateUser = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setNotice(null);
    setCreating(true);
    try {
      const response = await fetch("/api/v1/users", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          full_name: newName.trim(),
          email: newEmail.trim(),
          password: newPassword,
          role: newRole,
        }),
      });
      if (!response.ok) throw new Error(await parseErrorDetail(response));
      setNewName("");
      setNewEmail("");
      setNewPassword("");
      setNewRole("REQUESTOR");
      setAddUserOpen(false);
      setNotice("New user account provisioned successfully.");
      await refreshAll();
    } catch (err) {
      setError(formatNetworkError(err, "Unable to create user."));
    } finally {
      setCreating(false);
    }
  };

  const handlePatchUser = async (
    target: UserRead,
    patch: { role?: UserRole; is_active?: boolean; password?: string }
  ) => {
    setError(null);
    setNotice(null);
    setBusyUserId(target.id);
    try {
      const response = await fetch(`/api/v1/users/${target.id}`, {
        method: "PATCH",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(patch),
      });
      if (!response.ok) throw new Error(await parseErrorDetail(response));
      setNotice(
        patch.password
          ? `Credentials reset for ${target.full_name}.`
          : `Updated account settings for ${target.full_name}.`
      );
      setResetTarget(null);
      setResetPassword("");
      await refreshAll();
    } catch (err) {
      setError(formatNetworkError(err, "Unable to update user."));
    } finally {
      setBusyUserId(null);
    }
  };

  const filteredAudit = useMemo(() => {
    const rows = auditQuery.data ?? [];
    const q = auditSearch.trim().toLowerCase();
    return rows.filter((tx) => {
      if (auditType !== "ALL" && tx.transaction_type !== auditType) return false;
      if (!q) return true;
      return (
        tx.item_name.toLowerCase().includes(q) ||
        tx.item_sku.toLowerCase().includes(q) ||
        tx.user_full_name.toLowerCase().includes(q) ||
        tx.user_email.toLowerCase().includes(q) ||
        (tx.notes?.toLowerCase().includes(q) ?? false)
      );
    });
  }, [auditQuery.data, auditSearch, auditType]);

  const summary = summaryQuery.data;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-purple-300">
            System Governance &amp; Oversight
          </p>
          <h1 className="mt-1 text-2xl font-bold tracking-tight text-zinc-50">
            Administration Console
          </h1>
          <p className="mt-1 text-sm text-zinc-400">
            Manage user roles, account status, credentials, and audit every store stock event.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => void refreshAll()}
            disabled={summaryQuery.isFetching || usersQuery.isFetching}
          >
            <RefreshCw className={`h-4 w-4 ${summaryQuery.isFetching ? "animate-spin" : ""}`} />
            Refresh
          </Button>
          <Button type="button" onClick={() => setAddUserOpen(true)}>
            <UserPlus className="h-4 w-4" />
            Add User
          </Button>
        </div>
      </div>

      {notice && (
        <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-200">
          {notice}
        </div>
      )}
      {error && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-200">
          {error}
        </div>
      )}

      {/* KPI Strip */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardContent className="pt-5">
            <div className="flex items-center justify-between text-xs font-medium text-zinc-400">
              <span>Active Users</span>
              <Users className="h-4 w-4 text-purple-400" />
            </div>
            <p className="mt-2 text-2xl font-bold text-zinc-50">
              {summary ? `${summary.active_users} / ${summary.total_users}` : "—"}
            </p>
            {summary && (
              <p className="mt-1 text-xs text-zinc-400">
                {summary.users_by_role.ADMIN ?? 0} admin · {summary.users_by_role.STORE_MANAGER ?? 0}{" "}
                manager · {summary.users_by_role.REQUESTOR ?? 0} requestor
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-5">
            <div className="flex items-center justify-between text-xs font-medium text-zinc-400">
              <span>Catalog Items</span>
              <Package className="h-4 w-4 text-amber-400" />
            </div>
            <p className="mt-2 text-2xl font-bold text-zinc-50">
              {summary ? summary.total_items : "—"}
            </p>
            <p className="mt-1 text-xs text-zinc-400">
              {summary ? `${formatQty(summary.total_stock_units)} total units on hand` : "Loading..."}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-5">
            <div className="flex items-center justify-between text-xs font-medium text-zinc-400">
              <span>Low-Stock Alerts</span>
              <AlertTriangle className="h-4 w-4 text-amber-400" />
            </div>
            <p className="mt-2 text-2xl font-bold text-amber-300">
              {summary ? summary.low_stock_items : "—"}
            </p>
            <p className="mt-1 text-xs text-zinc-400">Items at or below reorder threshold</p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-5">
            <div className="flex items-center justify-between text-xs font-medium text-zinc-400">
              <span>Immutable Ledger Events</span>
              <Activity className="h-4 w-4 text-sky-400" />
            </div>
            <p className="mt-2 text-2xl font-bold text-zinc-50">
              {summary ? summary.total_transactions : "—"}
            </p>
            <p className="mt-1 text-xs text-zinc-400">Complete audited stock history</p>
          </CardContent>
        </Card>
      </div>

      {/* Section Switcher */}
      <div className="flex items-center gap-2 border-b border-zinc-800 pb-3">
        <Button
          type="button"
          size="sm"
          variant={activeTab === "users" ? "default" : "ghost"}
          onClick={() => setActiveTab("users")}
        >
          <ShieldCheck className="h-4 w-4" />
          User Management ({usersQuery.data?.length ?? 0})
        </Button>
        <Button
          type="button"
          size="sm"
          variant={activeTab === "audit" ? "default" : "ghost"}
          onClick={() => setActiveTab("audit")}
        >
          <Activity className="h-4 w-4" />
          Global Audit Log ({auditQuery.data?.length ?? 0})
        </Button>
      </div>

      {activeTab === "users" ? (
        <Card>
          <CardHeader>
            <CardTitle>System Users &amp; RBAC Roles</CardTitle>
            <CardDescription>
              Assign roles, reset credentials, or activate/deactivate accounts. Changes apply
              immediately.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="border-b border-zinc-800 text-xs uppercase text-zinc-400">
                  <tr>
                    <th className="py-3 pr-4">User</th>
                    <th className="py-3 pr-4">Role</th>
                    <th className="py-3 pr-4">Status</th>
                    <th className="py-3 pr-4">Created</th>
                    <th className="py-3 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/80">
                  {(usersQuery.data ?? []).map((u) => {
                    const isSelf = u.id === currentUser.id;
                    const busy = busyUserId === u.id;
                    return (
                      <tr key={u.id} className="hover:bg-zinc-800/30">
                        <td className="py-3 pr-4">
                          <div className="font-medium text-zinc-100">
                            {u.full_name}{" "}
                            {isSelf && (
                              <span className="ml-1 text-xs text-amber-400">(You)</span>
                            )}
                          </div>
                          <div className="text-xs text-zinc-400">{u.email}</div>
                        </td>
                        <td className="py-3 pr-4">
                          <Select
                            aria-label={`Role for ${u.full_name}`}
                            className="h-8 w-44 text-xs"
                            value={u.role}
                            disabled={isSelf || busy}
                            onChange={(e) =>
                              void handlePatchUser(u, { role: e.target.value as UserRole })
                            }
                          >
                            <option value="ADMIN">ADMIN (Oversight)</option>
                            <option value="STORE_MANAGER">STORE_MANAGER</option>
                            <option value="REQUESTOR">REQUESTOR</option>
                          </Select>
                        </td>
                        <td className="py-3 pr-4">
                          <Badge variant={u.is_active ? "success" : "danger"}>
                            {u.is_active ? "Active" : "Inactive"}
                          </Badge>
                        </td>
                        <td className="py-3 pr-4 text-xs text-zinc-400">
                          {formatDateTime(u.created_at)}
                        </td>
                        <td className="py-3 text-right">
                          <div className="flex items-center justify-end gap-2">
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              disabled={busy}
                              onClick={() => {
                                setResetTarget(u);
                                setResetPassword("");
                              }}
                            >
                              <KeyRound className="h-3.5 w-3.5" />
                              Reset Password
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              variant={u.is_active ? "ghost" : "success"}
                              disabled={isSelf || busy}
                              onClick={() =>
                                void handlePatchUser(u, { is_active: !u.is_active })
                              }
                            >
                              {u.is_active ? "Deactivate" : "Activate"}
                            </Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <CardTitle>Complete System Audit Log</CardTitle>
              <CardDescription>
                Append-only ledger of all stock additions, take-outs, and count corrections.
              </CardDescription>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-zinc-500" />
                <Input
                  className="h-9 w-56 pl-9 text-xs"
                  placeholder="Search item, SKU, user..."
                  value={auditSearch}
                  onChange={(e) => setAuditSearch(e.target.value)}
                />
              </div>
              <Select
                aria-label="Filter transaction type"
                className="h-9 w-40 text-xs"
                value={auditType}
                onChange={(e) => setAuditType(e.target.value as "ALL" | TransactionType)}
              >
                <option value="ALL">All Types</option>
                <option value="STOCK_IN">STOCK_IN</option>
                <option value="STOCK_OUT">STOCK_OUT</option>
                <option value="ADJUSTMENT">ADJUSTMENT</option>
              </Select>
            </div>
          </CardHeader>
          <CardContent>
            {filteredAudit.length === 0 ? (
              <p className="py-8 text-center text-sm text-zinc-400">
                No audit transactions match your filter.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="border-b border-zinc-800 text-xs uppercase text-zinc-400">
                    <tr>
                      <th className="py-3 pr-4">Timestamp</th>
                      <th className="py-3 pr-4">Actor</th>
                      <th className="py-3 pr-4">Type</th>
                      <th className="py-3 pr-4">Item</th>
                      <th className="py-3 pr-4">Balance Snapshot</th>
                      <th className="py-3">Notes</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/80">
                    {filteredAudit.map((tx) => (
                      <tr key={tx.id} className="hover:bg-zinc-800/30">
                        <td className="whitespace-nowrap py-3 pr-4 text-xs text-zinc-400">
                          {formatDateTime(tx.created_at)}
                        </td>
                        <td className="py-3 pr-4">
                          <div className="font-medium text-zinc-100">{tx.user_full_name}</div>
                          <div className="text-xs text-zinc-400">{formatRole(tx.user_role)}</div>
                        </td>
                        <td className="py-3 pr-4">
                          <Badge
                            variant={
                              tx.transaction_type === "STOCK_IN"
                                ? "success"
                                : tx.transaction_type === "STOCK_OUT"
                                  ? "warning"
                                  : "info"
                            }
                          >
                            {tx.transaction_type}
                          </Badge>
                        </td>
                        <td className="py-3 pr-4">
                          <div className="font-medium text-zinc-100">{tx.item_name}</div>
                          <div className="font-mono text-xs text-zinc-400">{tx.item_sku}</div>
                        </td>
                        <td className="whitespace-nowrap py-3 pr-4 font-mono text-xs">
                          <span className="text-zinc-400">{formatQty(tx.previous_stock)}</span>
                          {" → "}
                          <span className="font-bold text-zinc-100">
                            {formatQty(tx.remaining_stock)} {tx.item_unit}
                          </span>
                          <span className="ml-1.5 text-zinc-400">
                            ({tx.transaction_type === "STOCK_IN" ? "+" : tx.transaction_type === "STOCK_OUT" ? "-" : "±"}
                            {formatQty(tx.quantity)})
                          </span>
                        </td>
                        <td className="py-3 text-xs text-zinc-300">{tx.notes || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Add User Modal */}
      <Modal
        open={addUserOpen}
        onClose={() => setAddUserOpen(false)}
        title="Provision New User"
        description="Assign an exact system role. Users can sign in immediately."
      >
        <form onSubmit={(e) => void handleCreateUser(e)} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="new-user-name">Full Name</Label>
            <Input
              id="new-user-name"
              required
              placeholder="Jane Doe"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="new-user-email">Email Address</Label>
            <Input
              id="new-user-email"
              type="email"
              required
              placeholder="jane@organization.org"
              value={newEmail}
              onChange={(e) => setNewEmail(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="new-user-password">Password (min 8 characters)</Label>
            <Input
              id="new-user-password"
              type="password"
              required
              minLength={8}
              placeholder="Temporary password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="new-user-role">Role</Label>
            <Select
              id="new-user-role"
              value={newRole}
              onChange={(e) => setNewRole(e.target.value as UserRole)}
            >
              <option value="REQUESTOR">REQUESTOR — Stock Lookup &amp; Take-Out</option>
              <option value="STORE_MANAGER">STORE_MANAGER — Catalog &amp; Stock Intake</option>
              <option value="ADMIN">ADMIN — System Oversight &amp; Users</option>
            </Select>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="outline" onClick={() => setAddUserOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={creating}>
              {creating ? "Creating..." : "Create User"}
            </Button>
          </div>
        </form>
      </Modal>

      {/* Reset Password Modal */}
      <Modal
        open={resetTarget !== null}
        onClose={() => setResetTarget(null)}
        title={`Reset Password — ${resetTarget?.full_name ?? ""}`}
        description="Set a new password (minimum 8 characters) for this user account."
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (resetTarget) void handlePatchUser(resetTarget, { password: resetPassword });
          }}
          className="space-y-4"
        >
          <div className="space-y-1.5">
            <Label htmlFor="reset-password-input">New Password</Label>
            <Input
              id="reset-password-input"
              type="password"
              required
              minLength={8}
              placeholder="Enter new password"
              value={resetPassword}
              onChange={(e) => setResetPassword(e.target.value)}
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="outline" onClick={() => setResetTarget(null)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!resetTarget || busyUserId === resetTarget.id}>
              Update Password
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
