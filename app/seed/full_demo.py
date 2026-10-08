"""Full production-grade demo seeder for EHOS.

Populates all master data, roles, users, checklists, audit sessions, CAPA tickets,
CRM leads, RFPs, quotations, SBM rates, billing milestones, and audit trails
without requiring external Excel files (Constraint H1-H4 compliant).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
import uuid

from geoalchemy2.elements import WKTElement
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Brand,
    City,
    Hotel,
    HotelContact,
    HotelDepartment,
    Province,
    Region,
)
from app.seed.data import (
    BRANDS,
    CITY_PROVINCE,
    HOTEL_DEPARTMENTS,
    PROVINCES,
    REGION_CODE,
)
from app.seed.master import seed_brands, seed_provinces
from app.seed.identity import seed_system_ingest_user
from app.seed.rbac import seed_rbac
from app.seed.users import seed_users
from app.seed.checklist_bank import seed_checklist_bank
from app.seed.translations import seed_translations
from app.seed.sbm_rates import seed_sbm_rates
from app.seed.audit_scenarios import seed_audit_scenarios
from app.seed.capa import seed_capa_scenarios
from app.seed.crm_scenarios import seed_crm_referrals, seed_crm_scenarios
from app.seed.rfp_scenarios import seed_rfp_scenarios
from app.seed.quotation_scenarios import seed_quotation_scenarios
from app.seed.lost_reason_scenarios import seed_lost_reason_scenarios
from app.seed.billing_scenarios import seed_billing_milestones
from app.seed.audit_logs import seed_audit_logs
from app.services.billing_pipeline import remind_billing_milestones
from app.services.crm_pipeline import crm_followup_reminders
from app.services.notifications import remind_sla

logger = logging.getLogger(__name__)

# Complete list of demonstration hotels covering all brands, regions, and scenario keys
DEMO_HOTELS = [
    {
        "code": "CWS",
        "name": "Swiss-Belhotel Mangga Besar Jakarta",
        "brand_code": "SBH",
        "region_code": "JAKARTA",
        "province_code": "31",
        "city": "Jakarta",
        "lat": -6.1485,
        "lon": 106.8272,
        "status": "ACTIVE",
        "has_fb": True,
        "ballroom_capacity": 1200,
        "meeting_rooms": 8,
        "has_videotron": True,
        "image_url": "https://images.unsplash.com/photo-1566073771259-6a8506099945?auto=format&fit=crop&w=800&q=80",
    },
    {
        "code": "SBAI",
        "name": "Swiss-Belhotel Airport Jakarta",
        "brand_code": "SBH",
        "region_code": "BANTEN",
        "province_code": "36",
        "city": "Tangerang",
        "lat": -6.1256,
        "lon": 106.6631,
        "status": "ACTIVE",
        "has_fb": True,
        "ballroom_capacity": 800,
        "meeting_rooms": 6,
        "has_videotron": True,
        "image_url": "https://images.unsplash.com/photo-1582719508461-905c673771fd?auto=format&fit=crop&w=800&q=80",
    },
    {
        "code": "SQYO",
        "name": "Swiss-Belboutique Yogyakarta",
        "brand_code": "SBO",
        "region_code": "JATENG",
        "province_code": "34",
        "city": "Yogyakarta",
        "lat": -7.7828,
        "lon": 110.3712,
        "status": "ACTIVE",
        "has_fb": True,
        "ballroom_capacity": 600,
        "meeting_rooms": 5,
        "has_videotron": False,
        "image_url": "https://images.unsplash.com/photo-1542314831-068cd1dbfeeb?auto=format&fit=crop&w=800&q=80",
    },
    {
        "code": "ZHBA",
        "name": "Zest Sukajadi Bandung",
        "brand_code": "ZST",
        "region_code": "JABAR",
        "province_code": "32",
        "city": "Bandung",
        "lat": -6.8931,
        "lon": 107.5982,
        "status": "ACTIVE",
        "has_fb": True,
        "ballroom_capacity": 300,
        "meeting_rooms": 3,
        "has_videotron": False,
        "image_url": "https://images.unsplash.com/photo-1520250497591-112f2f40a3f4?auto=format&fit=crop&w=800&q=80",
    },
    {
        "code": "ZHAI",
        "name": "Zest Airport Jakarta",
        "brand_code": "ZST",
        "region_code": "BANTEN",
        "province_code": "36",
        "city": "Tangerang",
        "lat": -6.1260,
        "lon": 106.6620,
        "status": "ACTIVE",
        "has_fb": True,
        "ballroom_capacity": 250,
        "meeting_rooms": 2,
        "has_videotron": False,
        "image_url": "https://images.unsplash.com/photo-1551882547-ff40c63fe5fa?auto=format&fit=crop&w=800&q=80",
    },
    {
        "code": "MANP",
        "name": "MĀUA Nusa Penida Bali",
        "brand_code": "MAUA",
        "region_code": "BALI",
        "province_code": "51",
        "city": "Penida Island",
        "lat": -8.7275,
        "lon": 115.5444,
        "status": "ACTIVE",
        "has_fb": True,
        "ballroom_capacity": 150,
        "meeting_rooms": 2,
        "has_videotron": False,
        "image_url": "https://images.unsplash.com/photo-1571896349842-33c89424de2d?auto=format&fit=crop&w=800&q=80",
    },
    {
        "code": "SBAY",
        "name": "Swiss-Belinn Manyar Surabaya",
        "brand_code": "SBN",
        "region_code": "JATIM",
        "province_code": "35",
        "city": "Surabaya",
        "lat": -7.2845,
        "lon": 112.7661,
        "status": "ACTIVE",
        "has_fb": True,
        "ballroom_capacity": 450,
        "meeting_rooms": 4,
        "has_videotron": False,
        "image_url": "https://images.unsplash.com/photo-1564501049412-61c2a3083791?auto=format&fit=crop&w=800&q=80",
    },
    {
        "code": "SCTH",
        "name": "Hotel Ciputra Jakarta",
        "brand_code": "SBN",
        "region_code": "JAKARTA",
        "province_code": "31",
        "city": "Jakarta",
        "lat": -6.1685,
        "lon": 106.7865,
        "status": "ACTIVE",
        "has_fb": True,
        "ballroom_capacity": 1500,
        "meeting_rooms": 12,
        "has_videotron": True,
        "image_url": "https://images.unsplash.com/photo-1590490360182-c33d57733427?auto=format&fit=crop&w=800&q=80",
    },
    {
        "code": "SBKB",
        "name": "Swiss-Belhotel Harbour Bay Batam",
        "brand_code": "SBH",
        "region_code": "BATAM",
        "province_code": "21",
        "city": "Batam City",
        "lat": 1.1528,
        "lon": 103.9922,
        "status": "ACTIVE",
        "has_fb": True,
        "ballroom_capacity": 1000,
        "meeting_rooms": 7,
        "has_videotron": True,
        "image_url": "https://images.unsplash.com/photo-1578683010236-d716f9a3f461?auto=format&fit=crop&w=800&q=80",
    },
    {
        "code": "SBJKT",
        "name": "Swiss-Belhotel Pondok Indah",
        "brand_code": "SBH",
        "region_code": "JAKARTA",
        "province_code": "31",
        "city": "Jakarta",
        "lat": -6.2845,
        "lon": 106.7725,
        "status": "ACTIVE",
        "has_fb": True,
        "ballroom_capacity": 700,
        "meeting_rooms": 5,
        "has_videotron": True,
        "image_url": "https://images.unsplash.com/photo-1566073771259-6a8506099945?auto=format&fit=crop&w=800&q=80",
    },
    {
        "code": "SBCB",
        "name": "Swiss-Belinn Cibubur",
        "brand_code": "SBN",
        "region_code": "JAKARTA",
        "province_code": "31",
        "city": "Cibubur",
        "lat": -6.3752,
        "lon": 106.9021,
        "status": "ACTIVE",
        "has_fb": True,
        "ballroom_capacity": 500,
        "meeting_rooms": 4,
        "has_videotron": False,
        "image_url": "https://images.unsplash.com/photo-1584132967334-10e028bd69f7?auto=format&fit=crop&w=800&q=80",
    },
    {
        "code": "GSB",
        "name": "Grand Swiss-Belhotel Darmo",
        "brand_code": "GSB",
        "region_code": "JATIM",
        "province_code": "35",
        "city": "Surabaya",
        "lat": -7.2891,
        "lon": 112.7381,
        "status": "ACTIVE",
        "has_fb": True,
        "ballroom_capacity": 1000,
        "meeting_rooms": 6,
        "has_videotron": True,
        "image_url": "https://images.unsplash.com/photo-1542314831-068cd1dbfeeb?auto=format&fit=crop&w=800&q=80",
    },
    {
        "code": "SBR",
        "name": "Swiss-Belresort Dago Heritage",
        "brand_code": "SBR",
        "region_code": "JABAR",
        "province_code": "32",
        "city": "Bandung",
        "lat": -6.8622,
        "lon": 107.6251,
        "status": "ACTIVE",
        "has_fb": True,
        "ballroom_capacity": 600,
        "meeting_rooms": 4,
        "has_videotron": False,
        "image_url": "https://images.unsplash.com/photo-1571896349842-33c89424de2d?auto=format&fit=crop&w=800&q=80",
    },
    {
        "code": "SBBS",
        "name": "Swiss-Belhotel Borneo Samarinda",
        "brand_code": "SBH",
        "region_code": "KALIMANTAN",
        "province_code": "64",
        "city": "Samarinda City",
        "lat": -0.5021,
        "lon": 117.1536,
        "status": "TEMPORARILY_CLOSED",
        "has_fb": True,
        "ballroom_capacity": 800,
        "meeting_rooms": 5,
        "has_videotron": False,
        "image_url": "https://images.unsplash.com/photo-1566073771259-6a8506099945?auto=format&fit=crop&w=800&q=80",
    },
    {
        "code": "SBMR",
        "name": "Swiss-Belhotel Makassar",
        "brand_code": "SBH",
        "region_code": "SULAWESI",
        "province_code": "73",
        "city": "Makassar",
        "lat": -5.1476,
        "lon": 119.4327,
        "status": "ACTIVE",
        "has_fb": True,
        "ballroom_capacity": 1000,
        "meeting_rooms": 6,
        "has_videotron": True,
        "image_url": "https://images.unsplash.com/photo-1582719508461-905c673771fd?auto=format&fit=crop&w=800&q=80",
    },
    {
        "code": "SBBL",
        "name": "Swiss-Belresort Pecatu",
        "brand_code": "SBR",
        "region_code": "BALI",
        "province_code": "51",
        "city": "Kuta",
        "lat": -8.8021,
        "lon": 115.1189,
        "status": "ACTIVE",
        "has_fb": True,
        "ballroom_capacity": 500,
        "meeting_rooms": 3,
        "has_videotron": False,
        "image_url": "https://images.unsplash.com/photo-1520250497591-112f2f40a3f4?auto=format&fit=crop&w=800&q=80",
    },
    {
        "code": "SBMD",
        "name": "Swiss-Belhotel Maleosan Manado",
        "brand_code": "SBH",
        "region_code": "SULAWESI",
        "province_code": "71",
        "city": "Manado",
        "lat": 1.4886,
        "lon": 124.8428,
        "status": "ACTIVE",
        "has_fb": True,
        "ballroom_capacity": 900,
        "meeting_rooms": 7,
        "has_videotron": True,
        "image_url": "https://images.unsplash.com/photo-1551882547-ff40c63fe5fa?auto=format&fit=crop&w=800&q=80",
    },
    {
        "code": "SBC",
        "name": "Swiss-Belcourt Kupang",
        "brand_code": "SBC",
        "region_code": "NTT",
        "province_code": "53",
        "city": "Kupang",
        "lat": -10.1652,
        "lon": 123.6062,
        "status": "ACTIVE",
        "has_fb": True,
        "ballroom_capacity": 350,
        "meeting_rooms": 3,
        "has_videotron": False,
        "image_url": "https://images.unsplash.com/photo-1566073771259-6a8506099945?auto=format&fit=crop&w=800&q=80",
    },
    {
        "code": "SBX",
        "name": "Swiss-Belexpress Cilegon",
        "brand_code": "SBX",
        "region_code": "BANTEN",
        "province_code": "36",
        "city": "Cilegon",
        "lat": -6.0152,
        "lon": 106.0521,
        "status": "ACTIVE",
        "has_fb": False,
        "ballroom_capacity": 200,
        "meeting_rooms": 2,
        "has_videotron": False,
        "image_url": "https://images.unsplash.com/photo-1564501049412-61c2a3083791?auto=format&fit=crop&w=800&q=80",
    },
    {
        "code": "ZST",
        "name": "Zest Jemursari Surabaya",
        "brand_code": "ZST",
        "region_code": "JATIM",
        "province_code": "35",
        "city": "Surabaya",
        "lat": -7.3192,
        "lon": 112.7482,
        "status": "ACTIVE",
        "has_fb": True,
        "ballroom_capacity": 250,
        "meeting_rooms": 3,
        "has_videotron": False,
        "image_url": "https://images.unsplash.com/photo-1520250497591-112f2f40a3f4?auto=format&fit=crop&w=800&q=80",
    },
]

CITY_TO_REGION = {
    "Jakarta": "JAKARTA", "Thamrin": "JAKARTA", "Cibubur": "JAKARTA",
    "Bandung": "JABAR", "Bogor": "JABAR", "Majalengka": "JABAR", "Karawang": "JABAR",
    "Cibitung": "JABAR", "Cikarang": "JABAR", "Cirebon": "JABAR", "Indramayu": "JABAR",
    "Surakarta": "JATENG", "Semarang": "JATENG", "Brebes": "JATENG", "Yogyakarta": "JATENG",
    "Surabaya": "JATIM", "Malang": "JATIM", "Manyar": "JATIM",
    "Tangerang": "BANTEN", "Cilegon": "BANTEN", "Cikande": "BANTEN",
    "Bali": "BALI", "Ubud": "BALI", "Kuta": "BALI", "Sanur": "BALI", "Penida Island": "BALI",
    "Lombok": "NTB", "Kupang": "NTT", "Tambolaka": "NTT",
    "Medan": "SUMBAGBEL", "Jambi": "SUMBAGBEL", "Lampung": "SUMBAGBEL", "Pekanbaru": "SUMBAGBEL",
    "Batam City": "BATAM", "Bangka Belitung": "SUMBAGBEL", "Pangkal Pinang": "SUMBAGBEL",
    "Balikpapan": "KALIMANTAN", "Samarinda City": "KALIMANTAN", "Tarakan": "KALIMANTAN",
    "Banjarmasin": "KALIMANTAN", "Batulicin": "KALIMANTAN", "Palangkaraya": "KALIMANTAN", "Singkawang": "KALIMANTAN",
    "Makassar": "SULAWESI", "Manado": "SULAWESI", "Palu City": "SULAWESI", "Luwuk": "SULAWESI", "Kendari": "SULAWESI",
    "Ambon": "MALUKU",
    "Jayapura": "PAPUA", "Biak": "PAPUA", "Timika": "PAPUA", "Merauke": "PAPUA", "Manokwari": "PAPUA", "Sorong": "PAPUA",
}


async def seed_master_complete(session: AsyncSession) -> None:
    """Seed brands, provinces, regions, cities, and hotels with full attributes."""
    # 1. Brands & Provinces
    await seed_brands(session)
    await seed_provinces(session)
    await session.flush()

    # 2. Regions
    for reg_name, reg_code in REGION_CODE.items():
        stmt_reg = pg_insert(Region).values(
            code=reg_code,
            name=reg_name,
            country="Indonesia",
            sales_region=reg_name,
            ecommerce_region="Tier 1",
            status="ACTIVE",
        ).on_conflict_do_update(
            index_elements=[Region.code],
            set_={
                "name": reg_name,
                "status": "ACTIVE",
                "country": "Indonesia",
                "sales_region": reg_name,
                "ecommerce_region": "Tier 1",
            },
        )
        await session.execute(stmt_reg)
    await session.flush()

    province_ids = dict((await session.execute(select(Province.code, Province.id))).all())
    region_ids = dict((await session.execute(select(Region.code, Region.id))).all())
    brand_ids = dict((await session.execute(select(Brand.code, Brand.id))).all())

    # 3. Cities
    city_values = []
    for city_name, prov_code in CITY_PROVINCE.items():
        prov_id = province_ids.get(prov_code)
        reg_code = CITY_TO_REGION.get(city_name, "JAKARTA")
        reg_id = region_ids.get(reg_code)
        if prov_id and reg_id:
            city_values.append({
                "name": city_name,
                "province_id": prov_id,
                "region_id": reg_id,
                "ecommerce_city": city_name,
            })
    if city_values:
        stmt_city = pg_insert(City).values(city_values).on_conflict_do_update(
            constraint="uq_cities_name_province",
            set_={
                "region_id": pg_insert(City).excluded.region_id,
                "ecommerce_city": pg_insert(City).excluded.ecommerce_city,
            },
        )
        await session.execute(stmt_city)
    await session.flush()

    city_lookup = {
        (r[0], r[1]): r[2]
        for r in (await session.execute(select(City.name, City.province_id, City.id))).all()
    }

    # 4. Hotels
    hotel_values = []
    for h in DEMO_HOTELS:
        p_id = province_ids[h["province_code"]]
        c_id = city_lookup.get((h["city"], p_id))
        r_id = region_ids[h["region_code"]]
        b_id = brand_ids[h["brand_code"]]

        mice = {
            "ballroom_capacity": h["ballroom_capacity"],
            "meeting_rooms": h["meeting_rooms"],
            "has_videotron": h["has_videotron"],
        }

        hotel_values.append({
            "code": h["code"],
            "name": h["name"],
            "brand_id": b_id,
            "region_id": r_id,
            "province_id": p_id,
            "city_id": c_id,
            "city": h["city"],
            "geo": WKTElement(f"SRID=4326;POINT({h['lon']} {h['lat']})"),
            "geofence_radius_meters": 250,
            "mice_facilities": mice,
            "opening_date": date(2018, 1, 1),
            "status": h["status"],
            "image_url": h["image_url"],
            "has_fb": h["has_fb"],
        })

    if hotel_values:
        stmt_hotel = pg_insert(Hotel).values(hotel_values).on_conflict_do_update(
            index_elements=[Hotel.code],
            set_={
                "name": pg_insert(Hotel).excluded.name,
                "brand_id": pg_insert(Hotel).excluded.brand_id,
                "region_id": pg_insert(Hotel).excluded.region_id,
                "province_id": pg_insert(Hotel).excluded.province_id,
                "city_id": pg_insert(Hotel).excluded.city_id,
                "city": pg_insert(Hotel).excluded.city,
                "geo": pg_insert(Hotel).excluded.geo,
                "mice_facilities": pg_insert(Hotel).excluded.mice_facilities,
                "status": pg_insert(Hotel).excluded.status,
                "image_url": pg_insert(Hotel).excluded.image_url,
                "has_fb": pg_insert(Hotel).excluded.has_fb,
            },
        )
        await session.execute(stmt_hotel)
    await session.flush()

    hotel_ids = dict((await session.execute(select(Hotel.code, Hotel.id))).all())

    # 5. Hotel Contacts (ROM, GM, Sales, Finance)
    all_hids = list(hotel_ids.values())
    if all_hids:
        await session.execute(
            text("DELETE FROM hotel_contacts WHERE hotel_id = ANY(:hids)").bindparams(hids=all_hids)
        )

    contact_values = []
    dept_values = []
    for h in DEMO_HOTELS:
        hid = hotel_ids[h["code"]]
        for ctype in ("ROM", "GM", "SALES", "FINANCE"):
            contact_values.append({
                "hotel_id": hid,
                "contact_type": ctype,
                "name": f"{ctype} {h['code']}",
                "email": f"{ctype.lower()}.{h['code'].lower()}@ehos.local",
                "phone": "+62812" + str(abs(hash(h['code'] + ctype)) % 100000000).zfill(8),
                "is_primary": True,
            })
        for dept in HOTEL_DEPARTMENTS:
            dept_values.append({
                "hotel_id": hid,
                "code": dept["code"],
                "name": dept["name"],
            })

    if contact_values:
        await session.execute(pg_insert(HotelContact).values(contact_values))
    if dept_values:
        stmt_dept = pg_insert(HotelDepartment).values(dept_values).on_conflict_do_update(
            index_elements=[HotelDepartment.hotel_id, HotelDepartment.code],
            set_={"name": pg_insert(HotelDepartment).excluded.name},
        )
        await session.execute(stmt_dept)
    await session.flush()


async def seed_full_demo(session: AsyncSession) -> None:
    """Run full demonstration seeding across all application domains."""
    print("🌱 [1/16] Seeding Master Brands, Provinces, Regions, Cities & Hotels...")
    await seed_master_complete(session)

    print("🌱 [2/16] Seeding System Ingest User...")
    await seed_system_ingest_user(session)

    print("🌱 [3/16] Seeding RBAC Roles & Permissions...")
    role_ids = await seed_rbac(session)

    print("🌱 [4/16] Seeding Users & Hotel/Region Assignments...")
    resolved = await seed_users(session, role_ids)

    print("🌱 [5/16] Seeding Checklist Bank...")
    admin_id = resolved["root.admin@ehos.local"]
    await seed_checklist_bank(session, admin_id)

    print("🌱 [6/16] Seeding Master Translations...")
    await seed_translations(session, admin_id)

    print("🌱 [7/16] Seeding SBM Government Rates...")
    await seed_sbm_rates(session, resolved.get("corp.exec@ehos.local"))

    print("🌱 [8/16] Seeding Audit Scenarios...")
    await seed_audit_scenarios(session, resolved)

    print("🌱 [9/16] Seeding CAPA Tickets & SLA Workflow...")
    await seed_capa_scenarios(session, admin_id)

    print("🌱 [10/16] Seeding CRM Leads & Kanban Pipelines...")
    await seed_crm_scenarios(session, resolved)

    print("🌱 [11/16] Seeding CRM Cross-Property Referrals...")
    await seed_crm_referrals(session, resolved)

    print("🌱 [12/16] Seeding RFP Portal Requests...")
    await seed_rfp_scenarios(session, resolved)

    print("🌱 [13/16] Seeding Quotations with Pagu Validation...")
    await seed_quotation_scenarios(session, resolved)

    print("🌱 [14/16] Seeding Lost Deal Scenarios...")
    await seed_lost_reason_scenarios(session, resolved)

    print("🌱 [15/16] Seeding Billing Milestones (SPK, NPWP, BAST, LPJ)...")
    await seed_billing_milestones(session, resolved)

    print("🌱 [16/16] Seeding Corporate Audit Logs & Sweeping Reminders...")
    await seed_audit_logs(session, resolved)

    now = datetime.now(UTC)
    await remind_sla(session, now=now, window_hours=24)
    await crm_followup_reminders(session, now=now)
    await remind_billing_milestones(session, now=now, days_before=14)

    await session.commit()
    print("🎉 FULL DEMO DATA SEEDING COMPLETED SUCCESSFULLY!")
