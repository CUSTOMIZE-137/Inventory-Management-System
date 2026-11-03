# Inventory Management System

A FastAPI and PostgreSQL backend foundation for auditable, multi-location inventory. The implementation includes catalog and location endpoints, user and role management, JWT authentication with rate limiting, purchase order receiving, a staged requisition workflow with partial quantity approvals, stock receipts/issues/adjustments/transfers, audit movement queries, idempotency keys, and low-stock, valuation, and consumption reports.

## Architecture

- **API:** FastAPI with modular routers (`app/routers/auth.py`, `catalog.py`, `inventory.py`, `purchase_orders.py`, `requisitions.py`), Pydantic request/response validation, and CORS middleware.
- **Database:** PostgreSQL via SQLAlchemy 2.x and psycopg with lazy engine initialization (`app/db.py`). Stock updates run in the request's database transaction, lock balance rows using `SELECT ... FOR UPDATE`, and reject any resulting negative balance.
- **Authentication:** JWT bearer tokens (`sub`, `iat`, `exp`, `jti`); Argon2 password hashing; sliding-window login rate limiting (`429 Too Many Requests`).
- **Authorization:** Roles map to explicit permissions in `user_roles`, `roles`, `permissions`, and `role_permissions`.
- **Audit:** Posted stock transactions and per-location balance changes are stored separately. The PostgreSQL migration adds triggers that reject update, delete, and truncate operations on stock audit tables.
- **Approvals:** Requisitions progress through supervisor review, store-manager approval (supporting partial line quantity approvals), dispatch, and requester receipt confirmation. Restricted or approval-required items cannot be issued through the generic issue route.

## Run locally

Requirements: Python 3.10+ (and PostgreSQL 14+ or Docker Compose for production-grade database features; SQLite is also supported for quick local evaluation).

### Option 1: Quick Start (Local SQLite + Demo Data, No Postgres Required)

If you want to run the backend and branded web UI immediately on your machine without setting up PostgreSQL:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

# Configure SQLite and JWT secret for local development
$env:DATABASE_URL = "sqlite+pysqlite:///./inventory_dev.db"
$env:JWT_SECRET = "local-dev-secret-key-with-at-least-32-chars"

# Seed roles, demo accounts, catalog items, locations, and opening stock
python -m app.cli seed-demo

# Start the FastAPI server (serves both the Web UI and REST API)
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Then open **`http://127.0.0.1:8000`** in your browser to access the web interface.

#### Pre-seeded Demo Accounts (`python -m app.cli seed-demo`)

| Role | Email | Password |
|---|---|---|
| System Administrator | `admin@example.org` | `AdminPassword123!` |
| Stock Supervisor | `supervisor@example.org` | `AdminPassword123!` |
| Central Store Manager | `manager@example.org` | `AdminPassword123!` |
| Stock Requester | `requester@example.org` | `AdminPassword123!` |

---

### Option 2: Local Virtual Environment with PostgreSQL

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `DATABASE_URL` in `backend\.env` to the PostgreSQL connection string for your database (for example, `postgresql+psycopg://inventory:change-me@localhost:5432/inventory_db`). Replace `JWT_SECRET` with a random value of at least 32 characters. Create the database before applying migrations.

```powershell
alembic upgrade head
python -m app.cli init-roles

# Either seed the full demo dataset:
python -m app.cli seed-demo

# Or provision individual accounts manually:
python -m app.cli create-admin --email admin@example.org --name "System Administrator"
python -m app.cli create-user --email clerk@example.org --name "Warehouse Clerk" --role warehouse_clerk --department "Logistics"

uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

---

### Option 3: Using Docker Compose

```powershell
docker compose up --build
```

Once the containers are running, you can seed the demo accounts and sample inventory in a second terminal:

```powershell
docker compose exec backend python -m app.cli seed-demo
```

---

### Accessing the System Locally

- **Web User Interface:** `http://127.0.0.1:8000/` (serves `frontend/index.html` and company branding at `/logo.png`)
- **Interactive API Docs (Swagger UI):** `http://127.0.0.1:8000/docs`
- **Liveness Check:** `http://127.0.0.1:8000/health`
- **Database Readiness Check:** `http://127.0.0.1:8000/ready`

When schema models change, create and review a migration before applying it:

```powershell
alembic revision --autogenerate -m "describe schema change"
alembic upgrade head
```

## API outline

All endpoints except login, health, and readiness require a bearer token. Permission names are indicated in the table.

| Method | Route | Permission / purpose |
|---|---|---|
| `POST` | `/api/auth/login` | Public (rate-limited); issue JWT |
| `GET` | `/api/auth/me` | Authenticated; current user profile, roles, and permissions |
| `GET`, `POST`, `PATCH` | `/api/users`, `/api/users/{id}`, `/api/roles` | `users:manage`; user & role management |
| `GET`, `POST`, `PATCH` | `/api/items`, `/api/items/{id}` | `catalog:read`, `catalog:write` |
| `GET`, `POST` | `/api/categories`, `/api/departments`, `/api/units` | `catalog:read`, `catalog:write` |
| `GET`, `POST`, `PATCH` | `/api/vendors`, `/api/vendors/{id}` | `catalog:read`, `catalog:write` |
| `GET`, `POST`, `PATCH` | `/api/locations`, `/api/locations/{id}` | `inventory:read`, `catalog:write`; enforces `warehouse > room > shelf > bin` hierarchy |
| `GET` | `/api/stock?item_id=&location_id=&limit=100&offset=0` | `inventory:read`; paginated |
| `GET`, `POST` | `/api/purchase-orders`, `/api/purchase-orders/{id}` | `inventory:read`, `catalog:write`; list/inspect/create POs with lines |
| `POST` | `/api/purchase-orders/{id}/receive` | `inventory:transact`; receive and track PO quantities |
| `GET`, `POST` | `/api/transactions`, `/api/transactions/{id}` | `inventory:read`, `inventory:transact`; query or post receipt, issue, adjustment |
| `GET` | `/api/movements?item_id=&location_id=&transaction_id=` | `inventory:read`; query append-only audit movements |
| `POST` | `/api/transfers` | `inventory:transact`; atomic transfer |
| `GET`, `POST` | `/api/requisitions`, `/api/requisitions/{id}` | `requisitions:request` or `requisitions:approve` / `requisitions:dispatch` |
| `POST` | `/api/requisitions/{id}/approvals` | `requisitions:approve`; supervisor then store-manager decision (with optional `line_approvals`) |
| `POST` | `/api/requisitions/{id}/dispatch` | `requisitions:dispatch` and `inventory:transact` |
| `POST` | `/api/requisitions/{id}/confirm` | `requisitions:confirm`; requester confirmation |
| `GET` | `/api/reports/low-stock?limit=100&offset=0` | `reports:read`; paginated |
| `GET` | `/api/reports/valuation` | `reports:read`; active catalog standard-cost valuation and per-item breakdown |
| `GET` | `/api/reports/consumption?days=30&limit=100&offset=0` | `reports:read`; total and average daily issue quantity, paginated |
| `GET` | `/api/reports/{low-stock\|valuation\|consumption}/export?format=csv` | `reports:read`; CSV download (consumption accepts `days`) |

Stock requests accept an optional `Idempotency-Key` header. Reusing the key with the same request returns the original transaction; reusing it for a different payload returns `409 Conflict`.

## Validate

From the repository root:

```powershell
python -m pytest
```

Run the PostgreSQL concurrency integration test against a dedicated disposable database:

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://user:password@localhost/inventory_test"
python -m pytest -m postgresql
Remove-Item Env:TEST_DATABASE_URL
```
