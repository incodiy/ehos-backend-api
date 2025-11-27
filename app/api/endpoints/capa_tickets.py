"""CAPA tickets CRUD endpoints — openapi.yaml `/capa/tickets*` (F-03, task 7a)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession
from app.api.endpoints.capa_common import fresh_ticket, get_ticket, require_manage, require_read
from app.api.security_helpers import perms as _perms
from app.core.identity import get_by_uuid
from app.models import CapaMedia, CapaStatusHistory, CapaTicket, Finding, User
from app.models.master import Hotel, HotelDepartment
from app.schemas.capa import (
    CAPA_STATUSES,
    CapaHistoryOut,
    CapaMediaOut,
    CapaTicketCreateRequest,
    CapaTicketOut,
    CapaTicketUpdateRequest,
)
from app.schemas.common import Envelope, HybridId, Paginated, PaginationMeta
from app.services.capa_media import before_after_summary
from app.services.capa_trigger import auto_create_capa_ticket, resolve_department_id
from app.services.sla_escalation import SLA_CLASS_HOURS, sla_status

router = APIRouter()


@router.get("/tickets", response_model=Paginated[Envelope[list[CapaTicketOut]], CapaTicketOut])
async def list_tickets(
    current: CurrentUser,
    session: DbSession,
    hotel_id: HybridId | None = None,
    priority: int | None = None,
    status: str | None = None,
    only_overdue: bool = False,
    page: int = 1,
) -> Paginated[Envelope[list[CapaTicketOut]], CapaTicketOut]:
    if hotel_id is not None:
        await require_read(session, current, hotel_id)
    else:
        codes = await _perms(session, current)
        if not (codes & {"capa:read:global", "capa:approve"}):
            raise HTTPException(403, "Missing permission: capa:read:global (list tanpa filter hotel)")
    stmt = select(CapaTicket)
    count_stmt = select(func.count(CapaTicket.id))
    if hotel_id:
        hotel_row = await get_by_uuid(session, Hotel, hotel_id)
        hotel_internal: int | None = hotel_row.id if hotel_row else None
        stmt = stmt.where(CapaTicket.hotel_id == hotel_internal)
        count_stmt = count_stmt.where(CapaTicket.hotel_id == hotel_internal)
    if priority:
        if priority not in (1, 2, 3):
            raise HTTPException(422, "priority harus 1/2/3")
        stmt = stmt.where(CapaTicket.priority == priority)
        count_stmt = count_stmt.where(CapaTicket.priority == priority)
    if status:
        if status not in CAPA_STATUSES:
            raise HTTPException(422, f"status tidak dikenal: {status} (legal: {sorted(CAPA_STATUSES)})")
        stmt = stmt.where(CapaTicket.status == status)
        count_stmt = count_stmt.where(CapaTicket.status == status)
    if only_overdue:
        now = datetime.now(UTC)
        stmt = stmt.where(CapaTicket.due_at < now, CapaTicket.status != "CLOSED")
        count_stmt = count_stmt.where(CapaTicket.due_at < now, CapaTicket.status != "CLOSED")

    per_page = 50
    total = await session.scalar(count_stmt) or 0
    last_page = max(1, (total + per_page - 1) // per_page)
    rows = (await session.scalars(
        stmt.order_by(CapaTicket.priority.asc(), CapaTicket.due_at.asc())
        .offset((max(1, page) - 1) * per_page).limit(per_page)
    )).all()
    now = datetime.now(UTC)
    out: list[CapaTicketOut] = []
    for r in rows:
        row = CapaTicketOut.model_validate(r)
        row.sla_status = sla_status(r)
        row.overdue = r.status != "CLOSED" and r.due_at < now
        out.append(row)
    return Paginated(
        data=out,
        meta=PaginationMeta(current_page=max(1, page), per_page=per_page,
                            total=total or 0, last_page=last_page),
    )


@router.post("/tickets", response_model=Envelope[CapaTicketOut], status_code=201)
async def create_ticket(
    payload: CapaTicketCreateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[CapaTicketOut]:
    # 1. Flow finding-based (dari audit)
    if payload.finding_id is not None:
        finding = await get_by_uuid(session, Finding, payload.finding_id)
        if finding is None:
            raise HTTPException(404, "Finding tidak ditemukan")
        await require_manage(session, current, finding.hotel.uuid)
        existing = await session.scalar(
            select(CapaTicket).where(CapaTicket.finding_id == finding.id).limit(1)
        )
        if existing is not None:
            return Envelope(data=CapaTicketOut.model_validate(existing))

        sess = finding.session
        department = sess.department if sess else None
        ticket = await auto_create_capa_ticket(
            session, finding, actor_id=current.id, department=department
        )
        if payload.assigned_to and ticket.status != "CLOSED":
            assignee = await get_by_uuid(session, User, payload.assigned_to)
            ticket.assigned_to = assignee.id if assignee else None
        await session.commit()
        ticket = await get_by_uuid(session, CapaTicket, ticket.uuid)
        return Envelope(data=CapaTicketOut.model_validate(ticket))

    # 2. Flow manual ad-hoc / whistleblower
    if payload.hotel_id is None:
        raise HTTPException(422, "hotel_id wajib diisi untuk tiket ad-hoc manual")
    if not payload.title:
        raise HTTPException(422, "title wajib diisi untuk tiket ad-hoc manual")

    hotel = await get_by_uuid(session, Hotel, payload.hotel_id)
    if hotel is None:
        raise HTTPException(404, "Hotel tidak ditemukan")
    await require_manage(session, current, hotel.uuid)

    priority = payload.priority or 2
    sla_hours = SLA_CLASS_HOURS.get(priority, 48)
    now = datetime.now(UTC)
    dept_id = None
    if payload.department:
        dept_id = await resolve_department_id(session, hotel.id, payload.department)

    assignee_id = None
    init_status = "OPEN"
    if payload.assigned_to:
        assignee = await get_by_uuid(session, User, payload.assigned_to)
        if assignee and assignee.is_active:
            assignee_id = assignee.id
            init_status = "IN_PROGRESS"

    ticket = CapaTicket(
        finding_id=None,
        hotel_id=hotel.id,
        department_id=dept_id,
        priority=priority,
        sla_hours=sla_hours,
        due_at=now + timedelta(hours=sla_hours),
        status=init_status,
        title=payload.title,
        description=payload.description,
        assigned_to=assignee_id,
        escalation_level=0,
        created_by=current.id,
        reporter_id=current.id,
        receipt_id=f"CAPA-{uuid.uuid4().hex[:7].upper()}",
        origin="MANUAL",
    )
    session.add(ticket)
    await session.flush()

    session.add(CapaStatusHistory(
        ticket_id=ticket.id,
        from_status=None,
        to_status="OPEN",
        actor_id=current.id,
        note="Manual ticket created from Admin Panel",
    ))
    if init_status == "IN_PROGRESS":
        session.add(CapaStatusHistory(
            ticket_id=ticket.id,
            from_status="OPEN",
            to_status="IN_PROGRESS",
            actor_id=current.id,
            note=f"Assigned to resolver upon creation",
        ))

    await session.commit()
    ticket = await get_by_uuid(session, CapaTicket, ticket.uuid)
    return Envelope(data=CapaTicketOut.model_validate(ticket))


@router.get("/tickets/{id}", response_model=Envelope[dict])
async def get_ticket_endpoint(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[dict]:
    ticket = await get_ticket(session, id)
    await require_read(session, current, ticket.hotel.uuid)
    media = (await session.scalars(
        select(CapaMedia).where(CapaMedia.ticket_id == ticket.id).order_by(CapaMedia.captured_at.asc())
    )).all()
    history = (await session.scalars(
        select(CapaStatusHistory).where(CapaStatusHistory.ticket_id == ticket.id)
        .order_by(CapaStatusHistory.at.asc())
    )).all()
    hotel = ticket.hotel
    assignee = ticket.assigned_to_user
    return Envelope(data={
        **CapaTicketOut.model_validate(ticket).model_dump(),
        "hotel_code": hotel.code if hotel else None,
        "assignee_name": assignee.name if assignee else None,
        "overdue": ticket.status != "CLOSED" and ticket.due_at < datetime.now(UTC),
        "sla_status": sla_status(ticket),
        "media_summary": before_after_summary(list(media)),
        "media": [CapaMediaOut.model_validate(m) for m in media],
        "history": [CapaHistoryOut.model_validate(h) for h in history],
    })


@router.patch("/tickets/{id}", response_model=Envelope[CapaTicketOut])
async def update_ticket(
    id: HybridId,
    payload: CapaTicketUpdateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[CapaTicketOut]:
    ticket = await get_ticket(session, id)
    await require_manage(session, current, ticket.hotel.uuid)

    if ticket.status not in ("OPEN", "IN_PROGRESS"):
        raise HTTPException(409, f"Tiket dengan status {ticket.status} tidak dapat diubah metadatanya")

    if payload.title is not None:
        ticket.title = payload.title
    if payload.description is not None:
        ticket.description = payload.description
    if payload.priority is not None:
        ticket.priority = payload.priority
        ticket.sla_hours = SLA_CLASS_HOURS.get(payload.priority, ticket.sla_hours)
    if payload.due_at is not None:
        ticket.due_at = payload.due_at
    if payload.assigned_to is not None:
        assignee = await get_by_uuid(session, User, payload.assigned_to)
        if assignee and assignee.is_active:
            ticket.assigned_to = assignee.id
            if ticket.status == "OPEN":
                ticket.status = "IN_PROGRESS"
                session.add(CapaStatusHistory(
                    ticket_id=ticket.id,
                    from_status="OPEN",
                    to_status="IN_PROGRESS",
                    actor_id=current.id,
                    note="Assigned to resolver via update",
                ))

    await session.commit()
    ticket = await fresh_ticket(session, ticket)
    return Envelope(data=CapaTicketOut.model_validate(ticket))
