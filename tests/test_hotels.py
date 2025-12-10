"""Test CRUD & Lifecycle Modul Master Hotels — Zero-Trust Standard."""

import uuid
from httpx import AsyncClient

SEED_PASSWORD = "Ehos#2026!"
ROOT = "root.admin@ehos.local"
AUDITOR = "corp.auditor@ehos.local"


async def login(client: AsyncClient, email: str, password: str = SEED_PASSWORD) -> str:
    r = await client.post(
        "/auth/login",
        json={"email": email, "password": password, "remember_me": False},
    )
    assert r.status_code == 200, (email, r.text)
    return r.json()["data"]["access_token"]


def authh(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_list_hotels(client: AsyncClient) -> None:
    token = await login(client, ROOT)
    res = await client.get("/hotels?page=1&per_page=10", headers=authh(token))
    assert res.status_code == 200
    body = res.json()
    assert len(body["data"]) == 10
    assert body["meta"]["total"] >= 100

    # Filter status TEMPORARILY_CLOSED
    res_status = await client.get("/hotels?status=TEMPORARILY_CLOSED", headers=authh(token))
    assert res_status.status_code == 200
    assert len(res_status.json()["data"]) >= 1

    # Filter has_ballroom
    res_ballroom = await client.get("/hotels?has_ballroom=true", headers=authh(token))
    assert res_ballroom.status_code == 200
    assert len(res_ballroom.json()["data"]) >= 1


async def test_get_hotel_by_code_and_uuid(client: AsyncClient) -> None:
    token = await login(client, ROOT)
    res_code = await client.get("/hotels/CWS", headers=authh(token))
    assert res_code.status_code == 200
    hotel = res_code.json()["data"]
    assert hotel["code"] == "CWS"
    hotel_uuid = hotel["id"]

    # Lookup by UUID
    res_uuid = await client.get(f"/hotels/{hotel_uuid}", headers=authh(token))
    assert res_uuid.status_code == 200
    assert res_uuid.json()["data"]["code"] == "CWS"


async def test_crud_hotel_lifecycle(client: AsyncClient) -> None:
    token = await login(client, ROOT)

    # Ambil master options untuk relasi
    brands = (await client.get("/brands", headers=authh(token))).json()["data"]
    regions = (await client.get("/regions", headers=authh(token))).json()["data"]
    provinces = (await client.get("/provinces", headers=authh(token))).json()["data"]

    test_code = f"T{uuid.uuid4().hex[:4].upper()}"
    create_payload = {
        "code": test_code,
        "name": f"Hotel Uji Coba {test_code}",
        "brand_id": brands[0]["id"],
        "region_id": regions[0]["id"],
        "province_id": provinces[0]["id"],
        "city": "Bandung",
        "geo": {"lat": -6.9175, "lng": 107.6191},
        "geofence_radius_meters": 350,
        "mice_facilities": {"ballroom_capacity": 600, "meeting_rooms": 4, "has_videotron": True},
        "status": "ACTIVE",
    }

    # 1. CREATE
    res_create = await client.post("/hotels", json=create_payload, headers=authh(token))
    assert res_create.status_code == 201, res_create.text
    created = res_create.json()["data"]
    assert created["code"] == test_code
    assert created["geofence_radius_meters"] == 350

    # 2. VERIFIKASI AUTO-CREATE 6 DEPARTEMEN STANDAR
    res_depts = await client.get(f"/hotels/{test_code}/departments", headers=authh(token))
    assert res_depts.status_code == 200
    depts = res_depts.json()["data"]
    assert len(depts) == 6
    dept_codes = {d["code"] for d in depts}
    assert {"FO", "HK", "KFB", "SEC", "ENG", "SALES"}.issubset(dept_codes)

    # 3. UPDATE / PATCH
    update_payload = {
        "name": f"Hotel Uji Coba {test_code} (Renovasi)",
        "geofence_radius_meters": 400,
        "status": "TEMPORARILY_CLOSED",
    }
    res_update = await client.patch(f"/hotels/{test_code}", json=update_payload, headers=authh(token))
    assert res_update.status_code == 200
    updated = res_update.json()["data"]
    assert updated["name"] == f"Hotel Uji Coba {test_code} (Renovasi)"
    assert updated["geofence_radius_meters"] == 400
    assert updated["status"] == "TEMPORARILY_CLOSED"

    # 4. DELETE / SOFT-DELETE
    res_delete = await client.delete(f"/hotels/{test_code}", headers=authh(token))
    assert res_delete.status_code == 200
    assert res_delete.json()["data"]["deleted"] is True

    # 5. VERIFIKASI TIDAK DAPAT DITEMUKAN LAGI (404)
    res_get_after = await client.get(f"/hotels/{test_code}", headers=authh(token))
    assert res_get_after.status_code == 404
