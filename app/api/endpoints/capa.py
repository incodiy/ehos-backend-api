"""CAPA ticket lifecycle endpoints — openapi.yaml `/capa/tickets*` (F-03, task 7a).

Daftar endpoint:
- GET   /capa/tickets                       list + filter (hotel/priority/status/only_overdue/page)
- POST  /capa/tickets                       buat tiket dari finding (auto-settle SLA F-03)
- GET   /capa/tickets/{id}                  detail tiket + media + history
- POST  /capa/tickets/{id}/assign           assign ke resolver (HOD/EHK)
- POST  /capa/tickets/{id}/resolve          teknisi submit perbaikan (foto AFTER) → AWAITING_GM
- POST  /capa/tickets/{id}/verify/gm        GM first-approver (APPROVE/REJECT)
- POST  /capa/tickets/{id}/verify/qa        corporate QA final-approver (CLOSE/REOPEN)
- POST  /capa/tickets/{id}/escalate         naikkan escalation_level (alert receiver di-seed 7b)
- GET   /capa/tickets/{id}/history          riwayat transisi status (audit trail)

State machine & timeline SLA tinggal di service `capa_lifecycle`; endpoint ini
menjaga RBAC (Constraint A5/A1) + validasi input + envelope response.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession
from app.api.security_helpers import perms as _perms
from app.api.security_helpers import user_scoped_to_hotel as _user_scoped_to_hotel
from app.core.identity import get_by_uuid
from app.models import (
    CapaMedia,
    CapaStatusHistory,
    CapaTicket,
    Finding,
    User,
)
from app.models.master import Hotel
from app.schemas.capa import (
    CAPA_STATUSES,
    AssignTicketRequest,
    CapaHistoryOut,
    CapaMediaConfirmRequest,
    CapaMediaListOut,
    CapaMediaOut,
    CapaMediaPresignOut,
    CapaTicketCreateRequest,
    CapaTicketOut,
    GmReviewRequest,
    QaReviewRequest,
    ResolveTicketRequest,
)
from app.schemas.common import Envelope, HybridId, Paginated, PaginationMeta
from app.services.capa_lifecycle import (
    InvalidTransitionError,
    assign_ticket,
    resolve_ticket,
    verify_ticket,
)
from app.services.capa_media import (
    before_after_summary,
    confirm_ticket_media,
    presign_ticket_media,
)
from app.services.capa_trigger import auto_create_capa_ticket
from app.services.media_storage import StorageUnavailableError
from app.services.sla_escalation import escalate_one_level, sla_status

router = APIRouter(prefix="/capa", tags=["capa"])


# ─── RBAC / scope helpers (Constraint A1/A5) ─────────────────────────────
# `_perms` / `_user_scoped_to_hotel` shared di `app.api.security_helpers`.


async def _require_read(session: AsyncSession, user: User, hotel_id: HybridId) -> None:
    codes = await _perms(session, user)
    if "capa:read:global" in codes or "capa:approve" in codes:
        return  # corporate QA / ROOT_ADMIN : global read
    if not (codes & {"capa:read:hotel", "capa:manage:hotel", "capa:resolve:hotel"}):
        raise HTTPException(403, "Missing permission: capa:read (hotel/global)")
    if not await _user_scoped_to_hotel(session, user, hotel_id):
        raise HTTPException(403, "Missing permission: capa scope hotel/region/global")


async def _require_manage(session: AsyncSession, user: User, hotel_id: HybridId) -> None:
    codes = await _perms(session, user)
    if "capa:approve" in codes:
        return
    if "capa:manage:hotel" not in codes:
        raise HTTPException(403, "Missing permission: capa:manage:hotel")
    if not await _user_scoped_to_hotel(session, user, hotel_id):
        raise HTTPException(403, "Missing permission: capa scope hotel/region/global")


async def _require_resolve(session: AsyncSession, user: User, hotel_id: HybridId) -> None:
    codes = await _perms(session, user)
    if "capa:resolve:hotel" not in codes:
        raise HTTPException(403, "Missing permission: capa:resolve:hotel")
    if not await _user_scoped_to_hotel(session, user, hotel_id):
        raise HTTPException(403, "Missing permission: capa scope hotel/region/global")


async def _require_approve(session: AsyncSession, user: User) -> None:
    if "capa:approve" not in await _perms(session, user):
        raise HTTPException(403, "Missing permission: capa:approve")


async def _require_upload(session: AsyncSession, user: User, hotel_id: HybridId) -> None:
    """Resolve-ir/manage (teknisi/HOD/GM) dengan scope hotel — unggah bukti media."""
    codes = await _perms(session, user)
    if not (codes & {"capa:resolve:hotel", "capa:manage:hotel", "capa:approve"}):
        raise HTTPException(403, "Missing permission: capa:resolve:hotel / capa:manage:hotel")
    if "capa:approve" in codes:
        return
    if not await _user_scoped_to_hotel(session, user, hotel_id):
        raise HTTPException(403, "Missing permission: capa scope hotel/region/global")


# ─── helpers ─────────────────────────────────────────────────────────────

async def _get_ticket(session: AsyncSession, ticket_id: HybridId) -> CapaTicket:
    t = await get_by_uuid(session, CapaTicket, ticket_id)
    if t is None:
        raise HTTPException(404, "Tiket CAPA tidak ditemukan")
    return t


async def _fresh_ticket(session: AsyncSession, ticket: CapaTicket) -> CapaTicket:
    """Re-fetch ticket setelah commit + mutasi agar relationship (assigned_to_user,
    closed_by_user) tidak stale — expire_on_commit=False menyimpan cache identity map."""
    return (
        await session.execute(
            select(CapaTicket)
            .where(CapaTicket.uuid == ticket.uuid)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


def _media_after_required(media: list) -> None:
    """Constraint C1: bukti perbaikan wajib foto AFTER (live-camera, F-04)."""
    if media and any(m.phase != "AFTER" for m in media):
        raise HTTPException(422, "Bukti resolve hanya boleh phase=AFTER (foto perbaikan)")


# ─── endpoints ───────────────────────────────────────────────────────────

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
        await _require_read(session, current, hotel_id)
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
    return Paginated(
        data=[CapaTicketOut.model_validate(r) for r in rows],
        meta=PaginationMeta(current_page=max(1, page), per_page=per_page,
                            total=total or 0, last_page=last_page),
    )


@router.post("/tickets", response_model=Envelope[CapaTicketOut], status_code=201)
async def create_ticket(
    payload: CapaTicketCreateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[CapaTicketOut]:
    finding = await get_by_uuid(session, Finding, payload.finding_id)
    if finding is None:
        raise HTTPException(404, "Finding tidak ditemukan")
    await _require_manage(session, current, finding.hotel.uuid)
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


@router.get("/tickets/{id}", response_model=Envelope[dict])
async def get_ticket(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[dict]:
    ticket = await _get_ticket(session, id)
    await _require_read(session, current, ticket.hotel.uuid)
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
        "media_summary": before_after_summary(media),
        "media": [CapaMediaOut.model_validate(m) for m in media],
        "history": [CapaHistoryOut.model_validate(h) for h in history],
    })


@router.post("/tickets/{id}/assign", response_model=Envelope[CapaTicketOut])
async def assign(
    id: HybridId,
    payload: AssignTicketRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[CapaTicketOut]:
    ticket = await _get_ticket(session, id)
    await _require_manage(session, current, ticket.hotel.uuid)
    assignee = await get_by_uuid(session, User, payload.assigned_to)
    if assignee is None or not assignee.is_active:
        raise HTTPException(422, "Assignee tidak ditemukan / nonaktif")
    try:
        ticket = await assign_ticket(session, ticket, assignee.id, current.id, payload.note)
    except InvalidTransitionError as exc:
        raise HTTPException(409, str(exc)) from None
    await session.commit()
    ticket = await _fresh_ticket(session, ticket)
    return Envelope(data=CapaTicketOut.model_validate(ticket))


@router.post("/tickets/{id}/resolve", response_model=Envelope[dict])
async def resolve(
    id: HybridId,
    payload: ResolveTicketRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[dict]:
    ticket = await _get_ticket(session, id)
    await _require_resolve(session, current, ticket.hotel.uuid)
    _media_after_required(payload.media)
    try:
        ticket = await resolve_ticket(session, ticket, current.id, payload.note, payload.media)
    except InvalidTransitionError as exc:
        raise HTTPException(409, str(exc)) from None
    await session.commit()
    ticket = await _fresh_ticket(session, ticket)
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
    ticket = await _get_ticket(session, id)
    await _require_manage(session, current, ticket.hotel.uuid)
    action = "gm_approve" if payload.decision == "APPROVE" else "gm_reject"
    try:
        ticket = await verify_ticket(session, ticket, action, current.id, payload.note)
    except InvalidTransitionError as exc:
        raise HTTPException(409, str(exc)) from None
    await session.commit()
    ticket = await _fresh_ticket(session, ticket)
    return Envelope(data=CapaTicketOut.model_validate(ticket))


@router.post("/tickets/{id}/verify/qa", response_model=Envelope[CapaTicketOut])
async def verify_qa(
    id: HybridId,
    payload: QaReviewRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[CapaTicketOut]:
    ticket = await _get_ticket(session, id)
    await _require_approve(session, current)
    action = "qa_close" if payload.decision == "CLOSE" else "qa_reopen"
    try:
        ticket = await verify_ticket(session, ticket, action, current.id, payload.note)
    except InvalidTransitionError as exc:
        raise HTTPException(409, str(exc)) from None
    await session.commit()
    ticket = await _fresh_ticket(session, ticket)
    return Envelope(data=CapaTicketOut.model_validate(ticket))


@router.post("/tickets/{id}/escalate", response_model=Envelope[CapaTicketOut])
async def escalate(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[CapaTicketOut]:
    ticket = await _get_ticket(session, id)
    codes = await _perms(session, current)
    if "capa:approve" not in codes and "capa:manage:hotel" not in codes:
        raise HTTPException(403, "Missing permission: capa:manage:hotel / capa:approve")
    try:
        await escalate_one_level(session, ticket, current.id)
    except InvalidTransitionError as exc:
        raise HTTPException(409, str(exc)) from None
    await session.commit()
    ticket = await _fresh_ticket(session, ticket)
    return Envelope(data=CapaTicketOut.model_validate(ticket))


@router.get("/tickets/{id}/history", response_model=Envelope[list[CapaHistoryOut]])
async def ticket_history(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[list[CapaHistoryOut]]:
    ticket = await _get_ticket(session, id)
    await _require_read(session, current, ticket.hotel.uuid)
    rows = (await session.scalars(
        select(CapaStatusHistory).where(CapaStatusHistory.ticket_id == ticket.id)
        .order_by(CapaStatusHistory.at.asc())
    )).all()
    return Envelope(data=[CapaHistoryOut.model_validate(r) for r in rows])


# ─── CAPA media split-path (task 7c, C2 / ARD-005) ────────────────────────

async def _get_ticket_media(session: AsyncSession, ticket_id: int, media_id: HybridId) -> CapaMedia:
    media = await get_by_uuid(session, CapaMedia, media_id)
    if media is None:
        raise HTTPException(404, "Media CAPA tidak ditemukan")
    if media.ticket_id != ticket_id:
        raise HTTPException(404, "Media CAPA tidak ditemukan pada ticket ini")
    return media


@router.get("/tickets/{id}/media", response_model=Envelope[CapaMediaListOut])
async def list_ticket_media(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[CapaMediaListOut]:
    """Daftar bukti media ticket + komparasi BEFORE vs AFTER (verification hub)."""
    ticket = await _get_ticket(session, id)
    await _require_read(session, current, ticket.hotel.uuid)
    media = (await session.scalars(
        select(CapaMedia).where(CapaMedia.ticket_id == ticket.id).order_by(CapaMedia.captured_at.asc())
    )).all()
    return Envelope(data=CapaMediaListOut(
        items=[CapaMediaOut.model_validate(m) for m in media],
        summary=before_after_summary(list(media)),
    ))


@router.post("/tickets/{id}/media/{media_id}/presign", response_model=Envelope[CapaMediaPresignOut])
async def presign_ticket_media_endpoint(
    id: HybridId,
    media_id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[CapaMediaPresignOut]:
    """Keluarkan presigned PUT URL untuk media (expire 7 mnt, renew diizinkan)."""
    ticket = await _get_ticket(session, id)
    await _require_upload(session, current, ticket.hotel.uuid)
    media = await _get_ticket_media(session, ticket.id, media_id)
    try:
        media, url = await presign_ticket_media(session, media, ticket.id, current.id)
    except InvalidTransitionError as exc:
        raise HTTPException(409, str(exc)) from None
    await session.commit()
    return Envelope(data=CapaMediaPresignOut(
        media_id=media.uuid,
        object_key=media.object_key,
        presigned_url=url,
        upload_status=media.upload_status,
        expires_in=420,
    ))


@router.post("/tickets/{id}/media/{media_id}/confirm", response_model=Envelope[CapaMediaOut])
async def confirm_ticket_media_endpoint(
    id: HybridId,
    media_id: HybridId,
    current: CurrentUser,
    session: DbSession,
    payload: CapaMediaConfirmRequest | None = None,
) -> Envelope[CapaMediaOut]:
    """Konfirmasi upload selesai → verify object (size/mime) → VERIFIED / FAILED."""
    ticket = await _get_ticket(session, id)
    await _require_upload(session, current, ticket.hotel.uuid)
    media = await _get_ticket_media(session, ticket.id, media_id)
    if payload is not None and payload.object_key is not None:
        if payload.object_key != media.object_key:
            raise HTTPException(422, "object_key tidak cocok dengan media tersimpan")
    try:
        media = await confirm_ticket_media(session, media, ticket.id, current.id)
    except StorageUnavailableError as exc:
        raise HTTPException(503, str(exc)) from None
    await session.commit()
    await session.refresh(media)
    return Envelope(data=CapaMediaOut.model_validate(media))