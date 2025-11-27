"""CRM Leads endpoints — list, create, detail, update, activities, and referral."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.api.endpoints.crm_common import (
    _get_lead,
    _lead_activities,
    _lead_out,
    _lead_quotations,
    _lead_referrals,
    _require_manage,
    _require_read,
    _resolve_hotel_uuid,
    _scope_hotel_ids,
)
from app.api.security_helpers import perms as _perms
from app.core.identity import get_by_uuid
from app.models import (
    Hotel,
    Lead,
    LeadActivity,
    Province,
    User,
)
from app.schemas.common import Envelope, HybridId, Paginated, PaginationMeta
from app.schemas.crm import (
    ACTIVITY_TYPES,
    LEAD_STATUSES,
    AddActivityRequest,
    LeadActivityOut,
    LeadCreateRequest,
    LeadKanbanRowOut,
    LeadOut,
    LeadReferralOut,
    LeadUpdateRequest,
    ReferLeadRequest,
)
from app.services.crm_pipeline import (
    InvalidTransitionError,
    MissingLostReasonError,
    ReferralError,
    add_activity,
    change_status,
    create_lead,
    generate_lead_no,
    refer_cross_property,
)
from app.services.notifications import create_notification, human_due

router = APIRouter()


@router.get("/leads", response_model=Paginated[Envelope[list[LeadKanbanRowOut]], LeadKanbanRowOut])
async def list_leads(
    current: CurrentUser,
    session: DbSession,
    status: str | None = None,
    source: str | None = None,
    owner_id: HybridId | None = None,
    followup_due: bool = False,
    hotel_id: HybridId | None = None,
    page: int = 1,
) -> Paginated[Envelope[list[LeadKanbanRowOut]], LeadKanbanRowOut]:
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

    hotel_ids = {r.hotel_id for r in rows}
    owner_ids = {r.owner_id for r in rows}
    hotel_codes = dict(
        (await session.execute(select(Hotel.id, Hotel.code).where(Hotel.id.in_(hotel_ids)))).all()
    ) if hotel_ids else {}
    owner_names = dict(
        (await session.execute(select(User.id, User.name).where(User.id.in_(owner_ids)))).all()
    ) if owner_ids else {}

    kanban: list[LeadKanbanRowOut] = []
    for r in rows:
        row = _lead_out(r)
        row["hotel_code"] = hotel_codes.get(r.hotel_id)
        row["owner_name"] = owner_names.get(r.owner_id)
        kanban.append(LeadKanbanRowOut.model_validate(row))

    return Paginated(
        data=kanban,
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

    target_owner_id = current.id
    if payload.owner_id is not None:
        assigned_owner = await get_by_uuid(session, User, payload.owner_id)
        if assigned_owner is None or not assigned_owner.is_active:
            raise HTTPException(422, "Owner sales yang ditugaskan tidak ditemukan atau nonaktif")
        target_owner_id = assigned_owner.id

    lead = await create_lead(
        session,
        lead_no=generate_lead_no(hotel.code, payload.source),
        hotel_id=hotel.id,
        source=payload.source,
        institution_type=payload.institution_type,
        company_name=payload.company_name,
        owner_id=target_owner_id,
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

    for field in ("next_followup_at", "amount_est", "pic_name", "pic_email", "company_name", "institution_type", "source"):
        if field in changes:
            setattr(lead, field, changes[field])
    if "pic_phone" in changes:
        lead.pic_phone = changes["pic_phone"]
    if "province_id" in changes:
        if changes["province_id"] is None:
            lead.province_id = None
        else:
            province = await get_by_uuid(session, Province, changes["province_id"])
            if province is None:
                raise HTTPException(404, "Province tidak ditemukan")
            lead.province_id = province.id
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
