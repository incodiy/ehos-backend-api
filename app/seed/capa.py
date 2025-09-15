"""CAPA scenario seeder — SLA class + auto-escalation (task 7b, Constraint H1-H4).

Mensimulasikan kondisi produksi CAPA yang dinamis: multi-status, multi-SLA
(on-track / hampir-overdue / overdue), multi-escalation (0..3 → GM/ROM/VP),
multi-origin (AUDIT dari finding nyata / MANUAL intake), ditutup tepat vs
terlambat. Deterministik (receipt `SLA%05d`, prioritas dari severity temuan
nyata) & idempoten (skip bila receipt sudah ada).

Bila belum ada finding (fresh DB), tiket dibuat origin MANUAL dengan FK hotel/
department nyata — tetap menampilkan variasi SLA/escalation utk uji frontend
sejak task pertama (G2/H2). Saat findings tanpa tiket tersedia, dipakai untuk
menjaga rantai nyata: findings → capa_tickets → capa_status_histories.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuditSession,
    CapaMedia,
    CapaStatusHistory,
    CapaTicket,
    Finding,
)
from app.services.capa_trigger import SEVERITY_SLA, resolve_department_id
from app.services.sla_escalation import SLA_CLASS_HOURS, resolve_escalation_receivers

SCENARIOS: list[dict[str, Any]] = [
    {"n": 1, "status": "OPEN", "level": 0, "due_offset_h": 168, "closed_offset_h": None, "priority": 3},
    {"n": 2, "status": "OPEN", "level": 0, "due_offset_h": 20, "closed_offset_h": None, "priority": 1},
    {"n": 3, "status": "OPEN", "level": 0, "due_offset_h": 1, "closed_offset_h": None, "priority": 1},
    {"n": 4, "status": "OPEN", "level": 0, "due_offset_h": -2, "closed_offset_h": None, "priority": 2},
    {"n": 5, "status": "OPEN", "level": 1, "due_offset_h": -6, "closed_offset_h": None, "priority": 1},
    {"n": 6, "status": "AWAITING_GM", "level": 0, "due_offset_h": 48, "closed_offset_h": None, "priority": 2},
    {"n": 7, "status": "AWAITING_QA", "level": 2, "due_offset_h": -3, "closed_offset_h": None, "priority": 1},
    {"n": 8, "status": "AWAITING_QA", "level": 3, "due_offset_h": -24, "closed_offset_h": None, "priority": 1},
    {"n": 9, "status": "CLOSED", "level": 1, "due_offset_h": 10, "closed_offset_h": -2, "priority": 2},
    {"n": 10, "status": "CLOSED", "level": 2, "due_offset_h": -48, "closed_offset_h": -24, "priority": 3},
]

_CHAIN: dict[str, list[tuple[str | None, str, str]]] = {
    "OPEN": [(None, "OPEN", "OPEN (auto-create)")],
    "AWAITING_GM": [
        (None, "OPEN", "OPEN (auto-create)"),
        ("OPEN", "AWAITING_GM", "Perbaikan diterima, menunggu verifikasi GM"),
    ],
    "AWAITING_QA": [
        (None, "OPEN", "OPEN (auto-create)"),
        ("OPEN", "AWAITING_GM", "Perbaikan diterima, menunggu verifikasi GM"),
        ("AWAITING_GM", "AWAITING_QA", "Disetujui GM, menunggu verifikasi QA korporat"),
    ],
    "CLOSED": [
        (None, "OPEN", "OPEN (auto-create)"),
        ("OPEN", "AWAITING_GM", "Perbaikan diterima, menunggu verifikasi GM"),
        ("AWAITING_GM", "AWAITING_QA", "Disetujui GM, menunggu verifikasi QA korporat"),
        ("AWAITING_QA", "CLOSED", "CAPA ditutup oleh corporate QA"),
    ],
}


async def _existing_receipts(session: AsyncSession) -> set[str]:
    return set(
        (await session.scalars(select(CapaTicket.receipt_id).where(CapaTicket.receipt_id.like("SLA%")))).all() or []
    )


async def _free_findings(session: AsyncSession) -> list[Finding]:
    rows = await session.execute(
        text(
            "SELECT f.id FROM findings f "
            "WHERE NOT EXISTS (SELECT 1 FROM capa_tickets ct WHERE ct.finding_id = f.id) "
            "ORDER BY f.created_at ASC LIMIT 100"
        )
    )
    ids = [row[0] for row in rows]
    if not ids:
        return []
    return list((await session.scalars(select(Finding).where(Finding.id.in_(ids)))).all())


async def _resolver_for_hotel(session: AsyncSession, hotel_id: uuid.UUID) -> uuid.UUID | None:
    """Satu user resolver (HOTEL_HOD_TECH) untuk hotel tiket (assignee realistis)."""
    return await session.scalar(
        text(
            "SELECT u.id FROM users u "
            "JOIN user_roles ur ON ur.user_id = u.id "
            "JOIN roles r ON r.id = ur.role_id "
            "JOIN user_hotel_assignments uha ON uha.user_id = u.id AND uha.hotel_id = :h "
            "WHERE r.code = 'HOTEL_HOD_TECH' AND u.deleted_at IS NULL "
            "AND uha.deleted_at IS NULL ORDER BY u.id LIMIT 1"
        ),
        {"h": hotel_id},
    )


def _apply_status_chain(session: AsyncSession, ticket: CapaTicket, actor_id: uuid.UUID, base_at: datetime) -> None:
    step = 0
    for from_status, to_status, note in _CHAIN[ticket.status]:
        session.add(
            CapaStatusHistory(
                ticket_id=ticket.id,
                from_status=from_status,
                to_status=to_status,
                actor_id=actor_id,
                note=f"Seeded: {note}",
                at=base_at + timedelta(hours=step),
            )
        )
        step += 1
    for level in range(1, ticket.escalation_level + 1):
        session.add(
            CapaStatusHistory(
                ticket_id=ticket.id,
                from_status=ticket.status,
                to_status=ticket.status,
                actor_id=actor_id,
                note=f"Seeded: Escalation ke level {level}",
                at=base_at + timedelta(hours=6 + level),
            )
        )


async def _seed_escalation_notifications(session: AsyncSession, ticket: CapaTicket, level: int) -> None:
    """Notifikasi CAPA_ESCALATE (QUEUED) ke tier receiver level — multi-channel (7e)."""
    from app.models import Hotel
    from app.services.notifications import create_notification, human_due, ticket_context

    if ticket.hotel_id:
        ticket.hotel = await session.get(Hotel, ticket.hotel_id)
    context = ticket_context(ticket, escalation_level=level)
    context["due_at_fmt"] = human_due(ticket.due_at) if ticket.due_at else ""
    for lv in range(1, level + 1):
        receivers = await resolve_escalation_receivers(session, ticket, lv)
        for receiver in receivers:
            await create_notification(
                session,
                key="capa_escalate",
                user=receiver,
                context=context,
                entity_type="capa_ticket",
                entity_id=ticket.uuid,
            )


async def _ensure_scenario_media(session: AsyncSession, now: datetime) -> int:
    """Bukti media nyata utk scenario receipt tertentu (H: capa_tickets → capa_media).

    Self-healing & idempoten: dijalankan tiap seeding, menambahkan media yang
    belum ada (key unik = file_name deterministik). VERIFIED = sudah melalui
    split-path confirm (7c); PENDING = belum di-upload — variasi status media
    utk uji UI sejak task pertama (G2/H2/H3).
    """
    plans: dict[str, list[tuple[str, str, int]]] = {
        "SLA00005": [("AFTER", "PENDING", 6)],  # bukti belum di-upload
        "SLA00006": [("AFTER", "VERIFIED", 6)],  # AWAITING_GM siap first-approve
        "SLA00007": [("AFTER", "VERIFIED", 8), ("AFTER", "VERIFIED", 8)],  # AWAITING_QA
        "SLA00008": [("AFTER", "VERIFIED", 8), ("AFTER", "PENDING", 8)],  # campuran
        "SLA00009": [("BEFORE", "VERIFIED", 72), ("AFTER", "VERIFIED", 6), ("AFTER", "VERIFIED", 6)],
        "SLA00010": [("BEFORE", "VERIFIED", 96), ("AFTER", "VERIFIED", 30)],  # CLOSED terlambat
    }
    if not plans:
        return 0
    tickets = (await session.scalars(select(CapaTicket).where(CapaTicket.receipt_id.in_(plans)))).all()
    existing: set[str] = set()
    if tickets:
        existing = set(
            (
                await session.scalars(
                    select(CapaMedia.file_name).where(CapaMedia.ticket_id.in_([t.id for t in tickets]))
                )
            ).all()
        )
    added = 0
    for ticket in tickets:
        for i, (phase, status, offset_h) in enumerate(plans[ticket.receipt_id], start=1):
            fname = f"capa-{ticket.receipt_id}-{phase.lower()}-{i}.webp"
            if fname in existing:
                continue
            captured_at = now - timedelta(hours=offset_h)
            digest = hashlib.sha256(f"{ticket.receipt_id}-{ticket.title}".encode()).hexdigest()
            session.add(
                CapaMedia(
                    ticket_id=ticket.id,
                    phase=phase,
                    source_camera="LIVE_CAMERA",
                    object_key=f"capa/{str(ticket.id)}/{uuid.uuid4().hex}.webp",
                    file_name=fname,
                    mime="image/webp",
                    width=1280,
                    height=960,
                    size_bytes=187_000 + i * 1000,
                    checksum_sha256=digest,
                    gps_lat=-6.200000,
                    gps_lng=106.810000,
                    gps_valid=True,
                    captured_at=captured_at,
                    server_captured_at=captured_at,
                    upload_status=status,
                )
            )
            added += 1
    return added


async def seed_capa_scenarios(session: AsyncSession, actor_id: uuid.UUID) -> dict:
    existing = await _existing_receipts(session)
    findings = await _free_findings(session)
    now = datetime.now(UTC)
    manual_hotel_id = await session.scalar(text("SELECT id FROM hotels WHERE code='CWS'"))
    created = skipped = findings_used = manual_used = 0

    for scenario in SCENARIOS:
        n = scenario["n"]
        receipt = f"SLA{n:05d}"
        if receipt in existing:
            skipped += 1
            continue

        finding = findings.pop(0) if findings else None
        if finding is not None:
            priority, sla_hours = SEVERITY_SLA.get(finding.severity, (3, 168))
            title = finding.title or "Corrective action dari temuan audit"
            description = finding.description
            hotel_id = finding.hotel_id
            origin = "AUDIT"
            findings_used += 1
        else:
            priority = scenario["priority"]
            sla_hours = SLA_CLASS_HOURS[priority]
            title = f"CAPA intake {receipt} — perbaikan {priority}-{scenario['status']}"
            description = (
                "Tiket intake/manual tanpa temuan audit (simulasi laporan WHISTLEBLOWER/"
                "ops). Memverifikasi alur SLA dan eskalasi berjenjang."
            )
            hotel_id = manual_hotel_id
            origin = "MANUAL"
            manual_used += 1

        due_at = now + timedelta(hours=scenario["due_offset_h"])
        closed_at = (
            now - timedelta(hours=scenario["closed_offset_h"]) if scenario["closed_offset_h"] is not None else None
        )
        dept_id = None
        if finding is not None:
            sess = await session.get(AuditSession, finding.session_id)
            dept_id = await resolve_department_id(session, hotel_id, sess.department) if sess else None
        elif manual_hotel_id is not None:
            dept_id = await resolve_department_id(session, manual_hotel_id, "SECURITY_RISK")

        ticket = CapaTicket(
            finding_id=finding.id if finding else None,
            hotel_id=hotel_id,
            department_id=dept_id,
            priority=priority,
            sla_hours=sla_hours,
            due_at=due_at,
            status=scenario["status"],
            title=title,
            description=description,
            assigned_to=await _resolver_for_hotel(session, hotel_id),
            escalation_level=scenario["level"],
            created_by=actor_id,
            reporter_id=actor_id,
            receipt_id=receipt,
            origin=origin,
            closed_at=closed_at,
            closed_by=actor_id if closed_at else None,
        )
        session.add(ticket)
        await session.flush()
        if scenario["status"] in {"AWAITING_GM", "AWAITING_QA", "CLOSED"}:
            ticket.submitted_at = now - timedelta(hours=6)
        _apply_status_chain(session, ticket, actor_id, now - timedelta(hours=48))
        if scenario["level"] >= 1:
            await _seed_escalation_notifications(session, ticket, scenario["level"])
        created += 1

    media_added = await _ensure_scenario_media(session, now)

    await session.commit()
    return {
        "created": created,
        "skipped": skipped,
        "findings_used": findings_used,
        "manual_used": manual_used,
        "media_added": media_added,
    }
