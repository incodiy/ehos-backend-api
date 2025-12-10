"""Test CRUD, Versioning, FSM Status, & Rubric Guards Modul Checklist — Zero-Trust Standard."""

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


async def test_list_templates_and_filters(client: AsyncClient) -> None:
    token = await login(client, ROOT)

    # 1. List all templates
    res = await client.get("/checklist/templates", headers=authh(token))
    assert res.status_code == 200
    templates = res.json()["data"]
    assert len(templates) > 0

    # 2. Filter by status LOCKED
    res_locked = await client.get("/checklist/templates?status=LOCKED", headers=authh(token))
    assert res_locked.status_code == 200
    assert all(t["status"] == "LOCKED" for t in res_locked.json()["data"])

    # 3. Filter by status ARCHIVED
    res_archived = await client.get("/checklist/templates?status=ARCHIVED", headers=authh(token))
    assert res_archived.status_code == 200
    assert any(t["status"] == "ARCHIVED" for t in res_archived.json()["data"])

    # 4. Filter by department HOUSEKEEPING
    res_hk = await client.get("/checklist/templates?department=HOUSEKEEPING", headers=authh(token))
    assert res_hk.status_code == 200
    assert all(t["department"] == "HOUSEKEEPING" for t in res_hk.json()["data"])

    # 5. Soft-deleted template exclusion
    names = [t["name"] for t in templates]
    assert "Security Risk Obsolete Protocol v2023" not in names


async def test_template_full_lifecycle_and_version_forking(client: AsyncClient) -> None:
    token = await login(client, ROOT)
    uid = uuid.uuid4().hex[:6]
    version_1 = f"v2028.{uid}.1"
    version_2 = f"v2028.{uid}.2"

    # 1. Create new DRAFT template
    tpl_payload = {
        "department": "SECURITY_RISK",
        "name": f"Security Risk Automation Test {uid}",
        "version": version_1,
        "brand_tier": "Luxury",
    }
    res_create = await client.post("/checklist/templates", json=tpl_payload, headers=authh(token))
    assert res_create.status_code == 201
    tpl = res_create.json()["data"]
    tpl_id = tpl["id"]
    assert tpl["status"] == "DRAFT"

    # 2. Add section
    sec_payload = {
        "code": f"SEC-{uid}",
        "name": "Perimeter & CCTV",
        "sort_order": 10,
    }
    res_sec = await client.post(f"/checklist/templates/{tpl_id}/sections", json=sec_payload, headers=authh(token))
    assert res_sec.status_code == 201
    sec = res_sec.json()["data"]
    sec_id = sec["id"]

    # 3. Add item (with rubric validation)
    item_payload = {
        "code": f"ITM-{uid}-01",
        "question_text": "Kamera CCTV perimeter utama aktif 24 jam dan merekam selama 30 hari",
        "rubric_type": "TRAFFIC_LIGHT",
        "max_score": 90.0,
        "weight": 1.5,
        "na_allowed": False,
        "is_life_safety": True,
        "sort_order": 10,
    }
    res_item = await client.post(
        f"/checklist/templates/{tpl_id}/sections/{sec_id}/items",
        json=item_payload,
        headers=authh(token),
    )
    assert res_item.status_code == 201
    item = res_item.json()["data"]
    item_id = item["id"]
    assert item["is_life_safety"] is True

    # 4. Update rubric item in DRAFT
    res_upd = await client.patch(
        f"/checklist/templates/{tpl_id}/sections/{sec_id}/items/{item_id}",
        json={"weight": 2.0, "question_text": "Kamera CCTV perimeter utama aktif 24 jam (updated)"},
        headers=authh(token),
    )
    assert res_upd.status_code == 200
    assert res_upd.json()["data"]["weight"] == 2.0

    # 5. Lock template (DRAFT -> LOCKED)
    res_lock = await client.post(f"/checklist/templates/{tpl_id}/lock", headers=authh(token))
    assert res_lock.status_code == 200
    locked_tpl = res_lock.json()["data"]
    assert locked_tpl["status"] == "LOCKED"
    assert locked_tpl["locked_at"] is not None

    # 6. Verify modification blocked on LOCKED template
    res_blocked = await client.patch(
        f"/checklist/templates/{tpl_id}/sections/{sec_id}/items/{item_id}",
        json={"weight": 3.0},
        headers=authh(token),
    )
    assert res_blocked.status_code == 409

    # 7. Fork new version (LOCKED -> new DRAFT vX.2)
    res_fork = await client.post(
        f"/checklist/templates/{tpl_id}/versions",
        json={"new_version": version_2},
        headers=authh(token),
    )
    assert res_fork.status_code == 201
    forked_tpl = res_fork.json()["data"]
    forked_id = forked_tpl["id"]
    assert forked_tpl["status"] == "DRAFT"
    assert forked_tpl["version"] == version_2

    # Verify sections and items were cloned to new version
    res_detail = await client.get(f"/checklist/templates/{forked_id}", headers=authh(token))
    assert res_detail.status_code == 200
    detail = res_detail.json()["data"]
    assert len(detail["sections"]) == 1
    assert len(detail["sections"][0]["items"]) == 1
    assert detail["sections"][0]["items"][0]["code"] == f"ITM-{uid}-01"

    # 8. Archive locked template (LOCKED -> ARCHIVED)
    res_archive = await client.post(f"/checklist/templates/{tpl_id}/archive", headers=authh(token))
    assert res_archive.status_code == 200
    assert res_archive.json()["data"]["status"] == "ARCHIVED"

    # 9. Delete draft template
    res_del = await client.delete(f"/checklist/templates/{forked_id}", headers=authh(token))
    assert res_del.status_code == 200
    assert res_del.json()["data"]["status"] == "success"

    # Verify deleted draft cannot be retrieved
    res_not_found = await client.get(f"/checklist/templates/{forked_id}", headers=authh(token))
    assert res_not_found.status_code == 404