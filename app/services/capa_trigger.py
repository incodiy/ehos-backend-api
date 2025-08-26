"""Auto-CAPA trigger dari temuan audit (PRD-F-03, task 6e).

Saat sesi di-publish (6d), setiap Finding gagal (ratio == 0) otomatis membentuk
CapaTicket origin AUDIT dengan SLA berjenjang (constraint korporat):
- CRITICAL (life-safety)  → Priority 1, SLA 1x24 jam
- MAJOR                   → Priority 2, SLA 2x24 jam
- MINOR                   → Priority 3, SLA 7 hari (default model)

Mengikat department (hotel_departments) sesuai sesi + mencatat status history
OPEN. chained FK (H2): audit_sessions → findings → capa_tickets →
capa_status_histories.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CapaStatusHistory, CapaTicket, Finding, HotelDepartment

SEVERITY_SLA: dict[str, tuple[int, int]] = {
    "CRITICAL": (1, 24),
    "MAJOR": (2, 48),
    "MINOR": (3, 168),
}


async def resolve_department_id(
    session: AsyncSession, hotel_id: int, department: str
) -> int | None:
    return await session.scalar(
        select(HotelDepartment.id).where(
            HotelDepartment.hotel_id == hotel_id,
            HotelDepartment.code == department,
        )
    )


async def auto_create_capa_ticket(
    session: AsyncSession,
    finding: Finding,
    actor_id: uuid.UUID,
    department: str | None = None,
) -> CapaTicket:
    """Buat tiket CAPA dari satu Finding gagal + catat history OPEN.

    Idempoten per finding: bila tiket dengan finding_id tsb sudah ada
    (retry/partial), kembalikan yang lama — mencegah duplikat receipt.
    """
    existing = await session.scalar(
        select(CapaTicket).where(CapaTicket.finding_id == finding.id).limit(1)
    )
    if existing is not None:
        return existing

    priority, sla_hours = SEVERITY_SLA.get(finding.severity, (3, 168))
    now = datetime.now(UTC)
    ticket = CapaTicket(
        finding_id=finding.id,
        hotel_id=finding.hotel_id,
        department_id=await resolve_department_id(session, finding.hotel_id, department)
        if department
        else None,
        priority=priority,
        sla_hours=sla_hours,
        due_at=now + timedelta(hours=sla_hours),
        status="OPEN",
        title=finding.title,
        description=finding.description,
        origin="AUDIT",
        receipt_id=f"CAPA-{finding.uuid.hex[:7].upper()}",
        reporter_id=actor_id,
        created_by=actor_id,
    )
    session.add(ticket)
    await session.flush()
    session.add(CapaStatusHistory(
        ticket_id=ticket.id,
        from_status=None,
        to_status="OPEN",
        actor_id=actor_id,
        note="Auto-created dari temuan audit",
    ))
    return ticket