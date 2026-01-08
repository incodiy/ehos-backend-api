"""Audit Log business service & query repository — Single Responsibility Principle (G5).

Provides query builder with multi-criteria filtering (search, entity_type, action,
actor_id, date range) and clean separation of concerns from endpoint handlers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, User
from app.schemas.audit import AuditLogOut
from app.schemas.common import Paginated, PaginationMeta


async def list_audit_logs_service(
    session: AsyncSession,
    *,
    search: str | None = None,
    entity_type: str | None = None,
    action: str | None = None,
    actor_id: int | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    page: int = 1,
    per_page: int = 25,
) -> Paginated[list[AuditLogOut], AuditLogOut]:
    """Query audit logs with multi-parameter filtering and pagination."""
    stmt = (
        select(AuditLog, User.email.label("actor_email"))
        .join(User, User.id == AuditLog.actor_id, isouter=True)
        .order_by(AuditLog.at.desc())
    )
    count_stmt = select(func.count(AuditLog.id)).select_from(AuditLog)

    # Search filter: matches actor email, IP address, or entity_id text
    if search and search.strip():
        q = f"%{search.strip()}%"
        search_filter = or_(
            User.email.ilike(q),
            AuditLog.ip.ilike(q),
            AuditLog.action.ilike(q),
            AuditLog.entity_type.ilike(q),
        )
        stmt = stmt.where(search_filter)
        count_stmt = count_stmt.join(User, User.id == AuditLog.actor_id, isouter=True).where(search_filter)

    if entity_type and entity_type.strip():
        stmt = stmt.where(AuditLog.entity_type == entity_type.strip())
        count_stmt = count_stmt.where(AuditLog.entity_type == entity_type.strip())

    if action and action.strip():
        stmt = stmt.where(AuditLog.action == action.strip())
        count_stmt = count_stmt.where(AuditLog.action == action.strip())

    if actor_id is not None:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
        count_stmt = count_stmt.where(AuditLog.actor_id == actor_id)

    if from_date is not None:
        stmt = stmt.where(AuditLog.at >= from_date)
        count_stmt = count_stmt.where(AuditLog.at >= from_date)

    if to_date is not None:
        stmt = stmt.where(AuditLog.at <= to_date)
        count_stmt = count_stmt.where(AuditLog.at <= to_date)

    total = await session.scalar(count_stmt) or 0
    safe_page = max(1, page)
    safe_per_page = max(1, min(per_page, 100))
    last_page = max(1, (total + safe_per_page - 1) // safe_per_page)

    rows = (
        await session.execute(
            stmt.offset((safe_page - 1) * safe_per_page).limit(safe_per_page)
        )
    ).all()

    items: list[AuditLogOut] = []
    for log, actor_email in rows:
        items.append(
            AuditLogOut.model_validate(
                {
                    "uuid": log.uuid,
                    "actor_email": actor_email,
                    "action": log.action,
                    "entity_type": log.entity_type,
                    "entity_id": log.entity_id,
                    "before": log.before,
                    "after": log.after,
                    "ip": log.ip,
                    "at": log.at,
                }
            )
        )

    return Paginated(
        data=items,
        meta=PaginationMeta(
            current_page=safe_page,
            per_page=safe_per_page,
            total=total,
            last_page=last_page,
        ),
    )
