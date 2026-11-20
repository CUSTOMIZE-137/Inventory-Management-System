# Inventory Management System (PWA)

A focused, zero-bloat, offline-first **Inventory Management System** built with **FastAPI (async)**, **SQLAlchemy 2.0 (async)**, **PostgreSQL 16**, **React 19 + TypeScript (strict)**, **Tailwind CSS + shadcn/ui**, **TanStack Query v5**, **Dexie.js (IndexedDB)**, and **Workbox (`vite-plugin-pwa`)**.

## Core Role-Based Access Control (RBAC)

The entire application is built around three strict user roles:

| Role | Scope & Permissions | Restrictions |
|---|---|---|
| **`ADMIN`** (System Administrator) | Create/update users, assign roles, activate/deactivate accounts, reset credentials, inspect global audit logs and system KPIs | Governance and oversight focus; does not record operational store intake/take-out |
| **`STORE_MANAGER`** (Store Manager) | Create/edit catalog items (SKU, barcode, name, category, unit, reorder level), record incoming stock (`STOCK_IN`), perform verified count corrections (`ADJUSTMENT`), view low-stock alerts and full store ledger | Cannot manage users or assign roles; cannot log `STOCK_OUT` |
| **`REQUESTOR`** (Requestor) | Search/scan available store items with live stock levels, take out stock (`STOCK_OUT`) with live before/after remaining-stock preview, view personal take-out history | Read-only catalog access; cannot log `STOCK_IN`, edit items, or manage users |

## Architecture Highlights

- **3-Table Streamlined Schema (via Alembic)**: `users`, `items`, and immutable `stock_transactions`.
- **Automatic Atomic Stock Calculation**: Every `STOCK_IN`, `STOCK_OUT`, and `ADJUSTMENT` runs inside a single ACID transaction with `SELECT ... FOR UPDATE` row-level locking on `items`, validating `current_stock >= requested_quantity` (rejecting overdraws with `400 Bad Request` stating the exact available quantity), updating `items.current_stock`, and inserting an immutable ledger snapshot (`previous_stock`, `remaining_stock`).
- **Idempotent Offline Replay**: All write endpoints (`POST /api/v1/transactions` and `POST /api/v1/transactions/sync`) require a client-generated UUIDv4 `idempotency_key`. Replaying a duplicate key returns `200 OK` with the original transaction without double-calculating stock.
- **Offline-First PWA**: App shell assets are cached (`CacheFirst`), items and pending transactions live in IndexedDB (`Dexie.js`), and failed `POST /api/v1/transactions*` calls are backed by Workbox `BackgroundSyncPlugin` (`stock-transactions-queue`).
- **Barcode Scanning**: Uses native `BarcodeDetector` with lazy-loaded `zxing-wasm` fallback.

## API Reference (`/api/v1`)

| Method | Endpoint | Allowed Role(s) | Purpose |
|---|---|---|---|
| `POST` | `/api/v1/auth/login` | Public (rate-limited) | Issue JWT access token + user profile |
| `GET` | `/api/v1/auth/me` | Any authenticated | Active user profile |
| `GET`, `POST`, `PATCH` | `/api/v1/users`, `/api/v1/users/{id}` | `ADMIN` | Provision users, update roles, toggle active, reset passwords |
| `GET` | `/api/v1/audit/summary` | `ADMIN` | System-wide user, catalog, low-stock, and ledger summary |
| `GET` | `/api/v1/items`, `/api/v1/items/{id}`, `/api/v1/items/lookup/{code}` | Any authenticated | Browse catalog, filter low stock, lookup by barcode/SKU |
| `POST`, `PATCH` | `/api/v1/items`, `/api/v1/items/{id}` | `STORE_MANAGER` | Create/edit catalog metadata (stock always mutated via ledger) |
| `POST` | `/api/v1/transactions` | `STORE_MANAGER` (`STOCK_IN`, `ADJUSTMENT`), `REQUESTOR` (`STOCK_OUT`) | Atomic stock transaction (`201 Created`, or `200 OK` on idempotent replay) |
| `POST` | `/api/v1/transactions/sync` | `STORE_MANAGER`, `REQUESTOR` | Batch idempotent replay of offline Dexie queue |
| `GET` | `/api/v1/transactions` | Any authenticated | Global ledger (`ADMIN`, `STORE_MANAGER`) or personal history (`REQUESTOR`) |
| `GET` | `/health`, `/ready` | Public | Liveness and async database readiness probes |

## Local Development

### 1. Backend (FastAPI + Alembic + SQLite or PostgreSQL)

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

$env:DATABASE_URL = "sqlite+aiosqlite:///./inventory_dev.db"
$env:JWT_SECRET = "local-dev-secret-key-with-at-least-32-chars"

.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m app.cli create-superuser --email admin@your-domain.org --name "System Administrator"
.\.venv\Scripts\uvicorn.exe app.main:app --reload --host 127.0.0.1 --port 8000
```

The `create-superuser` command prompts for a password and creates an active `ADMIN`
account. Use the admin dashboard to create the `STORE_MANAGER` and `REQUESTOR`
accounts required by your organization.

### 2. Frontend (React 19 + Vite PWA)

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. To regenerate strict TypeScript OpenAPI types against a running backend:

```powershell
npm run generate:api
```

### 3. Production Stack via Docker Compose

Runs PostgreSQL 16, FastAPI (`:8000`), and Nginx serving the built PWA (`:80`):

```powershell
docker compose up --build
```

Create the initial administrator inside the running backend container:

```powershell
docker compose exec backend python -m app.cli create-superuser --email admin@your-domain.org --name "System Administrator"
```

Open `http://localhost` (PWA) or `http://localhost:8000/docs` (OpenAPI docs).

### 4. Deploy Full-Stack on Vercel (Vite PWA + FastAPI + Neon Postgres)

The root `vercel.json` deploys both `frontend/` (Vite) and `backend/` (FastAPI `app.main:app`) as a single same-origin Vercel project.

1. Import this GitHub repository in the **Vercel Dashboard** (leave Root Directory as repository root).
2. Add the following **Environment Variables** in Project Settings (Production & Preview):
   - `DATABASE_URL` — your Neon / PostgreSQL pooled connection string (e.g. `postgresql://...`)
   - `DATABASE_URL_UNPOOLED` — your direct (non-pooler) PostgreSQL URL for Alembic migrations (optional if using Neon's `-pooler.` naming)
   - `JWT_SECRET` — a random secret of at least 32 characters
   - `ACCESS_TOKEN_MINUTES` — `60` (optional)
3. Run database migrations and create the initial administrator once against your hosted PostgreSQL database:
   ```powershell
   cd backend
   .\.venv\Scripts\alembic.exe upgrade head
   .\.venv\Scripts\python.exe -m app.cli create-superuser --email admin@your-domain.org --name "System Administrator"
   ```
4. Deploy via Git push (auto-deploys on `main`) or run `npx vercel --prod` from the repository root.

## Automated Verification (`pytest`)

From the repository root:

```powershell
python -m pytest -v
```

Tests verify:
1. Strict RBAC enforcement across `ADMIN`, `STORE_MANAGER`, and `REQUESTOR`.
2. Automatic stock calculation (`STOCK_IN`, `STOCK_OUT`, `ADJUSTMENT`), `400 Bad Request` on insufficient stock, and concurrent `STOCK_OUT` non-negative safety.
3. Idempotent offline replay (`200 OK` on duplicate `idempotency_key` and batch `/api/v1/transactions/sync`).
4. Full preserved Alembic migration chain (`alembic upgrade head`).

## Lighthouse PWA & Offline Verification Steps

1. Build and serve the production bundle (`docker compose up --build` or `cd frontend && npm run build && npm run preview`).
2. Open the app in Chrome and inspect **DevTools → Application**:
   - **Manifest**: Confirm name, icons (`192x192`, `512x512`, `180x180`), `display: standalone`, and `start_url: /`.
   - **Service Workers**: Confirm `sw.js` is activated and controlling the page.
   - **Cache Storage**: Confirm Workbox precache and `app-shell-assets` (`CacheFirst`) are populated.
   - **IndexedDB**: Confirm `InventoryPWA_v2` contains `items`, `transactionQueue`, and `transactionsCache`.
3. Sign in with an account created by the administrator and verify the top bar displays **`Online · Synced`** alongside the role badge.
4. In **DevTools → Network**, toggle throttling to **Offline**:
   - Confirm the top status bar switches to **`Offline Mode`**.
   - Reload the page — verify the App Shell and cached items load immediately from Service Worker + IndexedDB.
   - Record a `STOCK_OUT` (or `STOCK_IN`) entry — verify the live stock balance updates immediately and the badge shows **`Offline Mode · 1 queued`**.
5. Restore network connectivity (**No throttling**):
   - Confirm the queue drains automatically via `/api/v1/transactions/sync` (with UUIDv4 idempotency protection) and the status bar returns to **`Online · Synced`**.
   - Verify no duplicate transactions appear in the ledger.
6. Run **DevTools → Lighthouse** (Navigation mode, Mobile/Desktop) to verify installability, service worker registration, offline start-URL support, and accessibility scores.
