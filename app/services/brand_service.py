import math
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.master import Brand, Hotel
from app.schemas.brand import (
    BrandCreateRequest,
    BrandDetailOut,
    BrandHotelSummary,
    BrandOut,
    BrandTierUpdateRequest,
    BrandUpdateRequest,
)
from app.schemas.common import PaginationMeta


async def list_brands(
    session: AsyncSession,
    search: str | None = None,
    tier: str | None = None,
    status: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[BrandOut], PaginationMeta]:
    """Daftar brand dengan filter pencarian, tier, status, dan agregasi total hotel terhubung."""
    page = max(1, page)
    per_page = max(1, min(100, per_page))

    # Base query dengan count hotel aktif per brand
    query = (
        select(
            Brand,
            func.count(Hotel.id).filter(Hotel.deleted_at.is_(None)).label("hotels_count"),
        )
        .outerjoin(Hotel, Hotel.brand_id == Brand.id)
        .where(Brand.deleted_at.is_(None))
        .group_by(Brand.id)
    )

    if search:
        s = f"%{search.strip()}%"
        query = query.where(Brand.name.ilike(s) | Brand.code.ilike(s))

    if tier:
        query = query.where(Brand.tier == tier.strip())

    if status and status.upper() != "ALL":
        query = query.where(Brand.status == status.upper().strip())

    # Total record count
    count_subq = (
        select(Brand.id)
        .where(Brand.deleted_at.is_(None))
    )
    if search:
        s = f"%{search.strip()}%"
        count_subq = count_subq.where(Brand.name.ilike(s) | Brand.code.ilike(s))
    if tier:
        count_subq = count_subq.where(Brand.tier == tier.strip())
    if status and status.upper() != "ALL":
        count_subq = count_subq.where(Brand.status == status.upper().strip())

    total_count = await session.scalar(select(func.count()).select_from(count_subq.subquery())) or 0
    total_pages = math.ceil(total_count / per_page) if total_count > 0 else 1

    query = query.order_by(Brand.tier, Brand.name).offset((page - 1) * per_page).limit(per_page)
    rows = (await session.execute(query)).all()

    items: list[BrandOut] = []
    for brand, h_count in rows:
        out = BrandOut(
            id=brand.uuid,
            code=brand.code,
            name=brand.name,
            tier=brand.tier,
            status=brand.status,
            created_at=brand.created_at,
            updated_at=brand.updated_at,
            hotels_count=h_count or 0,
        )
        items.append(out)

    meta = PaginationMeta(
        total=total_count,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
    )
    return items, meta


async def get_brand_detail(session: AsyncSession, identifier: str) -> BrandDetailOut:
    """Ambil detail brand berdasarkan UUID atau Kode Brand unik."""
    query = select(Brand).where(Brand.deleted_at.is_(None))

    try:
        val_uuid = uuid.UUID(identifier)
        query = query.where(Brand.uuid == val_uuid)
    except ValueError:
        query = query.where(Brand.code == identifier.upper().strip())

    brand = await session.scalar(query)
    if brand is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Brand '{identifier}' tidak ditemukan",
        )

    # Ambil hotel-hotel aktif yang terafiliasi dengan brand ini
    hotels_query = (
        select(Hotel)
        .where(Hotel.brand_id == brand.id, Hotel.deleted_at.is_(None))
        .order_by(Hotel.name)
    )
    hotels = (await session.scalars(hotels_query)).all()
    hotel_summaries = [
        BrandHotelSummary(
            id=h.uuid,
            code=h.code,
            name=h.name,
            city=h.city,
            status=h.status,
        )
        for h in hotels
    ]

    return BrandDetailOut(
        id=brand.uuid,
        code=brand.code,
        name=brand.name,
        tier=brand.tier,
        status=brand.status,
        created_at=brand.created_at,
        updated_at=brand.updated_at,
        hotels_count=len(hotel_summaries),
        hotels=hotel_summaries,
    )


async def create_brand(
    session: AsyncSession,
    data: BrandCreateRequest,
    current_user_id: int | None = None,
) -> BrandOut:
    """Tambah brand baru dengan validasi kode unik dan normalisasi uppercase."""
    clean_code = data.code.strip().upper()

    existing = await session.scalar(
        select(Brand).where(Brand.code == clean_code, Brand.deleted_at.is_(None))
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Kode brand '{clean_code}' sudah terdaftar dalam sistem",
        )

    brand = Brand(
        code=clean_code,
        name=data.name.strip(),
        tier=data.tier.value if hasattr(data.tier, "value") else str(data.tier),
        status=data.status.value if hasattr(data.status, "value") else str(data.status),
    )
    session.add(brand)
    await session.commit()
    await session.refresh(brand)

    return BrandOut(
        id=brand.uuid,
        code=brand.code,
        name=brand.name,
        tier=brand.tier,
        status=brand.status,
        created_at=brand.created_at,
        updated_at=brand.updated_at,
        hotels_count=0,
    )


async def update_brand(
    session: AsyncSession,
    identifier: str,
    data: BrandUpdateRequest,
    current_user_id: int | None = None,
) -> BrandOut:
    """Update data brand (nama, tier, status, atau kode) dengan validasi FSM status."""
    query = select(Brand).where(Brand.deleted_at.is_(None))
    try:
        val_uuid = uuid.UUID(identifier)
        query = query.where(Brand.uuid == val_uuid)
    except ValueError:
        query = query.where(Brand.code == identifier.upper().strip())

    brand = await session.scalar(query)
    if brand is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Brand '{identifier}' tidak ditemukan",
        )

    if data.code is not None:
        clean_code = data.code.strip().upper()
        if clean_code != brand.code:
            existing = await session.scalar(
                select(Brand).where(Brand.code == clean_code, Brand.id != brand.id, Brand.deleted_at.is_(None))
            )
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Kode brand '{clean_code}' sudah digunakan oleh brand lain",
                )
            brand.code = clean_code

    if data.name is not None:
        brand.name = data.name.strip()

    if data.tier is not None:
        brand.tier = data.tier.value if hasattr(data.tier, "value") else str(data.tier)

    if data.status is not None:
        brand.status = data.status.value if hasattr(data.status, "value") else str(data.status)

    brand.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(brand)

    # Ambil hotel count
    h_count = await session.scalar(
        select(func.count(Hotel.id)).where(Hotel.brand_id == brand.id, Hotel.deleted_at.is_(None))
    ) or 0

    return BrandOut(
        id=brand.uuid,
        code=brand.code,
        name=brand.name,
        tier=brand.tier,
        status=brand.status,
        created_at=brand.created_at,
        updated_at=brand.updated_at,
        hotels_count=h_count,
    )


async def update_brand_tier(
    session: AsyncSession,
    code: str,
    data: BrandTierUpdateRequest,
    current_user_id: int | None = None,
) -> BrandOut:
    """Konfigurasi khusus brand_tier (PRD-F-01: checklist menyesuaikan tipe hotel)."""
    clean_code = code.strip().upper()
    brand = await session.scalar(
        select(Brand).where(Brand.code == clean_code, Brand.deleted_at.is_(None))
    )
    if brand is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Brand '{clean_code}' tidak ditemukan",
        )

    brand.tier = data.tier.value if hasattr(data.tier, "value") else str(data.tier)
    brand.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(brand)

    h_count = await session.scalar(
        select(func.count(Hotel.id)).where(Hotel.brand_id == brand.id, Hotel.deleted_at.is_(None))
    ) or 0

    return BrandOut(
        id=brand.uuid,
        code=brand.code,
        name=brand.name,
        tier=brand.tier,
        status=brand.status,
        created_at=brand.created_at,
        updated_at=brand.updated_at,
        hotels_count=h_count,
    )


async def delete_brand(
    session: AsyncSession,
    identifier: str,
    current_user_id: int | None = None,
) -> None:
    """Soft-delete brand dengan proteksi ketat integritas relasional (Constraint G1/PRD-F-01)."""
    query = select(Brand).where(Brand.deleted_at.is_(None))
    try:
        val_uuid = uuid.UUID(identifier)
        query = query.where(Brand.uuid == val_uuid)
    except ValueError:
        query = query.where(Brand.code == identifier.upper().strip())

    brand = await session.scalar(query)
    if brand is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Brand '{identifier}' tidak ditemukan",
        )

    # Validasi chained FK: periksa apakah ada hotel aktif terikat
    active_hotels_count = await session.scalar(
        select(func.count(Hotel.id)).where(
            Hotel.brand_id == brand.id,
            Hotel.deleted_at.is_(None),
        )
    ) or 0

    if active_hotels_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Tidak dapat menghapus brand '{brand.name}' ({brand.code}) karena masih terdapat "
                f"{active_hotels_count} hotel aktif yang terhubung. Pindahkan atau nonaktifkan hotel tersebut terlebih dahulu."
            ),
        )

    brand.deleted_at = datetime.now(timezone.utc)
    await session.commit()
