"""Audit session media endpoints — openapi.yaml `/audit/sessions/{id}/media` (Constraint C1-C4, ARD-005)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession
from app.api.security_helpers import perms, user_scoped_to_hotel
from app.core.identity import get_by_uuid
from app.models import AuditMedia, AuditSession, ChecklistItem, Finding, User
from app.models.master import Hotel
from app.schemas.audit import AuditMediaOut, MediaRegisterRequest, MediaRegisterResult
from app.schemas.common import Envelope, HybridId
from app.services.media_storage import presigned_put_url

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


@router.post("/sessions/{id}/media", response_model=Envelope[MediaRegisterResult], status_code=201)
async def register_media(
    id: HybridId,
    payload: MediaRegisterRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[MediaRegisterResult]:
    """Daftarkan media temuan/audit -> return presigned upload URL (C2 split-path)."""
    sess = await _get_session(session, id)
    await _require_write(session, current, sess.hotel_id)

    finding_internal_id: int | None = None
    if payload.finding_id is not None:
        f = await get_by_uuid(session, Finding, payload.finding_id)
        if f is not None:
            finding_internal_id = f.id

    item_internal_id: int | None = None
    if payload.item_id is not None:
        it = await get_by_uuid(session, ChecklistItem, payload.item_id)
        if it is not None:
            item_internal_id = it.id

    media_obj = AuditMedia(
        session_id=sess.id,
        finding_id=finding_internal_id,
        item_id=item_internal_id,
        phase=payload.phase,
        source_camera=payload.source_camera,
        object_key=f"audit/{sess.uuid}/{payload.checksum_sha256}.webp",
        mime=payload.mime,
        width=payload.width,
        height=payload.height,
        size_bytes=payload.size_bytes,
        checksum_sha256=payload.checksum_sha256,
        gps_lat=payload.gps_lat,
        gps_lng=payload.gps_lng,
        gps_valid=bool(payload.gps_lat and payload.gps_lng),
        captured_at=payload.captured_at,
        server_captured_at=datetime.now(),
        upload_status="PENDING",
    )
    session.add(media_obj)
    await session.commit()
    await session.refresh(media_obj)

    upload_url = presigned_put_url(media_obj.object_key, media_obj.mime)
    return Envelope(
        data=MediaRegisterResult(
            media_id=media_obj.uuid,
            presigned_url=upload_url,
            object_key=media_obj.object_key,
        )
    )


@router.get("/sessions/{id}/media", response_model=Envelope[list[AuditMediaOut]])
async def list_session_media(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[list[AuditMediaOut]]:
    """Daftar media bukti yang terhubung dengan sesi audit ini."""
    sess = await _get_session(session, id)
    await _require_read(session, current, sess.hotel_id)

    media_rows = (
        await session.scalars(
            select(AuditMedia).where(AuditMedia.session_id == sess.id)
        )
    ).all()
    return Envelope(data=[AuditMediaOut.model_validate(m) for m in media_rows])


@router.post("/sessions/{id}/media/{mediaId}/confirm", response_model=Envelope[AuditMediaOut])
async def confirm_session_media(
    id: HybridId,
    mediaId: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[AuditMediaOut]:
    """Konfirmasi upload media telah sukses di object storage."""
    sess = await _get_session(session, id)
    await _require_write(session, current, sess.hotel_id)

    media = await get_by_uuid(session, AuditMedia, mediaId)
    if media is None or media.session_id != sess.id:
        raise HTTPException(404, "Media tidak ditemukan")

    media.upload_status = "UPLOADED"
    await session.commit()
    await session.refresh(media)
    return Envelope(data=AuditMediaOut.model_validate(media))
