"""Test CRUD, FSM Status, & Chained FK Guard Modul Regions — Zero-Trust Standard."""

import uuid
from httpx import AsyncClient

SEED_PASSWORD = "Ehos#2026!"
ROOT = "root.admin@ehos.local"
ROM = "regional.rom@ehos.local"


async def login(client: AsyncClient, email: str, password: str = SEED_PASSWORD) -> str:
    r = await client.post(
        "/auth/login",
        json={"email": email, "password": password, "remember_me": False},
    )
    assert r.status_code == 200, (email, r.text)
    return r.json()["data"]["access_token"]


def authh(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_list_regions_paginated_and_filtered(client: AsyncClient) -> None:
    token = await login(client, ROOT)

    # 1. Paginasi dasar
    res = await client.get("/regions?page=1&per_page=10", headers=authh(token))
    assert res.status_code == 200
    body = res.json()
    assert len(body["data"]) <= 10
    assert body["meta"]["total"] >= 14

    # 2. Filter search
    res_search = await client.get("/regions?search=Bali", headers=authh(token))
    assert res_search.status_code == 200
    search_data = res_search.json()["data"]
    assert any(r["code"] == "BALI" for r in search_data)

    # 3. Filter status FSM
    res_active = await client.get("/regions?status=ACTIVE", headers=authh(token))
    assert res_active.status_code == 200
    assert all(r["status"] == "ACTIVE" for r in res_active.json()["data"])

    # 4. Verifikasi soft-delete isolation (TEST_EXPANSION tidak muncul di list aktif)
    all_codes = [r["code"] for r in (await client.get("/regions?per_page=100", headers=authh(token))).json()["data"]]
    assert "TEST_EXPANSION" not in all_codes


async def test_get_region_detail_with_stats(client: AsyncClient) -> None:
    token = await login(client, ROOT)

    # Detail by code
    res = await client.get("/regions/BALI", headers=authh(token))
    assert res.status_code == 200
    region = res.json()["data"]
    assert region["code"] == "BALI"
    assert "hotels_count" in region
    assert region["hotels_count"] > 0
    assert "rom_names" in region

    # Detail by UUID
    region_uuid = region["id"]
    res_uuid = await client.get(f"/regions/{region_uuid}", headers=authh(token))
    assert res_uuid.status_code == 200
    assert res_uuid.json()["data"]["code"] == "BALI"


async def test_crud_region_lifecycle_and_conflict(client: AsyncClient) -> None:
    token = await login(client, ROOT)

    unique_code = f"TEST_{uuid.uuid4().hex[:6].upper()}"

    # 1. Create region
    create_payload = {
        "code": unique_code,
        "name": "Testing Autonomous Region",
        "country": "Indonesia",
        "sales_region": "Test Division",
        "status": "ACTIVE",
    }
    res_create = await client.post("/regions", json=create_payload, headers=authh(token))
    assert res_create.status_code == 201
    created_id = res_create.json()["data"]["id"]
    assert res_create.json()["data"]["code"] == unique_code

    # 2. Prevent duplicate code (HTTP 409)
    res_dup = await client.post("/regions", json=create_payload, headers=authh(token))
    assert res_dup.status_code == 409

    # 3. Update region fields & FSM status
    update_payload = {
        "name": "Testing Autonomous Region Updated",
        "sales_region": "Expanded Test Division",
        "status": "INACTIVE",
    }
    res_update = await client.patch(f"/regions/{created_id}", json=update_payload, headers=authh(token))
    assert res_update.status_code == 200
    assert res_update.json()["data"]["name"] == "Testing Autonomous Region Updated"
    assert res_update.json()["data"]["status"] == "INACTIVE"

    # 4. Soft-delete wilayah kosong (tanpa hotel) -> sukses HTTP 200
    res_del = await client.delete(f"/regions/{created_id}", headers=authh(token))
    assert res_del.status_code == 200
    assert res_del.json()["data"]["deleted"] is True

    # 5. Verifikasi get returns 404 after soft-delete
    res_after = await client.get(f"/regions/{created_id}", headers=authh(token))
    assert res_after.status_code == 404


async def test_delete_region_relational_guard_blocked_by_hotels(client: AsyncClient) -> None:
    token = await login(client, ROOT)

    # Wilayah BALI memiliki properti hotel aktif -> DELETE wajib ditolak HTTP 409!
    res_blocked = await client.delete("/regions/BALI", headers=authh(token))
    assert res_blocked.status_code == 409
    assert "tidak dapat dihapus karena masih memiliki" in res_blocked.json()["detail"]
