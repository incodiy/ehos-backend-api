"""Minimal seeder — only creates root admin user + RBAC roles (no external data dependency)."""

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models import Permission, Role, User, UserRole

SEED_PASSWORD = "Ehos#2026!"


async def seed_minimal(session: AsyncSession) -> None:
    """Seed only essential RBAC roles and root admin user (deployment-friendly)."""
    
    # 1. Create ROOT_ADMIN role
    root_role = await session.scalar(select(Role).where(Role.code == "ROOT_ADMIN"))
    if not root_role:
        root_role = Role(
            code="ROOT_ADMIN",
            name="Corporate Admin (Root / Sovereign)",
            scope_level=0,
            is_system=True,
        )
        session.add(root_role)
        await session.flush()
    
    # 2. Create root admin user
    root_user = await session.scalar(
        select(User).where(User.email == "root.admin@ehos.local")
    )
    if not root_user:
        root_user = User(
            email="root.admin@ehos.local",
            name="Root Admin",
            password_hash=hash_password(SEED_PASSWORD),
            is_active=True,
            must_change_password=True,
            preferred_locale="id",
        )
        session.add(root_user)
        await session.flush()
    
    # 3. Assign ROOT_ADMIN role to root user
    existing_role = await session.scalar(
        select(UserRole).where(
            UserRole.user_id == root_user.id,
            UserRole.role_id == root_role.id,
        )
    )
    if not existing_role:
        session.add(UserRole(user_id=root_user.id, role_id=root_role.id))
    
    await session.commit()
    print("✓ Minimal seeding completed: root.admin@ehos.local created")
