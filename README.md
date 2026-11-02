# Inventory Management System

A FastAPI and PostgreSQL backend foundation for auditable, multi-location inventory. The implementation includes catalog and location endpoints, role-based access, JWT authentication, purchase order receiving, a staged requisition workflow, stock receipts/issues/adjustments/transfers, audit movements, idempotency keys, and low-stock, valuation, and consumption reports.

## Architecture

- **API:** FastAPI with Pydantic request/response validation.
- **Database:** PostgreSQL via SQLAlchemy 2.x and psycopg. Stock updates run in the request's database transaction, lock balance rows using `SELECT ... FOR UPDATE`, and reject any resulting negative balance.
- **Authentication:** JWT bearer tokens; Argon2 password hashing.
- **Authorization:** Roles map to explicit permissions in `user_roles`, `roles`, `permissions`, and `role_permissions`.
- **Audit:** Posted stock transactions and per-location balance changes are stored separately. The PostgreSQL migration adds triggers that reject update, delete, and truncate operations on stock audit tables.
- **Approvals:** Requisitions progress through supervisor review, store-manager approval, dispatch, and requester receipt confirmation. Restricted or approval-required items cannot be issued through the generic issue route.
- **Frontend:** Not implemented yet; the API is ready to be consumed by a React/Tailwind client.

## Run locally

Requirements: Python 3.10+ and PostgreSQL 14+.

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `DATABASE_URL` in `backend\.env` to the PostgreSQL connection string for your database. Replace `JWT_SECRET` with a random value of at least 32 characters. Create the database before applying migrations.

```powershell
alembic upgrade head
python -m app.cli init-roles
python -m app.cli create-admin --email admin@example.org --name "System Administrator"
uvicorn app.main:app --reload
```

Interactive API documentation is available at `http://127.0.0.1:8000/docs`; liveness check: `/health`; database readiness check: `/ready`.

When schema models change, create and review a migration before applying it:

```powershell
alembic revision --autogenerate -m "describe schema change"
alembic upgrade head
```

## API outline

All endpoints except login, health, and readiness require a bearer token. Permission names are indicated in the table.

| Method | Route | Permission / purpose |
|---|---|---|
| `POST` | `/api/auth/login` | Public; issue JWT |
| `GET`, `POST` | `/api/items` | `catalog:read`, `catalog:write` |
| `GET`, `POST` | `/api/categories`, `/api/departments`, `/api/units`, `/api/vendors` | `catalog:read`, `catalog:write` |
| `GET`, `POST` | `/api/locations` | `inventory:read`, `catalog:write` |
| `GET` | `/api/stock?item_id=&location_id=&limit=100&offset=0` | `inventory:read`; paginated |
| `POST` | `/api/purchase-orders` | `catalog:write`; create a PO |
| `POST` | `/api/purchase-orders/{id}/receive` | `inventory:transact`; receive and track PO quantities |
| `POST` | `/api/transactions` | `inventory:transact`; receipt, issue, adjustment |
| `POST` | `/api/transfers` | `inventory:transact`; atomic transfer |
| `POST` | `/api/requisitions` | `requisitions:request`; submit request |
| `GET` | `/api/requisitions/{id}` | Requester or `requisitions:approve`; check status |
| `POST` | `/api/requisitions/{id}/approvals` | `requisitions:approve`; supervisor then store-manager decision by different users |
| `POST` | `/api/requisitions/{id}/dispatch` | `requisitions:dispatch` and `inventory:transact` |
| `POST` | `/api/requisitions/{id}/confirm` | `requisitions:confirm`; requester confirmation |
| `GET` | `/api/reports/low-stock?limit=100&offset=0` | `reports:read`; paginated |
| `GET` | `/api/reports/valuation` | `reports:read`; catalog standard-cost valuation |
| `GET` | `/api/reports/consumption?days=30&limit=100&offset=0` | `reports:read`; total and average daily issue quantity, paginated |
| `GET` | `/api/reports/{low-stock\|valuation\|consumption}/export?format=csv` | `reports:read`; CSV download (consumption accepts `days`) |

Stock requests accept an optional `Idempotency-Key` header. Reusing the key with the same request returns the original transaction; reusing it for a different payload returns `409 Conflict`.

The valuation report uses standard cost: on-hand quantity multiplied by each item's catalog `unit_cost`. It is not FIFO or weighted-average accounting valuation.

### Example requests

Login:

```json
{"email":"admin@example.org","password":"<password>"}
```

Receipt (`POST /api/transactions`):

```json
{
  "type": "receipt",
  "movements": [{"item_id":"<item-id>","location_id":"<location-id>","quantity_delta":"12"}],
  "reason": "Supplier delivery",
  "reference": "DELIVERY-104"
}
```

Issue:

```json
{
  "type": "issue",
  "movements": [{"item_id":"<item-id>","location_id":"<location-id>","quantity_delta":"-2"}],
  "reason": "Approved department request",
  "recipient": "Facilities"
}
```

Transfer (`POST /api/transfers`):

```json
{
  "item_id": "<item-id>",
  "source_location_id": "<source-id>",
  "destination_location_id": "<destination-id>",
  "quantity": "3",
  "reason": "Rebalance stock"
}
```

Every transaction saves quantity-before/after snapshots per location. A transfer produces an equal negative source movement and positive destination movement in the same commit. The service ensures balance rows exist, locks them in deterministic item/location order, and checks sufficiency while locked. The database also has nonnegative and movement arithmetic constraints. SQLite is used only in unit tests; its row locking is not representative of PostgreSQL concurrency behavior.

Mark high-value catalog items with `requires_approval`; `is_restricted` is also enforced. Both flags require a requisition to pass the complete approval workflow before issue.

## Schema

The ORM schema in `backend/app/models.py` covers users, departments, roles/permissions, categories, units, items, vendors, hierarchical locations, purchase orders, requisitions and approval actions, per-location stock balances, transactions, and append-only movements. Alembic migrations are in `backend/migrations/`.

## Validate

From the repository root:

```powershell
python -m pytest
```

The tests cover stock arithmetic and snapshots, insufficient-stock rejection, transfers, idempotency, requisition retry and approval rules, pagination, readiness, and password hashing. SQLite is used for fast unit tests; its locking does not model PostgreSQL concurrency.

Run the PostgreSQL concurrency integration test against a dedicated disposable database. It creates and drops a uniquely named schema:

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://user:password@localhost/inventory_test"
python -m pytest -m postgresql
Remove-Item Env:TEST_DATABASE_URL
```
