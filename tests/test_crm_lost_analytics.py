"""Lost Reason Analytics endpoint (PRD-F-07, task 8e).

GET /crm/analytics/lost-reasons — agregat lead LOST per alasan + trend per
kuartal (metrik "Lost Reason analytics per kuartal"). RBAC `crm:read` + scope
tenant isolation (hotel/region/global). Periode default = kuartal berjalan;
filter opsional `from`/`to` (inklusif), `hotel_id`, `region_id`.

Berjalan terhadap seeded dev DB: seeder `lost_reason_scenarios` membuat lead
LOST historis deterministik pada 2026-02-15 (Q1), 2026-05-20 (Q2), 2026-08-15
(Q3) di hotel CWS/SBAI/ZHBA/SQYO — window Feb-2026 bebas data lain sehingga
angka dapat diuji eksak (H2/H3/H4).
"""

import re

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.db.session import SessionLocal
from app.seed.lost_reason_scenarios import seed_lost_reason_scenarios

SEED_PASSWORD = "Ehos#2026!"
CORP_EXEC = "corp.exec@ehos.local"
SALES_CWS = "sales.cws@ehos.local"
ROM_JAWA = "rom.jawa@ehos.local"
CLIENT_PUBLIC = "client.public@ehos.local"


async def _login(client: AsyncClient, email: str) -> str:
    r = await client.post(
        "/auth/login",
        json={"email": email, "password": SEED_PASSWORD, "remember_me": False},
    )
    assert r.status_code == 200, (email, r.text)
    return r.json()["data"]["access_token"]


async def _headers(client: AsyncClient, email: str) -> dict:
    return {"Authorization": f"Bearer {await _login(client, email)}"}


async def _hotel_id(code: str) -> str:
    async with SessionLocal() as s:
        row = await s.execute(text("SELECT id FROM hotels WHERE code=:c"), {"c": code})
        r = row.first()
        assert r is not None, f"hotel {code} belum di-seed"
        return str(r[0])


@pytest.fixture(autouse=True)
async def _ensure_seeded():
    async with SessionLocal() as s:
        actor = await s.scalar(text("SELECT id FROM users WHERE email='corp.exec@ehos.local'"))
        assert actor is not None
        await seed_lost_reason_scenarios(s, {"root.admin@ehos.local": actor, "corp.exec@ehos.local": actor})
        await s.commit()
    yield


async def _api(client, headers, params=None):
    r = await client.get("/crm/analytics/lost-reasons", params=params or {}, headers=headers)
    ct = r.headers.get("content-type", "")
    return r.status_code, r.json() if ct.startswith("application/json") else r.text


async def test_global_quarter_window_deterministic(client: AsyncClient) -> None:
    """Window Feb-2026 hanya berisi lead LOST seeder Q1 (4 hotel) — angka pasti."""
    corp = await _headers(client, CORP_EXEC)
    status, body = await _api(client, corp, {"from": "2026-02-01", "to": "2026-02-28"})
    assert status == 200, body
    d = body["data"]
    assert d["total_lost"] == 4
    assert d["total_lost_amount"] > 0
    assert d["period"] == "2026-02-01..2026-02-28"
    assert all(r["reason"].strip() for r in d["breakdown"])
    assert abs(sum(r["pct"] for r in d["breakdown"]) - 100.0) <= 0.2
    assert d["trend"] and d["trend"][0]["quarter"] == "2026-Q1"
    assert d["trend"][0]["count"] == 4


async def test_hotel_scope_sales_single(client: AsyncClient) -> None:
    """sales.cws (scope = CWS) dengan hotel_id=CWS → hanya data CWS."""
    token = await _headers(client, SALES_CWS)
    cws = await _hotel_id("CWS")
    status, body = await _api(client, token, {"from": "2026-02-01", "to": "2026-02-28", "hotel_id": cws})
    assert status == 200, body
    assert body["data"]["total_lost"] == 1
    assert body["data"]["breakdown"][0]["reason"].strip()


async def test_scoped_user_denied_outside_hotel(client: AsyncClient) -> None:
    """sales.cws meminta hotel di luar scope (SQYO) → 403 (tenant isolation)."""
    token = await _headers(client, SALES_CWS)
    sqyo = await _hotel_id("SQYO")
    denied = await _api(client, token, {"from": "2026-02-01", "to": "2026-02-28", "hotel_id": sqyo})
    assert denied[0] == 403


async def test_region_filter_corporate(client: AsyncClient) -> None:
    """corp.exec filter region CWS → hanya hotel di region tsb (Feb-2026)."""
    corp = await _headers(client, CORP_EXEC)
    async with SessionLocal() as s:
        region_row = await s.execute(text("SELECT h.region_id FROM hotels h WHERE h.code='CWS'"))
        region = region_row.first()
        assert region is not None
        region_id = region[0]
        expected_lost = await s.scalar(
            text(
                "SELECT count(*) FROM leads WHERE status='LOST' "
                "AND btrim(lost_reason)<>'' "
                "AND hotel_id IN (SELECT id FROM hotels WHERE region_id=:r) "
                "AND updated_at >= '2026-02-01' AND updated_at < '2026-02-28'"
            ),
            {"r": region_id},
        )
        assert expected_lost >= 1
    status, body = await _api(client, corp, {"from": "2026-02-01", "to": "2026-02-28", "region_id": str(region_id)})
    assert status == 200, body
    assert body["data"]["total_lost"] == expected_lost


async def test_region_scope_rom_sees_only_own(client: AsyncClient) -> None:
    """ROM Jawa (region CWS/SBAI) → tanpa filter tetap regional, bukan 403."""
    token = await _headers(client, ROM_JAWA)
    status, body = await _api(client, token, {"from": "2026-02-01", "to": "2026-02-28"})
    assert status == 200, body
    assert body["data"]["total_lost"] >= 1  # CWS/SBAI di-scope rom.jawa


async def test_public_no_crm_read_403(client: AsyncClient) -> None:
    pub = await _headers(client, CLIENT_PUBLIC)
    status, body = await _api(client, pub, {"from": "2026-02-01", "to": "2026-02-28"})
    assert status == 403


async def test_empty_period_honest(client: AsyncClient) -> None:
    """Periode tanpa lead LOST → empty-state jujur (G4), bukan data palsu."""
    corp = await _headers(client, CORP_EXEC)
    status, body = await _api(client, corp, {"from": "2030-01-01", "to": "2030-03-31"})
    assert status == 200, body
    d = body["data"]
    assert d["total_lost"] == 0
    assert d["breakdown"] == []
    assert d["trend"] == []
    assert d["lost_rate"] is None


async def test_default_period_is_current_quarter(client: AsyncClient) -> None:
    """Tanpa `from`/`to` → default awal kuartal berjalan sampai hari ini."""
    corp = await _headers(client, CORP_EXEC)
    status, body = await _api(client, corp)
    assert status == 200, body
    d = body["data"]
    assert d["period"] is not None
    assert re.match(r"\d{4}-\d{2}-\d{2}\.\.\d{4}-\d{2}-\d{2}", d["period"])
    assert d["period"].split("..")[0].endswith("-01")  # awal bulan = 01


async def test_each_breakdown_reason_is_canonical(client: AsyncClient) -> None:
    """Semua alasan non-kosong; nilai seeder kanonikal muncul; not-free-text dipakai legacy."""
    corp = await _headers(client, CORP_EXEC)
    status, body = await _api(client, corp, {"from": "2026-01-01", "to": "2026-12-31"})
    assert status == 200, body
    d = body["data"]
    assert d["total_lost"] >= 4
    reasons = {r["reason"].strip() for r in d["breakdown"]}
    assert all(reasons)
    known = {
        "Anggaran tidak tersedia", "Memilih kompetitor", "Event dibatalkan",
        "Proses lelang gagal", "Dana pindah ke pos lain",
    }
    # Seeder kanonikal hadir di antara alasan yang dilaporkan.
    assert reasons & known
    quarters = {t["quarter"] for t in d["trend"]}
    assert "2026-Q1" in quarters


async def test_seeder_idempotent() -> None:
    """Seeder LOST analytics idempoten (H4): run ulang tidak menggandakan data."""
    async with SessionLocal() as s:
        actor = await s.scalar(text("SELECT id FROM users WHERE email='corp.exec@ehos.local'"))
        before = await s.scalar(text("SELECT count(*) FROM leads WHERE lead_no LIKE 'CRM-8E-%%'"))
        await seed_lost_reason_scenarios(s, {"root.admin@ehos.local": actor, "corp.exec@ehos.local": actor})
        await s.commit()
        after = await s.scalar(text("SELECT count(*) FROM leads WHERE lead_no LIKE 'CRM-8E-%%'"))
    assert after == before, "seeder harus idempoten (upsert lead_no)"


async def test_amount_est_aggregated_as_lost_value(client: AsyncClient) -> None:
    """Nilai yang hilang = jumlah `amount_est` lead LOST dalam periode."""
    corp = await _headers(client, CORP_EXEC)
    cws = await _hotel_id("CWS")
    async with SessionLocal() as s:
        expected = await s.scalar(
            text(
                "SELECT COALESCE(SUM(amount_est),0)::float FROM leads "
                "WHERE status='LOST' AND btrim(lost_reason)<>'' "
                "AND hotel_id=:h AND updated_at >= '2026-02-01' AND updated_at < '2026-02-28'"
            ),
            {"h": int(cws)},
        )
        assert expected > 0
    status, body = await _api(client, corp, {"from": "2026-02-01", "to": "2026-02-28", "hotel_id": cws})
    assert status == 200, body
    assert body["data"]["total_lost_amount"] == expected