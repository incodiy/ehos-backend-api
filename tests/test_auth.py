"""Auth integration tests (phase 5e) — JWT login/refresh/logout + session scope.

Runs against the seeded dev DB. Mutation tests (refresh rotation, logout, switch
hotel) restore the original state so reruns are stable/repeatable.
"""

import uuid

from httpx import AsyncClient

SEED_PASSWORD = "Ehos#2026!"
ROOT = "root.admin@ehos.local"
GM_CWS = "gm.cws@ehos.local"
GM_CLUSTER = "gm.cluster@ehos.local"


async def login(client: AsyncClient, email: str, password: str = SEED_PASSWORD) -> dict:
    r = await client.post(
        "/auth/login",
        json={"email": email, "password": password, "remember_me": False},
    )
    assert r.status_code == 200, (email, r.text)
    return r.json()


async def test_login_success_returns_token_pair(client: AsyncClient) -> None:
    body = await login(client, ROOT)
    assert body["success"] is True
    assert body["data"]["access_token"]
    assert body["data"]["refresh_token"]
    assert body["data"]["user"]["email"] == ROOT


async def test_login_bad_password_rejected(client: AsyncClient) -> None:
    r = await client.post(
        "/auth/login",
        json={"email": ROOT, "password": "wrong-pass-123", "remember_me": False},
    )
    assert r.status_code == 401


async def test_login_unknown_email_rejected(client: AsyncClient) -> None:
    r = await client.post(
        "/auth/login",
        json={"email": "nobody@ehos.local", "password": SEED_PASSWORD, "remember_me": False},
    )
    assert r.status_code == 401


async def test_login_disabled_account_rejected(client: AsyncClient) -> None:
    # system.ingest dibekukan (is_active=False) — 401 meski password benar
    r = await client.post(
        "/auth/login",
        json={"email": "system.ingest@ehos.local", "password": SEED_PASSWORD, "remember_me": False},
    )
    assert r.status_code == 401


async def test_login_invalid_email_format_422(client: AsyncClient) -> None:
    r = await client.post(
        "/auth/login",
        json={"email": "not-an-email", "password": SEED_PASSWORD, "remember_me": False},
    )
    assert r.status_code == 422


async def test_refresh_rotates_tokens(client: AsyncClient) -> None:
    body = await login(client, ROOT)
    old_refresh = body["data"]["refresh_token"]

    r = await client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 200, r.text
    new_pair = r.json()["data"]
    assert new_pair["access_token"]
    assert new_pair["refresh_token"] != old_refresh

    # refresh token lama sudah revoked → ditolak
    r = await client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 401


async def test_logout_revokes_refresh(client: AsyncClient) -> None:
    body = await login(client, ROOT)
    token = body["data"]["access_token"]
    refresh = body["data"]["refresh_token"]

    r = await client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 204

    r = await client.post("/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 401


async def test_me_returns_roles_and_scope(client: AsyncClient) -> None:
    body = await login(client, ROOT)
    token = body["data"]["access_token"]

    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    roles = [role["code"] for role in r.json()["data"]["roles"]]
    assert "ROOT_ADMIN" in roles


async def test_me_rejects_garbage_token(client: AsyncClient) -> None:
    r = await client.get("/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401


async def test_my_hotels_scoped_to_assignments(client: AsyncClient) -> None:
    body = await login(client, GM_CWS)
    token = body["data"]["access_token"]

    r = await client.get("/auth/hotels", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    items = r.json()["data"]
    assert len(items) == 1
    assert items[0]["hotel_code"] == "CWS"
    assert items[0]["is_primary"] is True
    assert items[0]["role_code"] == "HOTEL_GM"


async def test_switch_hotel_rotates_active_scope(client: AsyncClient) -> None:
    body = await login(client, GM_CLUSTER)
    token = body["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    hotels = (await client.get("/auth/hotels", headers=headers)).json()["data"]
    assert len(hotels) == 2  # SBAI (primary) + ZHBA
    primary = next(h for h in hotels if h["is_primary"])
    other = next(h for h in hotels if not h["is_primary"])

    r = await client.post(
        "/auth/switch-hotel", headers=headers, json={"hotel_id": other["hotel_id"]}
    )
    assert r.status_code == 200, r.text
    me = (await client.get("/auth/me", headers=headers)).json()["data"]
    assert me["active_hotel"]["code"] == other["hotel_code"]

    # restore primary
    r = await client.post(
        "/auth/switch-hotel", headers=headers, json={"hotel_id": primary["hotel_id"]}
    )
    assert r.status_code == 200
    me = (await client.get("/auth/me", headers=headers)).json()["data"]
    assert me["active_hotel"]["code"] == primary["hotel_code"]


async def test_switch_hotel_out_of_scope_403(client: AsyncClient) -> None:
    body = await login(client, GM_CWS)  # hanya CWS
    token = body["data"]["access_token"]
    hotel_id = uuid.uuid4()  # random hotel di luar scope
    r = await client.post(
        "/auth/switch-hotel",
        headers={"Authorization": f"Bearer {token}"},
        json={"hotel_id": str(hotel_id)},
    )
    assert r.status_code == 403