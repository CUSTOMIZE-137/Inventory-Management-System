import { useMemo, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  History,
  Minus,
  Package,
  Plus,
  RefreshCw,
  ScanBarcode,
  Search,
  ShoppingCart,
  Trash2,
  UploadCloud,
} from "lucide-react";
import type { ItemRead } from "../api/types";
import type { QueuedTransaction } from "../db";
import {
  enqueueOfflineTransaction,
  loadItemsOfflineFirst,
  loadTransactionsOfflineFirst,
} from "../sync";
import { formatDateTime, formatQty } from "../lib/utils";
import { BarcodeScannerModal } from "../components/BarcodeScannerModal";
import { SyncQueuePanel } from "../components/SyncQueuePanel";
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
  Select,
} from "../components/ui/primitives";

interface RequestorDashboardProps {
  online: boolean;
  syncing: boolean;
  queue: QueuedTransaction[];
  onTriggerSync: () => Promise<void>;
  onDiscardQueued: (key: string) => Promise<void>;
  onClearRejected: () => Promise<void>;
}

interface ReceiptItem {
  item_name: string;
  item_sku: string;
  item_unit: string;
  quantity: number;
  previous: number;
  remaining: number;
}

export function RequestorDashboard({
  online,
  syncing,
  queue,
  onTriggerSync,
  onDiscardQueued,
  onClearRejected,
}: RequestorDashboardProps) {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<"browse" | "history" | "queue">("browse");
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("ALL");
  const [scannerOpen, setScannerOpen] = useState(false);

  // Cart maps itemId -> requested take-out quantity
  const [cart, setCart] = useState<Record<string, number>>({});
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastReceipt, setLastReceipt] = useState<{
    offline: boolean;
    items: ReceiptItem[];
  } | null>(null);

  const itemsQuery = useQuery({
    queryKey: ["items"],
    queryFn: loadItemsOfflineFirst,
    retry: false,
  });

  const historyQuery = useQuery({
    queryKey: ["transactions"],
    queryFn: () => loadTransactionsOfflineFirst(100),
    retry: false,
  });

  const items = itemsQuery.data?.items ?? [];
  const itemById = useMemo(() => new Map(items.map((i) => [i.id, i])), [items]);
  const categories = useMemo(
    () => Array.from(new Set(items.map((i) => i.category))).sort(),
    [items]
  );

  const filteredItems = useMemo(() => {
    const q = search.trim().toLowerCase();
    return items.filter((item) => {
      if (categoryFilter !== "ALL" && item.category !== categoryFilter) return false;
      if (!q) return true;
      return (
        item.name.toLowerCase().includes(q) ||
        item.sku.toLowerCase().includes(q) ||
        item.category.toLowerCase().includes(q) ||
        (item.barcode?.toLowerCase().includes(q) ?? false)
      );
    });
  }, [items, search, categoryFilter]);

  const cartEntries = useMemo(() => {
    return Object.entries(cart)
      .map(([itemId, qty]) => {
        const item = itemById.get(itemId);
        if (!item || qty <= 0) return null;
        return { item, qty };
      })
      .filter((entry): entry is { item: ItemRead; qty: number } => entry !== null);
  }, [cart, itemById]);

  // Clamp requested cart quantities to live available stock to prevent client-side overdraws.
  const setCartQty = (item: ItemRead, nextQty: number) => {
    const maxAvail = Number(item.current_stock);
    if (nextQty <= 0) {
      setCart((prev) => {
        const copy = { ...prev };
        delete copy[item.id];
        return copy;
      });
      return;
    }
    const clamped = Math.min(nextQty, maxAvail);
    setCart((prev) => ({ ...prev, [item.id]: clamped }));
  };

  const handleBarcodeDetected = (code: string) => {
    const matched = items.find(
      (i) => i.barcode?.toLowerCase() === code.toLowerCase() || i.sku.toLowerCase() === code.toLowerCase()
    );
    if (!matched) {
      setError(`No store item matches barcode "${code}".`);
      return;
    }
    setError(null);
    setSearch(matched.sku);
    if (Number(matched.current_stock) <= 0) {
      setError(`${matched.name} (${matched.sku}) is currently out of stock.`);
      return;
    }
    const currentInCart = cart[matched.id] ?? 0;
    setCartQty(matched, currentInCart + 1);
  };

  // Queue each cart line locally first (guaranteeing offline durability and instant balance
  // preview), then immediately flush to the server ledger when online.
  const handleSubmitTakeOut = async (event: FormEvent) => {
    event.preventDefault();
    if (cartEntries.length === 0) return;

    for (const { item, qty } of cartEntries) {
      if (qty > Number(item.current_stock)) {
        setError(
          `Cannot take out ${formatQty(qty)} ${item.unit} of ${item.name}: only ${formatQty(item.current_stock)} available.`
        );
        return;
      }
    }

    setError(null);
    setSubmitting(true);
    const receiptItems: ReceiptItem[] = [];

    try {
      for (const { item, qty } of cartEntries) {
        const queued = await enqueueOfflineTransaction({
          item,
          transaction_type: "STOCK_OUT",
          quantity: qty,
          notes: notes.trim() || "Store take-out",
        });
        receiptItems.push({
          item_name: queued.item_name,
          item_sku: queued.item_sku,
          item_unit: queued.item_unit,
          quantity: qty,
          previous: queued.previous_stock_preview,
          remaining: queued.remaining_stock_preview,
        });
      }

      setCart({});
      setNotes("");
      setLastReceipt({ offline: !online, items: receiptItems });

      await queryClient.invalidateQueries({ queryKey: ["items"] });
      if (online) {
        await onTriggerSync();
        await queryClient.invalidateQueries({ queryKey: ["transactions"] });
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-sky-300">
            Store Issue &amp; Stock Lookup
          </p>
          <h1 className="mt-1 text-2xl font-bold tracking-tight text-zinc-50">
            Available Store Stock
          </h1>
          <p className="mt-1 text-sm text-zinc-400">
            Browse live stock levels, scan item barcodes, and take out supplies with automatic
            remaining-stock calculation.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" onClick={() => setScannerOpen(true)}>
            <ScanBarcode className="h-4 w-4" />
            Scan Barcode
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => void queryClient.invalidateQueries({ queryKey: ["items"] })}
          >
            <RefreshCw className={`h-4 w-4 ${itemsQuery.isFetching ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-200">
          {error}
        </div>
      )}

      {lastReceipt && (
        <div className="rounded-xl border border-emerald-500/40 bg-emerald-500/10 p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-2 text-emerald-300">
              <CheckCircle2 className="h-5 w-5 shrink-0" />
              <strong className="text-sm">
                {lastReceipt.offline
                  ? "Take-out saved offline (queued for automatic sync)"
                  : "Stock take-out recorded & synced"}
              </strong>
            </div>
            <button
              type="button"
              className="text-xs text-emerald-300 underline"
              onClick={() => setLastReceipt(null)}
            >
              Dismiss
            </button>
          </div>
          <ul className="mt-2.5 space-y-1 text-xs text-emerald-100">
            {lastReceipt.items.map((r) => (
              <li key={r.item_sku} className="font-mono">
                {r.item_name} ({r.item_sku}): took -{formatQty(r.quantity)} {r.item_unit} ·{" "}
                {formatQty(r.previous)} → <strong>{formatQty(r.remaining)} {r.item_unit}</strong>{" "}
                remaining
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Tabs */}
      <div className="flex flex-wrap items-center gap-2 border-b border-zinc-800 pb-3">
        <Button
          type="button"
          size="sm"
          variant={activeTab === "browse" ? "default" : "ghost"}
          onClick={() => setActiveTab("browse")}
        >
          <Package className="h-4 w-4" />
          Browse &amp; Take Out ({items.length})
        </Button>
        <Button
          type="button"
          size="sm"
          variant={activeTab === "history" ? "default" : "ghost"}
          onClick={() => setActiveTab("history")}
        >
          <History className="h-4 w-4" />
          My Take-Out History ({historyQuery.data?.length ?? 0})
        </Button>
        <Button
          type="button"
          size="sm"
          variant={activeTab === "queue" ? "default" : "ghost"}
          onClick={() => setActiveTab("queue")}
        >
          <UploadCloud className="h-4 w-4" />
          Sync Queue
          {queue.length > 0 && (
            <span className="ml-1 rounded-full bg-amber-400 px-1.5 py-0.5 text-[10px] font-bold text-zinc-950">
              {queue.length}
            </span>
          )}
        </Button>
      </div>

      {activeTab === "browse" && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          {/* Left 2 columns: Available Stock Grid */}
          <div className="space-y-4 lg:col-span-2">
            <div className="flex flex-col gap-2 sm:flex-row">
              <div className="relative flex-1">
                <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-zinc-500" />
                <Input
                  className="pl-9"
                  placeholder="Search item name, SKU, or barcode..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              <Select
                aria-label="Filter category"
                className="sm:w-48"
                value={categoryFilter}
                onChange={(e) => setCategoryFilter(e.target.value)}
              >
                <option value="ALL">All Categories</option>
                {categories.map((cat) => (
                  <option key={cat} value={cat}>
                    {cat}
                  </option>
                ))}
              </Select>
            </div>

            {filteredItems.length === 0 ? (
              <Card>
                <CardContent className="py-12 text-center text-sm text-zinc-400">
                  No store items match your search.
                </CardContent>
              </Card>
            ) : (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {filteredItems.map((item) => {
                  const avail = Number(item.current_stock);
                  const inCart = cart[item.id] ?? 0;
                  const remainingPreview = Math.max(0, avail - inCart);
                  const outOfStock = avail <= 0;

                  return (
                    <Card
                      key={item.id}
                      className={`transition-colors ${inCart > 0 ? "border-amber-400/70 bg-amber-400/5" : ""}`}
                    >
                      <CardContent className="flex h-full flex-col justify-between gap-4 pt-5">
                        <div className="space-y-2">
                          <div className="flex items-start justify-between gap-2">
                            <div>
                              <span className="font-mono text-xs text-zinc-400">{item.sku}</span>
                              <h3 className="font-semibold leading-snug text-zinc-100">
                                {item.name}
                              </h3>
                            </div>
                            {outOfStock ? (
                              <Badge variant="danger">Out</Badge>
                            ) : item.is_low_stock ? (
                              <Badge variant="warning">Low</Badge>
                            ) : (
                              <Badge variant="success">Available</Badge>
                            )}
                          </div>

                          <div className="flex items-baseline justify-between rounded-lg bg-zinc-950/70 px-3 py-2">
                            <span className="text-xs text-zinc-400">{item.category}</span>
                            <div className="text-right font-mono">
                              <span className="text-base font-bold text-zinc-50">
                                {formatQty(avail)}
                              </span>{" "}
                              <span className="text-xs text-zinc-400">{item.unit}</span>
                            </div>
                          </div>

                          {inCart > 0 && (
                            <p className="font-mono text-xs text-amber-300">
                              After take-out: {formatQty(avail)} → {formatQty(remainingPreview)}{" "}
                              {item.unit}
                            </p>
                          )}
                        </div>

                        <div>
                          {inCart === 0 ? (
                            <Button
                              type="button"
                              size="sm"
                              className="w-full"
                              variant={outOfStock ? "secondary" : "default"}
                              disabled={outOfStock}
                              onClick={() => setCartQty(item, 1)}
                            >
                              <Plus className="h-3.5 w-3.5" />
                              {outOfStock ? "Out of Stock" : "Add to Take-Out"}
                            </Button>
                          ) : (
                            <div className="flex items-center justify-between gap-2">
                              <Button
                                type="button"
                                size="icon"
                                variant="outline"
                                aria-label={`Decrease quantity for ${item.name}`}
                                onClick={() => setCartQty(item, inCart - 1)}
                              >
                                <Minus className="h-4 w-4" />
                              </Button>
                              <Input
                                type="number"
                                min="0"
                                max={avail}
                                step="any"
                                aria-label={`Quantity for ${item.name}`}
                                className="h-9 text-center font-mono"
                                value={inCart}
                                onChange={(e) => setCartQty(item, Number(e.target.value))}
                              />
                              <Button
                                type="button"
                                size="icon"
                                variant="outline"
                                aria-label={`Increase quantity for ${item.name}`}
                                disabled={inCart >= avail}
                                onClick={() => setCartQty(item, inCart + 1)}
                              >
                                <Plus className="h-4 w-4" />
                              </Button>
                            </div>
                          )}
                        </div>
                      </CardContent>
                    </Card>
                  );
                })}
              </div>
            )}
          </div>

          {/* Right column: Sticky Take-Out Cart with live remaining-stock calculation */}
          <div>
            <Card className="sticky top-20">
              <CardHeader>
                <div className="flex items-center justify-between">
                  <CardTitle className="flex items-center gap-2">
                    <ShoppingCart className="h-4 w-4 text-amber-400" />
                    Take-Out Cart
                  </CardTitle>
                  <Badge variant="requestor">{cartEntries.length} item(s)</Badge>
                </div>
                <CardDescription>
                  Live remaining stock is calculated automatically before and after submission.
                </CardDescription>
              </CardHeader>

              <CardContent>
                {cartEntries.length === 0 ? (
                  <p className="rounded-xl border border-dashed border-zinc-800 py-8 text-center text-xs text-zinc-400">
                    Select items from the grid or scan a barcode to start a take-out.
                  </p>
                ) : (
                  <form onSubmit={(e) => void handleSubmitTakeOut(e)} className="space-y-4">
                    <div className="divide-y divide-zinc-800 rounded-xl border border-zinc-800 bg-zinc-950/60">
                      {cartEntries.map(({ item, qty }) => {
                        const avail = Number(item.current_stock);
                        const after = Math.max(0, avail - qty);
                        return (
                          <div key={item.id} className="space-y-1.5 p-3">
                            <div className="flex items-start justify-between gap-2">
                              <div>
                                <p className="text-sm font-medium text-zinc-100">{item.name}</p>
                                <p className="font-mono text-xs text-zinc-400">
                                  Taking -{formatQty(qty)} {item.unit}
                                </p>
                              </div>
                              <button
                                type="button"
                                className="text-zinc-500 hover:text-red-400"
                                aria-label={`Remove ${item.name}`}
                                onClick={() => setCartQty(item, 0)}
                              >
                                <Trash2 className="h-4 w-4" />
                              </button>
                            </div>
                            <div className="flex items-center justify-between rounded-md bg-zinc-900 px-2.5 py-1.5 font-mono text-xs">
                              <span className="text-zinc-400">Remaining:</span>
                              <span>
                                <span className="text-zinc-400">{formatQty(avail)}</span>
                                {" → "}
                                <strong
                                  className={
                                    after <= item.reorder_level
                                      ? "text-amber-300"
                                      : "text-emerald-300"
                                  }
                                >
                                  {formatQty(after)} {item.unit}
                                </strong>
                              </span>
                            </div>
                          </div>
                        );
                      })}
                    </div>

                    <div className="space-y-1.5">
                      <Label htmlFor="takeout-notes">Purpose / Destination (optional)</Label>
                      <Input
                        id="takeout-notes"
                        placeholder="e.g. Clinic Ward A daily issue"
                        value={notes}
                        onChange={(e) => setNotes(e.target.value)}
                      />
                    </div>

                    <Button type="submit" className="w-full" disabled={submitting}>
                      {submitting
                        ? "Recording..."
                        : online
                          ? `Confirm Take-Out (${cartEntries.length})`
                          : `Queue Take-Out Offline (${cartEntries.length})`}
                    </Button>
                  </form>
                )}
              </CardContent>
            </Card>
          </div>
        </div>
      )}

      {activeTab === "history" && (
        <Card>
          <CardHeader>
            <CardTitle>My Personal Take-Out History</CardTitle>
            <CardDescription>
              Read-only ledger of all stock take-outs recorded under your account.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {(historyQuery.data ?? []).length === 0 ? (
              <p className="py-10 text-center text-sm text-zinc-400">
                You have not recorded any stock take-outs yet.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="border-b border-zinc-800 text-xs uppercase text-zinc-400">
                    <tr>
                      <th className="py-3 pr-4">Date</th>
                      <th className="py-3 pr-4">Item</th>
                      <th className="py-3 pr-4">Taken Out</th>
                      <th className="py-3 pr-4">Remaining Snapshot</th>
                      <th className="py-3">Notes</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/80">
                    {(historyQuery.data ?? []).map((tx) => (
                      <tr key={tx.id} className="hover:bg-zinc-800/30">
                        <td className="whitespace-nowrap py-3 pr-4 text-xs text-zinc-400">
                          {formatDateTime(tx.created_at)}
                        </td>
                        <td className="py-3 pr-4">
                          <div className="font-medium text-zinc-100">{tx.item_name}</div>
                          <div className="font-mono text-xs text-zinc-400">{tx.item_sku}</div>
                        </td>
                        <td className="py-3 pr-4 font-mono text-xs font-semibold text-amber-300">
                          -{formatQty(tx.quantity)} {tx.item_unit}
                        </td>
                        <td className="whitespace-nowrap py-3 pr-4 font-mono text-xs text-zinc-300">
                          {formatQty(tx.previous_stock)} →{" "}
                          <strong className="text-zinc-100">
                            {formatQty(tx.remaining_stock)} {tx.item_unit}
                          </strong>
                        </td>
                        <td className="py-3 text-xs text-zinc-400">{tx.notes || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {activeTab === "queue" && (
        <SyncQueuePanel
          queue={queue}
          online={online}
          syncing={syncing}
          onSyncNow={() => void onTriggerSync()}
          onDiscard={(key) => void onDiscardQueued(key)}
          onClearRejected={() => void onClearRejected()}
        />
      )}

      <BarcodeScannerModal
        open={scannerOpen}
        onClose={() => setScannerOpen(false)}
        onDetected={handleBarcodeDetected}
      />
    </div>
  );
}
