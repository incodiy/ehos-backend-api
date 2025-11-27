"""CRM Quotations endpoints — list, create, detail, and PDF generation."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.api.endpoints.crm_common import (
    _get_lead,
    _get_quotation,
    _quotation_detail,
    _require_manage,
    _require_read,
    _scope_hotel_ids,
)
from app.core.identity import get_by_uuid
from app.models import Hotel, Lead, Province, Quotation
from app.schemas.common import Envelope, HybridId, Paginated, PaginationMeta
from app.schemas.crm import (
    QUOTATION_STATUSES,
    QuotationCreateRequest,
    QuotationDetailOut,
    QuotationOut,
    QuotationUpdateRequest,
)
from app.api.endpoints import crm
from app.services.pdf_quotation import render_quotation_pdf
from app.services.quotation_pipeline import (
    QuotationCreateError,
    SbmPaguExceededError,
    SbmRateMissingError,
    create_quotation,
    review_quotation_discount,
    update_quotation_status,
)

router = APIRouter()


@router.get("/quotations", response_model=Paginated[Envelope[list[QuotationOut]], QuotationOut])
async def list_quotations(
    current: CurrentUser,
    session: DbSession,
    lead_id: HybridId | None = None,
    status: str | None = None,
    page: int = 1,
) -> Paginated[Envelope[list[QuotationOut]], QuotationOut]:
    scope = await _scope_hotel_ids(session, current)
    stmt = select(Quotation)
    count_stmt = select(func.count(Quotation.id))
    if scope is not None:
        stmt = stmt.where(Quotation.hotel_id.in_(scope))
        count_stmt = count_stmt.where(Quotation.hotel_id.in_(scope))
    if lead_id is not None:
        lead = await get_by_uuid(session, Lead, lead_id)
        lead_internal = lead.id if lead is not None else -1
        stmt = stmt.where(Quotation.lead_id == lead_internal)
        count_stmt = count_stmt.where(Quotation.lead_id == lead_internal)
    if status:
        status = status.upper()
        if status not in QUOTATION_STATUSES:
            raise HTTPException(422, f"status tidak dikenal: {status} (legal: {sorted(QUOTATION_STATUSES)})")
        stmt = stmt.where(Quotation.status == status)
        count_stmt = count_stmt.where(Quotation.status == status)
    per_page = 50
    total = await session.scalar(count_stmt) or 0
    last_page = max(1, (total + per_page - 1) // per_page)
    rows = (
        await session.scalars(
            stmt.order_by(Quotation.created_at.desc())
            .offset((max(1, page) - 1) * per_page)
            .limit(per_page)
        )
    ).all()
    return Paginated(
        data=[QuotationOut.model_validate(r) for r in rows],
        meta=PaginationMeta(current_page=max(1, page), per_page=per_page, total=total, last_page=last_page),
    )


@router.post("/quotations", response_model=Envelope[QuotationOut], status_code=201)
async def create_quotation_endpoint(
    payload: QuotationCreateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[QuotationOut]:
    """Generate quotation (F-09): verifikasi pagu SBM + snapshot E3.

    GOV lead dgn rate efektif > pagu provinsi/tahun → 409. LOST lead → 409.
    """
    lead = await _get_lead(session, payload.lead_id)
    await _require_manage(session, current, lead.hotel_id)
    hotel = await session.get(Hotel, lead.hotel_id)
    if hotel is None:
        raise HTTPException(404, "Hotel tidak ditemukan")
    try:
        gross_amount = payload.gross_amount or lead.amount_est or 0
        quotation = await create_quotation(
            session,
            lead=lead,
            hotel=hotel,
            event_date=payload.event_date,
            event_name=payload.event_name,
            package_type=payload.package_type,
            pax_count=payload.pax_count,
            gross_amount=float(gross_amount),
            discount_amount=float(payload.discount_amount),
            actor_id=current.id,
        )
    except (SbmPaguExceededError, SbmRateMissingError, QuotationCreateError) as exc:
        raise HTTPException(409, str(exc)) from None
    await session.commit()
    await session.refresh(quotation)
    return Envelope(data=QuotationOut.model_validate(quotation))


@router.get("/quotations/{id}", response_model=Envelope[QuotationDetailOut])
async def get_quotation(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[QuotationDetailOut]:
    quotation = await _get_quotation(session, id)
    await _require_read(session, current, quotation.hotel_id)
    return Envelope(data=await _quotation_detail(session, quotation, None))


@router.patch("/quotations/{id}", response_model=Envelope[QuotationOut])
async def update_quotation(
    id: HybridId,
    payload: QuotationUpdateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[QuotationOut]:
    """Update status FSM (SENT/ACCEPTED/DECLINED), GM discount approval, atau metadata quotation."""
    quotation = await _get_quotation(session, id)
    await _require_manage(session, current, quotation.hotel_id)

    try:
        # 1. Review diskon approval jika ditentukan
        if payload.discount_approval_status is not None:
            await review_quotation_discount(
                session,
                quotation,
                decision=payload.discount_approval_status,
                actor_id=current.id,
                note=payload.note,
            )

        # 2. Update status FSM jika ditentukan
        if payload.status is not None and payload.status != quotation.status:
            await update_quotation_status(
                session,
                quotation,
                target_status=payload.status,
                actor_id=current.id,
                note=payload.note,
            )

        # 3. Update detail rincian jika quotation masih DRAFT
        if quotation.status == "DRAFT":
            if payload.event_name is not None:
                quotation.event_name = payload.event_name
            if payload.event_date is not None:
                quotation.event_date = payload.event_date
            if payload.pax_count is not None:
                quotation.pax_count = payload.pax_count
            if payload.gross_amount is not None:
                quotation.gross_amount = payload.gross_amount
            if payload.discount_amount is not None:
                quotation.discount_amount = payload.discount_amount
            quotation.final_amount = quotation.gross_amount - quotation.discount_amount
            quotation.updated_by = current.id
    except QuotationCreateError as exc:
        raise HTTPException(409, str(exc)) from None

    await session.commit()
    await session.refresh(quotation)
    return Envelope(data=QuotationOut.model_validate(quotation))



@router.post("/quotations/{id}/pdf", response_model=Envelope[dict])
async def generate_quotation_pdf(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[dict]:
    """Generate PDF penawaran ber-kop + barcode (F-09) & simpan ke object store."""
    quotation = await _get_quotation(session, id)
    await _require_manage(session, current, quotation.hotel_id)
    lead = await session.get(Lead, quotation.lead_id)
    hotel = await session.get(Hotel, quotation.hotel_id)
    if lead is None or hotel is None:
        raise HTTPException(404, "Lead/Hotel penawar tidak ditemukan")
    province = await session.get(Province, hotel.province_id)
    pdf_bytes = render_quotation_pdf(lead, hotel, quotation, province_name=province.name if province else None)
    pdf_key = f"quotations/{quotation.quotation_no}.pdf"
    try:
        crm.upload_bytes(pdf_key, pdf_bytes, "application/pdf")
    except Exception as exc:
        raise HTTPException(503, f"Object store tidak terjangkau: {exc}") from exc
    quotation.pdf_key = pdf_key
    quotation.updated_by = current.id
    await session.commit()
    return Envelope(data={"pdf_url": crm.presigned_get_url(pdf_key)})
