"""SLA class & auto-escalation CAPA (PRD-F-03, task 7b) — GM → ROM → VP.

SLA class (jam) berbasis priority — constraint korporat:
    Priority 1 (life-safety / CRITICAL) → 1x24 jam  (24h)
    Priority 2 (MAJOR)                   → 2x24 jam  (48h)
    Priority 3 (MINOR)                   → 7 hari    (168h)
    Follow-up (rentang 7-14 hari)        → hingga 336h (14 hari)

Auto-escalation saat SLA breach (due_at lewat, tiket belum CLOSED):
    level 1 → HOTEL_GM     (GM hotel tempat tiket)
    level 2 → REGIONAL_ROM (ROM yang ditugasi region hotel)
    level 3 → CORP_EXEC    (VP/korporat — level maksimal, cap di sini)

Setiap kenaikan level mencatat CapaStatusHistory (append-only) + membuat
Notification type CAPA_ESCALATE (status QUEUED) ke penerima tier baru —
payload siap untuk channel pengiriman (WA/email/push) di task notifikasi (7e).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CapaTicket, User
from app.services.capa_lifecycle import InvalidTransitionError, escalate_ticket

SLA_CLASS_HOURS: dict[int, int] = {1: 24, 2: 48, 3: 168}
FOLLOW_UP_MAX_HOURS = 336  # rentang 7-14 hari utk follow-up audit

MAX_ESCALATION_LEVEL = 3
RECEIVER_ROLE_FOR_LEVEL: dict[int, str] = {
    1: "HOTEL_GM",
    2: "REGIONAL_ROM",
    3: "CORP_EXEC",
}


def sla_class_hours(priority: int) -> int:
    return SLA_CLASS_HOURS.get(priority, 168)


def sla_status(ticket: CapaTicket, now: datetime | None = None) -> str:
    """Ringkasan posisi SLA tiket: ON_TRACK / AT_RISK / OVERDUE."""
    now = now or datetime.now(UTC)
    if ticket.status == "CLOSED":
        if ticket.closed_at is not None and ticket.closed_at <= ticket.due_at:
            return "ON_TRACK"
        return "OVERDUE"
    if ticket.due_at < now:
        return "OVERDUE"
    window = sla_class_hours(ticket.priority) * 3600
    # AT_RISK: sisa waktu < 25% window (min. 1 jam)
    margin = max(window * 0.25, 3600)
    if ticket.due_at - now < timedelta(seconds=margin):
        return "AT_RISK"
    return "ON_TRACK"


async def resolve_escalation_receivers(
    session: AsyncSession, ticket: CapaTicket, level: int
) -> list[User]:
    """Penerima alert utk sebuah escalation level (scope hotel/region/global)."""
    role_code = RECEIVER_ROLE_FOR_LEVEL.get(level)
    if role_code is None:
        raise InvalidTransitionError(f"unknown escalation level: {level}")
    if role_code == "CORP_EXEC":
        rows = await session.execute(text(
            "SELECT u.id FROM users u "
            "JOIN user_roles ur ON ur.user_id = u.id "
            "JOIN roles r ON r.id = ur.role_id "
            "WHERE r.code = :code AND u.deleted_at IS NULL "
            "ORDER BY u.id"
        ), {"code": role_code})
    elif role_code == "REGIONAL_ROM":
        rows = await session.execute(text(
            "SELECT DISTINCT u.id FROM users u "
            "JOIN user_roles ur ON ur.user_id = u.id "
            "JOIN roles r ON r.id = ur.role_id "
            "JOIN user_region_assignments ura ON ura.user_id = u.id "
            "AND ura.deleted_at IS NULL "
            "JOIN hotels h ON h.region_id = ura.region_id "
            "WHERE r.code = :code AND h.id = :hotel AND u.deleted_at IS NULL "
            "ORDER BY u.id"
        ), {"code": role_code, "hotel": ticket.hotel_id})
    else:  # HOTEL_GM — scope hotel
        rows = await session.execute(text(
            "SELECT DISTINCT u.id FROM users u "
            "JOIN user_roles ur ON ur.user_id = u.id "
            "JOIN roles r ON r.id = ur.role_id "
            "JOIN user_hotel_assignments uha ON uha.user_id = u.id "
            "AND uha.hotel_id = :hotel AND uha.deleted_at IS NULL "
            "WHERE r.code = :code AND u.deleted_at IS NULL "
            "ORDER BY u.id"
        ), {"code": role_code, "hotel": ticket.hotel_id})
    ids = [row[0] for row in rows]
    if not ids:
        return []
    return list((await session.scalars(select(User).where(User.id.in_(ids)))).all())


async def notify_escalation(
    session: AsyncSession,
    ticket: CapaTicket,
    level: int,
    receivers: list[User],
    actor_id: uuid.UUID,
) -> int:
    """Buat Notification CAPA_ESCALATE (QUEUED) utk tiap receiver — task 7e:
    multi-channel via template kanonikal (PUSH + EMAIL + WA bila phone).
    """
    from app.services.notifications import (
        create_notification,
        human_due,
        ticket_context,
    )

    context = ticket_context(ticket, escalation_level=level)
    context["due_at_fmt"] = human_due(ticket.due_at) if ticket.due_at else ""
    rows = 0
    for receiver in receivers:
        rows += len(await create_notification(
            session, key="capa_escalate", user=receiver, context=context,
            entity_type="capa_ticket", entity_id=ticket.uuid,
        ))
    return rows


async def escalate_one_level(
    session: AsyncSession,
    ticket: CapaTicket,
    actor_id: uuid.UUID,
    note: str | None = None,
) -> list[User]:
    """Naikkan escalation_level satu tingkat + notifikasi ke tier baru.

    Mengembalikan list receiver yang telah di-notify (kosong bila level cap).
    """
    if ticket.status == "CLOSED":
        raise InvalidTransitionError("Cannot escalate a CLOSED ticket")
    if ticket.escalation_level >= MAX_ESCALATION_LEVEL:
        raise InvalidTransitionError(
            f"Escalation sudah di level maksimal ({MAX_ESCALATION_LEVEL})"
        )
    next_level = ticket.escalation_level + 1
    await escalate_ticket(
        session, ticket, actor_id,
        note or f"Escalation ke level {next_level}",
    )
    receivers = await resolve_escalation_receivers(session, ticket, next_level)
    if receivers:
        await notify_escalation(session, ticket, next_level, receivers, actor_id)
    return receivers


async def find_sla_breaches(
    session: AsyncSession, now: datetime | None = None, limit: int = 200
) -> list[CapaTicket]:
    """Tiket belum CLOSED dengan due_at lewat (kandidat auto-escalation)."""
    now = now or datetime.now(UTC)
    return list((await session.scalars(
        select(CapaTicket)
        .where(CapaTicket.status != "CLOSED", CapaTicket.due_at < now)
        .order_by(CapaTicket.due_at.asc())
        .limit(limit)
    )).all())


async def run_sla_escalation(
    session: AsyncSession,
    actor_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> dict:
    """Sweep SLA breach → naikkan satu level per tiket (cap MAX_ESCALATION_LEVEL).

    Dipanggil oleh worker/scheduler (SYSTEM.md Worker/RQ). Idempoten separuh:
    tiket yang sudah level cap atau CLOSED tidak ikut di-eskalasi.
    """
    if actor_id is None:
        actor_id = await session.scalar(select(User.id).where(User.email == "root.admin@ehos.local"))
    breaches = await find_sla_breaches(session, now)
    escalated = notified = skipped_max = 0
    items = []
    for ticket in breaches:
        if ticket.escalation_level >= MAX_ESCALATION_LEVEL:
            skipped_max += 1
            continue
        receivers = await escalate_one_level(session, ticket, actor_id)
        escalated += 1
        notified += len(receivers)
        items.append({
            "ticket_id": str(ticket.uuid),
            "receipt_id": ticket.receipt_id,
            "from_level": ticket.escalation_level - 1,
            "to_level": ticket.escalation_level,
            "receivers": len(receivers),
        })
    await session.commit()
    return {
        "scanned": len(breaches),
        "escalated": escalated,
        "skipped_at_max": skipped_max,
        "notifications": notified,
        "items": items,
    }