"""System audit logs endpoints — `/audit/logs` (Constraint A4, Phase 12b / G5)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CurrentUser, DbSession
from app.api.security_helpers import perms
from app.schemas.audit import AuditLogOut
from app.schemas.common import Envelope, Paginated
from app.services.audit_log_service import list_audit_logs_service

router = APIRouter(prefix="/audit", tags=["audit-logs"])


@router.get("/logs", response_model=Paginated[Envelope[list[AuditLogOut]], AuditLogOut])
async def list_audit_logs(
    current: CurrentUser,
    session: DbSession,
    search: str | None = Query(None, description="Search actor email, action, entity type, or IP"),
    entity_type: str | None = Query(None, description="Filter by entity type (user, role, hotel, etc.)"),
    action: str | None = Query(None, description="Filter by action verb (USER_CREATE, ROLE_UPDATE, etc.)"),
    from_date: datetime | None = Query(None, description="Filter logs starting from date-time"),
    to_date: datetime | None = Query(None, description="Filter logs up to date-time"),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
) -> Paginated[Envelope[list[AuditLogOut]], AuditLogOut]:
    """Trail audit read-only (audit_logs) — korporat/`users` scope."""
    user_perms = await perms(session, current)
    if "users" not in user_perms and "audit_log:read" not in user_perms and "audit:read:global" not in user_perms:
        raise HTTPException(403, "Missing permission: audit_log:read / users")

    return await list_audit_logs_service(
        session=session,
        search=search,
        entity_type=entity_type,
        action=action,
        from_date=from_date,
        to_date=to_date,
        page=page,
        per_page=per_page,
    )
