from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.deps import CurrentUser, DbSession, require_permission
from app.core.identity import get_by_uuid
from app.models import Notification, User
from app.schemas.common import HybridId
from app.services.billing_pipeline import remind_billing_milestones
from app.services.delivery import run_delivery_sweep
from app.services.notifications import list_user_notifications, remind_sla

router = APIRouter(prefix="/notifications", tags=["Notifications"])

_deliver_guard = Depends(require_permission("notifications:deliver"))


@router.get("")
async def list_notifications(
    session: DbSession,
    current_user: CurrentUser,
    unread_only: bool = Query(False, description="Hanya yang belum dibaca"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    include_channels: str | None = Query(None, description="Filter channel: PUSH|EMAIL|WA, pisahkan koma"),
) -> dict:
    """Inbox notifikasi user sendiri (scope owner; G2/G4 — data nyata dari DB)."""
    items, meta = await list_user_notifications(
        session,
        current_user.id,
        unread_only=unread_only,
        page=page,
        per_page=per_page,
    )
    if include_channels:
        wanted = {c.upper() for c in include_channels.split(",") if c.strip()}
        items = [i for i in items if i["channel"] in wanted]
        meta["total"] = len(items)
    return {"success": True, "data": items, "meta": meta}


@router.post("/{notification_id}/read")
async def mark_notification_read(
    notification_id: HybridId,
    session: DbSession,
    current_user: CurrentUser,
) -> Response:
    """Tandai dibaca (hanya milik user sendiri)."""
    notification = await get_by_uuid(session, Notification, notification_id)
    if notification is None or notification.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    if not notification.read_at:
        from datetime import UTC, datetime

        notification.read_at = datetime.now(UTC)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sweep")
async def run_notification_sweep(
    session: DbSession,
    _current: User = _deliver_guard,
    limit: int = Query(200, ge=1, le=1000),
) -> dict:
    """Jalankan delivery sweep (EMAIL/WA QUEUED → SENT/FAILED). Worker/job manajer."""
    result = await run_delivery_sweep(session, limit=limit)
    return {"success": True, "data": result}


@router.post("/remind-sla")
async def run_sla_reminder(
    session: DbSession,
    _current: User = _deliver_guard,
    window_hours: int = Query(24, ge=1, le=72, description="Batas jendela reminder SLA"),
) -> dict:
    """Sweep reminder SLA (due dalam window) → notif CAPA_SLA ke assignee+tier."""
    result = await remind_sla(session, window_hours=window_hours)
    return {"success": True, "data": result}


@router.post("/remind-billing")
async def run_billing_reminder(
    session: DbSession,
    _current: User = _deliver_guard,
    days_before: int = Query(14, ge=1, le=90, description="Jendela reminder jelang jatuh tempo tagihan dinas"),
) -> dict:
    """Sweep reminder billing (F-10): milestone belum PAID → notif BILLING_REMINDER ke finance+GM."""
    result = await remind_billing_milestones(session, days_before=days_before)
    return {"success": True, "data": result}
