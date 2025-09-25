"""Translation (i18n) integration tests (PRD-F-22) — phase 5f.

Covers the expanded /translations contract:
- GET bulk (locale/entity_type filter, requires translations:read)
- GET single entity all fields
- PUT bulk upsert (translations:write) + RBAC read-only role 403
- DELETE single (translations:write)
Plus seeded-data assertions (EN rows must exist after seeding).

Self-restoring: any created translation row is deleted at the end; using a
temporary entity uuid makes tests idempotent across reruns.
"""

import uuid

from httpx import AsyncClient

SEED_PASSWORD = "Ehos#2026!"
ROOT = "root.admin@ehos.local"
AUDITOR = "corp.auditor@ehos.local"
EXEC = "corp.exec@ehos.local"
GM = "gm.cws@ehos.local"
SALES = "sales.tele@ehos.local"
HOD_HK = "hod.hk.cws@ehos.local"


async def _token(client: AsyncClient, email: str) -> str:
    r = await client.post(
        "/auth/login",
        json={"email": email, "password": SEED_PASSWORD, "remember_me": False},
    )
    assert r.status_code == 200, (email, r.text)
    return r.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_translations_requires_auth(client: AsyncClient) -> None:
    r = await client.get("/translations", params={"locale": "en"})
    assert r.status_code == 401


async def test_bulk_get_returns_seeded_en(client: AsyncClient) -> None:
    token = await _token(client, ROOT)
    r = await client.get("/translations", params={"locale": "en"}, headers=_auth(token))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["locale"] == "en"
    items = data["items"]
    assert len(items) >= 600, len(items)

    types = {i["entity_type"] for i in items}
    assert "checklist_item" in types
    assert "checklist_section" in types
    assert "checklist_template" in types
    assert "province" in types
    assert "region" in types

    # a known curated EN translation must be present (province)
    jawa_timur = [i for i in items if i["entity_type"] == "province" and i["value"] == "East Java"]
    assert len(jawa_timur) >= 1, "expected curated East Java translation"


async def test_bulk_get_filter_by_entity_type(client: AsyncClient) -> None:
    token = await _token(client, ROOT)
    r = await client.get(
        "/translations", params={"locale": "en", "entity_type": "role"}, headers=_auth(token)
    )
    assert r.status_code == 200
    types = {i["entity_type"] for i in r.json()["data"]["items"]}
    assert types == {"role"}


async def test_read_only_roles_can_get_translations(client: AsyncClient) -> None:
    for email in (EXEC, GM, HOD_HK):
        token = await _token(client, email)
        r = await client.get("/translations", params={"locale": "en"}, headers=_auth(token))
        assert r.status_code == 200, (email, r.text)


async def test_read_only_role_cannot_write_translations(client: AsyncClient) -> None:
    token = await _token(client, EXEC)
    body = {
        "locale": "en",
        "items": [{"entity_type": "role", "entity_id": str(uuid.uuid4()), "field": "name", "value": "X"}],
    }
    r = await client.put("/translations", headers=_auth(token), json=body)
    assert r.status_code == 403


async def test_hotel_user_cannot_write_translations(client: AsyncClient) -> None:
    token = await _token(client, GM)
    body = {
        "locale": "en",
        "items": [{"entity_type": "role", "entity_id": str(uuid.uuid4()), "field": "name", "value": "X"}],
    }
    r = await client.put("/translations", headers=_auth(token), json=body)
    assert r.status_code == 403


async def test_bulk_upsert_create_and_read_single_entity(client: AsyncClient) -> None:
    token = await _token(client, AUDITOR)
    headers = _auth(token)
    eid = uuid.uuid4()

    r = await client.put(
        "/translations",
        headers=headers,
        json={
            "locale": "en",
            "items": [
                {"entity_type": "finding", "entity_id": str(eid), "field": "title", "value": "Broken AC"},
                {"entity_type": "finding", "entity_id": str(eid), "field": "description", "value": "AC not cooling"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["upserted"] == 2

    # single-entity GET
    r = await client.get(f"/translations/finding/{eid}", headers=headers)
    assert r.status_code == 200
    rows = r.json()["data"]
    fields = {i["field"]: i["value"] for i in rows}
    assert fields == {"title": "Broken AC", "description": "AC not cooling"}

    # locale filter on single entity
    r = await client.get(f"/translations/finding/{eid}", params={"locale": "en"}, headers=headers)
    assert r.status_code == 200
    assert len(r.json()["data"]) == 2

    # cleanup
    for i in rows:
        await client.delete(f"/translations/{i['id']}", headers=headers)
    r = await client.get(f"/translations/finding/{eid}", headers=headers)
    assert r.json()["data"] == []


async def test_bulk_upsert_is_upsert_not_duplicate(client: AsyncClient) -> None:
    token = await _token(client, AUDITOR)
    headers = _auth(token)
    eid = uuid.uuid4()
    item = {"entity_type": "capa_ticket", "entity_id": str(eid), "field": "title", "value": "First"}

    r = await client.put("/translations", headers=headers, json={"locale": "en", "items": [item]})
    assert r.status_code == 200
    r = await client.put(
        "/translations", headers=headers, json={"locale": "en", "items": [{**item, "value": "Second"}]}
    )
    assert r.status_code == 200
    r = await client.get(f"/translations/capa_ticket/{eid}", headers=headers)
    rows = r.json()["data"]
    assert len(rows) == 1, "upsert must not duplicate"
    assert rows[0]["value"] == "Second"

    await client.delete(f"/translations/{rows[0]['id']}", headers=headers)


async def test_delete_requires_write_and_404_cleanup(client: AsyncClient) -> None:
    token = await _token(client, AUDITOR)
    headers = _auth(token)
    eid = uuid.uuid4()

    item = {"entity_type": "lead", "entity_id": str(eid), "field": "lost_reason", "value": "Price"}
    r = await client.put("/translations", headers=headers, json={"locale": "en", "items": [item]})
    assert r.status_code == 200
    row = (await client.get(f"/translations/lead/{eid}", headers=headers)).json()["data"][0]

    # sales has read but not write -> 403 on delete
    stoken = await _token(client, SALES)
    r = await client.delete(f"/translations/{row['id']}", headers=_auth(stoken))
    assert r.status_code == 403

    # auditor writes -> 204
    r = await client.delete(f"/translations/{row['id']}", headers=headers)
    assert r.status_code == 204

    # deleting again -> 404
    r = await client.delete(f"/translations/{row['id']}", headers=headers)
    assert r.status_code == 404
