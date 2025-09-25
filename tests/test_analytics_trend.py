"""YoY Trend / historical analytics (PRD-F-21) — task 6g.

GET /analytics/yoy — dual-layer D: legacy dm_audit_ops (2024-26, dari
legacy_score_rows) + sesi live (audit_sessions PUBLISHED). Skor per
(tahun, hotel, departemen). RBAC: tanpa hotel → korporat; dengan hotel →
scope hotel/region/global.
"""

import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import text

from app.db.session import SessionLocal

SEED_PASSWORD = "Ehos#2026!"
CORP_AUDITOR = "corp.auditor@ehos.local"
GM_CWS = "gm.cws@ehos.local"
GM_SQYO = "gm.sqyo@ehos.local"


async def _login(client: AsyncClient, email: str) -> str:
    r = await client.post(
        "/auth/login",
        json={"email": email, "password": SEED_PASSWORD, "remember_me": False},
    )
    assert r.status_code == 200, (email, r.text)
    return r.json()["data"]["access_token"]


async def _headers(client: AsyncClient, email: str) -> dict:
    return {"Authorization": f"Bearer {await _login(client, email)}"}


async def _fixtures():
    async with SessionLocal() as s:
        cws = await s.scalar(text("SELECT id FROM hotels WHERE code='CWS'"))
        template_id = (await s.execute(text(
            "SELECT id FROM checklist_templates "
            "WHERE department='SECURITY_RISK' AND status='LOCKED' "
            "ORDER BY locked_at DESC LIMIT 1"
        ))).scalar_one()
        return {"cws": cws, "template_id": str(template_id)}


async def _live_scores(hotel_id, department: str = "SECURITY_RISK") -> dict[int, list[float]]:
    async with SessionLocal() as s:
        rows = await s.execute(text(
            "SELECT EXTRACT(year FROM date_start)::int AS y, total_score "
            "FROM audit_sessions WHERE status='PUBLISHED' AND total_score IS NOT NULL "
            "AND hotel_id = :h AND department = :d"
        ), {"h": hotel_id, "d": department})
        out: dict[int, list[float]] = {}
        for y, score in rows:
            out.setdefault(int(y), []).append(float(score))
        return out


_DAYFIX = uuid.uuid4().int % 280  # offset anti-bentrok antar run


async def _publish_live_session(client: AsyncClient, hotel_id: str, template_id: str,
                                *, year: int, seq: int = 0) -> float:
    """Buat sesi live PASS (100.0) utk tahun tertentu → kembalikan total_score."""
    h = await _headers(client, CORP_AUDITOR)
    int_h = int(hotel_id)
    day = (_DAYFIX + seq) % 344 + 1  # 1..344 (bukan 28): pilih juga bulan utk ruang lebih luas
    date_start = f"{year}-03-{min(day, 28):02d}" \
        if day <= 28 else f"{year}-{(day - 28 - 1) // 28 + 4:02d}-{(day - 28 - 1) % 28 + 1:02d}"
    async with SessionLocal() as s:
        probe = datetime.strptime(date_start, "%Y-%m-%d").date()
        while await s.scalar(text(
            "SELECT 1 FROM audit_sessions WHERE hotel_id=:h AND department='SECURITY_RISK' "
            "AND date_start=:d AND audit_type='FULL'"),
                {"h": int_h, "d": probe}):
            day = (day + 29) % 344 + 1
            date_start = f"{year}-03-{min(day, 28):02d}" \
                if day <= 28 else f"{year}-{(day - 28 - 1) // 28 + 4:02d}-{(day - 28 - 1) % 28 + 1:02d}"
            probe = datetime.strptime(date_start, "%Y-%m-%d").date()
    r = await client.post("/audit/sessions", headers=h, json={
        "hotel_id": hotel_id, "department": "SECURITY_RISK",
        "date_start": date_start,
        "date_end": (datetime.strptime(date_start, "%Y-%m-%d").date()
                     + timedelta(days=4)).isoformat(),
        "template_id": template_id,
    })
    sess_id = r.json()["data"]["id"]
    async with SessionLocal() as s:
        items = (await s.execute(text(
            "SELECT i.id, i.rubric_type, i.max_score FROM checklist_items i "
            "JOIN checklist_sections sec ON sec.id=i.section_id "
            "WHERE sec.template_id=:t ORDER BY i.sort_order"
        ), {"t": int(template_id)})).mappings().all()
    now = datetime.now(UTC)
    scores = []
    for it in items:
        value = str(it["max_score"]) if it["rubric_type"] == "NUMERIC_SCALE" else "YES"
        scores.append({"item_id": str(it["id"]), "value": value, "is_na": False,
                       "scored_at": now.isoformat(), "updated_at": now.isoformat()})
    await client.post(f"/audit/sessions/{sess_id}/items", json={"scores": scores}, headers=h)
    await client.post(f"/audit/sessions/{sess_id}/submit", headers=h)
    rp = await client.post(f"/audit/sessions/{sess_id}/publish", headers=h)
    assert rp.status_code == 200, rp.text
    return rp.json()["data"]["total_score"]


# ─── YoY trend ───────────────────────────────────────────────────────────

async def test_yoy_corporate_legacy_years(client: AsyncClient) -> None:
    """Korporat tanpa filter hotel → semua hotel+dept, legacy 2024-2026 ada."""
    h = await _headers(client, CORP_AUDITOR)
    r = await client.get("/analytics/yoy", headers=h,
                         params=[("years", 2024), ("years", 2025), ("years", 2026)])
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data
    years = {d["year"] for d in data}
    assert {2024, 2025, 2026} <= years
    # semuanya skor sah 0-100
    assert all(0.0 <= d["score"] <= 100.0 for d in data)
    # kode departemen kanonikal muncul (hasil mapping legacy)
    depts = {d["department"] for d in data}
    assert "SECURITY_RISK" in depts and "HOUSEKEEPING" in depts


async def test_yoy_hotel_department_filter(client: AsyncClient) -> None:
    """Filter hotel + departemen → hanya point CWS SECURITY_RISK (3 tahun legacy)."""
    fx = await _fixtures()
    h = await _headers(client, CORP_AUDITOR)
    r = await client.get(
        "/analytics/yoy",
        headers=h,
        params={"hotel_id": str(fx["cws"]), "department": "SECURITY_RISK"},
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data
    assert all(d["hotel_id"] == str(fx["cws"]) for d in data)
    assert all(d["department"] == "SECURITY_RISK" for d in data)
    years = {d["year"] for d in data}
    assert {2024, 2025, 2026} <= years
    s2024 = next(d for d in data if d["year"] == 2024)
    s2026 = next(d for d in data if d["year"] == 2026)
    assert s2024["score"] > 40 and s2026["score"] > 40  # seeded CWS ~63-65


async def test_yoy_live_and_legacy_2027(client: AsyncClient) -> None:
    """Point 2027 memadukan sesi live (dual-layer D) — deterministik via baseline."""
    fx = await _fixtures()
    # bersihkan sisa sesi PASS 2027 leftover (tanpa findings) agar baseline terkontrol
    async with SessionLocal() as s:
        await s.execute(text(
            "DELETE FROM audit_sessions WHERE hotel_id=:h AND department='SECURITY_RISK' "
            "AND status='PUBLISHED' AND EXTRACT(year FROM date_start)=2027 "
            "AND NOT EXISTS (SELECT 1 FROM findings f WHERE f.session_id = audit_sessions.id)"
        ), {"h": fx["cws"]})
        await s.commit()
    base = await _live_scores(fx["cws"])
    base_2027 = base.get(2027, [])
    await _publish_live_session(client, str(fx["cws"]), fx["template_id"], year=2027, seq=0)
    await _publish_live_session(client, str(fx["cws"]), fx["template_id"], year=2027, seq=1)
    expected = round((sum(base_2027) + 200.0) / (len(base_2027) + 2), 1)

    h = await _headers(client, CORP_AUDITOR)
    r = await client.get("/analytics/yoy", headers=h,
                         params=[("hotel_id", str(fx["cws"])), ("years", 2027)])
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    sec = [d for d in data if d["department"] == "SECURITY_RISK"]
    assert sec, f"tidak ada point live 2027 di {data}"
    assert sec[0]["score"] == expected, (sec[0]["score"], expected, data)


async def test_yoy_years_filter_excludes_other(client: AsyncClient) -> None:
    """years=[2024] hanya mengembalikan point 2024."""
    fx = await _fixtures()
    h = await _headers(client, CORP_AUDITOR)
    r = await client.get("/analytics/yoy", headers=h,
                          params=[("hotel_id", str(fx["cws"])), ("years", 2024)])
    data = r.json()["data"]
    assert data
    assert {d["year"] for d in data} == {2024}


async def test_yoy_rbac(client: AsyncClient) -> None:
    fx = await _fixtures()
    # GM SQYO tanpa hotel → 403
    r = await client.get("/analytics/yoy", headers=await _headers(client, GM_SQYO))
    assert r.status_code == 403
    # GM SQYO dengan hotel di luar scope → 403
    r = await client.get("/analytics/yoy", headers=await _headers(client, GM_SQYO),
                         params={"hotel_id": str(fx["cws"])})
    assert r.status_code == 403
    # GM CWS dengan hotel sendiri → 200
    r = await client.get("/analytics/yoy", headers=await _headers(client, GM_CWS),
                         params={"hotel_id": str(fx["cws"])})
    assert r.status_code == 200