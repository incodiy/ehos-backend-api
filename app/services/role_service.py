"""Role & Permission business services — Single Responsibility Principle.

Provides role queries, permission catalog breakdown by module, and sovereign
RBAC permission assignment (Constraint A4 / Root sovereignty).
"""

from collections import defaultdict
from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identity import get_by_uuid
from app.models.users import Permission, Role, RolesPermission, User
from app.schemas.common import HybridId
from app.schemas.users import PermissionOut, RoleOut, RoleWithPermissionsOut


async def list_roles_service(session: AsyncSession) -> list[RoleWithPermissionsOut]:
    """List all system & canonical roles along with their bound permissions."""
    roles = (await session.scalars(select(Role).where(Role.deleted_at.is_(None)).order_by(Role.scope_level, Role.code))).all()
    out: list[RoleWithPermissionsOut] = []
    for role in roles:
        rows = (
            await session.execute(
                text(
                    "SELECT p.uuid, p.code, p.module, p.action, p.description "
                    "FROM permissions p "
                    "JOIN roles_permissions rp ON rp.permission_id = p.id "
                    "WHERE rp.role_id = :rid ORDER BY p.module, p.code"
                ).bindparams(rid=role.id)
            )
        ).mappings().all()
        out.append(
            RoleWithPermissionsOut(
                uuid=role.uuid,
                code=role.code,
                name=role.name,
                scope_level=role.scope_level,
                is_system=role.is_system,
                permissions=[
                    PermissionOut(
                        id=r["uuid"],
                        code=r["code"],
                        module=r["module"],
                        action=r["action"],
                        description=r["description"],
                    )
                    for r in rows
                ],
            )
        )
    return out


async def get_role_service(session: AsyncSession, role_id: HybridId) -> RoleWithPermissionsOut:
    """Retrieve detail of a specific role by UUID or integer ID."""
    role = await get_by_uuid(session, Role, role_id)
    if role is None or role.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role tidak ditemukan")

    rows = (
        await session.execute(
            text(
                "SELECT p.uuid, p.code, p.module, p.action, p.description "
                "FROM permissions p "
                "JOIN roles_permissions rp ON rp.permission_id = p.id "
                "WHERE rp.role_id = :rid ORDER BY p.module, p.code"
            ).bindparams(rid=role.id)
        )
    ).mappings().all()

    return RoleWithPermissionsOut(
        uuid=role.uuid,
        code=role.code,
        name=role.name,
        scope_level=role.scope_level,
        is_system=role.is_system,
        permissions=[
            PermissionOut(
                id=r["uuid"],
                code=r["code"],
                module=r["module"],
                action=r["action"],
                description=r["description"],
            )
            for r in rows
        ],
    )


async def list_permissions_catalog_service(session: AsyncSession) -> dict[str, list[PermissionOut]]:
    """List all 38 master permissions catalog grouped by module."""
    perms = (await session.scalars(select(Permission).where(Permission.deleted_at.is_(None)).order_by(Permission.module, Permission.code))).all()
    grouped: dict[str, list[PermissionOut]] = defaultdict(list)
    for p in perms:
        grouped[p.module].append(
            PermissionOut(
                id=p.uuid,
                code=p.code,
                module=p.module,
                action=p.action,
                description=p.description,
            )
        )
    return dict(grouped)


async def set_role_permissions_service(
    session: AsyncSession,
    current: User,
    role_id: HybridId,
    permission_codes: list[str],
) -> RoleWithPermissionsOut:
    """Assign permissions to a role (ROOT_ADMIN sovereign only, Constraint A4)."""
    has_global = await session.scalar(
        text(
            "SELECT 1 FROM user_roles ur JOIN roles_permissions rp ON rp.role_id = ur.role_id "
            "JOIN permissions p ON p.id = rp.permission_id "
            "WHERE ur.user_id = :uid AND p.code = 'user:manage:global' LIMIT 1"
        ).bindparams(uid=current.id)
    )
    if not has_global:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Khusus ROOT_ADMIN (A4)")

    if not isinstance(permission_codes, list) or not all(isinstance(c, str) for c in permission_codes):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="permission_codes wajib array of string",
        )

    role = await get_by_uuid(session, Role, role_id)
    if role is None or role.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role tidak ditemukan")
    if role.code == "ROOT_ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Hak ROOT_ADMIN sovereign, tidak bisa diubah (A4)",
        )

    existing = set(
        (await session.execute(select(Permission.id).where(Permission.code.in_(permission_codes)))).scalars().all()
    )
    if len(existing) != len(set(permission_codes)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Ada permission code tidak dikenal",
        )

    await session.execute(
        text("DELETE FROM roles_permissions WHERE role_id = :rid").bindparams(rid=role.id)
    )
    for code in set(permission_codes):
        perm_id = await session.scalar(select(Permission.id).where(Permission.code == code))
        await session.execute(
            text(
                "INSERT INTO roles_permissions (role_id, permission_id) VALUES (:rid, :pid)"
            ).bindparams(rid=role.id, pid=perm_id)
        )
    await session.commit()

    return await get_role_service(session, role_id)
