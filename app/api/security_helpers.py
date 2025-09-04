"""Shared RBAC / tenant-scope helpers (Constraint A1/A5).

Digunakan oleh endpoint CAPA & CRM: resolve permission set user, cek scope
hotel (assignment langsung hotel ATAU via region), dan resolver daftar hotel
yang boleh dilihat user (tenant isolation tanpa RLS di lapis service).
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identity import get_by_uuid
from app.models import User, UserHotelAssignment, UserRegionAssignment
from app.models.master import Hotel


async def perms(session: AsyncSession, user: User) -> set[str]:
    rows = await session.execute(
        text(
            "SELECT p.code FROM users u "
            "JOIN user_roles ur ON ur.user_id = u.id "
            "JOIN roles_permissions rp ON rp.role_id = ur.role_id "
            "JOIN permissions p ON p.id = rp.permission_id "
            "WHERE u.id = :uid"
        ),
        {"uid": user.id},
    )
    return {r.code for r in rows}


async def user_scoped_to_hotel(session: AsyncSession, user: User, hotel_id: uuid.UUID) -> bool:
    hotel = await get_by_uuid(session, Hotel, hotel_id)
    if hotel is None:
        return False
    region = await session.scalar(
        select(UserRegionAssignment).where(
            UserRegionAssignment.user_id == user.id,
            UserRegionAssignment.region_id == hotel.region_id,
            UserRegionAssignment.deleted_at.is_(None),
        )
    )
    if region is not None:
        return True
    assigned = await session.scalar(
        select(UserHotelAssignment).where(
            UserHotelAssignment.user_id == user.id,
            UserHotelAssignment.hotel_id == hotel.id,
            UserHotelAssignment.deleted_at.is_(None),
        )
    )
    return assigned is not None


async def allowed_hotel_ids(session: AsyncSession, user: User) -> set[int]:
    """Hotel internal `id` yang boleh diakses user (tenant isolation).

    Nilai BIGINT internal — cocok untuk `Model.hotel_id.in_(scope)` di lapis
    service. Bila endpoint perlu mem-*expose* uuid, resolve via `Hotel.uuid`.
    """
    hotels = set((await session.execute(select(Hotel.id))).scalars().all())
    direct = set(
        (
            await session.execute(
                select(UserHotelAssignment.hotel_id).where(
                    UserHotelAssignment.user_id == user.id,
                    UserHotelAssignment.deleted_at.is_(None),
                )
            )
        ).scalars().all()
    )
    region_rows = await session.execute(
        select(UserRegionAssignment.region_id).where(
            UserRegionAssignment.user_id == user.id,
            UserRegionAssignment.deleted_at.is_(None),
        )
    )
    region_ids = {r.region_id for r in region_rows}
    via_region: set[int] = set()
    if region_ids:
        via_region = set(
            (
                await session.execute(
                    select(Hotel.id).where(Hotel.region_id.in_(region_ids), Hotel.id.in_(hotels))
                )
            ).scalars().all()
        )
    return direct | via_region


def forbid(detail: str) -> HTTPException:
    return HTTPException(403, detail)