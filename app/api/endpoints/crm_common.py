"""Shared helpers and RBAC guards for CRM endpoints."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.security_helpers import allowed_hotel_ids
from app.api.security_helpers import perms as _perms
from app.core.identity import get_by_uuid
from app.models import (
    BillingMilestone,
    Hotel,
    Lead,
    LeadActivity,
    LeadReferral,
    Quotation,
    User,
)
from app.schemas.common import HybridId
from app.schemas.crm import (
    BillingMilestoneOut,
    LeadActivityOut,
    LeadOut,
    LeadReferralOut,
    QuotationDetailOut,
)
from app.services.crm_pipeline import followup_due
from app.services.media_storage import presigned_get_url


# ─── RBAC / scope helpers (tenant isolation Golden Rule 3) ───────────────

async def _scope_hotel_ids(session: AsyncSession, user: User) -> set[int] | None:
    """None = global (ROOT/CORP tanpa assignment); set = hotel (internal id)."""
    codes = await _perms(session, user)
    if "crm:read" not in codes:
        raise HTTPException(403, "Missing permission: crm:read")
    allowed = await allowed_hotel_ids(session, user)
    return allowed or None


async def _require_read(session: AsyncSession, user: User, hotel_id: int) -> None:
    if "crm:read" not in await _perms(session, user):
        raise HTTPException(403, "Missing permission: crm:read")
    scope = await allowed_hotel_ids(session, user)
    if scope and hotel_id not in scope:
        raise HTTPException(403, "Missing permission: crm scope hotel/region/global")


async def _require_manage(session: AsyncSession, user: User, hotel_id: int) -> None:
    if "crm:manage" not in await _perms(session, user):
        raise HTTPException(403, "Missing permission: crm:manage")
    scope = await allowed_hotel_ids(session, user)
    if scope and hotel_id not in scope:
        raise HTTPException(403, "Missing permission: crm scope hotel/region/global")


# ─── entity getters & serializers ────────────────────────────────────────

async def _resolve_hotel_uuid(session: AsyncSession, hotel_uuid: HybridId) -> Hotel:
    hotel = await get_by_uuid(session, Hotel, hotel_uuid)
    if hotel is None:
        raise HTTPException(404, "Hotel tidak ditemukan")
    return hotel


async def _get_lead(session: AsyncSession, lead_uuid: HybridId) -> Lead:
    lead = await get_by_uuid(session, Lead, lead_uuid)
    if lead is None:
        raise HTTPException(404, "Lead tidak ditemukan")
    return lead


def _lead_out(lead: Lead) -> dict:
    data = LeadOut.model_validate(lead).model_dump()
    data["followup_due"] = followup_due(lead)
    return data


async def _lead_quotations(session: AsyncSession, lead_internal_id: int) -> list[dict]:
    rows = (
        await session.scalars(
            select(Quotation)
            .where(Quotation.lead_id == lead_internal_id)
            .order_by(Quotation.created_at.asc())
        )
    ).all()
    return [
        {
            "id": q.uuid,
            "quotation_no": q.quotation_no,
            "lead_id": q.lead.uuid if q.lead else None,
            "hotel_id": q.hotel.uuid if q.hotel else None,
            "event_date": q.event_date,
            "event_name": q.event_name,
            "package_type": q.package_type,
            "pax_count": q.pax_count,
            "sbm_rate_value": q.sbm_rate_value,
            "sbm_fiscal_year": q.sbm_fiscal_year,
            "gross_amount": q.gross_amount,
            "discount_amount": q.discount_amount,
            "final_amount": q.final_amount,
            "discount_approval_status": q.discount_approval_status,
            "status": q.status,
            "created_by": q.created_by_user.uuid if q.created_by_user else None,
        }
        for q in rows
    ]


async def _lead_activities(session: AsyncSession, lead: Lead) -> list[LeadActivityOut]:
    rows = (
        await session.scalars(
            select(LeadActivity)
            .where(LeadActivity.lead_id == lead.id)
            .order_by(LeadActivity.at.asc())
        )
    ).all()
    return [
        LeadActivityOut.model_validate(a).model_copy(update={"next_followup_at": lead.next_followup_at})
        for a in rows
    ]


async def _lead_referrals(session: AsyncSession, lead_id: uuid.UUID) -> list[dict]:
    rows = (
        await session.scalars(
            select(LeadReferral)
            .where(LeadReferral.lead_id == lead_id)
            .order_by(LeadReferral.created_at.asc())
        )
    ).all()
    return [LeadReferralOut.model_validate(r).model_dump() for r in rows]


async def _get_quotation(session: AsyncSession, quotation_uuid: HybridId) -> Quotation:
    quotation = await get_by_uuid(session, Quotation, quotation_uuid)
    if quotation is None:
        raise HTTPException(404, "Quotation tidak ditemukan")
    return quotation


async def _quotation_pdf_url(quotation: Quotation) -> str | None:
    if not quotation.pdf_key:
        return None
    try:
        from app.api.endpoints import crm

        return crm.presigned_get_url(quotation.pdf_key)
    except Exception:
        return None


async def _quotation_detail(
    session: AsyncSession,
    quotation: Quotation,
    scope: set[uuid.UUID] | None,
) -> QuotationDetailOut:
    if scope is not None and quotation.hotel_id not in scope:
        raise HTTPException(403, "Missing permission: crm scope hotel/region/global")
    milestones = list(
        (
            await session.scalars(
                select(BillingMilestone)
                .where(BillingMilestone.quotation_id == quotation.id)
                .order_by(BillingMilestone.due_date.asc())
            )
        ).all()
    )
    detail = QuotationDetailOut.model_validate(quotation)
    detail.milestones = [BillingMilestoneOut.model_validate(m) for m in milestones]
    detail.pdf_url = await _quotation_pdf_url(quotation)
    return detail


def _quarter_start(day: date) -> date:
    """Tanggal 1 awal kuartal saat `day` berada (default periode = kuartal berjalan)."""
    q = (day.month - 1) // 3
    return date(day.year, q * 3 + 1, 1)
