"""Quotation generator pipeline — PRD-F-09 (task 8d).

1-klik quotation dengan verifikasi **pagu SBM Kemenkeu** (Constraint E2/E3):
- `resolve_sbm_rate()` — lookup dinamis `government_sbm_rates` per
  (provinsi hotel × package × tahun anggaran = tahun event); tabel master SBM
  dikelola Corporate (E2) & di-seed (E1) — TIDAK ada angka hardcode.
- `create_quotation()` — generate quotation DRAFT + **snapshot E3**
  (`sbm_rate_id` / `sbm_rate_value` / `sbm_fiscal_year` disalin KE baris
  quotation saat generate, stabil walau PMK diupdate).
- **Pagu check (F-09):** untuk lead GOV, rate efektif per pax
  (`gross_amount / pax_count`) yang melebihi max rate provinsi/tahun →
  `SbmPaguExceededError` (endpoint → 409). Lead PRIVATE tidak terikat pagu
  dinas (SBM hanya mengatur belanja pemerintah) — tetap di-snapshot utk
  referensi bila rate tersedia.
- **Diskon butuh approval GM (F-09 matrix):** quotation GOV dgn
  `discount_amount > 0` → `discount_approval_status=PENDING` (approve GM);
  tanpa diskon / PRIVATE → APPROVED.

Immutability (ERD v1.3): `quotations` tidak boleh di-hard/soft-delete —
pembatalan lewat status (`DECLINED`). Snapshot E3 melindungi nilai historis.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GovernmentSbmRate, Lead, Quotation
from app.models.master import Hotel
from app.services.crm_pipeline import _log_activity

QUOTATION_STATUSES = frozenset({"DRAFT", "SENT", "ACCEPTED", "DECLINED"})
PACKAGE_TYPES = frozenset({"FULLDAY", "HALFDAY", "FULLBOARD"})


class SbmRateMissingError(ValueError):
    """GOV lead tanpa rate SBM utk (provinsi × package × tahun) → endpoint 409."""


class SbmPaguExceededError(ValueError):
    """Rate efektif per pax GOV melebihi pagu provinsi/tahun (F-09) → endpoint 409."""


class QuotationCreateError(ValueError):
    """Validasi bisnis quotation (lead LOST, diskon > gross) → endpoint 409/422."""


def generate_quotation_no() -> str:
    """Nomor quotation unik — `Q-YYMMDD-XXXXX` (uuid suffix)."""
    stamp = datetime.now(UTC).strftime("%y%m%d")
    return f"Q-{stamp}-{uuid.uuid4().hex[:5].upper()}"


async def resolve_sbm_rate(
    session: AsyncSession,
    hotel: Hotel,
    package_type: str,
    fiscal_year: int,
) -> GovernmentSbmRate | None:
    """Lookup dinamis SBM (E2): provinsi hotel × package × tahun anggaran."""
    return await session.scalar(
        select(GovernmentSbmRate).where(
            GovernmentSbmRate.province_id == hotel.province_id,
            GovernmentSbmRate.package_type == package_type,
            GovernmentSbmRate.fiscal_year == fiscal_year,
            GovernmentSbmRate.is_active.is_(True),
        )
    )


async def create_quotation(
    session: AsyncSession,
    *,
    lead: Lead,
    hotel: Hotel,
    event_date: date,
    package_type: str,
    pax_count: int,
    gross_amount: float,
    discount_amount: float = 0,
    event_name: str | None = None,
    actor_id: int,
    quotation_no: str | None = None,
) -> Quotation:
    """Generate quotation DRAFT + snapshot SBM (E3) + verifikasi pagu (F-09).

    `quotation_no` opsional utk seeder (deterministik/idempoten); endpoint
    memakai default generate.
    """
    if lead.status == "LOST":
        raise QuotationCreateError("Lead LOST terminal — tidak bisa dibuatkan quotation")
    if pax_count < 1:
        raise QuotationCreateError("pax_count minimal 1")
    if discount_amount and discount_amount > gross_amount:
        raise QuotationCreateError("discount_amount tidak boleh melebihi gross_amount")

    taxation_year = event_date.year
    rate = await resolve_sbm_rate(session, hotel, package_type, taxation_year)

    # Pagu check F-09 — hanya GOV (SBM mengatur belanja pemerintah/dinas).
    # PRIVATE tetap di-snapshot utk referensi, tidak di-409.
    if lead.institution_type == "GOV":
        if rate is None:
            raise SbmRateMissingError(
                f"SBM rate belum tersedia utk provinsi/tahun {taxation_year}/paket {package_type}"
            )
        effective_per_pax = gross_amount / pax_count
        if effective_per_pax > float(rate.max_rate_per_pax):
            raise SbmPaguExceededError(
                f"Rate efektif Rp{effective_per_pax:,.0f}/pax melebihi pagu SBM "
                f"Rp{float(rate.max_rate_per_pax):,.0f}/pax ({package_type} {taxation_year})"
            )

    discount_approval_status = "PENDING" if (lead.institution_type == "GOV" and discount_amount > 0) else "APPROVED"

    quotation = await _upsert_quotation(
        session,
        quotation_no=quotation_no or generate_quotation_no(),
        lead=lead,
        hotel=hotel,
        event_date=event_date,
        event_name=event_name,
        package_type=package_type,
        pax_count=pax_count,
        rate=rate,
        taxation_year=taxation_year,
        gross_amount=gross_amount,
        discount_amount=discount_amount,
        discount_approval_status=discount_approval_status,
        actor_id=actor_id,
    )
    await _log_activity(
        session,
        lead,
        activity_type="NOTE",
        note=(
            f"Quotation {quotation.quotation_no} dibuat — "
            + ("pagu SBM terverifikasi" if rate else "tanpa snapshot SBM (PRIVATE)")
            + (" (diskon menunggu approval GM)" if discount_approval_status == "PENDING" else "")
        ),
        actor_id=actor_id,
    )
    return quotation


async def _upsert_quotation(
    session: AsyncSession,
    *,
    quotation_no: str,
    lead: Lead,
    hotel: Hotel,
    event_date: date,
    event_name: str | None,
    package_type: str,
    pax_count: int,
    rate: GovernmentSbmRate | None,
    taxation_year: int,
    gross_amount: float,
    discount_amount: float,
    discount_approval_status: str,
    actor_id: int,
) -> Quotation:
    """Idempoten (H4): seeder/retry pakai `quotation_no` tetap — tidak duplikat."""
    quotation = await session.scalar(select(Quotation).where(Quotation.quotation_no == quotation_no))
    if quotation is None:
        quotation = Quotation(
            quotation_no=quotation_no,
            lead_id=lead.id,
            hotel_id=hotel.id,
            created_by=actor_id,
        )
        session.add(quotation)
    quotation.lead_id = lead.id
    quotation.hotel_id = hotel.id
    quotation.event_date = event_date
    quotation.event_name = event_name or f"{lead.company_name} \u2014 Paket {package_type}"
    quotation.package_type = package_type
    quotation.pax_count = pax_count
    quotation.sbm_rate_id = rate.id if rate else None
    quotation.sbm_rate_value = rate.max_rate_per_pax if rate else None
    quotation.sbm_fiscal_year = taxation_year if rate else None
    quotation.gross_amount = gross_amount
    quotation.discount_amount = discount_amount
    quotation.final_amount = gross_amount - discount_amount
    quotation.discount_approval_status = discount_approval_status
    quotation.updated_by = actor_id
    await session.flush()
    return quotation