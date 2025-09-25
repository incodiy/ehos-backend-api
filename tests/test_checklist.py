"""Checklist bank integration tests (PRD-F-01) — phase 5c.

Runs against the seeded dev DB. Mutation test uses a unique template name so
every rerun is idempotent (unique (department, name, version) constraint).
"""

import uuid

from httpx import AsyncClient

SEED_PASSWORD = "Ehos#2026!"
GM = "gm.cws@ehos.local"
AUDITOR = "corp.auditor@ehos.local"


async def _token(client: AsyncClient, email: str) -> str:
    r = await client.post(
        "/auth/login",
        json={"email": email, "password": SEED_PASSWORD, "remember_me": False},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_checklist_requires_auth(client: AsyncClient) -> None:
    r = await client.get("/checklist/templates")
    assert r.status_code == 401


async def test_list_templates_filter_brand_tier(client: AsyncClient) -> None:
    token = await _token(client, AUDITOR)
    headers = _auth(token)

    r = await client.get("/checklist/templates", headers=headers)
    assert r.status_code == 200
    locked = [t for t in r.json()["data"] if t["status"] == "LOCKED"]
    assert len(locked) >= 8, len(locked)

    r = await client.get(
        "/checklist/templates",
        params={"department": "HOUSEKEEPING", "brand_tier": "Luxury", "status": "LOCKED"},
        headers=headers,
    )
    assert r.status_code == 200
    assert len(r.json()["data"]) == 1
    assert r.json()["data"][0]["brand_tier"] == "Luxury"


async def test_gm_cannot_create_template(client: AsyncClient) -> None:
    token = await _token(client, GM)
    r = await client.post(
        "/checklist/templates",
        headers=_auth(token),
        json={"department": "GM", "name": "Forbidden Tpl", "version": "v9"},
    )
    assert r.status_code == 403


async def test_template_lifecycle(client: AsyncClient) -> None:
    token = await _token(client, AUDITOR)
    headers = _auth(token)
    name = f"Pytest Checklist {uuid.uuid4().hex[:8]}"

    r = await client.post(
        "/checklist/templates",
        headers=headers,
        json={"department": "GM", "name": name, "version": "v2026.1", "brand_tier": "Boutique"},
    )
    assert r.status_code == 201, r.text
    tid = r.json()["data"]["id"]
    assert r.json()["data"]["status"] == "DRAFT"

    r = await client.post(
        f"/checklist/templates/{tid}/sections", headers=headers, json={"code": "S1", "name": "Satu"}
    )
    assert r.status_code == 201
    sid = r.json()["data"]["id"]

    r = await client.post(
        f"/checklist/templates/{tid}/sections/{sid}/items",
        headers=headers,
        json={
            "code": "S1.01",
            "question_text": "Pertanyaan uji hidup?",
            "rubric_type": "TRAFFIC_LIGHT",
            "max_score": 90,
            "weight": 1,
            "na_allowed": False,
            "is_life_safety": True,
        },
    )
    assert r.status_code == 201
    item = r.json()["data"]
    assert item["rubric_type"] == "TRAFFIC_LIGHT"
    assert item["is_life_safety"] is True

    # MULTI_ROOM harus >= 90 x jumlah sample → 90 ditolak
    r = await client.post(
        f"/checklist/templates/{tid}/sections/{sid}/items",
        headers=headers,
        json={
            "code": "S1.02",
            "question_text": "Ambang salah",
            "rubric_type": "MULTI_ROOM",
            "max_score": 90,
            "weight": 1,
            "na_allowed": False,
            "is_life_safety": False,
        },
    )
    assert r.status_code == 422

    # lock → LOCKED
    r = await client.post(f"/checklist/templates/{tid}/lock", headers=headers)
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "LOCKED"

    # modifikasi template LOCKED → 409
    r = await client.post(
        f"/checklist/templates/{tid}/sections", headers=headers, json={"code": "S2", "name": "Dua"}
    )
    assert r.status_code == 409

    # fork versi baru (LOCKED → DRAFT)
    r = await client.post(
        f"/checklist/templates/{tid}/versions", headers=headers, json={"new_version": "v2026.2"}
    )
    assert r.status_code == 201
    assert r.json()["data"]["status"] == "DRAFT"

    r = await client.get(f"/checklist/templates/{tid}", headers=headers)
    assert r.status_code == 200
    payload = r.json()["data"]
    assert any(it["is_life_safety"] for sec in payload["sections"] for it in sec["items"])


async def test_template_detail_returns_structural(client: AsyncClient) -> None:
    token = await _token(client, AUDITOR)
    r = await client.get("/checklist/templates", headers=_auth(token))
    locked = [t for t in r.json()["data"] if t["status"] == "LOCKED"]
    tid = next(t["id"] for t in locked if t["name"] == "Security Risk Checklist — Universal")

    d = await client.get(f"/checklist/templates/{tid}", headers=_auth(token))
    assert d.status_code == 200
    payload = d.json()["data"]
    assert payload["template"]["status"] == "LOCKED"
    assert any(it["is_life_safety"] for sec in payload["sections"] for it in sec["items"])