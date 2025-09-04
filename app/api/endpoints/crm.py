"""CRM kanban pipeline endpoints — openapi.yaml `/crm/leads*` (F-07, task 8a).

Daftar endpoint:
- GET   /crm/leads                 daftar leads (tenant isolation hotel/region/global)
- POST  /crm/leads                 buat lead manual (sumber MANUAL, default status LEAD)
- GET   /crm/leads/{id}            detail lead + aktivitas + quotation
- PATCH /crm/leads/{id}            update lead (status kanban; LOST → lost_reason wajib 422)
- POST  /crm/leads/{id}/activities catat aktivitas follow-up + jadwal berikutnya

RBAC (Constraint A1/A5): `crm:read` untuk baca, `crm:manage` untuk tulis.
Scope: user dengan zero assignment (ROOT_ADMIN/CORP) = global; lainnya terisolasi
ke hotel/region miliknya. Transisi ilegal → 409; lost tanpa alasan → 422 (F-07).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession
from app.api.security_helpers import allowed_hotel_ids
from app.api.security_helpers import perms as _perms
from app.core.identity import get_by_uuid
from app.models import (
    BillingMilestone,
    GovernmentSbmRate,
    Lead,
    LeadActivity,
    LeadReferral,
    Quotation,
    User,
)
from app.models.master import Hotel, Province, Region
from app.schemas.common import Envelope, HybridId, Paginated, PaginationMeta
from app.schemas.crm import (
    ACTIVITY_TYPES,
    LEAD_STATUSES,
    QUOTATION_STATUSES,
    AddActivityRequest,
    BillingMilestoneOut,
    LeadActivityOut,
    LeadCreateRequest,
    LeadOut,
    LeadReferralOut,
    LeadUpdateRequest,
    LostReasonAnalyticsOut,
    QuotationCreateRequest,
    QuotationDetailOut,
    QuotationOut,
    ReferLeadRequest,
    SbmRateOut,
    SbmRateUpdateRequest,
)
from app.services.crm_analytics import compute_lost_reason_analytics, date_to_range_utc
from app.services.crm_pipeline import (
    InvalidTransitionError,
    MissingLostReasonError,
    ReferralError,
    add_activity,
    change_status,
    create_lead,
    followup_due,
    generate_lead_no,
    refer_cross_property,
)
from app.services.media_storage import presigned_get_url, upload_bytes
from app.services.notifications import create_notification, human_due
from app.services.pdf_quotation import render_quotation_pdf
from app.services.quotation_pipeline import (
    QuotationCreateError,
    SbmPaguExceededError,
    SbmRateMissingError,
    create_quotation,
)

router = APIRouter(prefix="/crm", tags=["crm"])


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


# ─── helpers ─────────────────────────────────────────────────────────────

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


# ─── endpoints ───────────────────────────────────────────────────────────

@router.get("/leads", response_model=Paginated[Envelope[list[LeadOut]], LeadOut])
async def list_leads(
    current: CurrentUser,
    session: DbSession,
    status: str | None = None,
    source: str | None = None,
    owner_id: HybridId | None = None,
    followup_due: bool = False,
    hotel_id: HybridId | None = None,
    page: int = 1,
) -> Paginated[Envelope[list[LeadOut]], LeadOut]:
    scope = await _scope_hotel_ids(session, current)
    hotel_internal: int | None = None
    if hotel_id is not None:
        if "crm:read" not in await _perms(session, current):
            raise HTTPException(403, "Missing permission: crm:read")
        hotel = await _resolve_hotel_uuid(session, hotel_id)
        hotel_internal = hotel.id
        if scope is not None and hotel_internal not in scope:
            raise HTTPException(403, "Missing permission: crm scope hotel/region/global")

    owner_internal: int | None = None
    if owner_id is not None:
        owner = await get_by_uuid(session, User, owner_id)
        owner_internal = owner.id if owner is not None else -1

    stmt = select(Lead)
    count_stmt = select(func.count(Lead.id))
    if hotel_internal is not None:
        stmt = stmt.where(Lead.hotel_id == hotel_internal)
        count_stmt = count_stmt.where(Lead.hotel_id == hotel_internal)
    elif scope is not None:
        stmt = stmt.where(Lead.hotel_id.in_(scope))
        count_stmt = count_stmt.where(Lead.hotel_id.in_(scope))
    if status:
        status = status.upper()
        if status not in LEAD_STATUSES:
            raise HTTPException(422, f"status tidak dikenal: {status} (legal: {sorted(LEAD_STATUSES)})")
        stmt = stmt.where(Lead.status == status)
        count_stmt = count_stmt.where(Lead.status == status)
    if source:
        stmt = stmt.where(Lead.source == source.upper())
        count_stmt = count_stmt.where(Lead.source == source.upper())
    if owner_internal is not None:
        stmt = stmt.where(Lead.owner_id == owner_internal)
        count_stmt = count_stmt.where(Lead.owner_id == owner_internal)
    if followup_due:
        now = datetime.now(UTC)
        stmt = stmt.where(
            Lead.status.notin_(["CONFIRMED", "LOST"]),
            Lead.next_followup_at.is_not(None),
            Lead.next_followup_at <= now,
        )
        count_stmt = count_stmt.where(
            Lead.status.notin_(["CONFIRMED", "LOST"]),
            Lead.next_followup_at.is_not(None),
            Lead.next_followup_at <= now,
        )

    per_page = 50
    total = await session.scalar(count_stmt) or 0
    last_page = max(1, (total + per_page - 1) // per_page)
    rows = (
        await session.scalars(
            stmt.order_by(Lead.created_at.desc())
            .offset((max(1, page) - 1) * per_page)
            .limit(per_page)
        )
    ).all()
    return Paginated(
        data=[LeadOut.model_validate(r) for r in rows],
        meta=PaginationMeta(current_page=max(1, page), per_page=per_page, total=total, last_page=last_page),
    )


@router.post("/leads", response_model=Envelope[LeadOut], status_code=201)
async def create_lead_endpoint(
    payload: LeadCreateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[LeadOut]:
    hotel = await _resolve_hotel_uuid(session, payload.hotel_id)
    await _require_manage(session, current, hotel.id)
    province = await get_by_uuid(session, Province, payload.province_id) if payload.province_id else None
    if payload.province_id and province is None:
        raise HTTPException(404, "Province tidak ditemukan")
    lead = await create_lead(
        session,
        lead_no=generate_lead_no(hotel.code, payload.source),
        hotel_id=hotel.id,
        source=payload.source,
        institution_type=payload.institution_type,
        company_name=payload.company_name,
        owner_id=current.id,
        pic_name=payload.pic_name,
        pic_phone=payload.pic_phone,
        pic_email=str(payload.pic_email) if payload.pic_email else None,
        province_id=province.id if province else None,
        amount_est=payload.amount_est,
        next_followup_at=payload.next_followup_at,
        created_by=current.id,
    )
    await session.commit()
    await session.refresh(lead)
    return Envelope(data=LeadOut.model_validate(lead))


@router.get("/leads/{id}", response_model=Envelope[dict])
async def get_lead(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[dict]:
    lead = await _get_lead(session, id)
    await _require_read(session, current, lead.hotel_id)
    hotel = await session.get(Hotel, lead.hotel_id)
    owner = await session.get(User, lead.owner_id)
    return Envelope(data={
        **_lead_out(lead),
        "hotel_code": hotel.code if hotel else None,
        "owner_name": owner.name if owner else None,
        "next_followup_human": human_due(lead.next_followup_at) if lead.next_followup_at else None,
        "activities": [a.model_dump() for a in await _lead_activities(session, lead)],
        "quotations": await _lead_quotations(session, lead.id),
        "referrals": await _lead_referrals(session, lead.id),
    })


@router.patch("/leads/{id}", response_model=Envelope[LeadOut])
async def update_lead(
    id: HybridId,
    payload: LeadUpdateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[LeadOut]:
    lead = await _get_lead(session, id)
    await _require_manage(session, current, lead.hotel_id)

    new_owner_internal: int | None = None
    if payload.owner_id is not None:
        new_owner = await get_by_uuid(session, User, payload.owner_id)
        if new_owner is None or not new_owner.is_active:
            raise HTTPException(422, "Owner baru tidak ditemukan / nonaktif")
        new_owner_internal = new_owner.id

    changes = payload.model_dump(exclude_unset=True)
    if "status" in changes and changes["status"] is not None:
        try:
            await change_status(
                session,
                lead,
                status=changes["status"],
                lost_reason=changes.get("lost_reason"),
                actor_id=current.id,
            )
        except MissingLostReasonError as exc:
            raise HTTPException(422, str(exc)) from None
        except InvalidTransitionError as exc:
            raise HTTPException(409, str(exc)) from None

    for field in ("next_followup_at", "amount_est", "pic_name", "pic_email"):
        if field in changes:
            setattr(lead, field, changes[field])
    if "pic_phone" in changes:
        lead.pic_phone = changes["pic_phone"]
    if new_owner_internal is not None and "owner_id" in changes:
        lead.owner_id = new_owner_internal
    lead.updated_by = current.id

    await session.commit()
    await session.refresh(lead)
    return Envelope(data=LeadOut.model_validate(lead))


@router.post("/leads/{id}/activities", response_model=Envelope[LeadActivityOut], status_code=201)
async def add_lead_activity(
    id: HybridId,
    payload: AddActivityRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[LeadActivityOut]:
    lead = await _get_lead(session, id)
    await _require_manage(session, current, lead.hotel_id)
    if payload.type not in ACTIVITY_TYPES:
        raise HTTPException(422, f"type tidak dikenal: {payload.type} (legal: {sorted(ACTIVITY_TYPES)})")
    if lead.status == "LOST":
        raise HTTPException(409, "Lead LOST terminal — tidak boleh aktivitas lanjutan")
    try:
        if payload.next_followup_at is not None:
            lead.next_followup_at = payload.next_followup_at
        activity = await add_activity(
            session,
            lead,
            activity_type=payload.type,
            note=payload.note,
            actor_id=current.id,
            next_followup_at=payload.next_followup_at,
        )
    except InvalidTransitionError as exc:
        raise HTTPException(409, str(exc)) from None
    await session.commit()
    await session.refresh(activity)
    return Envelope(data=LeadActivityOut.model_validate(activity).model_copy(
        update={"next_followup_at": lead.next_followup_at}
    ))


@router.post("/leads/{id}/refer", response_model=Envelope[LeadReferralOut], status_code=201)
async def refer_lead(
    id: HybridId,
    payload: ReferLeadRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[LeadReferralOut]:
    """Cross-property referral (F-08): serahkan lead ke hotel lain + tracking komisi.

    Guard `crm:manage` dua arah (asal sekarang, tujuan saat terima). Lead dipindah
    kepemilikan ke hotel tujuan (`source=REFERRAL`, `referred_from_hotel_id`); record
    `lead_referrals` immutable utk komisi. Pelanggaran aturan → 409, hotel tujuan
    tak ada → 404, komisi negatif → 422 (pydantic).
    """
    lead = await _get_lead(session, id)
    await _require_manage(session, current, lead.hotel_id)
    target = await _resolve_hotel_uuid(session, payload.to_hotel_id)
    from_hotel = await session.get(Hotel, lead.hotel_id)
    try:
        referral, new_owner = await refer_cross_property(
            session,
            lead,
            to_hotel_id=target.id,
            commission_amount=payload.commission_amount,
            note=payload.note,
            actor_id=current.id,
            target=target,
        )
    except ReferralError as exc:
        raise HTTPException(409, str(exc)) from None

    await create_notification(
        session,
        key="crm_referral",
        user=new_owner,
        context={
            "lead_no": lead.lead_no,
            "company_name": lead.company_name,
            "from_hotel_code": from_hotel.code if from_hotel else "-",
            "to_hotel_code": target.code,
            "url": f"/crm/leads/{lead.uuid}",
        },
        entity_type="lead",
        entity_id=lead.uuid,
    )
    await session.commit()
    await session.refresh(referral)
    return Envelope(data=LeadReferralOut.model_validate(referral))


# ─── Quotation generator + pagu SBM + PDF (F-09, task 8d) ──────────────────

async def _get_quotation(session: AsyncSession, quotation_uuid: HybridId) -> Quotation:
    quotation = await get_by_uuid(session, Quotation, quotation_uuid)
    if quotation is None:
        raise HTTPException(404, "Quotation tidak ditemukan")
    return quotation


async def _quotation_pdf_url(quotation: Quotation) -> str | None:
    if not quotation.pdf_key:
        return None
    try:
        return presigned_get_url(quotation.pdf_key)
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
        upload_bytes(pdf_key, pdf_bytes, "application/pdf")
    except Exception as exc:
        raise HTTPException(503, f"Object store tidak terjangkau: {exc}") from exc
    quotation.pdf_key = pdf_key
    quotation.updated_by = current.id
    await session.commit()
    return Envelope(data={"pdf_url": presigned_get_url(pdf_key)})


# ─── SBM rates (E1/E2, Corporate DOSM) ─────────────────────────────────────

@router.get("/sbm-rates", response_model=Envelope[list[SbmRateOut]])
async def list_sbm_rates(
    current: CurrentUser,
    session: DbSession,
    province_code: str | None = None,
    package_type: str | None = None,
    fiscal_year: int | None = None,
    is_active: bool | None = None,
) -> Envelope[list[SbmRateOut]]:
    if "crm:read" not in await _perms(session, current):
        raise HTTPException(403, "Missing permission: crm:read")
    stmt = select(GovernmentSbmRate, Province.code, Province.name).join(
        Province, GovernmentSbmRate.province_id == Province.id
    )
    if province_code:
        stmt = stmt.where(Province.code == province_code)
    if package_type:
        stmt = stmt.where(GovernmentSbmRate.package_type == package_type.upper())
    if fiscal_year:
        stmt = stmt.where(GovernmentSbmRate.fiscal_year == fiscal_year)
    if is_active is not None:
        stmt = stmt.where(GovernmentSbmRate.is_active == is_active)
    rows = (await session.execute(stmt.order_by(Province.code, GovernmentSbmRate.fiscal_year))).all()
    return Envelope(data=[
        SbmRateOut.model_validate(r[0]).model_copy(
            update={"province_code": r[1], "province_name": r[2]}
        )
        for r in rows
    ])


@router.patch("/sbm-rates/{id}", response_model=Envelope[SbmRateOut])
async def update_sbm_rate(
    id: HybridId,
    payload: SbmRateUpdateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[SbmRateOut]:
    """Update SBM rate (E2) — Corporate DOSM (corp.exec/root, perubahan PMK)."""
    if "sbm:write" not in await _perms(session, current):
        raise HTTPException(403, "Missing permission: sbm:write")
    scope = await allowed_hotel_ids(session, current)
    if scope:  # hanya corporate/global (tanpa assignment rumah) yang boleh ubah PMK master
        raise HTTPException(403, "SBM master hanya dikelola corporate (zero-assignment role)")
    rate = await get_by_uuid(session, GovernmentSbmRate, id)
    if rate is None:
        raise HTTPException(404, "SBM rate tidak ditemukan")
    changes = payload.model_dump(exclude_unset=True)
    if "fiscal_year" in changes and changes["fiscal_year"] != rate.fiscal_year:
        dup = await session.scalar(
            select(GovernmentSbmRate.id).where(
                GovernmentSbmRate.province_id == rate.province_id,
                GovernmentSbmRate.package_type == rate.package_type,
                GovernmentSbmRate.fiscal_year == changes["fiscal_year"],
            )
        )
        if dup is not None:
            raise HTTPException(409, "SBM rate utk provinsi/package/tahun itu sudah ada")
    for field in ("max_rate_per_pax", "fiscal_year", "is_active"):
        if field in changes and changes[field] is not None:
            setattr(rate, field, changes[field])
    rate.updated_by = current.id
    await session.commit()
    await session.refresh(rate)
    province = await session.get(Province, rate.province_id)
    return Envelope(data=SbmRateOut.model_validate(rate).model_copy(
        update={"province_code": province.code if province else None,
                "province_name": province.name if province else None}
    ))


# ─── Lost Reason Analytics (F-07, task 8e) ────────────────────────────────

def _quarter_start(day: date) -> date:
    """Tanggal 1 awal kuartal saat `day` berada (default periode = kuartal berjalan)."""
    q = (day.month - 1) // 3
    return date(day.year, q * 3 + 1, 1)


@router.get("/analytics/lost-reasons", response_model=Envelope[LostReasonAnalyticsOut])
async def lost_reason_analytics(
    current: CurrentUser,
    session: DbSession,
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
    hotel_id: HybridId | None = None,
    region_id: HybridId | None = None,
) -> Envelope[LostReasonAnalyticsOut]:
    """Lost Reason analytics per kuartal (F-07) — breakdown alasan + trend.

    RBAC: `crm:read` wajib. Scope tenant isolation: hotel/region user (scope)
    atau korporat (zero-assignment, global). Tanpa filter hotel/region → scope
    user dipakai (`hotel_ids=None` hanya untuk user global).
    """
    codes = await _perms(session, current)
    if "crm:read" not in codes:
        raise HTTPException(403, "Missing permission: crm:read")
    scope = await allowed_hotel_ids(session, current)
    scope_or_global = scope or None  # None = korporat/global

    if from_date and to_date:
        from_dt, to_dt = date_to_range_utc(from_date, to_date)
    else:
        from_dt, to_dt = date_to_range_utc(_quarter_start(date.today()), date.today())

    # resolusi scope: hotel = pin, region = subset scope, else scope user.
    if hotel_id is not None:
        hotel = await _resolve_hotel_uuid(session, hotel_id)
        await _require_read(session, current, hotel.id)
        hotel_ids: set[int] | None = {hotel.id}
    elif region_id is not None:
        region = await get_by_uuid(session, Region, region_id)
        if region is None:
            raise HTTPException(404, "Region tidak ditemukan")
        region_hotel_ids = set(
            (await session.scalars(select(Hotel.id).where(Hotel.region_id == region.id))).all()
        )
        if scope_or_global is not None:
            region_hotel_ids &= scope_or_global
            if not region_hotel_ids:
                raise HTTPException(403, "Missing permission: crm scope hotel/region/global")
        # set (mungkin kosong) = filter eksplisit region; None = tanpa filter.
        hotel_ids = region_hotel_ids
    else:
        hotel_ids = scope_or_global

    data = await compute_lost_reason_analytics(
        session,
        from_dt=from_dt,
        to_dt=to_dt,
        hotel_ids=hotel_ids,
    )
    data["period"] = f"{from_dt.date()}..{to_dt.date()}"
    return Envelope(data=LostReasonAnalyticsOut.model_validate(data))