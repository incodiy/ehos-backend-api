"""Region domain service — CRUD, business validation, chained FK integrity, and FSM status."""

from __future__ import annotations

import uuid
from typing import Sequence

from fastapi import HTTPException
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.master import Hotel, Region
from app.models.users import User, UserRegionAssignment
from app.schemas.common import PaginationMeta
from app.schemas.region import (
    RegionCreateRequest,
    RegionDetailOut,
    RegionOut,
    RegionUpdateRequest,
)


async def list_regions(
    session: AsyncSession,
    search: str | None = None,
    status: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[RegionOut], PaginationMeta]:
    stmt = select(Region).where(Region.deleted_at.is_(None))

    if status:
        stmt = stmt.where(Region.status == status.upper())

    if search:
        term = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Region.code.ilike(term),
                Region.name.ilike(term),
                Region.country.ilike(term),
                Region.sales_region.ilike(term),
            )
        )

    count_stmt = stmt.with_only_columns(func.count()).order_by(None)
    total = (await session.execute(count_stmt)).scalar() or 0

    per_page = max(1, min(per_page, 200))
    offset = (page - 1) * per_page
    rows = (
        await session.scalars(
            stmt.order_by(Region.name).offset(offset).limit(per_page)
        )
    ).all()

    last_page = max(1, (total + per_page - 1) // per_page)
    meta = PaginationMeta(current_page=page, per_page=per_page, total=total, last_page=last_page)
    return [RegionOut.model_validate(r) for r in rows], meta


async def get_region_by_id_or_code(
    session: AsyncSession,
    identifier: str,
) -> RegionDetailOut:
    is_uuid = False
    try:
        target_uuid = uuid.UUID(identifier)
        is_uuid = True
    except (ValueError, AttributeError):
        pass

    if is_uuid:
        region = await session.scalar(
            select(Region).where(Region.uuid == target_uuid, Region.deleted_at.is_(None))
        )
    else:
        region = await session.scalar(
            select(Region).where(Region.code == identifier.upper(), Region.deleted_at.is_(None))
        )

    if region is None:
        raise HTTPException(status_code=404, detail="Wilayah tidak ditemukan")

    # Ambil statistik properti hotel aktif yang terikat pada region ini
    hotels_count = (
        await session.scalar(
            select(func.count(Hotel.id)).where(
                Hotel.region_id == region.id,
                Hotel.deleted_at.is_(None),
            )
        )
    ) or 0

    # Ambil nama ROM yang ditugaskan ke region ini
    rom_users = (
        await session.scalars(
            select(User.name)
            .join(UserRegionAssignment, UserRegionAssignment.user_id == User.id)
            .where(
                UserRegionAssignment.region_id == region.id,
                UserRegionAssignment.deleted_at.is_(None),
                User.deleted_at.is_(None),
            )
        )
    ).all()

    return RegionDetailOut(
        id=region.uuid,
        code=region.code,
        name=region.name,
        country=region.country,
        sales_region=region.sales_region,
        status=region.status,
        created_at=region.created_at,
        updated_at=region.updated_at,
        hotels_count=hotels_count,
        rom_names=list(rom_users),
    )


async def create_region(
    session: AsyncSession,
    data: RegionCreateRequest,
    current_user_id: int | None = None,
) -> RegionOut:
    code_upper = data.code.strip().upper()
    dup = await session.scalar(
        select(Region).where(
            Region.code == code_upper,
            Region.deleted_at.is_(None),
        )
    )
    if dup is not None:
        raise HTTPException(status_code=409, detail=f"Wilayah dengan kode {code_upper} sudah ada")

    region = Region(
        code=code_upper,
        name=data.name.strip(),
        country=data.country.strip() if data.country else "Indonesia",
        sales_region=data.sales_region.strip() if data.sales_region else None,
        status=data.status.value,
    )
    session.add(region)
    await session.commit()
    await session.refresh(region)
    return RegionOut.model_validate(region)


async def update_region(
    session: AsyncSession,
    identifier: str,
    data: RegionUpdateRequest,
    current_user_id: int | None = None,
) -> RegionOut:
    is_uuid = False
    try:
        target_uuid = uuid.UUID(identifier)
        is_uuid = True
    except (ValueError, AttributeError):
        pass

    if is_uuid:
        region = await session.scalar(
            select(Region).where(Region.uuid == target_uuid, Region.deleted_at.is_(None))
        )
    else:
        region = await session.scalar(
            select(Region).where(Region.code == identifier.upper(), Region.deleted_at.is_(None))
        )

    if region is None:
        raise HTTPException(status_code=404, detail="Wilayah tidak ditemukan")

    if data.code is not None:
        code_upper = data.code.strip().upper()
        if code_upper != region.code:
            dup = await session.scalar(
                select(Region).where(
                    Region.code == code_upper,
                    Region.id != region.id,
                    Region.deleted_at.is_(None),
                )
            )
            if dup is not None:
                raise HTTPException(status_code=409, detail=f"Wilayah dengan kode {code_upper} sudah ada")
            region.code = code_upper

    if data.name is not None:
        region.name = data.name.strip()
    if data.country is not None:
        region.country = data.country.strip()
    if data.sales_region is not None:
        region.sales_region = data.sales_region.strip()
    if data.status is not None:
        region.status = data.status.value

    await session.commit()
    await session.refresh(region)
    return RegionOut.model_validate(region)


async def delete_region(
    session: AsyncSession,
    identifier: str,
    current_user_id: int | None = None,
) -> dict[str, object]:
    is_uuid = False
    try:
        target_uuid = uuid.UUID(identifier)
        is_uuid = True
    except (ValueError, AttributeError):
        pass

    if is_uuid:
        region = await session.scalar(
            select(Region).where(Region.uuid == target_uuid, Region.deleted_at.is_(None))
        )
    else:
        region = await session.scalar(
            select(Region).where(Region.code == identifier.upper(), Region.deleted_at.is_(None))
        )

    if region is None:
        raise HTTPException(status_code=404, detail="Wilayah tidak ditemukan")

    # RELATIONAL INTEGRITY GUARD (Chained FK Guard):
    # Dilarang menghapus wilayah jika masih terdapat hotel aktif yang menggunakannya
    active_hotels = (
        await session.scalar(
            select(func.count(Hotel.id)).where(
                Hotel.region_id == region.id,
                Hotel.deleted_at.is_(None),
            )
        )
    ) or 0

    if active_hotels > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Wilayah '{region.name}' ({region.code}) tidak dapat dihapus karena masih memiliki {active_hotels} hotel aktif terdaftar.",
        )

    # Soft delete penugasan ROM pada region ini
    await session.execute(
        text(
            "UPDATE user_region_assignments SET deleted_at = NOW() "
            "WHERE region_id = :rid AND deleted_at IS NULL"
        ).bindparams(rid=region.id)
    )

    region.deleted_at = func.now()
    await session.commit()
    return {"id": str(region.uuid), "code": region.code, "deleted": True}
