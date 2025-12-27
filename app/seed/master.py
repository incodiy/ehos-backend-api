"""Master-data seeder: brands, provinces, regions, cities, hotels, hotel_contacts, hotel_departments.

Deterministic & idempotent (Constraint H1-H4). Source of truth: `crm/Master Data Hotel.xlsx`.
"""

from collections import Counter
from datetime import date, datetime, timezone

import openpyxl
from geoalchemy2.elements import WKTElement
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Brand, City, Hotel, HotelContact, HotelDepartment, Province, Region
from app.seed.data import (
    BRAND_BY_SOURCE_LABEL,
    BRANDS,
    CITY_PROVINCE,
    COORDINATE_FALLBACK,
    HOTEL_DEPARTMENTS,
    HOTEL_STATUS_MAP,
    PROVINCES,
    REGION_CODE,
)

SOURCE_FILE = "Master Data Hotel.xlsx"


def _norm_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw or raw == "1000-01-01":
        return None
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return None


def _norm_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except ValueError:
        return None


async def seed_brands(session: AsyncSession) -> None:
    stmt = pg_insert(Brand).values(BRANDS)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Brand.code],
        set_={"name": stmt.excluded.name, "tier": stmt.excluded.tier, "status": stmt.excluded.status},
    )
    await session.execute(stmt)

    # Constraint H3: Skenario soft-delete untuk pengujian isolasi query
    soft_deleted_code = "TEST_LEGACY_BRAND"
    existing_soft = await session.scalar(select(Brand).where(Brand.code == soft_deleted_code))
    if not existing_soft:
        brand_sd = Brand(
            code=soft_deleted_code,
            name="Brand Prototype Historical Archive",
            tier="Midscale",
            status="RETIRED",
        )
        brand_sd.deleted_at = datetime.now(timezone.utc)
        session.add(brand_sd)
        await session.flush()


async def seed_provinces(session: AsyncSession) -> None:
    stmt = pg_insert(Province).values(PROVINCES)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Province.code],
        set_={"name": stmt.excluded.name},
    )
    await session.execute(stmt)


async def _load_hotel_rows(data_dir: str) -> tuple[list[dict], list[dict], list[dict]]:
    path = f"{data_dir}/{SOURCE_FILE}"
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb["md_hotel"]
    header = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
    col = {name: i for i, name in enumerate(header)}

    region_sales: dict[str, Counter] = {}
    region_ecom: dict[str, Counter] = {}
    cities_dict: dict[tuple[str, str], str | None] = {}
    rows: list[dict] = []

    for raw in ws.iter_rows(min_row=2, values_only=True):
        code = raw[col["Hotel Code"]]
        if not code:
            continue
        brand_label = raw[col["Brand"]]
        brand_code = BRAND_BY_SOURCE_LABEL.get(brand_label)
        if brand_code is None:
            raise ValueError(f"Unknown brand label `{brand_label}` for hotel {code}")
        region_name = raw[col["Region"]]
        city = raw[col["City"]]
        if region_name not in REGION_CODE:
            raise ValueError(f"Unknown region `{region_name}` for hotel {code}")
        if city not in CITY_PROVINCE:
            raise ValueError(f"Unknown city `{city}` for hotel {code}")

        sales_reg_val = raw[col["Sales Region"]]
        ecom_reg_val = raw[col["Ecommerce Region"]] if "Ecommerce Region" in col else None
        ecom_city_val = raw[col["Ecommerce City"]] if "Ecommerce City" in col else None

        if sales_reg_val:
            region_sales.setdefault(region_name, Counter())[sales_reg_val] += 1
        if ecom_reg_val:
            region_ecom.setdefault(region_name, Counter())[ecom_reg_val] += 1

        prov_code = CITY_PROVINCE[city]
        city_key = (city, prov_code)
        if city_key not in cities_dict or (not cities_dict[city_key] and ecom_city_val):
            cities_dict[city_key] = ecom_city_val

        # Ekstrak data kontak 4 pilar
        contacts = []
        # ROM
        rom_name = raw[col["ROM"]]
        rom_email = raw[col["ROM Email"]]
        if rom_name or rom_email:
            contacts.append({
                "contact_type": "ROM",
                "name": str(rom_name or "ROM In-Charge").strip(),
                "email": str(rom_email).strip() if rom_email else None,
                "phone": None,
                "is_primary": True,
            })
        # GM
        gm_name = raw[col["GM Name"]]
        gm_email = raw[col["GM Email"]]
        gm_phone = raw[col["GM Phone"]]
        if gm_name or gm_email or gm_phone:
            contacts.append({
                "contact_type": "GM",
                "name": str(gm_name or "General Manager").strip(),
                "email": str(gm_email).strip() if gm_email else None,
                "phone": str(gm_phone).strip() if gm_phone else None,
                "is_primary": True,
            })
        # Sales
        sales_name = raw[col["Sales Name"]]
        sales_email = raw[col["Sales Email"]]
        sales_phone = raw[col["Sales Phone"]]
        if sales_name or sales_email or sales_phone:
            contacts.append({
                "contact_type": "SALES",
                "name": str(sales_name or "Sales In-Charge").strip(),
                "email": str(sales_email).strip() if sales_email else None,
                "phone": str(sales_phone).strip() if sales_phone else None,
                "is_primary": True,
            })
        # Finance
        fin_name = raw[col["Finance Name"]]
        fin_email = raw[col["Finance Email"]]
        fin_phone = raw[col["Finance Phone"]]
        if fin_name or fin_email or fin_phone:
            contacts.append({
                "contact_type": "FINANCE",
                "name": str(fin_name or "Finance In-Charge").strip(),
                "email": str(fin_email).strip() if fin_email else None,
                "phone": str(fin_phone).strip() if fin_phone else None,
                "is_primary": True,
            })

        has_fb_bool = True
        if "FB" in col and raw[col["FB"]]:
            has_fb_bool = (str(raw[col["FB"]]).strip().upper() == "Y")

        image_url_val = raw[col["Images"]] if "Images" in col and raw[col["Images"]] else None
        period_update_val = _norm_datetime(raw[col["Period Update "]]) if "Period Update " in col else None

        rows.append(
            {
                "code": code,
                "name": (raw[col["Hotel Name"]] or "").strip().rstrip(",").strip(),
                "brand_code": brand_code,
                "region_code": REGION_CODE[region_name],
                "province_code": prov_code,
                "city": city,
                "opening_date": _norm_date(raw[col["Opening Date"]]),
                "terminate_date": _norm_date(raw[col["Terminate Date"]]),
                "status": HOTEL_STATUS_MAP.get(raw[col["Status"]], "ACTIVE"),
                "lat": raw[col["Latitude"]],
                "lon": raw[col["Longitude"]],
                "image_url": str(image_url_val).strip() if image_url_val else None,
                "has_fb": has_fb_bool,
                "period_update": period_update_val or datetime.now(timezone.utc),
                "contacts": contacts,
            }
        )
    wb.close()
    rows.sort(key=lambda x: x["code"])

    regions = [
        {
            "code": REGION_CODE[name],
            "name": name,
            "country": "Indonesia",
            "sales_region": region_sales[name].most_common(1)[0][0] if region_sales.get(name) else None,
            "ecommerce_region": region_ecom[name].most_common(1)[0][0] if region_ecom.get(name) else None,
            "status": "ACTIVE",
        }
        for name in REGION_CODE
    ]

    extra_scenarios = [
        {
            "code": "NUSANTARA",
            "name": "Nusantara Capital City (IKN)",
            "country": "Indonesia",
            "sales_region": "East Kalimantan & Nusantara Sales",
            "ecommerce_region": "Kalimantan",
            "status": "ACTIVE",
        },
        {
            "code": "TIMOR_BARAT",
            "name": "West Timor Special Region",
            "country": "Indonesia",
            "sales_region": "Sunda Kecil Sales",
            "ecommerce_region": "East Nusa Tenggara",
            "status": "INACTIVE",
        },
        {
            "code": "MALUKU_PAPUA",
            "name": "Maluku & Papua Combined (Legacy 2024)",
            "country": "Indonesia",
            "sales_region": "East Indonesia Combined",
            "ecommerce_region": "Maluku",
            "status": "RETIRED",
        },
        {
            "code": "TEST_EXPANSION",
            "name": "Test Regional Expansion Unit",
            "country": "Indonesia",
            "sales_region": "R&D Expansion",
            "ecommerce_region": "National",
            "status": "ACTIVE",
        },
        {
            "code": "APAC_OVERSEAS",
            "name": "Indochina & Philippines",
            "country": "Vietnam",
            "sales_region": "APAC International Division",
            "ecommerce_region": "APAC",
            "status": "ACTIVE",
        },
    ]
    regions.extend(extra_scenarios)
    regions.sort(key=lambda x: x["code"])

    # Format cities list
    cities = [
        {
            "name": city_name,
            "province_code": p_code,
            "ecommerce_city": e_city,
        }
        for (city_name, p_code), e_city in cities_dict.items()
    ]
    cities.sort(key=lambda x: x["name"])

    return rows, regions, cities


async def seed_regions_and_hotels(session: AsyncSession, data_dir: str) -> None:
    rows, regions, cities_raw = await _load_hotel_rows(data_dir)

    if regions:
        stmt = pg_insert(Region).values(regions)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Region.code],
            set_={
                "name": stmt.excluded.name,
                "country": stmt.excluded.country,
                "sales_region": stmt.excluded.sales_region,
                "ecommerce_region": stmt.excluded.ecommerce_region,
                "status": stmt.excluded.status,
            },
        )
        await session.execute(stmt)

        # Soft-delete test region for query verification
        await session.execute(
            text("UPDATE regions SET deleted_at = NOW() WHERE code = 'TEST_EXPANSION' AND deleted_at IS NULL")
        )

    brand_ids = dict((await session.execute(select(Brand.code, Brand.id))).all())
    region_ids = dict((await session.execute(select(Region.code, Region.id))).all())
    province_ids = dict((await session.execute(select(Province.code, Province.id))).all())

    # 1. Upsert Cities
    city_values = []
    for c in cities_raw:
        prov_id = province_ids.get(c["province_code"])
        if not prov_id:
            continue
        # Ambil salah satu region yang mencakup kota ini dari rows
        matching_reg_code = next(
            (r["region_code"] for r in rows if r["city"] == c["name"] and r["province_code"] == c["province_code"]),
            "JAKARTA"
        )
        reg_id = region_ids.get(matching_reg_code, list(region_ids.values())[0])

        city_values.append({
            "name": c["name"],
            "province_id": prov_id,
            "region_id": reg_id,
            "ecommerce_city": c["ecommerce_city"],
        })

    if city_values:
        stmt_city = pg_insert(City).values(city_values)
        stmt_city = stmt_city.on_conflict_do_update(
            constraint="uq_cities_name_province",
            set_={
                "region_id": stmt_city.excluded.region_id,
                "ecommerce_city": stmt_city.excluded.ecommerce_city,
            }
        )
        await session.execute(stmt_city)

    # Map (city_name, province_id) -> city.id
    city_lookup = {
        (r[0], r[1]): r[2]
        for r in (await session.execute(select(City.name, City.province_id, City.id))).all()
    }

    # 2. Upsert Hotels
    hotel_values = []
    for row in rows:
        lat, lon = row["lat"], row["lon"]
        if lat is None or lon is None:
            lat, lon = COORDINATE_FALLBACK[row["code"]]

        status = row["status"]
        if row["code"] in ("ATTB", "LHSB", "HCC"):
            status = "TEMPORARILY_CLOSED"

        mice_facilities = None
        if row["brand_code"] in ("SBH", "SBR", "GSB", "SBEC", "MDK") or row["code"] in ("CWS", "SBAI", "SQYO"):
            base_pax = 500 + (sum(ord(c) for c in row["code"]) % 8) * 100
            rooms = 4 + (sum(ord(c) for c in row["code"]) % 6)
            mice_facilities = {
                "ballroom_capacity": base_pax,
                "meeting_rooms": rooms,
                "has_videotron": (base_pax >= 800),
            }

        radius = 200
        if "resort" in row["name"].lower() or row["brand_code"] == "SBR":
            radius = 450
        elif row["brand_code"] in ("SBH", "GSB"):
            radius = 300
        elif row["brand_code"] in ("ZES", "SBEX"):
            radius = 150

        p_id = province_ids[row["province_code"]]
        c_id = city_lookup.get((row["city"], p_id))

        hotel_values.append(
            {
                "code": row["code"],
                "name": row["name"],
                "brand_id": brand_ids[row["brand_code"]],
                "region_id": region_ids[row["region_code"]],
                "province_id": p_id,
                "city_id": c_id,
                "city": row["city"],
                "geo": WKTElement(f"SRID=4326;POINT({lon} {lat})"),
                "geofence_radius_meters": radius,
                "mice_facilities": mice_facilities,
                "opening_date": row["opening_date"],
                "terminate_date": row["terminate_date"],
                "status": status,
                "image_url": row["image_url"],
                "has_fb": row["has_fb"],
                "period_update": row["period_update"],
            }
        )

    if hotel_values:
        stmt_hotel = pg_insert(Hotel).values(hotel_values)
        stmt_hotel = stmt_hotel.on_conflict_do_update(
            index_elements=[Hotel.code],
            set_={
                "name": stmt_hotel.excluded.name,
                "brand_id": stmt_hotel.excluded.brand_id,
                "region_id": stmt_hotel.excluded.region_id,
                "province_id": stmt_hotel.excluded.province_id,
                "city_id": stmt_hotel.excluded.city_id,
                "city": stmt_hotel.excluded.city,
                "geo": stmt_hotel.excluded.geo,
                "geofence_radius_meters": stmt_hotel.excluded.geofence_radius_meters,
                "mice_facilities": stmt_hotel.excluded.mice_facilities,
                "opening_date": stmt_hotel.excluded.opening_date,
                "terminate_date": stmt_hotel.excluded.terminate_date,
                "status": stmt_hotel.excluded.status,
                "image_url": stmt_hotel.excluded.image_url,
                "has_fb": stmt_hotel.excluded.has_fb,
                "period_update": stmt_hotel.excluded.period_update,
            },
        )
        await session.execute(stmt_hotel)

    hotel_ids = dict((await session.execute(select(Hotel.code, Hotel.id))).all())

    # 3. Seed Hotel Contacts (ROM, GM, Sales, Finance)
    # Hapus kontak lama dari hotel-hotel ini untuk menjaga idempotensi
    all_hids = list(hotel_ids.values())
    if all_hids:
        await session.execute(
            text("DELETE FROM hotel_contacts WHERE hotel_id = ANY(:hids)").bindparams(hids=all_hids)
        )

    contact_values = []
    for row in rows:
        h_id = hotel_ids[row["code"]]
        for c in row["contacts"]:
            contact_values.append({
                "hotel_id": h_id,
                "contact_type": c["contact_type"],
                "name": c["name"],
                "email": c["email"],
                "phone": c["phone"],
                "is_primary": c["is_primary"],
            })

    if contact_values:
        stmt_contacts = pg_insert(HotelContact).values(contact_values)
        await session.execute(stmt_contacts)

    # 4. Upsert Hotel Departments
    dept_values = [
        {
            "hotel_id": hotel_ids[row["code"]],
            "code": dept["code"],
            "name": dept["name"],
        }
        for row in rows
        for dept in HOTEL_DEPARTMENTS
    ]
    if dept_values:
        stmt_dept = pg_insert(HotelDepartment).values(dept_values)
        stmt_dept = stmt_dept.on_conflict_do_update(
            index_elements=[HotelDepartment.hotel_id, HotelDepartment.code],
            set_={"name": stmt_dept.excluded.name},
        )
        await session.execute(stmt_dept)


async def seed_master(session: AsyncSession, data_dir: str) -> None:
    await seed_brands(session)
    await seed_provinces(session)
    await seed_regions_and_hotels(session, data_dir)
