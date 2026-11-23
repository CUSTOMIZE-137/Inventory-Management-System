import Dexie, { type Table } from "dexie";
import type { ItemRead, TransactionRead, TransactionType } from "./api/types";

export interface QueuedTransaction {
  idempotency_key: string;
  item_id: string;
  item_sku: string;
  item_name: string;
  item_unit: string;
  transaction_type: TransactionType;
  stock_source?: "purchased" | "donated" | null;
  quantity?: number;
  counted_stock?: number;
  previous_stock_preview: number;
  remaining_stock_preview: number;
  notes?: string;
  created_at: number;
  attempts: number;
  error?: string;
}

/**
 * Local IndexedDB schema (via Dexie) powering offline-first browsing and queued writes:
 * - `items`: cached catalog with optimistic local stock previews.
 * - `transactionQueue`: FIFO outbox keyed by client-generated UUIDv4 `idempotency_key`.
 * - `transactionsCache`: last fetched read-only ledger snapshot for offline history views.
 */
class InventoryDatabase extends Dexie {
  items!: Table<ItemRead, string>;
  transactionQueue!: Table<QueuedTransaction, string>;
  transactionsCache!: Table<TransactionRead, string>;

  constructor() {
    super("InventoryPWA_v2");
    this.version(1).stores({
      items: "id, sku, barcode, category, updated_at",
      transactionQueue: "idempotency_key, item_id, transaction_type, created_at",
      transactionsCache: "id, item_id, user_id, created_at",
    });
  }
}

export const db = new InventoryDatabase();

/** Ask the browser for persistent storage so queued offline mutations are not evicted under disk pressure. */
export async function requestPersistentStorage(): Promise<boolean> {
  return navigator.storage?.persist ? navigator.storage.persist() : false;
}
