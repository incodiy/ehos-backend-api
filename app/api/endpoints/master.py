"""Master data endpoints — openapi.yaml `/hotels`, `/brands`, `/regions`, `/provinces`."""

from fastapi import APIRouter, HTTPException
from geoalchemy2 import Geometry
from sqlalchemy import Integer, func, select, text
from sqlalchemy.orm import aliased

from app.api.deps import CurrentUser, DbSession
from app.core.identity import get_by_uuid
from app.models import Brand, Hotel, Province, Region, User
from app.schemas.common import Envelope, HybridId, Paginated, PaginationMeta
from app.schemas.master import (
    BrandOut,
    DepartmentOut,
    HotelOut,
    HotelUpdateRequest,
    ProvinceOut,
    RegionOut,
)

router = APIRouter(tags=["master-data"])


async def _require_master_write(current: CurrentUser, session: DbSession) -> None:
    has_write = await session.scalar(
        text(
            "SELECT 1 FROM user_roles ur JOIN roles_permissions rp ON rp.role_id = ur.role_id "
            "JOIN permissions p ON p.id = rp.permission_id "
            "WHERE ur.user_id = :uid AND p.code = 'master:write' LIMIT 1"
        ).bindparams(uid=current.id)
    )
    if not has_write:
        raise HTTPException(status_code=403, detail="Missing permission: master:write")


def _hotel_row_columns():
    gm = aliased(User, name="gm")
    rom = aliased(User, name="rom")
    return (
        select(
            Hotel.uuid.label("uuid"),
            Hotel.code.label("code"),
            Hotel.name.label("name"),
            Brand.uuid.label("brand_id"),
            Brand.name.label("brand"),
            Brand.tier.label("brand_tier"),
            Region.uuid.label("region_id"),
            Region.name.label("region"),
            Province.uuid.label("province_id"),
            Hotel.city.label("city"),
            func.ST_Y(func.cast(Hotel.geo, Geometry)).label("lat"),
            func.ST_X(func.cast(Hotel.geo, Geometry)).label("lng"),
            Hotel.geofence_radius_meters.label("geofence_radius_meters"),
            Hotel.mice_facilities.label("mice_facilities"),
            Hotel.status.label("status"),
            gm.name.label("gm_name"),
            rom.name.label("rom_name"),
        )
        .outerjoin(Brand, Brand.id == Hotel.brand_id)
        .outerjoin(Region, Region.id == Hotel.region_id)
        .outerjoin(Province, Province.id == Hotel.province_id)
        .outerjoin(gm, gm.id == Hotel.gm_id)
        .outerjoin(rom, rom.id == Hotel.rom_id),
        gm,
        rom,
    )


def _to_hotel_out(row: dict) -> HotelOut:
    return HotelOut(
        **{k: v for k, v in row.items() if k not in ("lat", "lng")},
        geo={"lat": float(row["lat"]), "lng": float(row["lng"])},
    )


@router.get("/hotels", response_model=Paginated[HotelOut, HotelOut])
async def list_hotels(
    current: CurrentUser,
    session: DbSession,
    brand_tier: str | None = None,
    region_id: HybridId | None = None,
    city: str | None = None,
    status: str | None = None,
    has_ballroom: bool | None = None,
    page: int = 1,
    per_page: int = 50,
) -> Paginated[HotelOut, HotelOut]:
    stmt, _, _ = _hotel_row_columns()
    stmt = stmt.where(Hotel.deleted_at.is_(None))
    if brand_tier:
        stmt = stmt.where(Brand.tier == brand_tier)
    if region_id:
        region = await get_by_uuid(session, Region, region_id)
        if region is None:
            return Paginated[HotelOut, HotelOut](
                data=[],
                meta=PaginationMeta(current_page=page, per_page=per_page, total=0, last_page=1),
            )
        stmt = stmt.where(Hotel.region_id == region.id)
    if city:
        stmt = stmt.where(Hotel.city.ilike(f"%{city}%"))
    if status:
        stmt = stmt.where(Hotel.status == status)
    if has_ballroom is not None:
        capacity = func.coalesce(
            func.cast(Hotel.mice_facilities.op("->>")("ballroom_capacity").astext, Integer), 0
        )
        stmt = stmt.where(capacity > 0 if has_ballroom else capacity <= 0)

    total = (await session.execute(stmt.with_only_columns(func.count()).order_by(None))).scalar() or 0
    rows = (
        await session.execute(
            stmt.order_by(Hotel.code).offset((page - 1) * per_page).limit(min(per_page, 200))
        )
    ).mappings().all()
    return Paginated[HotelOut, HotelOut](
        data=[_to_hotel_out(dict(r)) for r in rows],
        meta=PaginationMeta(
            current_page=page, per_page=per_page,
            total=total, last_page=max(1, (total + per_page - 1) // per_page),
        ),
    )


@router.get("/hotels/{code}", response_model=Envelope[HotelOut])
async def get_hotel(
    code: str,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[HotelOut]:
    stmt, _, _ = _hotel_row_columns()
    row = (
        await session.execute(
            stmt.where(Hotel.code == code.upper(), Hotel.deleted_at.is_(None))
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Hotel tidak ditemukan")
    return Envelope(data=_to_hotel_out(dict(row)))


@router.patch("/hotels/{code}", response_model=Envelope[HotelOut])
async def update_hotel(
    code: str,
    body: HotelUpdateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[HotelOut]:
    await _require_master_write(current, session)
    hotel = await session.scalar(
        select(Hotel).where(Hotel.code == code.upper(), Hotel.deleted_at.is_(None))
    )
    if hotel is None:
        raise HTTPException(status_code=404, detail="Hotel tidak ditemukan")

    if body.name is not None:
        hotel.name = body.name.strip()
    if body.geofence_radius_meters is not None:
        hotel.geofence_radius_meters = body.geofence_radius_meters
    if body.mice_facilities is not None:
        hotel.mice_facilities = body.mice_facilities
    if body.status is not None:
        hotel.status = body.status
    hotel.updated_by = current.id
    await session.commit()
    return await get_hotel(code, current, session)


@router.get("/hotels/{code}/departments", response_model=Envelope[list[DepartmentOut]])
async def list_hotel_departments(
    code: str,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[list[DepartmentOut]]:
    rows = (
        await session.execute(
            text(
                "SELECT d.id, d.hotel_id, d.code, d.name, d.hod_user_id "
                "FROM hotel_departments d JOIN hotels h ON h.id = d.hotel_id "
                "WHERE h.code = :code AND d.deleted_at IS NULL "
                "ORDER BY d.code"
            ).bindparams(code=code.upper())
        )
    ).mappings().all()
    return Envelope(data=[DepartmentOut(**r) for r in rows])


@router.get("/brands", response_model=Envelope[list[BrandOut]])
async def list_brands(
    current: CurrentUser,
    session: DbSession,
) -> Envelope[list[BrandOut]]:
    brands = (
        await session.scalars(
            select(Brand).order_by(Brand.tier, Brand.name).where(Brand.deleted_at.is_(None))
        )
    ).all()
    return Envelope(data=[BrandOut.model_validate(b) for b in brands])


@router.get("/regions", response_model=Envelope[list[RegionOut]])
async def list_regions(
    current: CurrentUser,
    session: DbSession,
) -> Envelope[list[RegionOut]]:
    regions = (
        await session.scalars(select(Region).order_by(Region.name).where(Region.deleted_at.is_(None)))
    ).all()
    return Envelope(data=[RegionOut.model_validate(r) for r in regions])


@router.get("/provinces", response_model=Envelope[list[ProvinceOut]])
async def list_provinces(
    current: CurrentUser,
    session: DbSession,
) -> Envelope[list[ProvinceOut]]:
    provinces = (
        await session.scalars(
            select(Province).order_by(Province.name).where(Province.deleted_at.is_(None))
        )
    ).all()
    return Envelope(data=[ProvinceOut.model_validate(p) for p in provinces])