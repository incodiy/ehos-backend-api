"""Backfill department_breakdown (F-02) utk sesi PUBLISHED yg belum punya.

Idempoten: hanya menyetel baris dgn department_breakdown IS NULL. Sesi yg gagal
diagregasi (data legacy tidak konsisten) di-skip + dicatat jumlahnya.

Usage:
    .venv/Scripts/python -m scripts.backfill_audit_breakdown
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models.audit import AuditSession
from app.services.aggregation import AggregationError, aggregate_session


async def main() -> None:
    url = os.environ.get(
        "EHOS_DATABASE_URL",
        "postgresql+asyncpg://ehos:ehos@localhost:5434/ehos",
    )
    engine = create_async_engine(url)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    done = failed = skipped = 0

    async with Session() as s, s.begin():
        rows = (await s.scalars(
            select(AuditSession).where(
                AuditSession.status == "PUBLISHED",
                AuditSession.department_breakdown.is_(None),
            )
        )).all()
        for sess in rows:
            try:
                agg = await aggregate_session(s, sess.id)
            except AggregationError:
                failed += 1
                continue
            breakdown = [
                {
                    "section_code": sec.code,
                    "section_name": sec.name,
                    "score": round(sec.ratio * 100, 2) if sec.ratio is not None else None,
                    "max": 100.0,
                    "pct": sec.ratio,
                    "items_count": sec.items_scored + sec.items_na,
                }
                for sec in agg.sections
            ]
            if not breakdown:
                skipped += 1
                continue
            await s.execute(
                update(AuditSession)
                .where(AuditSession.id == sess.id)
                .values(department_breakdown=breakdown)
            )
            done += 1
    await engine.dispose()
    print(f"backfill selesai: done={done} failed={failed} skipped={skipped} (total={done + failed + skipped})")


if __name__ == "__main__":
    asyncio.run(main())