"""RBAC seeder — 9 roles, permission catalogue, role→permission bindings.

Deterministic & idempotent (Constraint H1-H4). Constitutional identity data
(not business data): rows are upserted by natural code keys, bindings rebuilt
per run to fully mirror the catalogue below. Full user seeding (Phase 5)
happens in `app/seed/users.py`; this module only owns Role / Permission /
roles_permissions.
"""

import uuid

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Permission, Role, RolesPermission

# code: (name, scope_level)
ROLES: dict[str, tuple[str, int]] = {
    "ROOT_ADMIN": ("Corporate Admin (Root / Sovereign)", 0),
    "CORP_EXEC": ("Corporate Executive", 1),
    "CORP_AUDITOR": ("Corporate QA / Auditor", 1),
    "REGIONAL_ROM": ("Regional ROM", 2),
    "HOTEL_GM": ("Hotel General Manager", 3),
    "HOTEL_HOD_TECH": ("Hotel HOD / Technical", 4),
    "HOTEL_SALES": ("Hotel Sales", 4),
    "HOTEL_FINANCE": ("Hotel Finance", 4),
    "PUBLIC_CLIENT": ("Public Client", 5),
}

# code: (module, action, description)
PERMISSIONS: dict[str, tuple[str, str, str]] = {
    "user:manage:global": ("user", "manage:global", "Create/deactivate/revoke any account (A4)"),
    "user:manage:hotel": ("user", "manage:hotel", "Delegated admin: manage unit staff within scope (A3)"),
    "user:read:global": ("user", "read:global", "Read all users"),
    "user:read:region": ("user", "read:region", "Read users within region scope"),
    "user:read:hotel": ("user", "read:hotel", "Read users within hotel scope"),
    "rbac:manage": ("rbac", "manage", "Set role permissions (ROOT_ADMIN only)"),
    "master:read": ("master", "read", "Read hotels/brands/regions master"),
    "master:write": ("master", "write", "Create/update master data"),
    "checklist:read": ("checklist", "read", "Read checklist bank & templates"),
    "checklist:write": ("checklist", "write", "Create/version/lock checklist templates"),
    "audit:read:global": ("audit", "read:global", "Read all audit sessions"),
    "audit:read:region": ("audit", "read:region", "Read audits within region scope"),
    "audit:read:hotel": ("audit", "read:hotel", "Read audits within hotel scope"),
    "audit:run:hotel": ("audit", "run:hotel", "Execute/score audit within hotel scope"),
    "capa:read:global": ("capa", "read:global", "Read CAPA across all hotels (corporate SLA/kritis monitoring)"),
    "capa:read:hotel": ("capa", "read:hotel", "Read CAPA tickets within hotel scope"),
    "capa:manage:hotel": ("capa", "manage:hotel", "Create/assign CAPA within hotel scope"),
    "capa:resolve:hotel": ("capa", "resolve:hotel", "Submit/approve resolution evidence"),
    "capa:approve": ("capa", "approve", "Approve CAPA closure (corporate QA)"),
    "crm:read": ("crm", "read", "Read leads/quotations/activities"),
    "crm:manage": ("crm", "manage", "Create/update leads & quotations"),
    "billing:manage": ("billing", "manage", "Create/update billing milestones & mark PAID (F-10)"),
    "sbm:read": ("sbm", "read", "Read SBM rate matrix & quote pagu check"),
    "sbm:write": ("sbm", "write", "Update SBM master rates (corporate PMK controller)"),
    "rfp:submit": ("rfp", "submit", "Submit RFP intake (public client)"),
    "analytics:read:global": ("analytics", "read:global", "Corporate analytics / YoY"),
    "analytics:read:region": ("analytics", "read:region", "Regional analytics"),
    "analytics:read:hotel": ("analytics", "read:hotel", "Hotel analytics"),
    "audit_log:read": ("audit_log", "read", "Read system audit trail"),
    "ingest:run": ("ingest", "run", "Run legacy/Excel ingestion jobs"),
    "notifications:read": ("notifications", "read", "Read own notifications"),
    "notifications:deliver": ("notifications", "deliver", "Run delivery & SLA reminder sweep"),
    "translations:read": ("translations", "read", "Read translations by locale/entity"),
    "translations:write": ("translations", "write", "Upsert/delete translations (content admin)"),
    "profile:manage": ("profile", "manage", "Manage own profile/locale"),
}

# role_code -> permission codes
ROLE_PERMISSIONS: dict[str, list[str]] = {
    "ROOT_ADMIN": list(PERMISSIONS),
    "CORP_EXEC": [
        "user:read:global",
        "master:read",
        "audit:read:global",
        "capa:read:global",
        "crm:read",
        "sbm:read",
        "sbm:write",
        "billing:manage",
        "analytics:read:global",
        "audit_log:read",
        "notifications:read",
        "notifications:deliver",
        "translations:read",
        "profile:manage",
    ],
    "CORP_AUDITOR": [
        "user:read:global",
        "master:read",
        "master:write",
        "checklist:read",
        "checklist:write",
        "audit:read:global",
        "capa:read:global",
        "capa:approve",
        "analytics:read:global",
        "notifications:read",
        "translations:read",
        "translations:write",
        "profile:manage",
    ],
    "REGIONAL_ROM": [
        "user:read:region",
        "master:read",
        "checklist:read",
        "audit:read:region",
        "audit:read:hotel",
        "capa:read:hotel",
        "crm:read",
        "sbm:read",
        "analytics:read:region",
        "notifications:read",
        "translations:read",
        "profile:manage",
    ],
    "HOTEL_GM": [
        "user:manage:hotel",
        "user:read:hotel",
        "master:read",
        "checklist:read",
        "audit:read:hotel",
        "audit:run:hotel",
        "capa:read:hotel",
        "capa:manage:hotel",
        "crm:read",
        "crm:manage",
        "billing:manage",
        "sbm:read",
        "analytics:read:hotel",
        "notifications:read",
        "translations:read",
        "profile:manage",
    ],
    "HOTEL_HOD_TECH": [
        "user:read:hotel",
        "checklist:read",
        "audit:read:hotel",
        "audit:run:hotel",
        "capa:read:hotel",
        "capa:resolve:hotel",
        "notifications:read",
        "translations:read",
        "profile:manage",
    ],
    "HOTEL_SALES": [
        "user:read:hotel",
        "crm:read",
        "crm:manage",
        "sbm:read",
        "notifications:read",
        "profile:manage",
    ],
    "HOTEL_FINANCE": [
        "user:read:hotel",
        "crm:read",
        "billing:manage",
        "sbm:read",
        "notifications:read",
        "profile:manage",
    ],
    "PUBLIC_CLIENT": ["rfp:submit", "profile:manage"],
}


async def seed_rbac(session: AsyncSession) -> dict[str, uuid.UUID]:
    """Upsert roles/permissions/bindings. Returns {role_code: role_id}."""
    role_ids: dict[str, uuid.UUID] = {}
    for code, (name, scope_level) in ROLES.items():
        stmt = pg_insert(Role).values(
            code=code,
            name=name,
            scope_level=scope_level,
            is_system=True,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Role.code],
            set_={"name": name, "scope_level": scope_level},
        )
        await session.execute(stmt)
        role_ids[code] = await session.scalar(select(Role.id).where(Role.code == code))

    perm_ids: dict[str, uuid.UUID] = {}
    for code, (module, action, description) in PERMISSIONS.items():
        stmt = pg_insert(Permission).values(code=code, module=module, action=action, description=description)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Permission.code],
            set_={"module": module, "action": action, "description": description},
        )
        await session.execute(stmt)
        perm_ids[code] = await session.scalar(select(Permission.id).where(Permission.code == code))

    await session.execute(
        text("DELETE FROM roles_permissions WHERE role_id = ANY(:role_ids)").bindparams(
            role_ids=list(role_ids.values())
        )
    )

    bindings = []
    for role_code, perm_codes in ROLE_PERMISSIONS.items():
        for perm_code in perm_codes:
            bindings.append(
                {
                    "role_id": role_ids[role_code],
                    "permission_id": perm_ids[perm_code],
                }
            )
    if bindings:
        for chunk in range(0, len(bindings), 500):
            await session.execute(
                pg_insert(RolesPermission)
                .values(bindings[chunk : chunk + 500])
                .on_conflict_do_nothing(index_elements=["role_id", "permission_id"])
            )

    return role_ids
