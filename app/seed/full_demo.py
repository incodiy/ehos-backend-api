"""Full demo seeder — no external files needed (H1-H4 compliant)."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.seed.rbac import seed_rbac
from app.seed.users import seed_users_minimal
from app.seed.master import seed_master_minimal
from app.seed.checklist_bank import seed_checklist_minimal


async def seed_users_minimal(session: AsyncSession, role_ids: dict) -> dict:
    """Seed minimal users without external data."""
    from app.core.security import hash_password
    from app.models import User, UserRole
    from sqlalchemy import select
    
    users_data = [
        {
            "email": "gm.jakarta@ehos.local",
            "name": "GM Jakarta",
            "password": "Ehos#2026!",
            "is_active": True,
            "role": "GM",
        },
        {
            "email": "rom.west@ehos.local",
            "name": "ROM West Region",
            "password": "Ehos#2026!",
            "is_active": True,
            "role": "ROM",
        },
    ]
    
    resolved = {"root.admin@ehos.local": 1}  # Assume root admin ID=1
    
    for data in users_data:
        existing = await session.scalar(select(User).where(User.email == data["email"]))
        if existing:
            resolved[data["email"]] = existing.id
            continue
        
        user = User(
            email=data["email"],
            name=data["name"],
            password_hash=hash_password(data["password"]),
            is_active=data["is_active"],
        )
        session.add(user)
        await session.flush()
        
        role_code = data["role"]
        if role_code in role_ids:
            session.add(UserRole(user_id=user.id, role_id=role_ids[role_code]))
        
        resolved[data["email"]] = user.id
    
    return resolved


async def seed_master_minimal(session: AsyncSession) -> None:
    """Seed minimal master data without external files."""
    from app.models import Brand, Region, Province, Hotel
    from sqlalchemy import select
    
    # Brands
    brands_data = [
        {"code": "SWB", "name": "Swiss-Belhotel International", "tier": "UPSCALE"},
        {"code": "SBR", "name": "Swiss-Belresidences", "tier": "UPSCALE"},
    ]
    
    for data in brands_data:
        existing = await session.scalar(select(Brand).where(Brand.code == data["code"]))
        if not existing:
            session.add(Brand(**data))
    
    await session.flush()
    
    # Regions
    regions_data = [
        {"code": "WEST", "name": "Western Region"},
        {"code": "EAST", "name": "Eastern Region"},
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
    
    if brand and region and province:
        hotels_data = [
            {
                "code": "SBJKT",
                "name": "Swiss-Belhotel Mangga Besar Jakarta",
                "brand_id": brand.id,
                "region_id": region.id,
                "province_id": province.id,
                "city": "Jakarta",
                "geofence_radius_meters": 100,
                "status": "ACTIVE",
            },
        ]
        
        for data in hotels_data:
            existing = await session.scalar(select(Hotel).where(Hotel.code == data["code"]))
            if not existing:
                session.add(Hotel(**data))


async def seed_checklist_minimal(session: AsyncSession, creator_id: int) -> None:
    """Seed minimal checklist templates."""
    from app.models import ChecklistTemplate, ChecklistCategory, ChecklistItem
    from sqlalchemy import select
    
    # Template
    existing = await session.scalar(select(ChecklistTemplate).where(ChecklistTemplate.code == "SB2025"))
    if existing:
        return
    
    template = ChecklistTemplate(
        code="SB2025",
        name="Swiss-Belhotel Standard 2025",
        brand_code="SWB",
        version=1,
        status="ACTIVE",
        created_by=creator_id,
    )
    session.add(template)
    await session.flush()
    
    # Category
    category = ChecklistCategory(
        template_id=template.id,
        code="FO",
        name="Front Office",
        display_order=1,
    )
    session.add(category)
    await session.flush()
    
    # Items
    items_data = [
        {"code": "FO-001", "text": "Lobby bersih dan rapi", "weight": 5},
        {"code": "FO-002", "text": "Staff berseragam lengkap", "weight": 3},
    ]
    
    for idx, data in enumerate(items_data):
        session.add(ChecklistItem(
            category_id=category.id,
            code=data["code"],
            text=data["text"],
            weight=data["weight"],
            display_order=idx + 1,
        ))


async def seed_full_demo(session: AsyncSession) -> None:
    """Run full demo seeding without external files."""
    print("🌱 Seeding RBAC...")
    role_ids = await seed_rbac(session)
    
    print("🌱 Seeding master data...")
    await seed_master_minimal(session)
    
    print("🌱 Seeding users...")
    resolved = await seed_users_minimal(session, role_ids)
    
    print("🌱 Seeding checklist...")
    await seed_checklist_minimal(session, resolved["root.admin@ehos.local"])
    
    await session.commit()
    print("✅ Full demo seeding completed!")
