import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from httpx import AsyncClient
from sqlalchemy import create_engine, inspect

from app.config import get_settings


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_sample_item(client: AsyncClient, manager_token: str, sku: str = "SKU-100") -> dict:
    resp = await client.post(
        "/api/v1/items",
        headers=_auth(manager_token),
        json={
            "sku": sku,
            "barcode": f"BC-{sku}",
            "name": "Surgical Gloves",
            "category": "Medical",
            "unit": "boxes",
            "reorder_level": 5,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1. RBAC Enforcement Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rbac_requestor_forbidden_from_stock_in_catalog_and_user_management(
    client: AsyncClient, seeded_users: dict[str, dict[str, str]]
):
    mgr = seeded_users["manager"]["token"]
    req = seeded_users["requestor"]["token"]

    item = await _create_sample_item(client, mgr, sku="RBAC-001")

    # Requestor cannot create or edit catalog items
    res_create_item = await client.post(
        "/api/v1/items",
        headers=_auth(req),
        json={"sku": "HACK-1", "name": "Unauthorized Item", "category": "General", "unit": "pcs"},
    )
    assert res_create_item.status_code == 403

    res_edit_item = await client.patch(
        f"/api/v1/items/{item['id']}",
        headers=_auth(req),
        json={"name": "Tampered"},
    )
    assert res_edit_item.status_code == 403

    # Requestor gets 403 when attempting STOCK_IN or ADJUSTMENT
    res_stock_in = await client.post(
        "/api/v1/transactions",
        headers=_auth(req),
        json={
            "idempotency_key": str(uuid4()),
            "item_id": item["id"],
            "transaction_type": "STOCK_IN",
            "quantity": 10,
        },
    )
    assert res_stock_in.status_code == 403

    res_adjust = await client.post(
        "/api/v1/transactions",
        headers=_auth(req),
        json={
            "idempotency_key": str(uuid4()),
            "item_id": item["id"],
            "transaction_type": "ADJUSTMENT",
            "counted_stock": 20,
        },
    )
    assert res_adjust.status_code == 403

    # Requestor gets 403 on user management and admin audit summary
    assert (await client.get("/api/v1/users", headers=_auth(req))).status_code == 403
    assert (
        await client.post(
            "/api/v1/users",
            headers=_auth(req),
            json={
                "full_name": "Rogue",
                "email": "rogue@test.local",
                "password": "Password123!",
                "role": "ADMIN",
            },
        )
    ).status_code == 403
    assert (await client.get("/api/v1/audit/summary", headers=_auth(req))).status_code == 403


@pytest.mark.asyncio
async def test_rbac_store_manager_forbidden_from_user_management_and_stock_out(
    client: AsyncClient, seeded_users: dict[str, dict[str, str]]
):
    mgr = seeded_users["manager"]["token"]
    item = await _create_sample_item(client, mgr, sku="RBAC-002")

    # Store Manager gets 403 on user management and admin oversight
    assert (await client.get("/api/v1/users", headers=_auth(mgr))).status_code == 403
    assert (
        await client.post(
            "/api/v1/users",
            headers=_auth(mgr),
            json={
                "full_name": "Unauthorized",
                "email": "unauth@test.local",
                "password": "Password123!",
                "role": "REQUESTOR",
            },
        )
    ).status_code == 403
    assert (await client.get("/api/v1/audit/summary", headers=_auth(mgr))).status_code == 403

    # Store Manager cannot log STOCK_OUT (reserved for Requestors)
    res_stock_out = await client.post(
        "/api/v1/transactions",
        headers=_auth(mgr),
        json={
            "idempotency_key": str(uuid4()),
            "item_id": item["id"],
            "transaction_type": "STOCK_OUT",
            "quantity": 1,
        },
    )
    assert res_stock_out.status_code == 403


@pytest.mark.asyncio
async def test_rbac_admin_user_lifecycle_and_global_audit_oversight(
    client: AsyncClient, seeded_users: dict[str, dict[str, str]]
):
    admin = seeded_users["admin"]["token"]
    mgr = seeded_users["manager"]["token"]
    req1 = seeded_users["requestor"]["token"]
    req2 = seeded_users["requestor2"]["token"]

    # Admin creates a new user, changes role, resets password, deactivates
    create_res = await client.post(
        "/api/v1/users",
        headers=_auth(admin),
        json={
            "full_name": "Eve Operator",
            "email": "eve@test.local",
            "password": "InitialPassword123!",
            "role": "REQUESTOR",
        },
    )
    assert create_res.status_code == 201
    eve = create_res.json()
    assert eve["role"] == "REQUESTOR"
    assert eve["is_active"] is True

    # Promote Eve to STORE_MANAGER and reset password
    patch_res = await client.patch(
        f"/api/v1/users/{eve['id']}",
        headers=_auth(admin),
        json={"role": "STORE_MANAGER", "password": "NewPassword456!"},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["role"] == "STORE_MANAGER"

    # Eve logs in with the reset password
    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": "eve@test.local", "password": "NewPassword456!"},
    )
    assert login_res.status_code == 200
    eve_token = login_res.json()["access_token"]

    # Admin deactivates Eve; existing JWT is immediately invalidated
    deact_res = await client.patch(
        f"/api/v1/users/{eve['id']}",
        headers=_auth(admin),
        json={"is_active": False},
    )
    assert deact_res.status_code == 200
    assert deact_res.json()["is_active"] is False
    assert (await client.get("/api/v1/auth/me", headers=_auth(eve_token))).status_code == 401

    # Stock activity by manager & two requestors
    item = await _create_sample_item(client, mgr, sku="AUDIT-01")
    await client.post(
        "/api/v1/transactions",
        headers=_auth(mgr),
        json={
            "idempotency_key": str(uuid4()),
            "item_id": item["id"],
            "transaction_type": "STOCK_IN",
            "quantity": 20,
            "notes": "Supplier delivery",
        },
    )
    await client.post(
        "/api/v1/transactions",
        headers=_auth(req1),
        json={
            "idempotency_key": str(uuid4()),
            "item_id": item["id"],
            "transaction_type": "STOCK_OUT",
            "quantity": 3,
            "notes": "Ward A",
        },
    )
    await client.post(
        "/api/v1/transactions",
        headers=_auth(req2),
        json={
            "idempotency_key": str(uuid4()),
            "item_id": item["id"],
            "transaction_type": "STOCK_OUT",
            "quantity": 2,
            "notes": "Ward B",
        },
    )

    # Requestor 1 sees ONLY their own take-out history
    req1_history = (await client.get("/api/v1/transactions", headers=_auth(req1))).json()
    assert len(req1_history) == 1
    assert req1_history[0]["notes"] == "Ward A"

    # Admin sees ALL transactions in global audit log and audit summary
    admin_history = (await client.get("/api/v1/transactions", headers=_auth(admin))).json()
    assert len(admin_history) == 3

    summary_res = await client.get("/api/v1/audit/summary", headers=_auth(admin))
    assert summary_res.status_code == 200
    summary = summary_res.json()
    assert summary["total_transactions"] == 3
    assert summary["total_items"] == 1
    assert summary["total_stock_units"] == 15


# ---------------------------------------------------------------------------
# 2. Automatic Stock Calculation & Negative-Stock Prevention Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_automatic_stock_calculation_and_insufficient_stock_400(
    client: AsyncClient, seeded_users: dict[str, dict[str, str]]
):
    mgr = seeded_users["manager"]["token"]
    req = seeded_users["requestor"]["token"]

    item = await _create_sample_item(client, mgr, sku="CALC-01")
    assert item["current_stock"] == 0

    # STOCK_IN +25
    tx_in = await client.post(
        "/api/v1/transactions",
        headers=_auth(mgr),
        json={
            "idempotency_key": str(uuid4()),
            "item_id": item["id"],
            "transaction_type": "STOCK_IN",
            "quantity": 25,
            "notes": "Opening batch",
        },
    )
    assert tx_in.status_code == 201
    in_body = tx_in.json()
    assert in_body["previous_stock"] == 0
    assert in_body["remaining_stock"] == 25
    assert in_body["quantity"] == 25

    # STOCK_OUT -10
    tx_out = await client.post(
        "/api/v1/transactions",
        headers=_auth(req),
        json={
            "idempotency_key": str(uuid4()),
            "item_id": item["id"],
            "transaction_type": "STOCK_OUT",
            "quantity": 10,
            "notes": "Clinic dispatch",
        },
    )
    assert tx_out.status_code == 201
    out_body = tx_out.json()
    assert out_body["previous_stock"] == 25
    assert out_body["remaining_stock"] == 15

    # STOCK_OUT -20 exceeds remaining 15 -> 400 Bad Request stating exact available quantity
    tx_over = await client.post(
        "/api/v1/transactions",
        headers=_auth(req),
        json={
            "idempotency_key": str(uuid4()),
            "item_id": item["id"],
            "transaction_type": "STOCK_OUT",
            "quantity": 20,
        },
    )
    assert tx_over.status_code == 400
    assert "15" in tx_over.json()["detail"]

    # Verified stock count ADJUSTMENT to 12
    tx_adj = await client.post(
        "/api/v1/transactions",
        headers=_auth(mgr),
        json={
            "idempotency_key": str(uuid4()),
            "item_id": item["id"],
            "transaction_type": "ADJUSTMENT",
            "counted_stock": 12,
            "notes": "Monthly physical recount",
        },
    )
    assert tx_adj.status_code == 201
    adj_body = tx_adj.json()
    assert adj_body["previous_stock"] == 15
    assert adj_body["remaining_stock"] == 12
    assert adj_body["quantity"] == 3

    # Live item balance matches 12
    live_item = (await client.get(f"/api/v1/items/{item['id']}", headers=_auth(req))).json()
    assert live_item["current_stock"] == 12


@pytest.mark.asyncio
async def test_concurrent_stock_out_cannot_push_stock_negative(
    client: AsyncClient, seeded_users: dict[str, dict[str, str]]
):
    mgr = seeded_users["manager"]["token"]
    req = seeded_users["requestor"]["token"]

    item = await _create_sample_item(client, mgr, sku="CONC-01")

    # Seed exactly 5 units
    seed_res = await client.post(
        "/api/v1/transactions",
        headers=_auth(mgr),
        json={
            "idempotency_key": str(uuid4()),
            "item_id": item["id"],
            "transaction_type": "STOCK_IN",
            "quantity": 5,
        },
    )
    assert seed_res.status_code == 201

    # Fire 6 concurrent STOCK_OUT requests of 2 units each (total requested = 12, only 5 available).
    # At most 2 can succeed (leaving 1 unit), and 4 must fail with 400 Bad Request.
    async def attempt_take_out():
        return await client.post(
            "/api/v1/transactions",
            headers=_auth(req),
            json={
                "idempotency_key": str(uuid4()),
                "item_id": item["id"],
                "transaction_type": "STOCK_OUT",
                "quantity": 2,
            },
        )

    responses = await asyncio.gather(*(attempt_take_out() for _ in range(6)))
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [201, 201, 400, 400, 400, 400]

    live_item = (await client.get(f"/api/v1/items/{item['id']}", headers=_auth(req))).json()
    assert live_item["current_stock"] == 1


# ---------------------------------------------------------------------------
# 3. Idempotent Offline Replay Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotent_offline_replay_single_and_batch_sync(
    client: AsyncClient, seeded_users: dict[str, dict[str, str]]
):
    mgr = seeded_users["manager"]["token"]
    req = seeded_users["requestor"]["token"]

    item = await _create_sample_item(client, mgr, sku="IDEMP-01")
    await client.post(
        "/api/v1/transactions",
        headers=_auth(mgr),
        json={
            "idempotency_key": str(uuid4()),
            "item_id": item["id"],
            "transaction_type": "STOCK_IN",
            "quantity": 10,
        },
    )

    # First STOCK_OUT of 4 -> 201 Created, stock becomes 6
    key1 = str(uuid4())
    first = await client.post(
        "/api/v1/transactions",
        headers=_auth(req),
        json={
            "idempotency_key": key1,
            "item_id": item["id"],
            "transaction_type": "STOCK_OUT",
            "quantity": 4,
            "notes": "Offline take-out #1",
        },
    )
    assert first.status_code == 201
    assert first.json()["remaining_stock"] == 6

    # Duplicate replay with same idempotency_key -> 200 OK, original transaction returned, NO double deduction
    replay = await client.post(
        "/api/v1/transactions",
        headers=_auth(req),
        json={
            "idempotency_key": key1,
            "item_id": item["id"],
            "transaction_type": "STOCK_OUT",
            "quantity": 4,
            "notes": "Offline take-out #1",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["remaining_stock"] == 6

    # Batch sync containing: 1 duplicate replay, 1 valid new take-out (-2), 1 overdraw (-99)
    key2 = str(uuid4())
    key3 = str(uuid4())
    sync_res = await client.post(
        "/api/v1/transactions/sync",
        headers=_auth(req),
        json={
            "transactions": [
                {
                    "idempotency_key": key1,
                    "item_id": item["id"],
                    "transaction_type": "STOCK_OUT",
                    "quantity": 4,
                },
                {
                    "idempotency_key": key2,
                    "item_id": item["id"],
                    "transaction_type": "STOCK_OUT",
                    "quantity": 2,
                    "notes": "Offline take-out #2",
                },
                {
                    "idempotency_key": key3,
                    "item_id": item["id"],
                    "transaction_type": "STOCK_OUT",
                    "quantity": 99,
                    "notes": "Too much",
                },
            ]
        },
    )
    assert sync_res.status_code == 200
    body = sync_res.json()
    assert body["replayed_count"] == 1
    assert body["created_count"] == 1
    assert body["rejected_count"] == 1
    assert [r["status"] for r in body["results"]] == ["replayed", "created", "rejected"]
    assert "4" in body["results"][2]["error"]

    live_item = (await client.get(f"/api/v1/items/{item['id']}", headers=_auth(req))).json()
    assert live_item["current_stock"] == 4


# ---------------------------------------------------------------------------
# 4. Alembic Full Migration Chain Verification
# ---------------------------------------------------------------------------


def test_alembic_upgrade_head_from_empty_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "alembic_test.db"
    sync_url = f"sqlite+pysqlite:///{db_file.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", sync_url)
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-for-inventory-system-at-least-32-chars")
    get_settings.cache_clear()

    backend_dir = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "migrations"))

    command.upgrade(cfg, "head")

    eng = create_engine(sync_url)
    inspector = inspect(eng)
    tables = set(inspector.get_table_names())
    assert {"users", "items", "stock_transactions", "alembic_version"} == tables
    assert {"id", "full_name", "email", "hashed_password", "role", "is_active", "created_at"} == {
        c["name"] for c in inspector.get_columns("users")
    }
    assert {
        "id",
        "sku",
        "barcode",
        "name",
        "category",
        "unit",
        "current_stock",
        "reorder_level",
        "updated_at",
    } == {c["name"] for c in inspector.get_columns("items")}
    assert {
        "id",
        "idempotency_key",
        "item_id",
        "user_id",
        "transaction_type",
        "quantity",
        "previous_stock",
        "remaining_stock",
        "notes",
        "created_at",
    } == {c["name"] for c in inspector.get_columns("stock_transactions")}
    eng.dispose()
    get_settings.cache_clear()
