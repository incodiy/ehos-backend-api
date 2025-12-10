"""Test CRUD, FSM Status, & Chained FK Guard Modul Brands — Zero-Trust Standard."""

import uuid
from httpx import AsyncClient

SEED_PASSWORD = "Ehos#2026!"
ROOT = "root.admin@ehos.local"


async def login(client: AsyncClient, email: str, password: str = SEED_PASSWORD) -> str:
    r = await client.post(
        "/auth/login",
        json={"email": email, "password": password, "remember_me": False},
    )
    assert r.status_code == 200, (email, r.text)
    return r.json()["data"]["access_token"]


def authh(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_list_brands_paginated_and_filtered(client: AsyncClient) -> None:
    token = await login(client, ROOT)

    # 1. Paginasi dasar
    res = await client.get("/brands?page=1&per_page=5", headers=authh(token))
    assert res.status_code == 200
    body = res.json()
    assert len(body["data"]) <= 5
    assert body["meta"]["total"] >= 12

    # 2. Filter search
    res_search = await client.get("/brands?search=Swiss-Belresort", headers=authh(token))
    assert res_search.status_code == 200
    search_data = res_search.json()["data"]
    assert any(b["code"] == "SBR" for b in search_data)

    # 3. Filter tier
    res_luxury = await client.get("/brands?tier=Luxury", headers=authh(token))
    assert res_luxury.status_code == 200
    assert all(b["tier"] == "Luxury" for b in res_luxury.json()["data"])

    # 4. Filter status FSM
    res_inactive = await client.get("/brands?status=INACTIVE", headers=authh(token))
    assert res_inactive.status_code == 200
    assert all(b["status"] == "INACTIVE" for b in res_inactive.json()["data"])

    # 5. Verifikasi isolasi soft-delete (TEST_LEGACY_BRAND tidak boleh muncul)
    all_codes = [b["code"] for b in (await client.get("/brands?per_page=100", headers=authh(token))).json()["data"]]
    assert "TEST_LEGACY_BRAND" not in all_codes


async def test_get_brand_detail_with_hotels(client: AsyncClient) -> None:
    token = await login(client, ROOT)

    # Detail by code
    res = await client.get("/brands/SBH", headers=authh(token))
    assert res.status_code == 200
    brand = res.json()["data"]
    assert brand["code"] == "SBH"
    assert "hotels_count" in brand
    assert brand["hotels_count"] > 0
    assert "hotels" in brand
    assert len(brand["hotels"]) == brand["hotels_count"]

    # Detail by UUID
    brand_uuid = brand["id"]
    res_uuid = await client.get(f"/brands/{brand_uuid}", headers=authh(token))
    assert res_uuid.status_code == 200
    assert res_uuid.json()["data"]["code"] == "SBH"

    # Detail 404 for unknown brand
    res_404 = await client.get(f"/brands/{uuid.uuid4()}", headers=authh(token))
    assert res_404.status_code == 404


async def test_create_and_update_brand_lifecycle(client: AsyncClient) -> None:
    token = await login(client, ROOT)
    unique_suffix = uuid.uuid4().hex[:6].upper()
    test_code = f"T_{unique_suffix}"

    # 1. Create brand
    create_payload = {
        "code": test_code.lower(),  # Verify uppercase normalization
        "name": f"Test Brand {unique_suffix}",
        "tier": "Boutique",
        "status": "ACTIVE",
    }
    res_create = await client.post("/brands", json=create_payload, headers=authh(token))
    assert res_create.status_code == 201
    created = res_create.json()["data"]
    assert created["code"] == test_code
    assert created["tier"] == "Boutique"
    assert created["status"] == "ACTIVE"
    brand_id = created["id"]

    # 2. Duplicate code rejected with 409
    res_dup = await client.post("/brands", json=create_payload, headers=authh(token))
    assert res_dup.status_code == 409

    # 3. Update brand
    update_payload = {
        "name": f"Updated Brand {unique_suffix}",
        "tier": "Luxury",
        "status": "INACTIVE",
    }
    res_update = await client.put(f"/brands/{brand_id}", json=update_payload, headers=authh(token))
    assert res_update.status_code == 200
    updated = res_update.json()["data"]
    assert updated["name"] == f"Updated Brand {unique_suffix}"
    assert updated["tier"] == "Luxury"
    assert updated["status"] == "INACTIVE"

    # 4. Safe delete of brand with zero hotels
    res_del = await client.delete(f"/brands/{brand_id}", headers=authh(token))
    assert res_del.status_code == 200

    # 5. Confirm soft-deleted
    res_after = await client.get(f"/brands/{brand_id}", headers=authh(token))
    assert res_after.status_code == 404


async def test_delete_brand_blocked_by_active_hotels(client: AsyncClient) -> None:
    token = await login(client, ROOT)

    # SBH has active hotels connected (Swiss-Belhotel)
    res_del = await client.delete("/brands/SBH", headers=authh(token))
    assert res_del.status_code == 409
    assert "masih terdapat" in res_del.json()["detail"]
