import { AlertTriangle, CheckCircle2, RefreshCw, Trash2, WifiOff } from "lucide-react";
import type { QueuedTransaction } from "../db";
import { formatDateTime, formatQty } from "../lib/utils";
import { Badge, Button, Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/primitives";

interface SyncQueuePanelProps {
  queue: QueuedTransaction[];
  online: boolean;
  syncing: boolean;
  onSyncNow: () => void;
  onDiscard: (key: string) => void;
  onClearRejected: () => void;
}

export function SyncQueuePanel({
  queue,
  online,
  syncing,
  onSyncNow,
  onDiscard,
  onClearRejected,
}: SyncQueuePanelProps) {
  const rejectedCount = queue.filter((q) => Boolean(q.error)).length;

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <CardTitle>Offline Transaction Queue</CardTitle>
          <CardDescription>
            Idempotent UUIDv4 stock entries waiting to sync with the server ledger.
          </CardDescription>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            size="sm"
            disabled={!online || syncing || queue.length === 0}
            onClick={onSyncNow}
          >
            <RefreshCw className={`h-3.5 w-3.5 ${syncing ? "animate-spin" : ""}`} />
            {syncing ? "Syncing..." : "Sync Now"}
          </Button>
          {rejectedCount > 0 && (
            <Button type="button" size="sm" variant="destructive" onClick={onClearRejected}>
              Clear Rejected ({rejectedCount})
            </Button>
          )}
        </div>
      </CardHeader>

      <CardContent className="space-y-3">
        {!online && (
          <div className="flex items-center gap-2.5 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3.5 py-2.5 text-sm text-amber-200">
            <WifiOff className="h-4 w-4 shrink-0" />
            <span>
              You are in Offline Mode. Entries saved in IndexedDB will automatically sync when
              connectivity returns.
            </span>
          </div>
        )}

        {queue.length === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-zinc-800 py-10 text-center">
            <CheckCircle2 className="h-8 w-8 text-emerald-400" />
            <p className="mt-2 font-medium text-zinc-200">All transactions synced</p>
            <p className="mt-1 text-xs text-zinc-400">
              No pending offline stock additions, take-outs, or count adjustments on this device.
            </p>
          </div>
        ) : (
          <div className="divide-y divide-zinc-800 rounded-xl border border-zinc-800 bg-zinc-950/60">
            {queue.map((entry) => (
              <div
                key={entry.idempotency_key}
                className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold text-zinc-100">{entry.item_name}</span>
                    <span className="font-mono text-xs text-zinc-400">{entry.item_sku}</span>
                    <Badge
                      variant={
                        entry.transaction_type === "STOCK_IN"
                          ? "success"
                          : entry.transaction_type === "STOCK_OUT"
                            ? "warning"
                            : "info"
                      }
                    >
                      {entry.transaction_type}
                    </Badge>
                  </div>
                  <p className="text-xs text-zinc-400">
                    {entry.transaction_type === "ADJUSTMENT"
                      ? `Verified count: ${formatQty(entry.counted_stock ?? 0)} ${entry.item_unit}`
                      : `Qty: ${entry.transaction_type === "STOCK_IN" ? "+" : "-"}${formatQty(entry.quantity ?? 0)} ${entry.item_unit}`}
                    {" · "}
                    Preview: {formatQty(entry.previous_stock_preview)} →{" "}
                    <strong className="text-zinc-200">
                      {formatQty(entry.remaining_stock_preview)} {entry.item_unit}
                    </strong>
                    {" · "}
                    Queued {formatDateTime(new Date(entry.created_at).toISOString())}
                  </p>
                  {entry.notes && <p className="text-xs text-zinc-400">Note: {entry.notes}</p>}
                  {entry.error && (
                    <p className="flex items-center gap-1.5 text-xs text-red-400">
                      <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                      {entry.error}
                    </p>
                  )}
                </div>

                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => onDiscard(entry.idempotency_key)}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Discard
                </Button>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
