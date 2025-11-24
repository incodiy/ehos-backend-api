"""Dashboard korporat (PRD-F-12/F-21) — geo-heatmap, Risk Index, drill-down,
dan overview operasional. Read-only agregasi.

Risk Index (deterministik, dual-layer D seperti YoY — ARD-008):
  - skor hotel = skor LIVE terbaru (`audit_sessions` PUBLISHED, `date_start`
    terbaru), dengan fallback rata-rata tahun legacy terakhir
    (`legacy_score_rows` 2024-26) utk hotel yang belum pernah diaudit live.
  - Level: >= 80 LOW · >= 70 MEDIUM · >= 60 HIGH · < 60 CRITICAL.
  - Hotel tanpa skor (belum pernah diaudit) → `risk_level=None` (jujur, G4).
  - Boost life-safety: temuan open life-safety (`findings.is_life_safety` dan
    belum ada CAPA CLOSED utk temuan tsb) menaikkan 1 level
    (LOW→MEDIUM→HIGH→CRITICAL; CRITICAL tetap).

Semua fungsi menerima `hotel_ids: set[int] | None` (BIGINT internal) sebagai
tenant scope — None = global (korporat), set = hotel milik user (A1/A5).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

RISK_LOW = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH = "HIGH"
RISK_CRITICAL = "CRITICAL"

RISK_ORDER = {RISK_LOW: 0, RISK_MEDIUM: 1, RISK_HIGH: 2, RISK_CRITICAL: 3}

# status ticket yang masih aktif (belum terminal)
ACTIVE_CAPA = "status <> 'CLOSED'"


def _classify(score: float | None) -> str | None:
    """Skor → level risiko (deterministik). None bila belum ada skor."""
    if score is None:
        return None
    if score >= 80:
        return RISK_LOW
    if score >= 70:
        return RISK_MEDIUM
    if score >= 60:
        return RISK_HIGH
    return RISK_CRITICAL


def _boost_life_safety(level: str | None, has_life_safety: bool) -> str | None:
    if not has_life_safety or level is None:
        return level
    if level == RISK_LOW:
        return RISK_MEDIUM
    if level == RISK_MEDIUM:
        return RISK_HIGH
    return RISK_CRITICAL


def _in_clause(column: str, ids: set[int] | None, prefix: str = "sc",
               leading: str = " AND ") -> tuple[str, dict]:
    """WHERE/AND IN clause utk text() query — expanding binding raw text tidak didukung."""
    if ids is None:
        return "", {}
    if not ids:
        return " AND 1=0", {}
    vals = sorted(ids)
    names = [f"{prefix}{i}" for i in range(len(vals))]
    placeholders = ", ".join(f":{n}" for n in names)
    return f"{leading}{column} IN ({placeholders})", dict(zip(names, vals, strict=True))


async def _latest_live_scores(session: AsyncSession, hotel_ids: set[int] | None) -> dict[int, float]:
    """Skor LIVE terbaru per hotel (satu sesi PUBLISHED terakhir)."""
    clause, params = _in_clause("hotel_id", hotel_ids)
    rows = await session.execute(text(
        "SELECT DISTINCT ON (hotel_id) hotel_id, total_score "
        "FROM audit_sessions "
        "WHERE status = 'PUBLISHED' AND total_score IS NOT NULL" + clause + " "
        "ORDER BY hotel_id, date_start DESC, created_at DESC"
    ), params)
    return {int(h): float(s) for h, s in rows}


async def _latest_legacy_scores(session: AsyncSession, hotel_ids: set[int] | None) -> dict[int, float]:
    """Rata-rata skor tahun legacy TERAKHIR per hotel (fallback dual-layer D)."""
    clause, params = _in_clause("hotel_id", hotel_ids)
    rows = await session.execute(text(
        "SELECT hotel_id, round(avg(score)::numeric, 1)::float AS score "
        "FROM legacy_score_rows "
        "WHERE years = (SELECT max(years) FROM legacy_score_rows)" + clause + " "
        "GROUP BY hotel_id"
    ), params)
    return {int(h): float(s) for h, s in rows}


async def _open_capa_counts(session: AsyncSession, hotel_ids: set[int] | None) -> dict[int, int]:
    clause, params = _in_clause("hotel_id", hotel_ids)
    rows = await session.execute(text(
        "SELECT hotel_id, count(*) FROM capa_tickets WHERE " + ACTIVE_CAPA + clause + " "
        "GROUP BY hotel_id"
    ), params)
    return {int(h): int(c) for h, c in rows}


async def _open_life_safety_hotels(session: AsyncSession, hotel_ids: set[int] | None) -> set[int]:
    """Hotel dengan temuan life-safety yang belum ditutup (CAPA CLOSED)."""
    clause, params = _in_clause("f.hotel_id", hotel_ids)
    rows = await session.execute(text(
        "SELECT DISTINCT f.hotel_id FROM findings f "
        "WHERE f.is_life_safety = TRUE "
        "  AND NOT EXISTS (SELECT 1 FROM capa_tickets ct "
        "                  WHERE ct.finding_id = f.id AND ct.status = 'CLOSED')" + clause
    ), params)
    return {int(h) for h, in rows}


async def _hotels_rows(session: AsyncSession, hotel_ids: set[int] | None) -> list[dict]:
    clause, params = _in_clause("h.id", hotel_ids, prefix="ho", leading=" WHERE ")
    rows = await session.execute(text(
        "SELECT h.id, h.uuid, h.code, h.name, b.tier, r.name AS region, "
        "       ST_X(h.geo::geometry)::float8 AS lat, ST_Y(h.geo::geometry)::float8 AS lng "
        "FROM hotels h "
        "JOIN brands b ON b.id = h.brand_id "
        "LEFT JOIN regions r ON r.id = h.region_id" + clause + " "
        "ORDER BY h.code"
    ), params)
    return [dict(r) for r in rows.mappings()]


async def compute_heatmap(
    session: AsyncSession,
    hotel_ids: set[int] | None = None,
) -> list[dict]:
    """Point geo-heatmap utk semua hotel scope + skor/risk/capa/life-safety."""
    live = await _latest_live_scores(session, hotel_ids)
    legacy = await _latest_legacy_scores(session, hotel_ids)
    open_capa = await _open_capa_counts(session, hotel_ids)
    life_hotels = await _open_life_safety_hotels(session, hotel_ids)

    out: list[dict] = []
    for h in await _hotels_rows(session, hotel_ids):
        hid = int(h["id"])
        has_life = hid in life_hotels
        score = live.get(hid) if live.get(hid) is not None else legacy.get(hid)
        level = _boost_life_safety(_classify(score), has_life)
        out.append({
            "hotel_id": h["uuid"],
            "code": h["code"],
            "name": h["name"],
            "lat": float(h["lat"] or 0.0),
            "lng": float(h["lng"] or 0.0),
            "brand_tier": h["tier"],
            "region": h["region"],
            "score": score,
            "risk_level": level,
            "has_life_safety": has_life,
            "open_capa": open_capa.get(hid, 0),
        })
    return out


async def compute_risk_index(
    session: AsyncSession,
    hotel_ids: set[int] | None = None,
) -> list[dict]:
    """Ranking risiko — hanya hotel yang punya level skor, urut CRITICAL→LOW."""
    points = await compute_heatmap(session, hotel_ids)
    ranked = [p for p in points if p["risk_level"] is not None]
    ranked.sort(key=lambda p: (-RISK_ORDER[p["risk_level"]], -(p["score"] or 0)))
    return ranked


async def compute_hotel_drilldown(
    session: AsyncSession,
    hotel_internal_id: int,
    hotel_uuid: uuid.UUID,
) -> dict:
    """Performa satu hotel: score_history (YoY), CAPA terbuka, level risiko."""
    from app.services.analytics import compute_yoy

    history = await compute_yoy(session, hotel_id=hotel_uuid)
    open_capa = await _open_capa_counts(session, {hotel_internal_id})
    points = await compute_heatmap(session, {hotel_internal_id})
    point = points[0] if points else {}
    return {
        "hotel_id": hotel_uuid,
        "code": point.get("code"),
        "name": point.get("name"),
        "score_history": history,
        "open_capa_count": open_capa.get(hotel_internal_id, 0),
        "risk_level": point.get("risk_level"),
    }


async def compute_overview(
    session: AsyncSession,
    hotel_ids: set[int] | None = None,
) -> dict:
    """Agregat overview dashboard (hero stats + pipeline + SLA + insights)."""
    points = await compute_heatmap(session, hotel_ids)
    now = datetime.now(UTC)
    tomorrow = now + timedelta(hours=24)

    # capa pipeline & SLA (scope terbatas bilangan hotel bila non-korporat)
    clause, params = _in_clause("hotel_id", hotel_ids)
    rows = await session.execute(text(
        "SELECT status, to_char(due_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS') AS due_utc "
        "FROM capa_tickets WHERE 1=1" + clause
    ), params)
    status_cnt: dict[str, int] = {}
    sla = {"on_time": 0, "near_overdue": 0, "overdue": 0, "unknown": 0}
    for status, due_utc in rows:
        status = str(status)
        status_cnt[status] = status_cnt.get(status, 0) + 1
        if status == "CLOSED":
            continue
        if not due_utc:
            sla["unknown"] += 1
            continue
        due = datetime.fromisoformat(due_utc).replace(tzinfo=UTC)
        if due < now:
            sla["overdue"] += 1
        elif due <= tomorrow:
            sla["near_overdue"] += 1
        else:
            sla["on_time"] += 1

    capa_active = sum(status_cnt.get(s, 0) for s in ("OPEN", "AWAITING_GM", "AWAITING_QA"))
    capa_closed = status_cnt.get("CLOSED", 0)

    # audit YTD
    ytd_clause, ytd_params = _in_clause("hotel_id", hotel_ids)
    audits_ytd = int((await session.execute(text(
        "SELECT count(*) FROM audit_sessions "
        "WHERE status='PUBLISHED' AND EXTRACT(year FROM date_start) = :y" + ytd_clause
    ), {"y": now.year, **ytd_params})).scalar_one())

    audits_today = int((await session.execute(text(
        "SELECT count(*) FROM audit_sessions "
        "WHERE status='PUBLISHED' AND date_start = CURRENT_DATE" + ytd_clause
    ), dict(ytd_params))).scalar_one())

    findings_rows = await session.execute(text(
        "SELECT count(*) AS total, count(*) FILTER (WHERE is_life_safety = TRUE) AS ls "
        "FROM findings WHERE 1=1" + clause
    ), params)
    ft = findings_rows.mappings().first()
    findings_total = int(ft["total"] or 0)
    life_safety_open = sum(1 for p in points if p["has_life_safety"])

    high_risk = [p for p in points if p["risk_level"] in (RISK_HIGH, RISK_CRITICAL)]

    insights = [
        {"type": "high_risk_hotels", "count": len(high_risk), "open_capa": sum(p["open_capa"] for p in high_risk)},
        {"type": "sla_overdue", "count": sla["overdue"], "near_overdue": sla["near_overdue"]},
        {"type": "life_safety_open", "count": life_safety_open, "findings_ls": findings_total},
        {"type": "audits_today", "count": audits_today},
    ]

    return {
        "as_of": now.isoformat(),
        "hotels_total": len(points),
        "hotels_with_risk": sum(1 for p in points if p["risk_level"] is not None),
        "audits_ytd": audits_ytd,
        "audits_today": audits_today,
        "findings_total": findings_total,
        "life_safety_open": life_safety_open,
        "capa_active": capa_active,
        "capa_closed": capa_closed,
        "pipeline": {
            "OPEN": status_cnt.get("OPEN", 0),
            "AWAITING_GM": status_cnt.get("AWAITING_GM", 0),
            "AWAITING_QA": status_cnt.get("AWAITING_QA", 0),
            "CLOSED": capa_closed,
        },
        "sla": sla,
        "insights": insights,
    }