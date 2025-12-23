"""Master data endpoints — openapi.yaml `/hotels`, `/brands`, `/regions`, `/provinces`, `/cities`."""

from datetime import date, datetime, timezone
import uuid
from fastapi import APIRouter, HTTPException
from geoalchemy2 import Geometry
from geoalchemy2.elements import WKTElement
from sqlalchemy import Integer, func, or_, select, text
from sqlalchemy.orm import aliased

from app.api.deps import CurrentUser, DbSession
from app.core.identity import get_by_uuid
from app.models import Brand, City, Hotel, HotelContact, HotelDepartment, Province, Region, User
from app.schemas.common import Envelope, HybridId, Paginated, PaginationMeta
from app.schemas.master import (
    BrandOut,
    BrandTierUpdateRequest,
    CityCreateRequest,
    CityOut,
    CityUpdateRequest,
    DepartmentOut,
    HotelContactCreateRequest,
    HotelContactGlobalCreateRequest,
    HotelContactOut,
    HotelContactUpdateRequest,
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
            Hotel.id.label("internal_id"),
            Hotel.uuid.label("uuid"),
            Hotel.code.label("code"),
            Hotel.name.label("name"),
            Brand.uuid.label("brand_id"),
            Brand.name.label("brand"),
            Brand.tier.label("brand_tier"),
            Region.uuid.label("region_id"),
            Region.name.label("region"),
            Region.sales_region.label("sales_region"),
            Region.ecommerce_region.label("ecommerce_region"),
            Province.uuid.label("province_id"),
            City.uuid.label("city_id"),
            Hotel.city.label("city"),
            City.ecommerce_city.label("ecommerce_city"),
            func.ST_Y(func.cast(Hotel.geo, Geometry)).label("lat"),
            func.ST_X(func.cast(Hotel.geo, Geometry)).label("lng"),
            Hotel.geofence_radius_meters.label("geofence_radius_meters"),
            Hotel.mice_facilities.label("mice_facilities"),
            Hotel.status.label("status"),
            Hotel.image_url.label("image_url"),
            Hotel.has_fb.label("has_fb"),
            Hotel.opening_date.label("opening_date"),
            Hotel.terminate_date.label("terminate_date"),
            Hotel.period_update.label("period_update"),
            gm.name.label("gm_name"),
            rom.name.label("rom_name"),
        )
        .outerjoin(Brand, Brand.id == Hotel.brand_id)
        .outerjoin(Region, Region.id == Hotel.region_id)
        .outerjoin(Province, Province.id == Hotel.province_id)
        .outerjoin(City, City.id == Hotel.city_id)
        .outerjoin(gm, gm.id == Hotel.gm_id)
        .outerjoin(rom, rom.id == Hotel.rom_id),
        gm,
        rom,
    )


async def _load_contacts_map(session: DbSession, hotel_ids: list[int]) -> dict[int, list[HotelContactOut]]:
    if not hotel_ids:
        return {}
    contacts_stmt = (
        select(HotelContact)
        .where(HotelContact.hotel_id.in_(hotel_ids), HotelContact.deleted_at.is_(None))
        .order_by(HotelContact.is_primary.desc(), HotelContact.contact_type)
    )
    contacts = (await session.scalars(contacts_stmt)).all()
    res: dict[int, list[HotelContactOut]] = {hid: [] for hid in hotel_ids}
    for c in contacts:
        res[c.hotel_id].append(
            HotelContactOut(
                id=c.uuid,
                contact_type=c.contact_type,
                name=c.name,
                email=c.email,
                phone=c.phone,
                user_id=None,
                is_primary=c.is_primary,
            )
        )
    return res


def _to_hotel_out(row: dict, contacts: list[HotelContactOut] | None = None) -> HotelOut:
    data = {k: v for k, v in row.items() if k not in ("lat", "lng", "internal_id")}
    if isinstance(data.get("opening_date"), (date, datetime)):
        data["opening_date"] = data["opening_date"].isoformat()
    if isinstance(data.get("terminate_date"), (date, datetime)):
        data["terminate_date"] = data["terminate_date"].isoformat()
    if isinstance(data.get("period_update"), datetime):
        data["period_update"] = data["period_update"].isoformat()

    lat = float(row["lat"]) if row.get("lat") is not None else 0.0
    lng = float(row["lng"]) if row.get("lng") is not None else 0.0

    return HotelOut(
        **data,
        geo={"lat": lat, "lng": lng},
        contacts=contacts or [],
    )


# ============================================================================
# HOTELS ENDPOINTS
# ============================================================================

@router.get("/hotels", response_model=Paginated[HotelOut, HotelOut])
async def list_hotels(
    current: CurrentUser,
    session: DbSession,
    search: str | None = None,
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
    if search:
        s_term = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Hotel.name.ilike(s_term),
                Hotel.code.ilike(s_term),
                Hotel.city.ilike(s_term),
                City.name.ilike(s_term),
                City.ecommerce_city.ilike(s_term),
            )
        )
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

    hotel_ids = [r["internal_id"] for r in rows if r.get("internal_id")]
    contacts_map = await _load_contacts_map(session, hotel_ids)

    return Paginated[HotelOut, HotelOut](
        data=[_to_hotel_out(dict(r), contacts_map.get(r["internal_id"], [])) for r in rows],
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

    city_obj = None
    if body.city_id:
        city_obj = await get_by_uuid(session, City, body.city_id)

    gm = await get_by_uuid(session, User, body.gm_id) if body.gm_id else None
    rom = await get_by_uuid(session, User, body.rom_id) if body.rom_id else None

    opening_d = None
    if body.opening_date:
        try:
            opening_d = date.fromisoformat(body.opening_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="Format opening_date harus YYYY-MM-DD")

    terminate_d = None
    if body.terminate_date:
        try:
            terminate_d = date.fromisoformat(body.terminate_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="Format terminate_date harus YYYY-MM-DD")

    geo_point = WKTElement(f"SRID=4326;POINT({body.geo.lng} {body.geo.lat})")

    hotel = Hotel(
        code=body.code.upper(),
        name=body.name.strip(),
        brand_id=brand.id,
        region_id=region.id,
        province_id=province.id,
        city_id=city_obj.id if city_obj else None,
        city=body.city.strip(),
        geo=geo_point,
        geofence_radius_meters=body.geofence_radius_meters,
        mice_facilities=body.mice_facilities,
        gm_id=gm.id if gm else None,
        rom_id=rom.id if rom else None,
        opening_date=opening_d,
        terminate_date=terminate_d,
        status=body.status,
        image_url=body.image_url,
        has_fb=body.has_fb,
        period_update=datetime.now(timezone.utc),
    )
    session.add(hotel)
    await session.flush()

    # Tambahkan kontak jika ada
    if body.contacts:
        for c_in in body.contacts:
            u_rel = await get_by_uuid(session, User, c_in.user_id) if c_in.user_id else None
            session.add(
                HotelContact(
                    hotel_id=hotel.id,
                    contact_type=c_in.contact_type,
                    name=c_in.name.strip(),
                    email=c_in.email.strip() if c_in.email else None,
                    phone=c_in.phone.strip() if c_in.phone else None,
                    user_id=u_rel.id if u_rel else None,
                    is_primary=c_in.is_primary,
                )
            )

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

    contacts_map = await _load_contacts_map(session, [row["internal_id"]])
    return Envelope(data=_to_hotel_out(dict(row), contacts_map.get(row["internal_id"], [])))


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

    if body.city_id is not None:
        city_obj = await get_by_uuid(session, City, body.city_id)
        hotel.city_id = city_obj.id if city_obj else None

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

    if body.image_url is not None:
        hotel.image_url = body.image_url.strip() if body.image_url else None

    if body.has_fb is not None:
        hotel.has_fb = body.has_fb

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

    hotel.period_update = datetime.now(timezone.utc)

    # Sinkronisasi kontak jika disertakan di update payload
    if body.contacts is not None:
        # Hapus kontak lama yang soft-deleted
        existing_contacts = (
            await session.scalars(
                select(HotelContact).where(HotelContact.hotel_id == hotel.id, HotelContact.deleted_at.is_(None))
            )
        ).all()
        for ec in existing_contacts:
            ec.deleted_at = func.now()

        for c_in in body.contacts:
            u_rel = await get_by_uuid(session, User, c_in.user_id) if c_in.user_id else None
            session.add(
                HotelContact(
                    hotel_id=hotel.id,
                    contact_type=c_in.contact_type,
                    name=c_in.name.strip(),
                    email=c_in.email.strip() if c_in.email else None,
                    phone=c_in.phone.strip() if c_in.phone else None,
                    user_id=u_rel.id if u_rel else None,
                    is_primary=c_in.is_primary,
                )
            )

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


# ============================================================================
# HOTEL CONTACTS (CRUD DEDICATED)
# ============================================================================

@router.get("/hotels/{code}/contacts", response_model=Envelope[list[HotelContactOut]])
async def list_hotel_contacts(
    code: str,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[list[HotelContactOut]]:
    hotel = await session.scalar(
        select(Hotel).where(Hotel.code == code.upper(), Hotel.deleted_at.is_(None))
    )
    if hotel is None:
        raise HTTPException(status_code=404, detail="Hotel tidak ditemukan")

    contacts = (
        await session.scalars(
            select(HotelContact)
            .where(HotelContact.hotel_id == hotel.id, HotelContact.deleted_at.is_(None))
            .order_by(HotelContact.is_primary.desc(), HotelContact.contact_type)
        )
    ).all()
    return Envelope(
        data=[
            HotelContactOut(
                id=c.uuid,
                contact_type=c.contact_type,
                name=c.name,
                email=c.email,
                phone=c.phone,
                user_id=None,
                is_primary=c.is_primary,
            )
            for c in contacts
        ]
    )


@router.post("/hotels/{code}/contacts", response_model=Envelope[HotelContactOut], status_code=201)
async def create_hotel_contact(
    code: str,
    body: HotelContactCreateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[HotelContactOut]:
    await _require_master_write(current, session)
    hotel = await session.scalar(
        select(Hotel).where(Hotel.code == code.upper(), Hotel.deleted_at.is_(None))
    )
    if hotel is None:
        raise HTTPException(status_code=404, detail="Hotel tidak ditemukan")

    u_rel = await get_by_uuid(session, User, body.user_id) if body.user_id else None

    # Jika is_primary true, set existing contact type ini ke non-primary
    if body.is_primary:
        await session.execute(
            text("UPDATE hotel_contacts SET is_primary = FALSE WHERE hotel_id = :hid AND contact_type = :ctype")
            .bindparams(hid=hotel.id, ctype=body.contact_type)
        )

    contact = HotelContact(
        hotel_id=hotel.id,
        contact_type=body.contact_type,
        name=body.name.strip(),
        email=body.email.strip() if body.email else None,
        phone=body.phone.strip() if body.phone else None,
        user_id=u_rel.id if u_rel else None,
        is_primary=body.is_primary,
    )
    session.add(contact)
    hotel.period_update = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(contact)

    return Envelope(
        data=HotelContactOut(
            id=contact.uuid,
            contact_type=contact.contact_type,
            name=contact.name,
            email=contact.email,
            phone=contact.phone,
            user_id=None,
            is_primary=contact.is_primary,
        )
    )


@router.patch("/hotels/{code}/contacts/{contact_id}", response_model=Envelope[HotelContactOut])
async def update_hotel_contact(
    code: str,
    contact_id: uuid.UUID,
    body: HotelContactUpdateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[HotelContactOut]:
    await _require_master_write(current, session)
    contact = await get_by_uuid(session, HotelContact, contact_id)
    if contact is None or contact.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Kontak tidak ditemukan")

    if body.contact_type is not None:
        contact.contact_type = body.contact_type
    if body.name is not None:
        contact.name = body.name.strip()
    if body.email is not None:
        contact.email = body.email.strip() if body.email else None
    if body.phone is not None:
        contact.phone = body.phone.strip() if body.phone else None
    if body.user_id is not None:
        u_rel = await get_by_uuid(session, User, body.user_id) if body.user_id else None
        contact.user_id = u_rel.id if u_rel else None
    if body.is_primary is not None:
        contact.is_primary = body.is_primary

    await session.commit()
    await session.refresh(contact)
    return Envelope(
        data=HotelContactOut(
            id=contact.uuid,
            contact_type=contact.contact_type,
            name=contact.name,
            email=contact.email,
            phone=contact.phone,
            user_id=None,
            is_primary=contact.is_primary,
        )
    )


@router.delete("/hotels/{code}/contacts/{contact_id}", response_model=Envelope[dict])
async def delete_hotel_contact(
    code: str,
    contact_id: uuid.UUID,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[dict]:
    await _require_master_write(current, session)
    contact = await get_by_uuid(session, HotelContact, contact_id)
    if contact is None or contact.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Kontak tidak ditemukan")

    contact.deleted_at = func.now()
    await session.commit()
    return Envelope(data={"id": str(contact.uuid), "deleted": True})


# ============================================================================
# GLOBAL HOTEL CONTACTS ENDPOINTS (CRUD MASTER KONTAK PIC HOTEL)
# ============================================================================

@router.get("/hotel-contacts", response_model=Envelope[list[HotelContactOut]])
async def list_global_hotel_contacts(
    current: CurrentUser,
    session: DbSession,
    contact_type: str | None = None,
    hotel_code: str | None = None,
    hotel_id: uuid.UUID | None = None,
    search: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> Envelope[list[HotelContactOut]]:
    stmt = (
        select(HotelContact, Hotel)
        .join(Hotel, HotelContact.hotel_id == Hotel.id)
        .where(HotelContact.deleted_at.is_(None), Hotel.deleted_at.is_(None))
        .order_by(Hotel.name, HotelContact.is_primary.desc(), HotelContact.contact_type)
    )
    if contact_type:
        stmt = stmt.where(HotelContact.contact_type == contact_type.upper())
    if hotel_code:
        stmt = stmt.where(Hotel.code == hotel_code.upper())
    if hotel_id:
        hotel = await get_by_uuid(session, Hotel, hotel_id)
        if hotel:
            stmt = stmt.where(HotelContact.hotel_id == hotel.id)
    if search:
        s = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                HotelContact.name.ilike(s),
                HotelContact.email.ilike(s),
                HotelContact.phone.ilike(s),
                Hotel.name.ilike(s),
                Hotel.code.ilike(s),
            )
        )
    if limit > 0:
        stmt = stmt.limit(limit).offset(offset)

    rows = (await session.execute(stmt)).all()
    return Envelope(
        data=[
            HotelContactOut(
                id=c.uuid,
                contact_type=c.contact_type,
                name=c.name,
                email=c.email,
                phone=c.phone,
                user_id=None,
                hotel_id=h.uuid,
                hotel_code=h.code,
                hotel_name=h.name,
                is_primary=c.is_primary,
            )
            for c, h in rows
        ]
    )


@router.post("/hotel-contacts", response_model=Envelope[HotelContactOut], status_code=201)
async def create_global_hotel_contact(
    body: HotelContactGlobalCreateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[HotelContactOut]:
    await _require_master_write(current, session)
    hotel = await get_by_uuid(session, Hotel, body.hotel_id)
    if hotel is None or hotel.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Hotel tidak ditemukan")

    u_rel = await get_by_uuid(session, User, body.user_id) if body.user_id else None

    if body.is_primary:
        await session.execute(
            text("UPDATE hotel_contacts SET is_primary = FALSE WHERE hotel_id = :hid AND contact_type = :ctype")
            .bindparams(hid=hotel.id, ctype=body.contact_type)
        )

    contact = HotelContact(
        hotel_id=hotel.id,
        contact_type=body.contact_type,
        name=body.name.strip(),
        email=body.email.strip() if body.email else None,
        phone=body.phone.strip() if body.phone else None,
        user_id=u_rel.id if u_rel else None,
        is_primary=body.is_primary,
    )
    session.add(contact)
    hotel.period_update = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(contact)

    return Envelope(
        data=HotelContactOut(
            id=contact.uuid,
            contact_type=contact.contact_type,
            name=contact.name,
            email=contact.email,
            phone=contact.phone,
            user_id=None,
            hotel_id=hotel.uuid,
            hotel_code=hotel.code,
            hotel_name=hotel.name,
            is_primary=contact.is_primary,
        )
    )


@router.get("/hotel-contacts/{id}", response_model=Envelope[HotelContactOut])
async def get_global_hotel_contact(
    id: uuid.UUID,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[HotelContactOut]:
    row = (
        await session.execute(
            select(HotelContact, Hotel)
            .join(Hotel, HotelContact.hotel_id == Hotel.id)
            .where(HotelContact.uuid == id, HotelContact.deleted_at.is_(None))
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Kontak tidak ditemukan")
    c, h = row
    return Envelope(
        data=HotelContactOut(
            id=c.uuid,
            contact_type=c.contact_type,
            name=c.name,
            email=c.email,
            phone=c.phone,
            user_id=None,
            hotel_id=h.uuid,
            hotel_code=h.code,
            hotel_name=h.name,
            is_primary=c.is_primary,
        )
    )


@router.patch("/hotel-contacts/{id}", response_model=Envelope[HotelContactOut])
async def update_global_hotel_contact(
    id: uuid.UUID,
    body: HotelContactUpdateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[HotelContactOut]:
    await _require_master_write(current, session)
    row = (
        await session.execute(
            select(HotelContact, Hotel)
            .join(Hotel, HotelContact.hotel_id == Hotel.id)
            .where(HotelContact.uuid == id, HotelContact.deleted_at.is_(None))
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Kontak tidak ditemukan")
    contact, hotel = row

    if body.hotel_id is not None:
        new_hotel = await get_by_uuid(session, Hotel, body.hotel_id)
        if new_hotel:
            contact.hotel_id = new_hotel.id
            hotel = new_hotel

    if body.contact_type is not None:
        contact.contact_type = body.contact_type
    if body.name is not None:
        contact.name = body.name.strip()
    if body.email is not None:
        contact.email = body.email.strip() if body.email else None
    if body.phone is not None:
        contact.phone = body.phone.strip() if body.phone else None
    if body.user_id is not None:
        u_rel = await get_by_uuid(session, User, body.user_id) if body.user_id else None
        contact.user_id = u_rel.id if u_rel else None
    if body.is_primary is not None:
        contact.is_primary = body.is_primary

    await session.commit()
    await session.refresh(contact)

    return Envelope(
        data=HotelContactOut(
            id=contact.uuid,
            contact_type=contact.contact_type,
            name=contact.name,
            email=contact.email,
            phone=contact.phone,
            user_id=None,
            hotel_id=hotel.uuid,
            hotel_code=hotel.code,
            hotel_name=hotel.name,
            is_primary=contact.is_primary,
        )
    )


@router.delete("/hotel-contacts/{id}", response_model=Envelope[dict])
async def delete_global_hotel_contact(
    id: uuid.UUID,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[dict]:
    await _require_master_write(current, session)
    contact = await get_by_uuid(session, HotelContact, id)
    if contact is None or contact.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Kontak tidak ditemukan")

    contact.deleted_at = func.now()
    await session.commit()
    return Envelope(data={"id": str(contact.uuid), "deleted": True})


# ============================================================================
# CITIES ENDPOINTS (CRUD MASTER KOTA)
# ============================================================================

@router.get("/cities", response_model=Envelope[list[CityOut]])
async def list_cities(
    current: CurrentUser,
    session: DbSession,
    province_id: HybridId | None = None,
    region_id: HybridId | None = None,
    search: str | None = None,
) -> Envelope[list[CityOut]]:
    stmt = (
        select(City)
        .where(City.deleted_at.is_(None))
        .order_by(City.name)
    )
    if province_id:
        prov = await get_by_uuid(session, Province, province_id)
        if prov:
            stmt = stmt.where(City.province_id == prov.id)
    if region_id:
        reg = await get_by_uuid(session, Region, region_id)
        if reg:
            stmt = stmt.where(City.region_id == reg.id)
    if search:
        stmt = stmt.where(City.name.ilike(f"%{search}%"))

    cities = (await session.scalars(stmt)).all()
    return Envelope(
        data=[
            CityOut(
                id=c.uuid,
                name=c.name,
                province_id=c.province.uuid,
                province=c.province.name,
                region_id=c.region.uuid,
                region=c.region.name,
                ecommerce_city=c.ecommerce_city,
            )
            for c in cities
        ]
    )


@router.post("/cities", response_model=Envelope[CityOut], status_code=201)
async def create_city(
    body: CityCreateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[CityOut]:
    await _require_master_write(current, session)
    prov = await get_by_uuid(session, Province, body.province_id)
    if prov is None:
        raise HTTPException(status_code=404, detail="Provinsi tidak ditemukan")
    reg = await get_by_uuid(session, Region, body.region_id)
    if reg is None:
        raise HTTPException(status_code=404, detail="Region tidak ditemukan")

    existing = await session.scalar(
        select(City).where(
            City.name == body.name.strip(),
            City.province_id == prov.id,
            City.deleted_at.is_(None),
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Kota {body.name} di provinsi tersebut sudah ada")

    city = City(
        name=body.name.strip(),
        province_id=prov.id,
        region_id=reg.id,
        ecommerce_city=body.ecommerce_city.strip() if body.ecommerce_city else None,
    )
    session.add(city)
    await session.commit()
    await session.refresh(city)

    return Envelope(
        data=CityOut(
            id=city.uuid,
            name=city.name,
            province_id=prov.uuid,
            province=prov.name,
            region_id=reg.uuid,
            region=reg.name,
            ecommerce_city=city.ecommerce_city,
        )
    )


@router.get("/cities/{city_id}", response_model=Envelope[CityOut])
async def get_city(
    city_id: uuid.UUID,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[CityOut]:
    city = await get_by_uuid(session, City, city_id)
    if city is None or city.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Kota tidak ditemukan")

    return Envelope(
        data=CityOut(
            id=city.uuid,
            name=city.name,
            province_id=city.province.uuid,
            province=city.province.name,
            region_id=city.region.uuid,
            region=city.region.name,
            ecommerce_city=city.ecommerce_city,
        )
    )


@router.patch("/cities/{city_id}", response_model=Envelope[CityOut])
async def update_city(
    city_id: uuid.UUID,
    body: CityUpdateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[CityOut]:
    await _require_master_write(current, session)
    city = await get_by_uuid(session, City, city_id)
    if city is None or city.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Kota tidak ditemukan")

    if body.name is not None:
        city.name = body.name.strip()
    if body.province_id is not None:
        prov = await get_by_uuid(session, Province, body.province_id)
        if prov is None:
            raise HTTPException(status_code=404, detail="Provinsi tidak ditemukan")
        city.province_id = prov.id
    if body.region_id is not None:
        reg = await get_by_uuid(session, Region, body.region_id)
        if reg is None:
            raise HTTPException(status_code=404, detail="Region tidak ditemukan")
        city.region_id = reg.id
    if body.ecommerce_city is not None:
        city.ecommerce_city = body.ecommerce_city.strip() if body.ecommerce_city else None

    await session.commit()
    await session.refresh(city)

    return Envelope(
        data=CityOut(
            id=city.uuid,
            name=city.name,
            province_id=city.province.uuid,
            province=city.province.name,
            region_id=city.region.uuid,
            region=city.region.name,
            ecommerce_city=city.ecommerce_city,
        )
    )


@router.delete("/cities/{city_id}", response_model=Envelope[dict])
async def delete_city(
    city_id: uuid.UUID,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[dict]:
    await _require_master_write(current, session)
    city = await get_by_uuid(session, City, city_id)
    if city is None or city.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Kota tidak ditemukan")

    # Cek apakah masih terikat hotel aktif
    linked_hotels = await session.scalar(
        select(func.count(Hotel.id)).where(Hotel.city_id == city.id, Hotel.deleted_at.is_(None))
    )
    if linked_hotels and linked_hotels > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Kota tidak dapat dihapus karena masih digunakan oleh {linked_hotels} hotel aktif",
        )

    city.deleted_at = func.now()
    await session.commit()
    return Envelope(data={"id": str(city.uuid), "name": city.name, "deleted": True})


# ============================================================================
# DEPARTMENTS & PROVINCES
# ============================================================================

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