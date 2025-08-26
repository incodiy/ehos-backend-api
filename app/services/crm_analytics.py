"""Lost Reason Analytics (PRD-F-07, task 8e).

Agregat lead LOST per `lost_reason` dalam periode tertentu, disertai trend
per kuartal — memenuhi metrik keberhasilan "Lost Reason analytics tersedia
per kuartal". Tenant isolation diterapkan di layer endpoint (scope hotel/region
disaring sebelum masuk ke service); service hanya menerima `hotel_ids` yang
sudah menjadi hak akses pemanggil (bukan resolusi ulang).

Sumber data: tabel `leads` — status `LOST` terminal (F-07) + `lost_reason`
non-kosong. `amount_est` (estimasi nilai MICE) diagregasi sebagai nilai yang
hilang. Periode: `updated_at` lead LOST berada di dalam rentang [from, to).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crm import Lead


async def compute_lost_reason_analytics(
    session: AsyncSession,
    *,
    from_dt: datetime,
    to_dt: datetime,
    hotel_ids: set[uuid.UUID] | None,
) -> dict:
    """Agregasi lead LOST per alasan + trend per kuartal dalam rentang periode.

    - `from_dt` (inklusif) & `to_dt` (eksklusif), timezone-aware (UTC).
    - `hotel_ids=None` = semua hotel (hanya dipanggil oleh user global);
      set kosong = hasil kosong (terisolasi).
    """
    where_hotel = (
        [] if hotel_ids is None else [Lead.hotel_id.in_(list(hotel_ids))]
    )
    lost_where = [
        Lead.status == "LOST",
        Lead.lost_reason.is_not(None),
        func.btrim(Lead.lost_reason) != "",
        Lead.updated_at >= from_dt,
        Lead.updated_at < to_dt,
        *where_hotel,
    ]

    # total leads (semua status) dalam periode — penyebut untuk lost_rate (F-07).
    total_leads = await session.scalar(
        select(func.count(Lead.id)).where(
            Lead.created_at >= from_dt,
            Lead.created_at < to_dt,
            *where_hotel,
        )
    ) or 0

    # lead LOST dalam periode + nilai hilang.
    total_lost_row = (
        await session.execute(
            select(
                func.count(Lead.id),
                func.coalesce(func.sum(Lead.amount_est), 0),
            ).where(*lost_where)
        )
    ).one()
    total_lost = int(total_lost_row[0])
    total_lost_amount = float(total_lost_row[1])

    if total_lost == 0:
        return {
            "total_leads": int(total_leads),
            "total_lost": 0,
            "lost_rate": None,
            "total_lost_amount": 0.0,
            "breakdown": [],
            "trend": [],
        }

    # breakdown per alasan (urutan: terbanyak dulu).
    reason_rows = (
        await session.execute(
            select(
                func.btrim(Lead.lost_reason).label("reason"),
                func.count(Lead.id),
                func.coalesce(func.sum(Lead.amount_est), 0),
            )
            .where(*lost_where)
            .group_by("reason")
            .order_by(func.count(Lead.id).desc(), func.sum(Lead.amount_est).desc())
        )
    ).all()
    breakdown = [
        {
            "reason": r[0],
            "count": int(r[1]),
            "lost_amount": float(r[2]),
            "pct": round(int(r[1]) / total_lost * 100, 1),
        }
        for r in reason_rows
    ]

    # trend per kuartal (format "2026-Q3") dalam periode — metrik "per kuartal".
    quarter_rows = (
        await session.execute(
            select(
                func.to_char(func.date_trunc("quarter", Lead.updated_at), 'YYYY-"Q"Q').label(
                    "quarter"
                ),
                func.count(Lead.id),
                func.coalesce(func.sum(Lead.amount_est), 0),
            )
            .where(*lost_where)
            .group_by("quarter")
            .order_by(func.min(Lead.updated_at).asc())
        )
    ).all()
    trend = [
        {"quarter": r[0], "count": int(r[1]), "lost_amount": float(r[2])}
        for r in quarter_rows
    ]

    return {
        "total_leads": int(total_leads),
        "total_lost": total_lost,
        "lost_rate": round(total_lost / total_leads * 100, 1) if total_leads else None,
        "total_lost_amount": total_lost_amount,
        "breakdown": breakdown,
        "trend": trend,
    }


def date_to_range_utc(from_date, to_date) -> tuple[datetime, datetime]:
    """Konversi batas `date` (inklusif) → rentang datetime UTC [from, to)."""
    from_dt = datetime.combine(from_date, datetime.min.time(), tzinfo=UTC)
    to_dt = datetime.combine(to_date, datetime.min.time(), tzinfo=UTC)
    return from_dt, to_dt