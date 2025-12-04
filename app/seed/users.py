"""Full identity seeder — Phase 5 (RBAC users + scoped assignments + FK repoint).

Replaces the Phase-4 `system.ingest@ehos.local` placeholder with real actors:
audit templates/sessions/scores → CORP_AUDITOR, CRM leads → the regional
cross-sell sales rep (multi-hotel scope per A1), legacy batches → ROOT_ADMIN.

Deterministic & idempotent (H1-H4): users upsert by unique email, bindings by
natural composite keys. Seed password is a well-known *dev* value with
`must_change_password=True` — rotated by the user or root admin at first login.
"""

import uuid

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models import (
    Hotel,
    Lead,
    Role,
    User,
    UserHotelAssignment,
    UserRegionAssignment,
    UserRole,
)

SEED_PASSWORD = "Ehos#2026!"

# email -> (name, role_code, [hotel_codes], region-hotels, region, primary)
USERS: list[dict] = [
    {"email": "root.admin@ehos.local", "name": "Root Admin", "role": "ROOT_ADMIN"},
    {"email": "corp.exec@ehos.local", "name": "Corporate Executive", "role": "CORP_EXEC"},
    {"email": "corp.auditor@ehos.local", "name": "Corporate QA Auditing", "role": "CORP_AUDITOR"},
    {
        "email": "rom.jawa@ehos.local",
        "name": "ROM Jawa",
        "role": "REGIONAL_ROM",
        "regions_for_hotels": ["CWS", "SBAI"],
        "phone": "+6281200000002",
    },
    {
        "email": "gm.cws@ehos.local",
        "name": "GM CWS",
        "role": "HOTEL_GM",
        "hotels": ["CWS"],
        "phone": "+6281200000003",
    },
    {"email": "gm.sqyo@ehos.local", "name": "GM SQYO", "role": "HOTEL_GM", "hotels": ["SQYO"]},
    {
        "email": "gm.cluster@ehos.local",
        "name": "Cluster GM (SBAI + ZHBA)",
        "role": "HOTEL_GM",
        "hotels": ["SBAI", "ZHBA"],
    },
    {
        "email": "hod.hk.cws@ehos.local",
        "name": "HOD Housekeeping CWS",
        "role": "HOTEL_HOD_TECH",
        "hotels": ["CWS"],
        "phone": "+6281200000004",
    },
    {
        "email": "hod.kfb.cws@ehos.local",
        "name": "HOD Kitchen & FB CWS",
        "role": "HOTEL_HOD_TECH",
        "hotels": ["CWS"],
        "phone": "+6281200000005",
    },
    {
        "email": "hod.srm.cws@ehos.local",
        "name": "HOD Security CWS",
        "role": "HOTEL_HOD_TECH",
        "hotels": ["CWS"],
        "phone": "+6281200000006",
    },
    {"email": "sales.cws@ehos.local", "name": "Sales CWS", "role": "HOTEL_SALES", "hotels": ["CWS"]},
    {
        "email": "sales.tele@ehos.local",
        "name": "Cross-Sell Telemarketing",
        "role": "HOTEL_SALES",
        "hotels": "ALL_LEAD_HOTELS",
    },
    {"email": "finance.cws@ehos.local", "name": "Finance CWS", "role": "HOTEL_FINANCE", "hotels": ["CWS"]},
    {"email": "client.public@ehos.local", "name": "Public Client", "role": "PUBLIC_CLIENT"},
]


async def _role_id(session: AsyncSession, role_code: str) -> uuid.UUID:
    rid = await session.scalar(select(Role.id).where(Role.code == role_code))
    if rid is None:
        raise RuntimeError(f"Role {role_code} not seeded — run seed_rbac first")
    return rid


async def seed_users(session: AsyncSession, role_ids: dict[str, uuid.UUID]) -> dict[str, uuid.UUID]:
    hotel_ids: dict[str, uuid.UUID] = dict((await session.execute(select(Hotel.code, Hotel.id))).all())
    lead_hotel_ids = set((await session.execute(select(Lead.hotel_id).distinct())).scalars().all())

    resolved: dict[str, uuid.UUID] = {}

    async def _upsert_user(email: str, name: str, created_by: uuid.UUID | None, phone: str | None = None) -> uuid.UUID:
        stmt = pg_insert(User).values(
            email=email,
            name=name,
            password_hash=hash_password(SEED_PASSWORD),
            is_active=True,
            must_change_password=True,
            preferred_locale="id",
            phone=phone,
            created_by=created_by,
            updated_by=created_by,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[User.email],
            index_where=text("deleted_at IS NULL"),
            set_={
                "name": name,
                "phone": phone,
                "must_change_password": True,
                "updated_by": created_by if created_by is not None else User.updated_by,
            },
        )
        await session.execute(stmt)
        user_id = await session.scalar(select(User.id).where(User.email == email, User.deleted_at.is_(None)))
        if user_id is None:
            raise RuntimeError(f"User {email} could not be resolved after upsert")
        return user_id

    root_email = "root.admin@ehos.local"
    root_user_id = await _upsert_user(root_email, "Root Admin", None)
    await session.execute(
        pg_insert(UserRole)
        .values(user_id=root_user_id, role_id=role_ids["ROOT_ADMIN"])
        .on_conflict_do_nothing(index_elements=["user_id", "role_id"])
    )
    resolved[root_email] = root_user_id

    for item in USERS:
        email = item["email"]
        if email == root_email:
            continue
        user_id = await _upsert_user(email, item["name"], root_user_id, item.get("phone"))
        resolved[email] = user_id

        role_id = role_ids[item["role"]]
        await session.execute(
            pg_insert(UserRole)
            .values(user_id=user_id, role_id=role_id)
            .on_conflict_do_nothing(index_elements=["user_id", "role_id"])
        )

        if item.get("hotels") == "ALL_LEAD_HOTELS":
            target_codes = sorted(code for code, hid in hotel_ids.items() if hid in lead_hotel_ids)
        else:
            target_codes = item.get("hotels", [])

        for position, code in enumerate(target_codes):
            nested = pg_insert(UserHotelAssignment).values(
                user_id=user_id,
                hotel_id=hotel_ids[code],
                role_id=role_id,
                is_primary=position == 0,
            )
            nested = nested.on_conflict_do_update(
                index_elements=["user_id", "hotel_id", "role_id"],
                set_={"is_primary": position == 0},
            )
            await session.execute(nested)

            # Chained FK repoint: Hotel.gm_id jika role adalah HOTEL_GM (Constraint H2)
            if item["role"] == "HOTEL_GM":
                await session.execute(
                    text("UPDATE hotels SET gm_id = :uid WHERE id = :hid").bindparams(
                        uid=user_id, hid=hotel_ids[code]
                    )
                )

        for anchor in item.get("regions_for_hotels", []):
            region_id = await session.scalar(
                text("SELECT h.region_id FROM hotels h WHERE h.code = :code").bindparams(code=anchor)
            )
            if region_id is None:
                continue
            await session.execute(
                pg_insert(UserRegionAssignment)
                .values(user_id=user_id, region_id=region_id)
                .on_conflict_do_nothing(index_elements=["user_id", "region_id"])
            )
            # Chained FK repoint: Hotel.rom_id jika role adalah REGIONAL_ROM (Constraint H2)
            if item["role"] == "REGIONAL_ROM":
                await session.execute(
                    text("UPDATE hotels SET rom_id = :uid WHERE region_id = :rid").bindparams(
                        uid=user_id, rid=region_id
                    )
                )

    auditor_id = resolved["corp.auditor@ehos.local"]
    sales_id = resolved["sales.tele@ehos.local"]

    await session.execute(text("UPDATE checklist_templates SET published_by = :uid").bindparams(uid=auditor_id))
    await session.execute(text("UPDATE audit_sessions SET auditor_id = :uid").bindparams(uid=auditor_id))
    await session.execute(text("UPDATE audit_item_scores SET scored_by = :uid").bindparams(uid=auditor_id))
    await session.execute(
        text("UPDATE leads SET owner_id = :uid, created_by = :uid, updated_by = :uid").bindparams(uid=sales_id)
    )
    await session.execute(text("UPDATE legacy_ingestion_batches SET imported_by = :uid").bindparams(uid=root_user_id))

    # Pastikan minimal 1 hotel uji soft-deleted untuk verifikasi skenario soft delete (Constraint H3)
    await session.execute(
        text("UPDATE hotels SET deleted_at = NOW() WHERE code = 'SBCB' AND deleted_at IS NULL")
    )

    return resolved
