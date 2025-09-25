"""User & RBAC integration tests (phase 5e) — Constraint A3/A4/A5 access control.

Runs against the seeded dev DB. Mutation tests are self-restoring:
- reset-password reverts the seeded password,
- role permission changes restore the original binding set,
- created temp users are soft-deactivated at the end.
"""

import uuid

from httpx import AsyncClient

SEED_PASSWORD = "Ehos#2026!"
ROOT = "root.admin@ehos.local"
AUDITOR = "corp.auditor@ehos.local"
GM_CWS = "gm.cws@ehos.local"
SALES = "sales.tele@ehos.local"
HOD_HK = "hod.hk.cws@ehos.local"


async def login(client: AsyncClient, email: str, password: str = SEED_PASSWORD) -> str:
    r = await client.post(
        "/auth/login",
        json={"email": email, "password": password, "remember_me": False},
    )
    assert r.status_code == 200, (email, r.text)
    return r.json()["data"]["access_token"]


def authh(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _hotel_id(client: AsyncClient, token: str, code: str) -> str:
    r = await client.get(f"/hotels/{code}", headers=authh(token))
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


async def _create_temp_user(client: AsyncClient, root: str, email: str, role: str, hotel_id: str) -> None:
    r = await client.post(
        "/users",
        headers=authh(root),
        json={"email": email, "name": "QA Temp User", "role_code": role, "hotel_ids": [hotel_id], "password": SEED_PASSWORD},
    )
    assert r.status_code == 201, r.text


async def test_root_lists_users(client: AsyncClient) -> None:
    token = await login(client, ROOT)
    r = await client.get("/users", headers=authh(token))
    assert r.status_code == 200
    emails = {u["email"] for u in r.json()["data"]}
    assert "root.admin@ehos.local" in emails
    assert "corp.auditor@ehos.local" in emails


async def test_auditor_reads_but_cannot_manage_users(client: AsyncClient) -> None:
    token = await login(client, AUDITOR)
    r = await client.get("/users", headers=authh(token))
    assert r.status_code == 200

    r = await client.post(
        "/users",
        headers=authh(token),
        json={"email": "blocked@ehos.local", "name": "X", "role_code": "HOTEL_HOD_TECH", "hotel_ids": [], "password": SEED_PASSWORD},
    )
    assert r.status_code == 403


async def test_public_client_cannot_read_users(client: AsyncClient) -> None:
    token = await login(client, "client.public@ehos.local")
    r = await client.get("/users", headers=authh(token))
    assert r.status_code == 403

    r = await client.get("/auth/me", headers=authh(token))
    assert r.status_code == 200  # tapi login tetap jalan


async def test_sales_can_read_users_scoped(client: AsyncClient) -> None:
    token = await login(client, SALES)
    r = await client.get("/users", headers=authh(token))
    assert r.status_code == 200  # HOTEL_SALES punya user:read:hotel


async def test_gm_delegated_create_hod_in_scope(client: AsyncClient) -> None:
    root = await login(client, ROOT)
    gm = await login(client, GM_CWS)
    hotels = (await client.get("/auth/hotels", headers=authh(gm))).json()["data"]
    cws_id = next(h["hotel_id"] for h in hotels if h["hotel_code"] == "CWS")

    email = f"qa.hod.{uuid.uuid4().hex[:6]}@ehos.local"
    r = await client.post(
        "/users",
        headers=authh(gm),
        json={"email": email, "name": "QA HOD", "role_code": "HOTEL_HOD_TECH", "hotel_ids": [cws_id], "password": SEED_PASSWORD},
    )
    assert r.status_code == 201, r.text
    uid = r.json()["data"]["id"]

    # account yang baru dibuat bisa login (A3: dalam scope delegasi GM)
    lr = await client.post(
        "/auth/login",
        json={"email": email, "password": SEED_PASSWORD, "remember_me": False},
    )
    assert lr.status_code == 200

    # cleanup (soft-deactivate) via ROOT
    r = await client.delete(f"/users/{uid}", headers=authh(root))
    assert r.status_code == 204


async def test_gm_create_corporate_role_rejected(client: AsyncClient) -> None:
    gm = await login(client, GM_CWS)
    cws_id = await _hotel_id(client, gm, "CWS")
    r = await client.post(
        "/users",
        headers=authh(gm),
        json={"email": "fake.corp@ehos.local", "name": "Fake", "role_code": "CORP_AUDITOR", "hotel_ids": [cws_id], "password": SEED_PASSWORD},
    )
    assert r.status_code == 403


async def test_gm_create_out_of_scope_hotel_rejected(client: AsyncClient) -> None:
    root = await login(client, ROOT)
    gm = await login(client, GM_CWS)
    sbai_id = await _hotel_id(client, root, "SBAI")
    r = await client.post(
        "/users",
        headers=authh(gm),
        json={"email": "fringe@ehos.local", "name": "Fringe", "role_code": "HOTEL_HOD_TECH", "hotel_ids": [sbai_id], "password": SEED_PASSWORD},
    )
    assert r.status_code == 403


async def test_gm_patch_corporate_user_rejected(client: AsyncClient) -> None:
    root = await login(client, ROOT)
    gm = await login(client, GM_CWS)
    rows = (await client.get("/users?search=corp.auditor", headers=authh(root))).json()["data"]
    auditor_id = next(u["id"] for u in rows if u["email"] == AUDITOR)
    r = await client.patch(f"/users/{auditor_id}", headers=authh(gm), json={"name": "Hacked"})
    assert r.status_code == 403


async def test_gm_reset_password_within_scope_restores(client: AsyncClient) -> None:
    root = await login(client, ROOT)
    gm = await login(client, GM_CWS)
    rows = (await client.get("/users?search=hod.hk.cws", headers=authh(root))).json()["data"]
    hod_id = next(u["id"] for u in rows if u["email"] == HOD_HK)

    temp_pw = "TempPass#2026!"
    r = await client.post(f"/users/{hod_id}/reset-password", headers=authh(gm), json={"new_password": temp_pw})
    assert r.status_code == 204, r.text

    # login dengan password baru berhasil
    lr = await client.post("/auth/login", json={"email": HOD_HK, "password": temp_pw, "remember_me": False})
    assert lr.status_code == 200

    # restore ke seed password
    r = await client.post(f"/users/{hod_id}/reset-password", headers=authh(root), json={"new_password": SEED_PASSWORD})
    assert r.status_code == 204
    lr = await client.post("/auth/login", json={"email": HOD_HK, "password": SEED_PASSWORD, "remember_me": False})
    assert lr.status_code == 200


async def test_gm_reset_corporate_password_rejected(client: AsyncClient) -> None:
    root = await login(client, ROOT)
    gm = await login(client, GM_CWS)
    rows = (await client.get("/users?search=corp.auditor", headers=authh(root))).json()["data"]
    auditor_id = next(u["id"] for u in rows if u["email"] == AUDITOR)
    r = await client.post(f"/users/{auditor_id}/reset-password", headers=authh(gm), json={"new_password": "Should#Fail1"})
    assert r.status_code == 403


async def test_root_set_role_permissions_and_restore(client: AsyncClient) -> None:
    root = await login(client, ROOT)
    roles = (await client.get("/roles", headers=authh(root))).json()["data"]
    finance = next(r for r in roles if r["code"] == "HOTEL_FINANCE")
    original_codes = [p["code"] for p in finance["permissions"]]
    target = ["notifications:read"]

    r = await client.put(
        f"/roles/{finance['id']}/permissions", headers=authh(root), json={"permission_codes": target}
    )
    assert r.status_code == 200, r.text
    assert {p["code"] for p in r.json()["data"]["permissions"]} == set(target)

    # restore
    r = await client.put(
        f"/roles/{finance['id']}/permissions", headers=authh(root), json={"permission_codes": original_codes}
    )
    assert r.status_code == 200
    assert {p["code"] for p in r.json()["data"]["permissions"]} == set(original_codes)


async def test_gm_set_role_permissions_rejected(client: AsyncClient) -> None:
    root = await login(client, ROOT)
    gm = await login(client, GM_CWS)
    roles = (await client.get("/roles", headers=authh(root))).json()["data"]
    sales_role = next(r for r in roles if r["code"] == "HOTEL_SALES")
    r = await client.put(
        f"/roles/{sales_role['id']}/permissions", headers=authh(gm), json={"permission_codes": ["notifications:read"]}
    )
    assert r.status_code == 403


async def test_root_admin_sovereign_locked(client: AsyncClient) -> None:
    root = await login(client, ROOT)
    roles = (await client.get("/roles", headers=authh(root))).json()["data"]
    root_admin = next(r for r in roles if r["code"] == "ROOT_ADMIN")
    r = await client.put(
        f"/roles/{root_admin['id']}/permissions", headers=authh(root), json={"permission_codes": ["notifications:read"]}
    )
    assert r.status_code == 403


async def test_create_deactivate_reactivate_toggle(client: AsyncClient) -> None:
    root = await login(client, ROOT)
    cws_id = await _hotel_id(client, root, "CWS")
    email = f"qa.toggle.{uuid.uuid4().hex[:6]}@ehos.local"
    await _create_temp_user(client, root, email, "HOTEL_HOD_TECH", cws_id)

    uid = (await client.get(f"/users?search={email}", headers=authh(root))).json()["data"][0]["id"]

    # deactivate
    r = await client.delete(f"/users/{uid}", headers=authh(root))
    assert r.status_code == 204
    lr = await client.post("/auth/login", json={"email": email, "password": SEED_PASSWORD, "remember_me": False})
    assert lr.status_code == 401  # akun nonaktif

    # reactivate
    r = await client.delete(f"/users/{uid}", headers=authh(root))
    assert r.status_code == 204
    lr = await client.post("/auth/login", json={"email": email, "password": SEED_PASSWORD, "remember_me": False})
    assert lr.status_code == 200

    # cleanup (soft-delete final)
    r = await client.delete(f"/users/{uid}", headers=authh(root))
    assert r.status_code == 204