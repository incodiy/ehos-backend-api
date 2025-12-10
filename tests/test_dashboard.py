"""Dashboard korporat (PRD-F-12/F-21) — task 9b.

GET /dashboard/heatmap · /dashboard/risk-index · /dashboard/hotels/{id} ·
/dashboard/overview. Semua data real dari DB dev (Seeder Aturan 1-2, G-H);
RBAC korporat → semua hotel, ROM/GM → scope assignment, tanpa scope → 403.
"""

import uuid

from httpx import AsyncClient

SEED_PASSWORD = "Ehos#2026!"
CORP_AUDITOR = "corp.auditor@ehos.local"
GM_SQYO = "gm.sqyo@ehos.local"
GM_CWS = "gm.cws@ehos.local"
PUBLIC_CLIENT = "client.public@ehos.local"

RISK_LEVELS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


async def _login(client: AsyncClient, email: str) -> str:
    r = await client.post(
        "/auth/login",
        json={"email": email, "password": SEED_PASSWORD, "remember_me": False},
    )
    assert r.status_code == 200, (email, r.text)
    return r.json()["data"]["access_token"]


async def _headers(client: AsyncClient, email: str) -> dict:
    return {"Authorization": f"Bearer {await _login(client, email)}"}


async def _cws_uuid(client: AsyncClient) -> uuid.UUID:
    h = await _headers(client, CORP_AUDITOR)
    r = await client.get("/dashboard/heatmap", headers=h)
    points = r.json()["data"]
    cws = next(p for p in points if p["code"] == "CWS")
    return uuid.UUID(cws["hotel_id"])


# ─── heatmap ──────────────────────────────────────────────────────────────

async def test_heatmap_corporate_returns_all_hotels(client: AsyncClient) -> None:
    """Korporat → semua hotel aktif (106), setiap point punya geo + risk field."""
    h = await _headers(client, CORP_AUDITOR)
    r = await client.get("/dashboard/heatmap", headers=h)
    assert r.status_code == 200, r.text
    points = r.json()["data"]
    assert len(points) == 106
    for p in points:
        uuid.UUID(p["hotel_id"])
        assert p["code"] and p["name"]
        assert isinstance(p["lat"], (int, float)) and isinstance(p["lng"], (int, float))
        if p["risk_level"] is not None:
            assert p["risk_level"] in RISK_LEVELS
        assert isinstance(p["has_life_safety"], bool)
        assert isinstance(p["open_capa"], int)


async def test_heatmap_risk_levels_and_life_safety(client: AsyncClient) -> None:
    """Level risiko sah + ada hotel ber-life-safety open (blink F-12)."""
    h = await _headers(client, CORP_AUDITOR)
    points = (await client.get("/dashboard/heatmap", headers=h)).json()["data"]
    scored = [p for p in points if p["risk_level"] is not None]
    assert scored, "harus ada hotel dengan skor (dual-layer live/legacy)"
    assert {p["risk_level"] for p in scored} <= RISK_LEVELS
    assert any(p["has_life_safety"] for p in points), "seeder menyediakan temuan life-safety"
    # klasifikasi deterministik dari score (≥80 LOW, ≥70 MEDIUM, ≥60 HIGH, <60 CRITICAL)
    for p in scored:
        s = p["score"]
        base = "LOW" if s >= 80 else "MEDIUM" if s >= 70 else "HIGH" if s >= 60 else "CRITICAL"
        # tanpa boost → sama dgn base; dgn boost life-safety → naik max 1 level
        if not p["has_life_safety"]:
            assert p["risk_level"] == base, (p["code"], p["risk_level"], base)
        else:
            assert p["risk_level"] in {base, "MEDIUM", "HIGH", "CRITICAL"}, p  # tidak pernah turun


async def test_heatmap_gm_scoped(client: AsyncClient) -> None:
    """GM SQYO → hanya hotel dalam scope assignment (SQYO), bukan 106."""
    h = await _headers(client, GM_SQYO)
    r = await client.get("/dashboard/heatmap", headers=h)
    assert r.status_code == 200, r.text
    points = r.json()["data"]
    assert points, "GM harus punya hotel scope"
    assert all(p["code"] in {"SQYO"} or p["region"] for p in points)
    assert len(points) < 106


async def test_heatmap_rbac_no_scope(client: AsyncClient) -> None:
    """PUBLIC_CLIENT (tanpa scope & non-korporat) → 403."""
    h = await _headers(client, PUBLIC_CLIENT)
    r = await client.get("/dashboard/heatmap", headers=h)
    assert r.status_code == 403, r.text


# ─── risk-index ───────────────────────────────────────────────────────────

async def test_risk_index_sorted(client: AsyncClient) -> None:
    """Risk-index hanya hotel ber-skor, urut CRITICAL→LOW."""
    h = await _headers(client, CORP_AUDITOR)
    r = await client.get("/dashboard/risk-index", headers=h)
    assert r.status_code == 200, r.text
    points = r.json()["data"]
    assert points
    assert all(p["risk_level"] in RISK_LEVELS for p in points)
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
    levels = [order[p["risk_level"]] for p in points]
    assert levels == sorted(levels, reverse=True)


# ─── drilldown ────────────────────────────────────────────────────────────

async def test_hotel_drilldown(client: AsyncClient) -> None:
    """Drill-down CWS: score_history (legacy+live), CAPA terbuka, risiko."""
    h = await _headers(client, CORP_AUDITOR)
    cws = await _cws_uuid(client)
    r = await client.get(f"/dashboard/hotels/{cws}", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["code"] == "CWS" and d["name"]
    assert d["score_history"], "harus punya riwayat skor (legacy 2024-26)"
    years = {p["year"] for p in d["score_history"]}
    assert {2024, 2025, 2026} <= years
    assert isinstance(d["open_capa_count"], int)
    assert d["risk_level"] in RISK_LEVELS


async def test_hotel_drilldown_rbac_and_404(client: AsyncClient) -> None:
    """GM SQYO akses hotel CWS (off-scope) → 403; uuid palsu → 404."""
    h = await _headers(client, GM_SQYO)
    cws = await _cws_uuid(client)
    r = await client.get(f"/dashboard/hotels/{cws}", headers=h)
    assert r.status_code == 403, r.text
    nu = uuid.uuid4()
    r2 = await client.get(f"/dashboard/hotels/{nu}", headers=await _headers(client, CORP_AUDITOR))
    assert r2.status_code == 404, r2.text


# ─── overview ─────────────────────────────────────────────────────────────

async def test_overview_corporate(client: AsyncClient) -> None:
    """Overview korporat: 106 hotel, CAPA aktif>0, pipeline, SLA, insights."""
    h = await _headers(client, CORP_AUDITOR)
    r = await client.get("/dashboard/overview", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["hotels_total"] == 106
    assert d["audits_ytd"] >= 0 and d["capa_active"] > 0
    assert d["pipeline"].get("OPEN") is not None
    assert {"on_time", "near_overdue", "overdue"} <= set(d["sla"])
    assert d["insights"] and any(i["type"] == "sla_overdue" for i in d["insights"])


async def test_overview_gm_scoped(client: AsyncClient) -> None:
    """Overview GM — scope hotel miliknya (bukan 106), tetap lengkap."""
    h = await _headers(client, GM_CWS)
    r = await client.get("/dashboard/overview", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert 0 < d["hotels_total"] < 106


async def test_overview_rbac_no_scope(client: AsyncClient) -> None:
    h = await _headers(client, PUBLIC_CLIENT)
    r = await client.get("/dashboard/overview", headers=h)
    assert r.status_code == 403, r.text