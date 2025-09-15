"""Minimal identity seeder — system ingest identity for audit FK refs.

Deterministic & idempotent (Constraint H1-H4). Full RBAC seeding belongs to
Phase 5 (auth/users); this only guarantees the `users` row required by
`checklist_templates.published_by`, `audit_sessions.auditor_id` and
`audit_item_scores.scored_by` non-null foreign keys.
"""

import uuid

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models import User

SYSTEM_INGEST_EMAIL = "system.ingest@ehos.local"
SYSTEM_INGEST_NAME = "SYSTEM · Ingestion Engine"


async def seed_system_ingest_user(session: AsyncSession) -> uuid.UUID:
    stmt = pg_insert(User).values(
        email=SYSTEM_INGEST_EMAIL,
        name=SYSTEM_INGEST_NAME,
        password_hash=hash_password(uuid.uuid4().hex),
        is_active=False,
        must_change_password=True,
        preferred_locale="en",
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=[User.email], index_where=text("deleted_at IS NULL"))
    await session.execute(stmt)

    user_id = await session.scalar(select(User.id).where(User.email == SYSTEM_INGEST_EMAIL))
    if user_id is None:
        raise RuntimeError("System ingest user could not be resolved")
    return user_id
