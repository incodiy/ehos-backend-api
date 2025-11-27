"""CAPA Four-Eyes workflow & resolution endpoints — openapi.yaml (F-03, task 7a)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.api.endpoints.capa_common import (
    fresh_ticket,
    get_ticket,
    require_approve,
    require_manage,
    require_read,
    require_resolve,
)
from app.api.security_helpers import perms as _perms
from app.core.identity import get_by_uuid
from app.models import CapaMedia, CapaStatusHistory, User
from app.schemas.capa import (
    AssignTicketRequest,
    CapaHistoryOut,
    CapaMediaOut,
    CapaTicketOut,
    GmReviewRequest,
    QaReviewRequest,
    ResolveTicketRequest,
)
from app.schemas.common import Envelope, HybridId
from app.services.capa_lifecycle import (
    InvalidTransitionError,
    assign_ticket,
    resolve_ticket,
    verify_ticket,
)
from app.services.sla_escalation import escalate_one_level

router = APIRouter()


def _media_after_required(media: list) -> None:
    """Constraint C1: bukti perbaikan wajib foto AFTER (live-camera, F-04)."""
    if media and any(m.phase != "AFTER" for m in media):
        raise HTTPException(422, "Bukti resolve hanya boleh phase=AFTER (foto perbaikan)")


@router.post("/tickets/{id}/assign", response_model=Envelope[CapaTicketOut])
async def assign(
    id: HybridId,
    payload: AssignTicketRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[CapaTicketOut]:
    ticket = await get_ticket(session, id)
    await require_manage(session, current, ticket.hotel.uuid)
    assignee = await get_by_uuid(session, User, payload.assigned_to)
    if assignee is None or not assignee.is_active:
        raise HTTPException(422, "Assignee tidak ditemukan / nonaktif")
    try:
        ticket = await assign_ticket(session, ticket, assignee.id, current.id, payload.note)
    except InvalidTransitionError as exc:
        raise HTTPException(409, str(exc)) from None
    await session.commit()
    ticket = await fresh_ticket(session, ticket)
    return Envelope(data=CapaTicketOut.model_validate(ticket))


@router.post("/tickets/{id}/resolve", response_model=Envelope[dict])
async def resolve(
    id: HybridId,
    payload: ResolveTicketRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[dict]:
    ticket = await get_ticket(session, id)
    await require_resolve(session, current, ticket.hotel.uuid)
    _media_after_required(payload.media)
    try:
        ticket = await resolve_ticket(session, ticket, current.id, payload.note, payload.media)
    except InvalidTransitionError as exc:
        raise HTTPException(409, str(exc)) from None
    await session.commit()
    ticket = await fresh_ticket(session, ticket)
    media = (await session.scalars(
        select(CapaMedia).where(CapaMedia.ticket_id == ticket.id)
    )).all()
    return Envelope(data={
        "ticket": CapaTicketOut.model_validate(ticket),
        "media": [CapaMediaOut.model_validate(m) for m in media],
    })


@router.post("/tickets/{id}/verify/gm", response_model=Envelope[CapaTicketOut])
async def verify_gm(
    id: HybridId,
    payload: GmReviewRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[CapaTicketOut]:
    ticket = await get_ticket(session, id)
    await require_manage(session, current, ticket.hotel.uuid)
    action = "gm_approve" if payload.decision == "APPROVE" else "gm_reject"
    try:
        ticket = await verify_ticket(session, ticket, action, current.id, payload.note)
    except InvalidTransitionError as exc:
        raise HTTPException(409, str(exc)) from None
    await session.commit()
    ticket = await fresh_ticket(session, ticket)
    return Envelope(data=CapaTicketOut.model_validate(ticket))


@router.post("/tickets/{id}/verify/qa", response_model=Envelope[CapaTicketOut])
async def verify_qa(
    id: HybridId,
    payload: QaReviewRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[CapaTicketOut]:
    ticket = await get_ticket(session, id)
    await require_approve(session, current)
    action = "qa_close" if payload.decision == "CLOSE" else "qa_reopen"
    try:
        ticket = await verify_ticket(session, ticket, action, current.id, payload.note)
    except InvalidTransitionError as exc:
        raise HTTPException(409, str(exc)) from None
    await session.commit()
    ticket = await fresh_ticket(session, ticket)
    return Envelope(data=CapaTicketOut.model_validate(ticket))


@router.post("/tickets/{id}/escalate", response_model=Envelope[CapaTicketOut])
async def escalate(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[CapaTicketOut]:
    ticket = await get_ticket(session, id)
    codes = await _perms(session, current)
    if "capa:approve" not in codes and "capa:manage:hotel" not in codes:
        raise HTTPException(403, "Missing permission: capa:manage:hotel / capa:approve")
    try:
        await escalate_one_level(session, ticket, current.id)
    except InvalidTransitionError as exc:
        raise HTTPException(409, str(exc)) from None
    await session.commit()
    ticket = await fresh_ticket(session, ticket)
    return Envelope(data=CapaTicketOut.model_validate(ticket))


@router.get("/tickets/{id}/history", response_model=Envelope[list[CapaHistoryOut]])
async def ticket_history(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[list[CapaHistoryOut]]:
    ticket = await get_ticket(session, id)
    await require_read(session, current, ticket.hotel.uuid)
    rows = (await session.scalars(
        select(CapaStatusHistory).where(CapaStatusHistory.ticket_id == ticket.id)
        .order_by(CapaStatusHistory.at.asc())
    )).all()
    return Envelope(data=[CapaHistoryOut.model_validate(r) for r in rows])
