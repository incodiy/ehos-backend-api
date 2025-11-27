"""Document & billing milestone endpoints — PRD-F-10 (task 8f).

- GET   /crm/billing                     daftar milestone dinas (scoped finance)
- POST  /crm/billing/milestones          buat milestone SPK/NPWP/BAST/LPJ
- PATCH /crm/billing/milestones/{id}     lampirkan dokumen / tandai PAID

RBAC (Constraint A1/A5): baca `crm:read`; tulis **`billing:manage`** (baru —
HOTEL_FINANCE / HOTEL_GM / CORP_EXEC / ROOT). Scope tenant isolation sama dgn
CRM: hotel assignment / region / global (korporat zero-assignment).

F-10: milestone dibuat hanya utk quotation **ACCEPTED** (alur menang →
dokumen dinas); PAID terminal (imutabilitas finansial ERD v1.3).
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession
from app.api.security_helpers import allowed_hotel_ids
from app.api.security_helpers import perms as _perms
from app.core.identity import get_by_uuid
from app.models import BillingMilestone, Quotation, User
from app.schemas.common import Envelope, HybridId, Paginated, PaginationMeta
from app.schemas.crm import BillingMilestoneCreateRequest, BillingMilestoneOut, BillingMilestoneUpdateRequest
from app.services.media_storage import presigned_get_url
from app.services.billing_pipeline import (
    MILESTONE_STATUSES,
    BillingDuplicateError,
    BillingMilestoneError,
    BillingQuotationError,
    BillingTransitionError,
    create_billing_milestone,
    update_billing_milestone,
)

router = APIRouter(prefix="/crm/billing", tags=["billing"])


async def _require_billing_read(session: AsyncSession, user: User) -> set[int] | None:
    if "crm:read" not in await _perms(session, user):
        raise HTTPException(403, "Missing permission: crm:read")
    return await allowed_hotel_ids(session, user) or None


async def _require_billing_manage(session: AsyncSession, user: User) -> set[int] | None:
    if "billing:manage" not in await _perms(session, user):
        raise HTTPException(403, "Missing permission: billing:manage")
    return await allowed_hotel_ids(session, user) or None


async def _get_quotation(session: AsyncSession, quotation_id: HybridId) -> Quotation:
    quotation = await get_by_uuid(session, Quotation, quotation_id)
    if quotation is None:
        raise HTTPException(404, "Quotation tidak ditemukan")
    return quotation


async def _get_milestone(session: AsyncSession, milestone_id: HybridId) -> BillingMilestone:
    milestone = await get_by_uuid(session, BillingMilestone, milestone_id)
    if milestone is None:
        raise HTTPException(404, "Milestone tidak ditemukan")
    return milestone


def _to_out(milestone: BillingMilestone) -> BillingMilestoneOut:
    out = BillingMilestoneOut.model_validate(milestone)
    if milestone.doc_key:
        try:
            out.doc_url = presigned_get_url(milestone.doc_key)
        except Exception:
            out.doc_url = None
    return out


@router.get("", response_model=Paginated[Envelope[list[BillingMilestoneOut]], BillingMilestoneOut])
async def list_milestones(
    current: CurrentUser,
    session: DbSession,
    quotation_id: HybridId | None = None,
    status: str | None = None,
    due_before: date | None = None,
    page: int = Query(1, ge=1),
) -> Paginated[Envelope[list[BillingMilestoneOut]], BillingMilestoneOut]:
    """Daftar milestone dinas (scoped finance, F-10) — data nyata dari DB."""
    scope = await _require_billing_read(session, current)
    stmt = select(BillingMilestone)
    count_stmt = select(func.count(BillingMilestone.id))
    # scope: join quotation utk men-dereference hotel (markup relasi tenantable).
    if scope is not None:
        stmt = stmt.join(Quotation, BillingMilestone.quotation_id == Quotation.id)
        count_stmt = count_stmt.join(Quotation, BillingMilestone.quotation_id == Quotation.id)
        stmt = stmt.where(Quotation.hotel_id.in_(scope))
        count_stmt = count_stmt.where(Quotation.hotel_id.in_(scope))
    if quotation_id is not None:
        quotation = await get_by_uuid(session, Quotation, quotation_id)
        quotation_internal = quotation.id if quotation is not None else -1
        stmt = stmt.where(BillingMilestone.quotation_id == quotation_internal)
        count_stmt = count_stmt.where(BillingMilestone.quotation_id == quotation_internal)
    if status:
        status = status.upper()
        if status not in MILESTONE_STATUSES:
            raise HTTPException(422, f"status tidak dikenal: {status} (legal: {sorted(MILESTONE_STATUSES)})")
        stmt = stmt.where(BillingMilestone.status == status)
        count_stmt = count_stmt.where(BillingMilestone.status == status)
    if due_before is not None:
        stmt = stmt.where(BillingMilestone.due_date <= due_before)
        count_stmt = count_stmt.where(BillingMilestone.due_date <= due_before)

    per_page = 50
    total = await session.scalar(count_stmt) or 0
    last_page = max(1, (total + per_page - 1) // per_page)
    rows = (
        await session.scalars(
            stmt.order_by(BillingMilestone.due_date.desc())
            .offset((max(1, page) - 1) * per_page)
            .limit(per_page)
        )
    ).all()
    return Paginated(
        data=[_to_out(r) for r in rows],
        meta=PaginationMeta(current_page=max(1, page), per_page=per_page, total=total, last_page=last_page),
    )


@router.get("/milestones/{id}", response_model=Envelope[BillingMilestoneOut])
async def get_milestone_endpoint(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[BillingMilestoneOut]:
    """Detail milestone dinas SPK/NPWP/BAST/LPJ (F-10)."""
    scope = await _require_billing_read(session, current)
    milestone = await _get_milestone(session, id)
    quotation = milestone.quotation
    if quotation is None:
        raise HTTPException(404, "Quotation tidak ditemukan")
    if scope is not None and quotation.hotel_id not in scope:
        raise HTTPException(403, "Missing permission: billing scope hotel/region/global")
    return Envelope(data=_to_out(milestone))


@router.post("/milestones", response_model=Envelope[BillingMilestoneOut], status_code=201)
async def create_milestone_endpoint(
    payload: BillingMilestoneCreateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[BillingMilestoneOut]:
    """Buat milestone SPK/NPWP/BAST/LPJ (F-10) utk quotation ACCEPTED."""
    scope = await _require_billing_manage(session, current)
    quotation = await _get_quotation(session, payload.quotation_id)
    if scope is not None and quotation.hotel_id not in scope:
        raise HTTPException(403, "Missing permission: billing scope hotel/region/global")
    try:
        milestone = await create_billing_milestone(
            session,
            quotation=quotation,
            milestone_type=payload.milestone_type,
            due_date=payload.due_date,
            amount=payload.amount,
            doc_no=payload.doc_no,
            actor_id=current.id,
        )
    except (BillingMilestoneError, BillingQuotationError) as exc:
        code = 409 if isinstance(exc, (BillingQuotationError, BillingDuplicateError)) else 422
        raise HTTPException(code, str(exc)) from None
    await session.commit()
    await session.refresh(milestone)
    milestone.quotation = quotation
    milestone.updated_by_user = current
    return Envelope(data=_to_out(milestone))


@router.patch("/milestones/{id}", response_model=Envelope[BillingMilestoneOut])
async def update_milestone_endpoint(
    id: HybridId,
    payload: BillingMilestoneUpdateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[BillingMilestoneOut]:
    """Update milestone: lampirkan dokumen / tandai PAID (F-10)."""
    scope = await _require_billing_manage(session, current)
    milestone = await _get_milestone(session, id)
    quotation = milestone.quotation
    if quotation is None:
        raise HTTPException(404, "Quotation tidak ditemukan")
    if scope is not None and quotation.hotel_id not in scope:
        raise HTTPException(403, "Missing permission: billing scope hotel/region/global")
    try:
        milestone = await update_billing_milestone(
            session,
            milestone,
            status=payload.status,
            doc_key=payload.doc_key,
            doc_no=payload.doc_no,
            paid_at=payload.paid_at,
            actor_id=current.id,
        )
    except BillingMilestoneError as exc:
        raise HTTPException(409 if isinstance(exc, BillingTransitionError) else 422, str(exc)) from None
    await session.commit()
    await session.refresh(milestone)
    milestone.updated_by_user = current
    return Envelope(data=_to_out(milestone))