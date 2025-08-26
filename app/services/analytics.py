"""YoY Trend Engine (PRD-F-21) — dual-layer D: legacy dm_audit_ops (2024-26,
`legacy_score_rows`) + sesi live (`audit_sessions` PUBLISHED).

Skor per (tahun, hotel, departemen) = rata-rata sumber yang tersedia:
- legacy: avg(score) baris temuan per main_category (dipetakan ke kode dept).
- live: avg(total_score) sesi terpublish (tahun dari date_start).

Mapping kategori legacy → departemen kanonikal (sesuai ARD-008 legacy mapper):
HOUSEKEEPING / SECURITY RISK MANAGEMENT / KITCHEN & FB → kode checklist.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

LEGACY_DEPT_SQL = """
CASE
    WHEN main_category = 'HOUSEKEEPING' THEN 'HOUSEKEEPING'
    WHEN main_category = 'SECURITY RISK MANAGEMENT' THEN 'SECURITY_RISK'
    WHEN main_category = 'KITCHEN & FB' THEN 'KITCHEN_FB'
    ELSE main_category
END
"""


def _legacy_query() -> str:
    return f"""
    SELECT t.year, t.hotel_id, t.department,
           round(avg(t.score)::numeric, 1)::float AS score
    FROM (
        SELECT l.years AS year, l.hotel_id, l.score, {LEGACY_DEPT_SQL} AS department
        FROM legacy_score_rows l
    ) t
    WHERE t.year IS NOT NULL
      AND t.department IS NOT NULL
      AND (CAST(:hotel_id AS bigint) IS NULL OR t.hotel_id = CAST(:hotel_id AS bigint))
      AND (CAST(:department AS text) IS NULL OR t.department = CAST(:department AS text))
    GROUP BY t.year, t.hotel_id, t.department
    """


def _live_query() -> str:
    return """
    SELECT EXTRACT(year FROM date_start)::int AS year,
           hotel_id, department,
           round(avg(total_score)::numeric, 1)::float AS score
    FROM audit_sessions
    WHERE status = 'PUBLISHED' AND total_score IS NOT NULL AND date_start IS NOT NULL
      AND (CAST(:hotel_id AS bigint) IS NULL OR hotel_id = CAST(:hotel_id AS bigint))
      AND (CAST(:department AS text) IS NULL OR department = CAST(:department AS text))
    GROUP BY 1, 2, 3
    """


async def compute_yoy(
    session: AsyncSession,
    hotel_id: int | uuid.UUID | None = None,
    department: str | None = None,
    years: list[int] | None = None,
) -> list[dict]:
    """Agregat YoY: gabungkan legacy + live, rata-ratakan per (tahun, hotel, dept)."""
    from app.core.identity import get_by_uuid
    from app.models.master import Hotel

    hotel_internal: int | None = None
    if hotel_id is not None:
        hotel = await get_by_uuid(session, Hotel, hotel_id)
        hotel_internal = hotel.id if hotel is not None else None
    params = {"hotel_id": hotel_internal, "department": department}
    merged: dict[tuple[int, str, str], list[float]] = {}

    legacy = await session.execute(text(_legacy_query()), params)
    for row in legacy.mappings():
        y, hid, dept, score = row["year"], row["hotel_id"], row["department"], row["score"]
        merged.setdefault((y, str(hid), dept), []).append(float(score))

    live = await session.execute(text(_live_query()), params)
    for row in live.mappings():
        y, hid, dept, score = row["year"], row["hotel_id"], row["department"], row["score"]
        merged.setdefault((y, str(hid), dept), []).append(float(score))

    want = set(years) if years else None
    out = []
    for (y, hid, dept), values in merged.items():
        if want is not None and y not in want:
            continue
        out.append({
            "year": y,
            "department": dept,
            "hotel_id": hid,
            "score": round(sum(values) / len(values), 1),
        })
    out.sort(key=lambda p: (p["year"], p["department"], p["hotel_id"]))
    return out