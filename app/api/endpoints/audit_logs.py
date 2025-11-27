"""System audit logs endpoints — `/audit/logs` (Constraint A4, Phase 12b / G5)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.api.security_helpers import perms
from app.models import AuditLog, User
from app.schemas.audit import AuditLogOut
from app.schemas.common import Envelope, Paginated, PaginationMeta

router = APIRouter(prefix="/audit", tags=["audit-logs"])


@router.get("/logs", response_model=Paginated[Envelope[list[AuditLogOut]], AuditLogOut])
async def list_audit_logs(
    current: CurrentUser,
    session: DbSession,
    entity_type: str | None = None,
    action: str | None = None,
    page: int = 1,
) -> Paginated[Envelope[list[AuditLogOut]], AuditLogOut]:
    """Trail audit read-only (audit_logs) — korporat/`users` scope."""
    user_perms = await perms(session, current)
    if "users" not in user_perms and "audit_log:read" not in user_perms and "audit:read:global" not in user_perms:
        raise HTTPException(403, "Missing permission: audit_log:read / users")

    stmt = (
        select(AuditLog, User.email.label("actor_email"))
        .join(User, User.id == AuditLog.actor_id, isouter=True)
        .order_by(AuditLog.at.desc())
    )
    count_stmt = select(func.count(AuditLog.id))
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
        count_stmt = count_stmt.where(AuditLog.entity_type == entity_type)
    if action:
        stmt = stmt.where(AuditLog.action == action)
        count_stmt = count_stmt.where(AuditLog.action == action)

    per_page = 50
    total = await session.scalar(count_stmt) or 0
    last_page = max(1, (total + per_page - 1) // per_page)
    rows = (
        await session.execute(
            stmt.offset((max(1, page) - 1) * per_page).limit(per_page)
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
            current_page=max(1, page),
            per_page=per_page,
            total=total,
            last_page=last_page,
        ),
    )
