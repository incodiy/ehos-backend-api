"""CAPA media split-path endpoints — openapi.yaml (F-04, task 7c, C2 / ARD-005)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.api.endpoints.capa_common import (
    get_ticket,
    get_ticket_media,
    require_read,
    require_upload,
)
from app.models import CapaMedia
from app.schemas.capa import (
    CapaMediaConfirmRequest,
    CapaMediaListOut,
    CapaMediaOut,
    CapaMediaPresignGetOut,
    CapaMediaPresignOut,
)
from app.schemas.common import Envelope, HybridId
from app.services.capa_lifecycle import InvalidTransitionError
from app.services.capa_media import (
    before_after_summary,
    confirm_ticket_media,
    presign_ticket_media,
    presign_ticket_media_get,
)
from app.services.media_storage import StorageUnavailableError

router = APIRouter()


@router.get("/tickets/{id}/media", response_model=Envelope[CapaMediaListOut])
async def list_ticket_media(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[CapaMediaListOut]:
    """Daftar bukti media ticket + komparasi BEFORE vs AFTER (verification hub)."""
    ticket = await get_ticket(session, id)
    await require_read(session, current, ticket.hotel.uuid)
    media = (await session.scalars(
        select(CapaMedia).where(CapaMedia.ticket_id == ticket.id).order_by(CapaMedia.captured_at.asc())
    )).all()
    return Envelope(data=CapaMediaListOut(
        items=[CapaMediaOut.model_validate(m) for m in media],
        summary=before_after_summary(list(media)),
    ))


@router.get("/tickets/{id}/media/{media_id}/presign-get", response_model=Envelope[CapaMediaPresignGetOut])
async def presign_get_ticket_media_endpoint(
    id: HybridId,
    media_id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[CapaMediaPresignGetOut]:
    """Presigned GET URL utk menampilkan bukti media (verification hub, 5 mnt)."""
    ticket = await get_ticket(session, id)
    await require_read(session, current, ticket.hotel.uuid)
    media = await get_ticket_media(session, ticket.id, media_id)
    try:
        url = presign_ticket_media_get(media, ticket.id)
    except InvalidTransitionError as exc:
        raise HTTPException(409, str(exc)) from None
    return Envelope(data=CapaMediaPresignGetOut(
        media_id=media.uuid,
        object_key=media.object_key,
        presigned_url=url,
        expires_in=300,
    ))


@router.post("/tickets/{id}/media/{media_id}/presign", response_model=Envelope[CapaMediaPresignOut])
async def presign_ticket_media_endpoint(
    id: HybridId,
    media_id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[CapaMediaPresignOut]:
    """Keluarkan presigned PUT URL untuk media (expire 7 mnt, renew diizinkan)."""
    ticket = await get_ticket(session, id)
    await require_upload(session, current, ticket.hotel.uuid)
    media = await get_ticket_media(session, ticket.id, media_id)
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
    ticket = await get_ticket(session, id)
    await require_upload(session, current, ticket.hotel.uuid)
    media = await get_ticket_media(session, ticket.id, media_id)
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
