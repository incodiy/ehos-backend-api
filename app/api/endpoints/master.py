"""Master data endpoints — openapi.yaml `/hotels`, `/brands`, `/regions`, `/provinces`."""

from datetime import date
import uuid
from fastapi import APIRouter, HTTPException
from geoalchemy2 import Geometry
from geoalchemy2.elements import WKTElement
from sqlalchemy import Integer, func, select, text
from sqlalchemy.orm import aliased

from app.api.deps import CurrentUser, DbSession
from app.core.identity import get_by_uuid
from app.models import Brand, Hotel, HotelDepartment, Province, Region, User
from app.schemas.common import Envelope, HybridId, Paginated, PaginationMeta
from app.schemas.master import (
    BrandOut,
    BrandTierUpdateRequest,
    DepartmentOut,
    HotelCreateRequest,
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


@router.post("/hotels", response_model=Envelope[HotelOut], status_code=201)
async def create_hotel(
    body: HotelCreateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[HotelOut]:
    await _require_master_write(current, session)

    existing = await session.scalar(
        select(Hotel).where(Hotel.code == body.code.upper(), Hotel.deleted_at.is_(None))
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Hotel dengan kode {body.code.upper()} sudah ada")

    brand = await get_by_uuid(session, Brand, body.brand_id)
    if brand is None:
        raise HTTPException(status_code=404, detail="Brand tidak ditemukan")
    region = await get_by_uuid(session, Region, body.region_id)
    if region is None:
        raise HTTPException(status_code=404, detail="Region tidak ditemukan")
    province = await get_by_uuid(session, Province, body.province_id)
    if province is None:
        raise HTTPException(status_code=404, detail="Province tidak ditemukan")

    gm = await get_by_uuid(session, User, body.gm_id) if body.gm_id else None
    rom = await get_by_uuid(session, User, body.rom_id) if body.rom_id else None

    opening_d = None
    if body.opening_date:
        try:
            opening_d = date.fromisoformat(body.opening_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="Format opening_date harus YYYY-MM-DD")

    geo_point = WKTElement(f"SRID=4326;POINT({body.geo.lng} {body.geo.lat})")

    hotel = Hotel(
        code=body.code.upper(),
        name=body.name.strip(),
        brand_id=brand.id,
        region_id=region.id,
        province_id=province.id,
        city=body.city.strip(),
        geo=geo_point,
        geofence_radius_meters=body.geofence_radius_meters,
        mice_facilities=body.mice_facilities,
        gm_id=gm.id if gm else None,
        rom_id=rom.id if rom else None,
        opening_date=opening_d,
        status=body.status,
    )
    session.add(hotel)
    await session.flush()

    standard_depts = [
        ("FO", "Front Office"),
        ("HK", "Housekeeping"),
        ("KFB", "Kitchen & FB"),
        ("SEC", "Security"),
        ("ENG", "Engineering"),
        ("SALES", "Sales & Marketing"),
    ]
    for d_code, d_name in standard_depts:
        session.add(
            HotelDepartment(
                hotel_id=hotel.id,
                code=d_code,
                name=d_name,
            )
        )

    await session.commit()
    return await get_hotel(str(hotel.uuid), current, session)


@router.get("/hotels/{code}", response_model=Envelope[HotelOut])
async def get_hotel(
    code: str,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[HotelOut]:
    stmt, _, _ = _hotel_row_columns()
    is_uuid = False
    try:
        target_uuid = uuid.UUID(code)
        is_uuid = True
    except (ValueError, AttributeError):
        pass

    if is_uuid:
        cond = (Hotel.uuid == target_uuid)
    else:
        cond = (Hotel.code == code.upper())

    row = (
        await session.execute(
            stmt.where(cond, Hotel.deleted_at.is_(None))
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

    is_uuid = False
    try:
        target_uuid = uuid.UUID(code)
        is_uuid = True
    except (ValueError, AttributeError):
        pass

    if is_uuid:
        hotel = await session.scalar(
            select(Hotel).where(Hotel.uuid == target_uuid, Hotel.deleted_at.is_(None))
        )
    else:
        hotel = await session.scalar(
            select(Hotel).where(Hotel.code == code.upper(), Hotel.deleted_at.is_(None))
        )

    if hotel is None:
        raise HTTPException(status_code=404, detail="Hotel tidak ditemukan")

    if body.code is not None and body.code.upper() != hotel.code:
        dup = await session.scalar(
            select(Hotel).where(
                Hotel.code == body.code.upper(),
                Hotel.id != hotel.id,
                Hotel.deleted_at.is_(None),
            )
        )
        if dup is not None:
            raise HTTPException(status_code=409, detail=f"Hotel dengan kode {body.code.upper()} sudah ada")
        hotel.code = body.code.upper()

    if body.name is not None:
        hotel.name = body.name.strip()
    if body.city is not None:
        hotel.city = body.city.strip()

    if body.brand_id is not None:
        brand = await get_by_uuid(session, Brand, body.brand_id)
        if brand is None:
            raise HTTPException(status_code=404, detail="Brand tidak ditemukan")
        hotel.brand_id = brand.id

    if body.region_id is not None:
        region = await get_by_uuid(session, Region, body.region_id)
        if region is None:
            raise HTTPException(status_code=404, detail="Region tidak ditemukan")
        hotel.region_id = region.id

    if body.province_id is not None:
        province = await get_by_uuid(session, Province, body.province_id)
        if province is None:
            raise HTTPException(status_code=404, detail="Province tidak ditemukan")
        hotel.province_id = province.id

    if body.gm_id is not None:
        gm = await get_by_uuid(session, User, body.gm_id)
        hotel.gm_id = gm.id if gm else None

    if body.rom_id is not None:
        rom = await get_by_uuid(session, User, body.rom_id)
        hotel.rom_id = rom.id if rom else None

    if body.geo is not None:
        hotel.geo = WKTElement(f"SRID=4326;POINT({body.geo.lng} {body.geo.lat})")

    if body.geofence_radius_meters is not None:
        hotel.geofence_radius_meters = body.geofence_radius_meters

    if body.mice_facilities is not None:
        hotel.mice_facilities = body.mice_facilities

    if body.opening_date is not None:
        try:
            hotel.opening_date = date.fromisoformat(body.opening_date) if body.opening_date else None
        except ValueError:
            raise HTTPException(status_code=422, detail="Format opening_date harus YYYY-MM-DD")

    if body.terminate_date is not None:
        try:
            hotel.terminate_date = date.fromisoformat(body.terminate_date) if body.terminate_date else None
        except ValueError:
            raise HTTPException(status_code=422, detail="Format terminate_date harus YYYY-MM-DD")

    if body.status is not None:
        hotel.status = body.status
        if body.status == "TERMINATED" and not hotel.terminate_date:
            hotel.terminate_date = date.today()

    await session.commit()
    return await get_hotel(str(hotel.uuid), current, session)


@router.delete("/hotels/{code}", response_model=Envelope[dict])
async def delete_hotel(
    code: str,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[dict]:
    await _require_master_write(current, session)

    is_uuid = False
    try:
        target_uuid = uuid.UUID(code)
        is_uuid = True
    except (ValueError, AttributeError):
        pass

    if is_uuid:
        hotel = await session.scalar(
            select(Hotel).where(Hotel.uuid == target_uuid, Hotel.deleted_at.is_(None))
        )
    else:
        hotel = await session.scalar(
            select(Hotel).where(Hotel.code == code.upper(), Hotel.deleted_at.is_(None))
        )

    if hotel is None:
        raise HTTPException(status_code=404, detail="Hotel tidak ditemukan")

    hotel.deleted_at = func.now()
    await session.commit()
    return Envelope(data={"id": str(hotel.uuid), "code": hotel.code, "deleted": True})


@router.get("/hotels/{code}/departments", response_model=Envelope[list[DepartmentOut]])
async def list_hotel_departments(
    code: str,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[list[DepartmentOut]]:
    rows = (
        await session.execute(
            text(
                "SELECT d.uuid AS uuid, h.uuid AS hotel_uuid, d.code, d.name, u.uuid AS hod_user_uuid "
                "FROM hotel_departments d JOIN hotels h ON h.id = d.hotel_id "
                "LEFT JOIN users u ON u.id = d.hod_user_id "
                "WHERE h.code = :code AND d.deleted_at IS NULL "
                "ORDER BY d.code"
            ).bindparams(code=code.upper())
        )
    ).mappings().all()
    return Envelope(
        data=[
            DepartmentOut(
                id=r["uuid"],
                hotel_id=r["hotel_uuid"],
                code=r["code"],
                name=r["name"],
                hod_user_id=r["hod_user_uuid"],
            )
            for r in rows
        ]
    )




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