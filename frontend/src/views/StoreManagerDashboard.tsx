import { useMemo, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowDownToLine,
  ClipboardCheck,
  Edit3,
  History,
  Package,
  PackagePlus,
  Plus,
  RefreshCw,
  ScanBarcode,
  Search,
  UploadCloud,
} from "lucide-react";
import { authHeaders, formatNetworkError, parseErrorDetail } from "../auth";
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
  Modal,
  Select,
} from "../components/ui/primitives";

interface StoreManagerDashboardProps {
  online: boolean;
  syncing: boolean;
  queue: QueuedTransaction[];
  onTriggerSync: () => Promise<void>;
  onDiscardQueued: (key: string) => Promise<void>;
  onClearRejected: () => Promise<void>;
}

export function StoreManagerDashboard({
  online,
  syncing,
  queue,
  onTriggerSync,
  onDiscardQueued,
  onClearRejected,
}: StoreManagerDashboardProps) {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<"catalog" | "ledger" | "queue">("catalog");
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("ALL");
  const [lowStockOnly, setLowStockOnly] = useState(false);
  const [scannerOpen, setScannerOpen] = useState(false);

  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Create / Edit item modal
  const [itemModalOpen, setItemModalOpen] = useState(false);
  const [editingItem, setEditingItem] = useState<ItemRead | null>(null);
  const [sku, setSku] = useState("");
  const [barcode, setBarcode] = useState("");
  const [name, setName] = useState("");
  const [category, setCategory] = useState("General");
  const [unit, setUnit] = useState("pcs");
  const [reorderLevel, setReorderLevel] = useState("5");
  const [savingItem, setSavingItem] = useState(false);

  // Stock-In modal
  const [stockInItem, setStockInItem] = useState<ItemRead | null>(null);
  const [stockInQty, setStockInQty] = useState("10");
  const [stockInNotes, setStockInNotes] = useState("");
  const [submittingTx, setSubmittingTx] = useState(false);

  // Adjustment (Verified Count) modal
  const [adjustItem, setAdjustItem] = useState<ItemRead | null>(null);
  const [countedStock, setCountedStock] = useState("");
  const [adjustNotes, setAdjustNotes] = useState("");

  const itemsQuery = useQuery({
    queryKey: ["items"],
    queryFn: loadItemsOfflineFirst,
    retry: false,
  });

  const ledgerQuery = useQuery({
    queryKey: ["transactions"],
    queryFn: () => loadTransactionsOfflineFirst(150),
    retry: false,
  });

  const items = itemsQuery.data?.items ?? [];
  const categories = useMemo(
    () => Array.from(new Set(items.map((i) => i.category))).sort(),
    [items]
  );

  const lowStockItems = useMemo(() => items.filter((i) => i.is_low_stock), [items]);
  const totalUnits = useMemo(
    () => items.reduce((sum, i) => sum + Number(i.current_stock), 0),
    [items]
  );

  const filteredItems = useMemo(() => {
    const q = search.trim().toLowerCase();
    return items.filter((item) => {
      if (lowStockOnly && !item.is_low_stock) return false;
      if (categoryFilter !== "ALL" && item.category !== categoryFilter) return false;
      if (!q) return true;
      return (
        item.name.toLowerCase().includes(q) ||
        item.sku.toLowerCase().includes(q) ||
        item.category.toLowerCase().includes(q) ||
        (item.barcode?.toLowerCase().includes(q) ?? false)
      );
    });
  }, [items, search, categoryFilter, lowStockOnly]);

  const refreshData = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["items"] }),
      queryClient.invalidateQueries({ queryKey: ["transactions"] }),
    ]);
  };

  const openCreateModal = () => {
    setEditingItem(null);
    setSku("");
    setBarcode("");
    setName("");
    setCategory("General");
    setUnit("pcs");
    setReorderLevel("5");
    setItemModalOpen(true);
  };

  const openEditModal = (item: ItemRead) => {
    setEditingItem(item);
    setSku(item.sku);
    setBarcode(item.barcode ?? "");
    setName(item.name);
    setCategory(item.category);
    setUnit(item.unit);
    setReorderLevel(String(item.reorder_level));
    setItemModalOpen(true);
  };

  // Catalog metadata mutations require an active server connection to validate SKU/barcode
  // uniqueness; stock quantity changes always flow through the offline-capable ledger below.
  const handleSaveItem = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setNotice(null);
    setSavingItem(true);
    try {
      const payload = {
        sku: sku.trim().toUpperCase(),
        barcode: barcode.trim() || null,
        name: name.trim(),
        category: category.trim() || "General",
        unit: unit.trim() || "pcs",
        reorder_level: Math.max(0, Number.parseInt(reorderLevel || "5", 10)),
      };
      const url = editingItem ? `/api/v1/items/${editingItem.id}` : "/api/v1/items";
      const response = await fetch(url, {
        method: editingItem ? "PATCH" : "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error(await parseErrorDetail(response));
      setItemModalOpen(false);
      setNotice(
        editingItem
          ? `Updated catalog metadata for ${payload.name}.`
          : `Created catalog item ${payload.name} (${payload.sku}).`
      );
      await refreshData();
    } catch (err) {
      setError(formatNetworkError(err, "Unable to save catalog item (requires online connection)."));
    } finally {
      setSavingItem(false);
    }
  };

  // Record STOCK_IN through the idempotent IndexedDB queue so intake works offline or online.
  const handleStockInSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!stockInItem) return;
    const qty = Number(stockInQty);
    if (!Number.isFinite(qty) || qty <= 0) {
      setError("Quantity to add must be greater than zero.");
      return;
    }
    setError(null);
    setNotice(null);
    setSubmittingTx(true);
    try {
      const queued = await enqueueOfflineTransaction({
        item: stockInItem,
        transaction_type: "STOCK_IN",
        quantity: qty,
        notes: stockInNotes || "Incoming stock intake",
      });
      setStockInItem(null);
      setStockInQty("10");
      setStockInNotes("");
      await refreshData();
      if (online) {
        await onTriggerSync();
        setNotice(
          `Added +${formatQty(qty)} ${queued.item_unit} to ${queued.item_name}. New balance: ${formatQty(queued.remaining_stock_preview)} ${queued.item_unit}.`
        );
      } else {
        setNotice(
          `Saved offline: +${formatQty(qty)} ${queued.item_unit} for ${queued.item_name} (will auto-sync when online).`
        );
      }
    } finally {
      setSubmittingTx(false);
    }
  };

  // Record a verified physical shelf count as an ADJUSTMENT event (server computes the delta).
  const handleAdjustmentSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!adjustItem) return;
    const target = Number(countedStock);
    if (!Number.isFinite(target) || target < 0) {
      setError("Verified count must be zero or greater.");
      return;
    }
    if (target === Number(adjustItem.current_stock)) {
      setError("Verified count matches current stock; no adjustment needed.");
      return;
    }
    setError(null);
    setNotice(null);
    setSubmittingTx(true);
    try {
      const queued = await enqueueOfflineTransaction({
        item: adjustItem,
        transaction_type: "ADJUSTMENT",
        counted_stock: target,
        notes: adjustNotes || "Verified physical stock count",
      });
      setAdjustItem(null);
      setCountedStock("");
      setAdjustNotes("");
      await refreshData();
      if (online) {
        await onTriggerSync();
        setNotice(
          `Reconciled ${queued.item_name} from ${formatQty(queued.previous_stock_preview)} to ${formatQty(queued.remaining_stock_preview)} ${queued.item_unit}.`
        );
      } else {
        setNotice(
          `Saved adjustment offline for ${queued.item_name} (new balance: ${formatQty(queued.remaining_stock_preview)} ${queued.item_unit}).`
        );
      }
    } finally {
      setSubmittingTx(false);
    }
  };

  const handleBarcodeDetected = (code: string) => {
    const matched = items.find(
      (i) => i.barcode?.toLowerCase() === code.toLowerCase() || i.sku.toLowerCase() === code.toLowerCase()
    );
    if (matched) {
      setSearch(matched.sku);
      setStockInItem(matched);
      setStockInQty("10");
      setStockInNotes("");
      setNotice(`Scanned ${matched.name} (${matched.sku}) — ready to log STOCK_IN.`);
    } else {
      setSearch(code);
      setError(`No catalog item matches barcode "${code}". You can register it via New Item.`);
    }
  };

  const stockInPreviewNew = stockInItem
    ? Number(stockInItem.current_stock) + Math.max(0, Number(stockInQty) || 0)
    : 0;

  const adjustDelta = adjustItem
    ? (Number(countedStock) || 0) - Number(adjustItem.current_stock)
    : 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-amber-300">
            Store Operations &amp; Stock Intake
          </p>
          <h1 className="mt-1 text-2xl font-bold tracking-tight text-zinc-50">
            Inventory Control Dashboard
          </h1>
          <p className="mt-1 text-sm text-zinc-400">
            Manage catalog items, record incoming stock, verify physical counts, and monitor low
            stock.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" variant="outline" onClick={() => setScannerOpen(true)}>
            <ScanBarcode className="h-4 w-4" />
            Scan Barcode
          </Button>
          <Button type="button" variant="outline" onClick={() => void refreshData()}>
            <RefreshCw className={`h-4 w-4 ${itemsQuery.isFetching ? "animate-spin" : ""}`} />
            Refresh
          </Button>
          <Button type="button" disabled={!online} onClick={openCreateModal}>
            <PackagePlus className="h-4 w-4" />
            New Item
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

      {/* KPI Cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card>
          <CardContent className="pt-5">
            <div className="flex items-center justify-between text-xs font-medium text-zinc-400">
              <span>Catalog Items</span>
              <Package className="h-4 w-4 text-amber-400" />
            </div>
            <p className="mt-2 text-2xl font-bold text-zinc-50">{items.length}</p>
            <p className="mt-1 text-xs text-zinc-400">{categories.length} categories</p>
          </CardContent>
        </Card>

        <Card
          className={`cursor-pointer transition-colors ${lowStockOnly ? "border-amber-400" : "hover:border-zinc-700"}`}
          onClick={() => {
            setActiveTab("catalog");
            setLowStockOnly((prev) => !prev);
          }}
        >
          <CardContent className="pt-5">
            <div className="flex items-center justify-between text-xs font-medium text-zinc-400">
              <span>Low-Stock Alerts</span>
              <AlertTriangle className="h-4 w-4 text-amber-400" />
            </div>
            <p className="mt-2 text-2xl font-bold text-amber-300">{lowStockItems.length}</p>
            <p className="mt-1 text-xs text-zinc-400">
              {lowStockOnly ? "Showing low-stock only" : "Click to filter alerts"}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-5">
            <div className="flex items-center justify-between text-xs font-medium text-zinc-400">
              <span>Total Units in Store</span>
              <ArrowDownToLine className="h-4 w-4 text-emerald-400" />
            </div>
            <p className="mt-2 text-2xl font-bold text-zinc-50">{formatQty(totalUnits)}</p>
            <p className="mt-1 text-xs text-zinc-400">Live calculated balance</p>
          </CardContent>
        </Card>

        <Card
          className="cursor-pointer transition-colors hover:border-zinc-700"
          onClick={() => setActiveTab("queue")}
        >
          <CardContent className="pt-5">
            <div className="flex items-center justify-between text-xs font-medium text-zinc-400">
              <span>Offline Queue</span>
              <UploadCloud className="h-4 w-4 text-sky-400" />
            </div>
            <p className="mt-2 text-2xl font-bold text-zinc-50">{queue.length}</p>
            <p className="mt-1 text-xs text-zinc-400">
              {queue.length === 0 ? "All synced" : "Pending upload"}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Navigation Tabs */}
      <div className="flex flex-wrap items-center gap-2 border-b border-zinc-800 pb-3">
        <Button
          type="button"
          size="sm"
          variant={activeTab === "catalog" ? "default" : "ghost"}
          onClick={() => setActiveTab("catalog")}
        >
          <Package className="h-4 w-4" />
          Inventory Catalog ({items.length})
        </Button>
        <Button
          type="button"
          size="sm"
          variant={activeTab === "ledger" ? "default" : "ghost"}
          onClick={() => setActiveTab("ledger")}
        >
          <History className="h-4 w-4" />
          Store Movement Ledger ({ledgerQuery.data?.length ?? 0})
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

      {activeTab === "catalog" && (
        <Card>
          <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <CardTitle>Store Catalog &amp; Live Stock</CardTitle>
              <CardDescription>
                {itemsQuery.data?.fromCache
                  ? "Showing locally cached catalog from IndexedDB."
                  : "Live balances calculated automatically from the immutable ledger."}
              </CardDescription>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-zinc-500" />
                <Input
                  className="h-9 w-56 pl-9 text-xs"
                  placeholder="Search SKU, name, barcode..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              <Select
                aria-label="Filter category"
                className="h-9 w-40 text-xs"
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
              <Button
                type="button"
                size="sm"
                variant={lowStockOnly ? "default" : "outline"}
                onClick={() => setLowStockOnly((prev) => !prev)}
              >
                <AlertTriangle className="h-3.5 w-3.5" />
                Low Stock
              </Button>
            </div>
          </CardHeader>

          <CardContent>
            {filteredItems.length === 0 ? (
              <p className="py-10 text-center text-sm text-zinc-400">
                No items match your current filters.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="border-b border-zinc-800 text-xs uppercase text-zinc-400">
                    <tr>
                      <th className="py-3 pr-4">Item / SKU</th>
                      <th className="py-3 pr-4">Category</th>
                      <th className="py-3 pr-4">Live Stock</th>
                      <th className="py-3 pr-4">Reorder Level</th>
                      <th className="py-3 text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/80">
                    {filteredItems.map((item) => (
                      <tr key={item.id} className="hover:bg-zinc-800/30">
                        <td className="py-3 pr-4">
                          <div className="font-medium text-zinc-100">{item.name}</div>
                          <div className="font-mono text-xs text-zinc-400">
                            {item.sku}
                            {item.barcode ? ` · BC: ${item.barcode}` : ""}
                          </div>
                        </td>
                        <td className="py-3 pr-4 text-xs text-zinc-300">{item.category}</td>
                        <td className="py-3 pr-4">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-base font-bold text-zinc-50">
                              {formatQty(item.current_stock)}
                            </span>
                            <span className="text-xs text-zinc-400">{item.unit}</span>
                            {item.current_stock <= 0 ? (
                              <Badge variant="danger">Out</Badge>
                            ) : item.is_low_stock ? (
                              <Badge variant="warning">Low</Badge>
                            ) : (
                              <Badge variant="success">Healthy</Badge>
                            )}
                          </div>
                        </td>
                        <td className="py-3 pr-4 text-xs text-zinc-400">
                          {item.reorder_level} {item.unit}
                        </td>
                        <td className="py-3 text-right">
                          <div className="flex items-center justify-end gap-1.5">
                            <Button
                              type="button"
                              size="sm"
                              variant="success"
                              onClick={() => {
                                setStockInItem(item);
                                setStockInQty("10");
                                setStockInNotes("");
                              }}
                            >
                              <Plus className="h-3.5 w-3.5" />
                              Add Stock
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              variant="outline"
                              onClick={() => {
                                setAdjustItem(item);
                                setCountedStock(String(item.current_stock));
                                setAdjustNotes("");
                              }}
                            >
                              <ClipboardCheck className="h-3.5 w-3.5" />
                              Count
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              variant="ghost"
                              disabled={!online}
                              title="Edit item metadata"
                              onClick={() => openEditModal(item)}
                            >
                              <Edit3 className="h-3.5 w-3.5" />
                            </Button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {activeTab === "ledger" && (
        <Card>
          <CardHeader>
            <CardTitle>Store Movement Ledger</CardTitle>
            <CardDescription>
              Full chronological history of stock additions, take-outs, and count adjustments.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {(ledgerQuery.data ?? []).length === 0 ? (
              <p className="py-10 text-center text-sm text-zinc-400">
                No stock transactions recorded yet.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="border-b border-zinc-800 text-xs uppercase text-zinc-400">
                    <tr>
                      <th className="py-3 pr-4">Date</th>
                      <th className="py-3 pr-4">Type</th>
                      <th className="py-3 pr-4">Item</th>
                      <th className="py-3 pr-4">Balance Change</th>
                      <th className="py-3 pr-4">Recorded By</th>
                      <th className="py-3">Notes</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/80">
                    {(ledgerQuery.data ?? []).map((tx) => (
                      <tr key={tx.id} className="hover:bg-zinc-800/30">
                        <td className="whitespace-nowrap py-3 pr-4 text-xs text-zinc-400">
                          {formatDateTime(tx.created_at)}
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
                        </td>
                        <td className="py-3 pr-4 text-xs text-zinc-300">{tx.user_full_name}</td>
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

      {/* Create / Edit Item Modal */}
      <Modal
        open={itemModalOpen}
        onClose={() => setItemModalOpen(false)}
        title={editingItem ? `Edit Item — ${editingItem.sku}` : "Register Catalog Item"}
        description="Stock quantities are always updated via STOCK_IN / ADJUSTMENT ledger entries."
      >
        <form onSubmit={(e) => void handleSaveItem(e)} className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="item-sku">SKU</Label>
              <Input
                id="item-sku"
                required
                placeholder="MED-GLV-L"
                value={sku}
                onChange={(e) => setSku(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="item-barcode">Barcode (optional)</Label>
              <Input
                id="item-barcode"
                placeholder="6001000000099"
                value={barcode}
                onChange={(e) => setBarcode(e.target.value)}
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="item-name">Item Name</Label>
            <Input
              id="item-name"
              required
              placeholder="Nitrile Gloves (Large)"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="item-category">Category</Label>
              <Input
                id="item-category"
                required
                placeholder="Medical Supplies"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="item-unit">Unit</Label>
              <Select id="item-unit" value={unit} onChange={(e) => setUnit(e.target.value)}>
                <option value="pcs">pcs</option>
                <option value="boxes">boxes</option>
                <option value="kg">kg</option>
                <option value="liters">liters</option>
                <option value="packs">packs</option>
                <option value="rolls">rolls</option>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="item-reorder">Reorder Level</Label>
              <Input
                id="item-reorder"
                type="number"
                min="0"
                step="1"
                required
                value={reorderLevel}
                onChange={(e) => setReorderLevel(e.target.value)}
              />
            </div>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="outline" onClick={() => setItemModalOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={savingItem}>
              {savingItem ? "Saving..." : editingItem ? "Save Changes" : "Create Item"}
            </Button>
          </div>
        </form>
      </Modal>

      {/* Add Stock (STOCK_IN) Quick Modal */}
      <Modal
        open={stockInItem !== null}
        onClose={() => setStockInItem(null)}
        title={`Add Stock (STOCK_IN) — ${stockInItem?.name ?? ""}`}
        description="Automatically increments store stock inside an atomic transaction (works offline)."
      >
        {stockInItem && (
          <form onSubmit={(e) => void handleStockInSubmit(e)} className="space-y-4">
            <div className="flex items-center justify-between rounded-xl border border-zinc-800 bg-zinc-950 p-4">
              <div>
                <p className="text-xs text-zinc-400">Current Stock</p>
                <p className="font-mono text-lg font-bold text-zinc-200">
                  {formatQty(stockInItem.current_stock)} {stockInItem.unit}
                </p>
              </div>
              <div className="text-right">
                <p className="text-xs text-emerald-400">After Intake</p>
                <p className="font-mono text-lg font-bold text-emerald-300">
                  {formatQty(stockInPreviewNew)} {stockInItem.unit}
                </p>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="stock-in-qty">Quantity to Add ({stockInItem.unit})</Label>
              <Input
                id="stock-in-qty"
                type="number"
                min="0.001"
                step="any"
                required
                value={stockInQty}
                onChange={(e) => setStockInQty(e.target.value)}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="stock-in-notes">Reference / Delivery Note (optional)</Label>
              <Input
                id="stock-in-notes"
                placeholder="e.g. Supplier invoice #INV-402"
                value={stockInNotes}
                onChange={(e) => setStockInNotes(e.target.value)}
              />
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <Button type="button" variant="outline" onClick={() => setStockInItem(null)}>
                Cancel
              </Button>
              <Button type="submit" variant="success" disabled={submittingTx}>
                {submittingTx ? "Recording..." : online ? "Record STOCK_IN" : "Queue Offline"}
              </Button>
            </div>
          </form>
        )}
      </Modal>

      {/* Verified Stock Count (ADJUSTMENT) Modal */}
      <Modal
        open={adjustItem !== null}
        onClose={() => setAdjustItem(null)}
        title={`Verified Stock Count — ${adjustItem?.name ?? ""}`}
        description="Reconcile physical shelf count against system stock via an immutable ADJUSTMENT entry."
      >
        {adjustItem && (
          <form onSubmit={(e) => void handleAdjustmentSubmit(e)} className="space-y-4">
            <div className="flex items-center justify-between rounded-xl border border-zinc-800 bg-zinc-950 p-4">
              <div>
                <p className="text-xs text-zinc-400">System Balance</p>
                <p className="font-mono text-lg font-bold text-zinc-200">
                  {formatQty(adjustItem.current_stock)} {adjustItem.unit}
                </p>
              </div>
              <div className="text-right">
                <p className="text-xs text-sky-400">Variance</p>
                <p className="font-mono text-lg font-bold text-sky-300">
                  {adjustDelta > 0 ? `+${formatQty(adjustDelta)}` : formatQty(adjustDelta)}{" "}
                  {adjustItem.unit}
                </p>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="counted-stock">Verified Physical Count ({adjustItem.unit})</Label>
              <Input
                id="counted-stock"
                type="number"
                min="0"
                step="any"
                required
                value={countedStock}
                onChange={(e) => setCountedStock(e.target.value)}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="adjust-notes">Reason / Audit Note</Label>
              <Input
                id="adjust-notes"
                required
                placeholder="e.g. Monthly shelf recount — 2 damaged boxes removed"
                value={adjustNotes}
                onChange={(e) => setAdjustNotes(e.target.value)}
              />
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <Button type="button" variant="outline" onClick={() => setAdjustItem(null)}>
                Cancel
              </Button>
              <Button type="submit" disabled={submittingTx}>
                {submittingTx ? "Saving..." : "Post Adjustment"}
              </Button>
            </div>
          </form>
        )}
      </Modal>

      <BarcodeScannerModal
        open={scannerOpen}
        onClose={() => setScannerOpen(false)}
        onDetected={handleBarcodeDetected}
      />
    </div>
  );
}
