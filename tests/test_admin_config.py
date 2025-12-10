"""Admin config (PRD-F-01, task 9e) — brand_tier + rubrik item konfigurabel.

Validasi dua endpoint konfigurasi baru:
- `PATCH /brands/{code}` — ubah `brands.tier` (filter checklist dinamis).
- `PATCH /checklist/templates/{id}/sections/{section_id}/items/{item_id}` — ubah
  rubrik item (rubric_type/max_score/weight/na_allowed/is_life_safety) pada template DRAFT.

Berjalan terhadap seeded dev DB (app/seed/master.py brands; app.seed.* checklist).
Template DRAFT dibuat khusus per run (nama unik) agar deterministik & idempoten.
"""

import uuid

from httpx import AsyncClient

SEED_PASSWORD = "Ehos#2026!"
ROOT_ADMIN = "root.admin@ehos.local"
PUBLIC_CLIENT = "client.public@ehos.local"


async def _login(client: AsyncClient, email: str) -> str:
    r = await client.post(
        "/auth/login",
        json={"email": email, "password": SEED_PASSWORD, "remember_me": False},
    )
    assert r.status_code == 200, (email, r.text)
    return r.json()["data"]["access_token"]


async def _hdr(client: AsyncClient, email: str) -> dict:
    return {"Authorization": f"Bearer {await _login(client, email)}"}


async def test_admin_config_brand_tier_update(client: AsyncClient) -> None:
    """Konfigurasi brand_tier: patch valid, invalid tier 422, 404, RBAC 403."""
    hroot = await _hdr(client, ROOT_ADMIN)

    brands = await client.get("/brands", headers=hroot)
    assert brands.status_code == 200, brands.text
    brand = brands.json()["data"][0]

    target = "Midscale" if brand["tier"] != "Midscale" else "Luxury"
    r = await client.patch(
        f"/brands/{brand['code']}", headers=hroot, json={"tier": target}
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["tier"] == target

    r = await client.patch(
        f"/brands/{brand['code']}", headers=hroot, json={"tier": "Galaksi"}
    )
    assert r.status_code == 422, r.text

    r = await client.patch("/brands/ZZZ", headers=hroot, json={"tier": "Luxury"})
    assert r.status_code == 404, r.text

    r = await client.patch(
        f"/brands/{brand['code']}",
        headers=await _hdr(client, PUBLIC_CLIENT),
        json={"tier": "Budget"},
    )
    assert r.status_code == 403, r.text

    # restore tier asli agar deterministik
    r = await client.patch(
        f"/brands/{brand['code']}", headers=hroot, json={"tier": brand["tier"]}
    )
    assert r.status_code == 200, r.text


async def _create_draft_template(client: AsyncClient, headers: dict, name: str) -> dict:
    r = await client.post(
        "/checklist/templates",
        headers=headers,
        json={
            "department": "GM",
            "name": name,
            "version": f"v{uuid.uuid4().hex[:6]}",
            "brand_tier": "Luxury",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]


async def _add_section(client: AsyncClient, headers: dict, template_id: str) -> dict:
    r = await client.post(
        f"/checklist/templates/{template_id}/sections",
        headers=headers,
        json={"code": f"S{uuid.uuid4().hex[:6]}", "name": "Section konfigurasi"},
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]


async def _add_item(client: AsyncClient, headers: dict, template_id: str, section_id: str) -> dict:
    r = await client.post(
        f"/checklist/templates/{template_id}/sections/{section_id}/items",
        headers=headers,
        json={
            "code": f"I{uuid.uuid4().hex[:6]}",
            "question_text": "Item rubrik awal untuk uji config",
            "rubric_type": "TRAFFIC_LIGHT",
            "max_score": 90,
            "weight": 1,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]


async def test_admin_config_item_rubric_update(client: AsyncClient) -> None:
    """Rubrik item konfigurabel (PATCH): ganti rubric_type, clamp kelipatan 90, MULTI_ROOM>90."""
    hroot = await _hdr(client, ROOT_ADMIN)
    tpl = await _create_draft_template(client, hroot, "WO-9e Rubrik Update")
    sec = await _add_section(client, hroot, tpl["id"])
    item = await _add_item(client, hroot, tpl["id"], sec["id"])
    base = f"/checklist/templates/{tpl['id']}/sections/{sec['id']}/items/{item['id']}"

    r = await client.patch(
        base, headers=hroot, json={"rubric_type": "NUMERIC_SCALE", "max_score": 100}
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["rubric_type"] == "NUMERIC_SCALE"
    assert r.json()["data"]["max_score"] == 100.0

    r = await client.patch(base, headers=hroot, json={"weight": 2, "na_allowed": True})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["weight"] == 2.0
    assert r.json()["data"]["na_allowed"] is True

    # clamp kelipatan 90 utk TRAFFIC_LIGHT
    r = await client.patch(
        base, headers=hroot, json={"rubric_type": "TRAFFIC_LIGHT", "max_score": 33}
    )
    assert r.status_code == 422, r.text

    # MULTI_ROOM wajib max_score > 90
    r = await client.patch(
        base, headers=hroot, json={"rubric_type": "MULTI_ROOM", "max_score": 90}
    )
    assert r.status_code == 422, r.text

    # minimal satu field
    r = await client.patch(base, headers=hroot, json={})
    assert r.status_code == 422, r.text


async def test_admin_config_item_rubric_guards(client: AsyncClient) -> None:
    """Guard rubrik PATCH: 404 template, 422 section/item asing, 409 locked, RBAC 403."""
    hroot = await _hdr(client, ROOT_ADMIN)
    tpl = await _create_draft_template(client, hroot, "WO-9e Rubrik Guard")
    sec = await _add_section(client, hroot, tpl["id"])
    item = await _add_item(client, hroot, tpl["id"], sec["id"])

    # 404 template tidak dikenal
    r = await client.patch(
        f"/checklist/templates/{uuid.uuid4()}/sections/{sec['id']}/items/{item['id']}",
        headers=hroot,
        json={"weight": 2},
    )
    assert r.status_code == 404, r.text

    # 422 section dari template lain
    other = await _create_draft_template(client, hroot, "WO-9e Rubrik Guard B")
    other_sec = await _add_section(client, hroot, other["id"])
    r = await client.patch(
        f"/checklist/templates/{tpl['id']}/sections/{other_sec['id']}/items/{item['id']}",
        headers=hroot,
        json={"weight": 2},
    )
    assert r.status_code == 422, r.text

    # 422 item dari section lain
    await _add_item(client, hroot, other["id"], other_sec["id"])
    r = await client.patch(
        f"/checklist/templates/{other['id']}/sections/{other_sec['id']}/items/{item['id']}",
        headers=hroot,
        json={"weight": 2},
    )
    assert r.status_code == 422, r.text

    # 409 template bukan DRAFT (locked)
    r = await client.post(f"/checklist/templates/{tpl['id']}/lock", headers=hroot)
    assert r.status_code == 200, r.text
    r = await client.patch(
        f"/checklist/templates/{tpl['id']}/sections/{sec['id']}/items/{item['id']}",
        headers=hroot,
        json={"weight": 3},
    )
    assert r.status_code == 409, r.text

    # RBAC 403 (client publik tnp checklist:write)
    tpl_rbac = await _create_draft_template(client, hroot, "WO-9e Rubrik RBAC")
    sec_rbac = await _add_section(client, hroot, tpl_rbac["id"])
    item_rbac = await _add_item(client, hroot, tpl_rbac["id"], sec_rbac["id"])
    r = await client.patch(
        f"/checklist/templates/{tpl_rbac['id']}/sections/{sec_rbac['id']}/items/{item_rbac['id']}",
        headers=await _hdr(client, PUBLIC_CLIENT),
        json={"weight": 2},
    )
    assert r.status_code == 403, r.text