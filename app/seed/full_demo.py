"""Full demo seeder — no external files needed (H1-H4 compliant)."""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from geoalchemy2.elements import WKTElement

from app.core.security import hash_password
from app.models import (
    Brand,
    ChecklistItem,
    ChecklistSection,
    ChecklistTemplate,
    Hotel,
    Province,
    Region,
    User,
    UserHotelAssignment,
    UserRole,
)
from app.seed.rbac import seed_rbac

SEED_PASSWORD = "Ehos#2026!"


async def seed_master_minimal(session: AsyncSession) -> int:
    """Seed minimal master data without external files."""
    # Brands
    brands_data = [
        {"code": "SWB", "name": "Swiss-Belhotel International", "tier": "Upscale", "status": "ACTIVE"},
        {"code": "SBR", "name": "Swiss-Belresidences", "tier": "Upscale", "status": "ACTIVE"},
    ]
    for data in brands_data:
        existing = await session.scalar(select(Brand).where(Brand.code == data["code"]))
        if not existing:
            session.add(Brand(**data))
    await session.flush()

    # Regions
    regions_data = [
        {"code": "WEST", "name": "Western Region", "status": "ACTIVE"},
        {"code": "EAST", "name": "Eastern Region", "status": "ACTIVE"},
    ]
    for data in regions_data:
        existing = await session.scalar(select(Region).where(Region.code == data["code"]))
        if not existing:
            session.add(Region(**data))
    await session.flush()

    # Provinces
    provinces_data = [
        {"code": "DKI", "name": "DKI Jakarta"},
        {"code": "JB", "name": "Jawa Barat"},
    ]
    for data in provinces_data:
        existing = await session.scalar(select(Province).where(Province.code == data["code"]))
        if not existing:
            session.add(Province(**data))
    await session.flush()

    # Hotels
    brand = await session.scalar(select(Brand).where(Brand.code == "SWB"))
    region = await session.scalar(select(Region).where(Region.code == "WEST"))
    province = await session.scalar(select(Province).where(Province.code == "DKI"))

    hotel = await session.scalar(select(Hotel).where(Hotel.code == "SBJKT"))
    if not hotel and brand and region and province:
        hotel = Hotel(
            code="SBJKT",
            name="Swiss-Belhotel Mangga Besar Jakarta",
            brand_id=brand.id,
            region_id=region.id,
            province_id=province.id,
            city="Jakarta",
            geo=WKTElement("SRID=4326;POINT(106.8272 -6.1485)"),
            geofence_radius_meters=100,
            status="ACTIVE",
        )
        session.add(hotel)
        await session.flush()

    return hotel.id if hotel else 1


async def seed_users_minimal(session: AsyncSession, role_ids: dict, hotel_id: int) -> dict[str, int]:
    """Seed users including root admin and key hotel actors."""
    users_data = [
        {"email": "root.admin@ehos.local", "name": "Root Admin", "role": "ROOT_ADMIN"},
        {"email": "corp.exec@ehos.local", "name": "Corporate Executive", "role": "CORP_EXEC"},
        {"email": "corp.auditor@ehos.local", "name": "Corporate QA Auditing", "role": "CORP_AUDITOR"},
        {"email": "rom.jawa@ehos.local", "name": "ROM Jawa", "role": "REGIONAL_ROM"},
        {"email": "gm.cws@ehos.local", "name": "GM CWS", "role": "HOTEL_GM"},
        {"email": "hod.hk.cws@ehos.local", "name": "HOD Housekeeping CWS", "role": "HOTEL_HOD_TECH"},
        {"email": "sales.cws@ehos.local", "name": "Sales CWS", "role": "HOTEL_SALES"},
        {"email": "finance.cws@ehos.local", "name": "Finance CWS", "role": "HOTEL_FINANCE"},
    ]

    resolved: dict[str, int] = {}

    for data in users_data:
        email = data["email"]
        existing = await session.scalar(select(User).where(User.email == email))
        if existing:
            resolved[email] = existing.id
            user_id = existing.id
        else:
            user = User(
                email=email,
                name=data["name"],
                password_hash=hash_password(SEED_PASSWORD),
                is_active=True,
                must_change_password=True,
            )
            session.add(user)
            await session.flush()
            user_id = user.id
            resolved[email] = user_id

        role_code = data["role"]
        if role_code in role_ids:
            role_id = role_ids[role_code]
            # Role binding
            await session.execute(
                pg_insert(UserRole)
                .values(user_id=user_id, role_id=role_id)
                .on_conflict_do_nothing(index_elements=["user_id", "role_id"])
            )
            # Hotel assignment binding
            if hotel_id:
                await session.execute(
                    pg_insert(UserHotelAssignment)
                    .values(user_id=user_id, hotel_id=hotel_id, role_id=role_id, is_primary=True)
                    .on_conflict_do_nothing(index_elements=["user_id", "hotel_id", "role_id"])
                )

    return resolved


async def seed_checklist_minimal(session: AsyncSession, creator_id: int) -> None:
    """Seed minimal checklist templates."""
    existing = await session.scalar(
        select(ChecklistTemplate).where(
            ChecklistTemplate.department == "GM",
            ChecklistTemplate.name == "Swiss-Belhotel Standard 2026",
            ChecklistTemplate.version == "1.0",
        )
    )
    if existing:
        return

    template = ChecklistTemplate(
        department="GM",
        name="Swiss-Belhotel Standard 2026",
        version="1.0",
        status="LOCKED",
        published_by=creator_id,
    )
    session.add(template)
    await session.flush()

    section = ChecklistSection(
        template_id=template.id,
        code="SEC-FO",
        name="Front Office",
        sort_order=1,
    )
    session.add(section)
    await session.flush()

    items_data = [
        {"code": "FO-001", "question_text": "Lobby bersih dan rapi", "rubric_type": "TRAFFIC_LIGHT", "weight": 5.0, "max_score": 100.0},
        {"code": "FO-002", "question_text": "Staff berseragam lengkap", "rubric_type": "TRAFFIC_LIGHT", "weight": 3.0, "max_score": 100.0},
    ]

    for idx, data in enumerate(items_data):
        session.add(
            ChecklistItem(
                section_id=section.id,
                code=data["code"],
                question_text=data["question_text"],
                rubric_type=data["rubric_type"],
                weight=data["weight"],
                max_score=data["max_score"],
                sort_order=idx + 1,
            )
        )


async def seed_full_demo(session: AsyncSession) -> None:
    """Run full demo seeding without external files."""
    print("🌱 Seeding RBAC...")
    role_ids = await seed_rbac(session)

    print("🌱 Seeding master data...")
    hotel_id = await seed_master_minimal(session)

    print("🌱 Seeding users...")
    resolved = await seed_users_minimal(session, role_ids, hotel_id)

    print("🌱 Seeding checklist...")
    admin_id = resolved.get("root.admin@ehos.local", 1)
    await seed_checklist_minimal(session, admin_id)

    await session.commit()
    print("✅ Full demo seeding completed successfully!")
