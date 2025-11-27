"""Audit offline sync & conflict resolution endpoints — PRD-F-05."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession
from app.api.security_helpers import perms, user_scoped_to_hotel
from app.core.identity import get_by_uuid
from app.models import AuditSession, User
from app.models.master import Hotel
from app.schemas.audit import (
    ResolveConflictRequest,
    SyncConflictOut,
    SyncPushRequest,
    SyncPushResult,
)
from app.schemas.common import Envelope, HybridId
from app.services import audit_sync

router = APIRouter()


async def _require_read(session: AsyncSession, user: User, hotel_internal_id: int | None = None) -> None:
    user_perms = await perms(session, user)
    if hotel_internal_id is None:
        if "audit:read:global" not in user_perms:
            raise HTTPException(403, "Missing permission: audit:read:global (list tanpa filter hotel)")
        return
    if "audit:read:global" in user_perms:
        return
    hotel = await session.get(Hotel, hotel_internal_id)
    if hotel and await user_scoped_to_hotel(session, user, hotel.uuid):
        return
    raise HTTPException(403, "Missing permission: audit scope hotel/region/global")


async def _require_write(session: AsyncSession, user: User, hotel_internal_id: int) -> None:
    user_perms = await perms(session, user)
    if "audit:read:global" in user_perms or "audit:create" in user_perms or "audit:score" in user_perms:
        return
    hotel = await session.get(Hotel, hotel_internal_id)
    if hotel and await user_scoped_to_hotel(session, user, hotel.uuid):
        if "audit:run:hotel" in user_perms:
            return
    raise HTTPException(403, "Missing permission: audit:run:hotel")


async def _get_session(session: AsyncSession, session_id: HybridId) -> AuditSession:
    s = await get_by_uuid(session, AuditSession, session_id)
    if s is None:
        raise HTTPException(404, "Sesi audit tidak ditemukan")
    return s


@router.post("/sessions/sync", response_model=Envelope[SyncPushResult])
async def sync_push_endpoint(
    payload: SyncPushRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[SyncPushResult]:
    """Push batch offline (dedupe client_id, timestamp merge conflict F-05)."""
    result = await audit_sync.sync_push(session, payload, current)
    return Envelope(data=result)


@router.get("/sessions/{id}/sync", response_model=Envelope[dict])
async def sync_pull_endpoint(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
    since: datetime,
) -> Envelope[dict]:
    """Pull perubahan sejak timestamp tertentu (incremental sync F-05)."""
    sess = await _get_session(session, id)
    await _require_read(session, current, sess.hotel_id)

    result = await audit_sync.sync_pull(session, sess, since)
    return Envelope(data=result)


@router.get("/sessions/{id}/conflicts", response_model=Envelope[list[SyncConflictOut]])
async def list_conflicts_endpoint(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[list[SyncConflictOut]]:
    """Daftar konflik sinkronisasi yang berstatus PENDING."""
    sess = await _get_session(session, id)
    await _require_read(session, current, sess.hotel_id)

    conflicts = await audit_sync.list_conflicts(session, sess)
    return Envelope(data=[SyncConflictOut.model_validate(r) for r in conflicts])


@router.post("/sessions/{id}/conflicts/{conflictId}/resolve", response_model=Envelope[SyncConflictOut])
async def resolve_conflict_endpoint(
    id: HybridId,
    conflictId: HybridId,
    payload: ResolveConflictRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[SyncConflictOut]:
    """Resolusi konflik manual: tentukan nilai pemenang (F-05)."""
    sess = await _get_session(session, id)
    await _require_write(session, current, sess.hotel_id)

    conflict = await audit_sync.resolve_conflict(
        session, sess, conflictId, payload.winning_value, payload.note, current
    )
    return Envelope(data=SyncConflictOut.model_validate(conflict))
