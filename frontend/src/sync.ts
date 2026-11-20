import {
  authHeaders,
  formatNetworkError,
  getStoredToken,
  parseErrorDetail,
  setStoredToken,
} from "./auth";
import type {
  ItemRead,
  TransactionCreate,
  TransactionRead,
  TransactionSyncResponse,
  TransactionType,
} from "./api/types";
import { db, type QueuedTransaction } from "./db";

export interface SyncSummary {
  created: number;
  replayed: number;
  rejected: number;
  networkFailed: boolean;
  authExpired: boolean;
  lastError: string | null;
}

/**
 * Persist a stock mutation to the local IndexedDB outbox and optimistically update
 * the cached item's displayed balance in a single atomic Dexie transaction.
 *
 * Every queued entry receives a fresh UUIDv4 `idempotency_key` so retries or
 * Workbox BackgroundSync replays can never double-apply the balance change.
 */
export async function enqueueOfflineTransaction(input: {
  item: ItemRead;
  transaction_type: TransactionType;
  quantity?: number;
  counted_stock?: number;
  notes?: string;
}): Promise<QueuedTransaction> {
  const previous = Number(input.item.current_stock);
  let remaining = previous;
  if (input.transaction_type === "STOCK_IN") {
    remaining = previous + Number(input.quantity ?? 0);
  } else if (input.transaction_type === "STOCK_OUT") {
    remaining = Math.max(0, previous - Number(input.quantity ?? 0));
  } else if (input.transaction_type === "ADJUSTMENT") {
    remaining = Math.max(0, Number(input.counted_stock ?? previous));
  }

  const queued: QueuedTransaction = {
    idempotency_key: crypto.randomUUID(),
    item_id: input.item.id,
    item_sku: input.item.sku,
    item_name: input.item.name,
    item_unit: input.item.unit,
    transaction_type: input.transaction_type,
    quantity: input.quantity,
    counted_stock: input.counted_stock,
    previous_stock_preview: previous,
    remaining_stock_preview: remaining,
    notes: input.notes?.trim() || undefined,
    created_at: Date.now(),
    attempts: 0,
  };

  await db.transaction("rw", db.items, db.transactionQueue, async () => {
    await db.transactionQueue.put(queued);
    await db.items.update(input.item.id, {
      current_stock: remaining,
      is_low_stock: remaining <= input.item.reorder_level,
      updated_at: new Date().toISOString(),
    });
  });

  return queued;
}

/**
 * Flush all queued offline transactions to `/api/v1/transactions/sync` in FIFO order.
 *
 * Per-entry reconciliation:
 * - `created` / `replayed`: remove from outbox and overwrite cached stock with server balance.
 * - `rejected`: keep in outbox with the server's error message so the user can review/discard it.
 * - Network failure: retain all entries and increment attempt counters for the next reconnect.
 */
export async function syncTransactionQueue(): Promise<SyncSummary> {
  const summary: SyncSummary = {
    created: 0,
    replayed: 0,
    rejected: 0,
    networkFailed: false,
    authExpired: false,
    lastError: null,
  };

  if (!getStoredToken()) return summary;

  const pending = await db.transactionQueue.orderBy("created_at").toArray();
  if (pending.length === 0) return summary;

  const payload: TransactionCreate[] = pending.map((entry) => ({
    idempotency_key: entry.idempotency_key,
    item_id: entry.item_id,
    transaction_type: entry.transaction_type,
    quantity: entry.transaction_type === "ADJUSTMENT" ? null : entry.quantity ?? null,
    counted_stock: entry.transaction_type === "ADJUSTMENT" ? entry.counted_stock ?? null : null,
    notes: entry.notes ?? null,
  }));

  try {
    const response = await fetch("/api/v1/transactions/sync", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ transactions: payload }),
    });

    if (response.status === 401) {
      setStoredToken(null);
      summary.authExpired = true;
      summary.lastError = "Your session expired. Sign in again to sync queued transactions.";
      return summary;
    }

    if (!response.ok) {
      throw new Error(await parseErrorDetail(response));
    }

    const data = (await response.json()) as TransactionSyncResponse;
    const entryByKey = new Map(pending.map((p) => [p.idempotency_key, p]));

    for (const res of data.results) {
      const queued = entryByKey.get(res.idempotency_key);
      if (res.status === "created" || res.status === "replayed") {
        await db.transactionQueue.delete(res.idempotency_key);
        if (res.transaction) {
          await db.transactionsCache.put(res.transaction);
          const cachedItem = await db.items.get(res.transaction.item_id);
          if (cachedItem) {
            await db.items.update(cachedItem.id, {
              current_stock: res.transaction.remaining_stock,
              is_low_stock: res.transaction.remaining_stock <= cachedItem.reorder_level,
            });
          }
        }
        if (res.status === "created") summary.created += 1;
        else summary.replayed += 1;
      } else {
        summary.rejected += 1;
        summary.lastError = res.error ?? "Transaction rejected by server.";
        await db.transactionQueue.update(res.idempotency_key, {
          attempts: (queued?.attempts ?? 0) + 1,
          error: summary.lastError,
        });
      }
    }
  } catch (error) {
    summary.networkFailed = true;
    summary.lastError = formatNetworkError(
      error,
      "Unable to sync right now. Queued transactions will retry when online."
    );
    for (const entry of pending) {
      await db.transactionQueue.update(entry.idempotency_key, {
        attempts: entry.attempts + 1,
      });
    }
  }

  return summary;
}

export async function discardQueuedTransaction(idempotencyKey: string): Promise<void> {
  await db.transactionQueue.delete(idempotencyKey);
}

export async function clearRejectedTransactions(): Promise<number> {
  const rejected = await db.transactionQueue.filter((q) => Boolean(q.error)).toArray();
  await db.transactionQueue.bulkDelete(rejected.map((q) => q.idempotency_key));
  return rejected.length;
}

/**
 * Fetch the live catalog from the server and overlay any un-synced local queue deltas
 * so optimistic balances remain consistent until sync completes. Falls back to the
 * IndexedDB `items` cache whenever the device is offline or unreachable.
 */
export async function loadItemsOfflineFirst(): Promise<{ items: ItemRead[]; fromCache: boolean }> {
  try {
    const response = await fetch("/api/v1/items?limit=500", { headers: authHeaders() });
    if (response.status === 401) {
      setStoredToken(null);
      throw new Error("AUTH_EXPIRED");
    }
    if (!response.ok) {
      throw new Error(await parseErrorDetail(response));
    }
    const items = (await response.json()) as ItemRead[];
    // Re-apply optimistic pending deltas on top of server items if queue is non-empty.
    const pending = await db.transactionQueue.orderBy("created_at").toArray();
    const adjusted = items.map((item) => {
      const queuedForItem = pending.filter((q) => q.item_id === item.id && !q.error);
      if (queuedForItem.length === 0) return item;
      let stock = Number(item.current_stock);
      for (const q of queuedForItem) {
        if (q.transaction_type === "STOCK_IN") stock += Number(q.quantity ?? 0);
        else if (q.transaction_type === "STOCK_OUT") stock = Math.max(0, stock - Number(q.quantity ?? 0));
        else if (q.transaction_type === "ADJUSTMENT") stock = Number(q.counted_stock ?? stock);
      }
      return {
        ...item,
        current_stock: stock,
        is_low_stock: stock <= item.reorder_level,
      };
    });
    await db.transaction("rw", db.items, async () => {
      await db.items.clear();
      await db.items.bulkPut(adjusted);
    });
    return { items: adjusted, fromCache: false };
  } catch (error) {
    if (error instanceof Error && error.message === "AUTH_EXPIRED") throw error;
    const cached = await db.items.orderBy("sku").toArray();
    if (cached.length > 0) {
      return { items: cached, fromCache: true };
    }
    throw new Error(formatNetworkError(error, "Unable to load inventory catalog."));
  }
}

export async function loadTransactionsOfflineFirst(limit = 100): Promise<TransactionRead[]> {
  try {
    const response = await fetch(`/api/v1/transactions?limit=${limit}`, {
      headers: authHeaders(),
    });
    if (response.status === 401) {
      setStoredToken(null);
      throw new Error("AUTH_EXPIRED");
    }
    if (!response.ok) {
      throw new Error(await parseErrorDetail(response));
    }
    const rows = (await response.json()) as TransactionRead[];
    await db.transaction("rw", db.transactionsCache, async () => {
      await db.transactionsCache.clear();
      await db.transactionsCache.bulkPut(rows);
    });
    return rows;
  } catch (error) {
    if (error instanceof Error && error.message === "AUTH_EXPIRED") throw error;
    return await db.transactionsCache.orderBy("created_at").reverse().toArray();
  }
}
