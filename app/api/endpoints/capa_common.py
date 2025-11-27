"""CAPA common helpers & security guards (Constraint A1/A5)."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.security_helpers import perms as _perms
from app.api.security_helpers import user_scoped_to_hotel as _user_scoped_to_hotel
from app.core.identity import get_by_uuid
from app.models import CapaMedia, CapaTicket, User
from app.schemas.common import HybridId


async def require_read(session: AsyncSession, user: User, hotel_id: HybridId) -> None:
    codes = await _perms(session, user)
    if "capa:read:global" in codes or "capa:approve" in codes:
        return  # corporate QA / ROOT_ADMIN : global read
    if not (codes & {"capa:read:hotel", "capa:manage:hotel", "capa:resolve:hotel"}):
        raise HTTPException(403, "Missing permission: capa:read (hotel/global)")
    if not await _user_scoped_to_hotel(session, user, hotel_id):
        raise HTTPException(403, "Missing permission: capa scope hotel/region/global")


async def require_manage(session: AsyncSession, user: User, hotel_id: HybridId) -> None:
    codes = await _perms(session, user)
    if "capa:approve" in codes:
        return
    if "capa:manage:hotel" not in codes:
        raise HTTPException(403, "Missing permission: capa:manage:hotel")
    if not await _user_scoped_to_hotel(session, user, hotel_id):
        raise HTTPException(403, "Missing permission: capa scope hotel/region/global")


async def require_resolve(session: AsyncSession, user: User, hotel_id: HybridId) -> None:
    codes = await _perms(session, user)
    if "capa:resolve:hotel" not in codes:
        raise HTTPException(403, "Missing permission: capa:resolve:hotel")
    if not await _user_scoped_to_hotel(session, user, hotel_id):
        raise HTTPException(403, "Missing permission: capa scope hotel/region/global")


async def require_approve(session: AsyncSession, user: User) -> None:
    if "capa:approve" not in await _perms(session, user):
        raise HTTPException(403, "Missing permission: capa:approve")


async def require_upload(session: AsyncSession, user: User, hotel_id: HybridId) -> None:
    """Resolve-ir/manage (teknisi/HOD/GM) dengan scope hotel — unggah bukti media."""
    codes = await _perms(session, user)
    if not (codes & {"capa:resolve:hotel", "capa:manage:hotel", "capa:approve"}):
        raise HTTPException(403, "Missing permission: capa:resolve:hotel / capa:manage:hotel")
    if "capa:approve" in codes:
        return
    if not await _user_scoped_to_hotel(session, user, hotel_id):
        raise HTTPException(403, "Missing permission: capa scope hotel/region/global")


async def get_ticket(session: AsyncSession, ticket_id: HybridId) -> CapaTicket:
    t = await get_by_uuid(session, CapaTicket, ticket_id)
    if t is None:
        raise HTTPException(404, "Tiket CAPA tidak ditemukan")
    return t


async def fresh_ticket(session: AsyncSession, ticket: CapaTicket) -> CapaTicket:
    """Re-fetch ticket setelah commit + mutasi agar relationship tidak stale."""
    return (
        await session.execute(
            select(CapaTicket)
            .where(CapaTicket.uuid == ticket.uuid)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def get_ticket_media(session: AsyncSession, ticket_id: int, media_id: HybridId) -> CapaMedia:
    media = await get_by_uuid(session, CapaMedia, media_id)
    if media is None:
        raise HTTPException(404, "Media CAPA tidak ditemukan")
    if media.ticket_id != ticket_id:
        raise HTTPException(404, "Media CAPA tidak ditemukan pada ticket ini")
    return media
