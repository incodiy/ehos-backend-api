"""SBM master seeder — Constraint E1/E2 (task 8d, PRD-F-09).

Tabel master `government_sbm_rates` — JANGAN hardcode di app code; data live di
PostgreSQL via seeder (E1). Tiers per provinsi (PMK lookup realistis, range
Rp 350-441rb/pax fullday/FULLBOARD) + snipped inflation historis. Deterministik
& idempoten (H4): UPSERT `ON CONFLICT (province_id, package_type, fiscal_year)`.

Package mapping dari rate dasar FULLDAY:
- HALFDAY  = FULLDAY * 0.92
- FULLBOARD = FULLDAY * 1.12
Tahun berjalan = 2027 + historis 2026 (+5% degradasi historis utk YoY lookback).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GovernmentSbmRate
from app.models.master import Province

# Tier rate dasar FULLDAY per provinsi (Rp/pax, PMK lookup realistic).
TIER_A = {"31", "35", "12", "14", "64", "51", "62"}   # metropolitan
TIER_B = {"32", "33", "34", "36", "73", "71", "21", "15", "18", "19", "61", "63"}
TIER_C = {"52", "53", "65", "72", "74", "81", "91", "92", "93", "94", "95"}

BASE_RATE = {
    "A": 400_000,
    "B": 375_000,
    "C": 355_000,
}
PACKAGE_FACTOR = {"FULLDAY": 1.0, "HALFDAY": 0.92, "FULLBOARD": 1.12}
FISCAL_YEARS = (2027, 2026)  # live + historis (retrogresi 5% utk 2026)


def _tier(province_code: str) -> str:
    if province_code in TIER_A:
        return "A"
    if province_code in TIER_B:
        return "B"
    return "C"


def _rate_value(province_code: str, package_type: str, fiscal_year: int) -> float:
    base = BASE_RATE[_tier(province_code)]
    factor = PACKAGE_FACTOR[package_type]
    hist = 0.95 if fiscal_year == 2026 else 1.0
    value = base * factor * hist
    return round(value / 500) * 500  # bulat ke Rp500


async def seed_sbm_rates(session: AsyncSession, actor_id: uuid.UUID | None = None) -> dict:
    provinces = dict((await session.execute(select(Province.code, Province.id))).all())
    if not provinces:
        raise RuntimeError("Provinces belum di-seed — jalankan seed_master dulu")

    for code, province_id in sorted(provinces.items()):
        for fiscal_year in FISCAL_YEARS:
            for package_type in ("FULLDAY", "HALFDAY", "FULLBOARD"):
                rate = _rate_value(code, package_type, fiscal_year)
                stmt = (
                    pg_insert(GovernmentSbmRate)
                    .values(
                        province_id=province_id,
                        package_type=package_type,
                        max_rate_per_pax=rate,
                        fiscal_year=fiscal_year,
                        is_active=True,
                        updated_by=actor_id,
                    )
                    .on_conflict_do_update(
                        index_elements=[
                            GovernmentSbmRate.province_id,
                            GovernmentSbmRate.package_type,
                            GovernmentSbmRate.fiscal_year,
                        ],
                        set_={
                            "max_rate_per_pax": rate,
                            "is_active": True,
                            "updated_by": actor_id,
                        },
                    )
                )
                await session.execute(stmt)
    return {
        "provinces": len(provinces),
        "rows": len(provinces) * 3 * len(FISCAL_YEARS),
    }