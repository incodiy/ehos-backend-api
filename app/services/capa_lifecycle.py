"""CAPA ticket lifecycle (PRD-F-03/F-04, task 7a + 7d) — Four-Eyes status machine.

Status alur (openapi.yaml, constraint korporat — tiket tidak bisa ditutup
sepihak oleh teknisi):

    OPEN ──resolve──▶ AWAITING_GM ──gm/APPROVE──▶ AWAITING_QA ──qa/CLOSE──▶ CLOSED
     ▲                    │                          │
     └────── resolve ◀────┘                          │
         (rework, GM REJECT / teknisi tambal)        │
     └──────────────────── qa/REOPEN ────────────────┘

- resolve   : teknisi submit perbaikan + bukti foto AFTER (AWAITING_GM)
- verify/gm : GM first-approver → APPROVE (lanjut QA) / REJECT (ke OPEN)
- verify/qa : corporate QA final-approver → CLOSE (tutup tiket) / REOPEN (ke OPEN)
- escalate  : bump escalation_level (level mapping GM→ROM→VP di-seed 7b)

**Hierarki approval (task 7d, PRD-F-04 / STATE-SPECS §2.5):** approval tidak
diberikan tanpa bukti — `gm_approve` & `qa_close` mewajibkan ≥1 CapaMedia
AFTER berstatus VERIFIED (bukti foto After tertaut split-path 7c). Four-Eyes:
approver TIDAK boleh sama dengan assignee (si penanggung perbaikan); untuk
origin WHISTLEBLOWER approver juga tidak boleh sama dengan reporter. REJECT/
REOPEN sengaja TIDAK digate (memang menolak karena bukti kurang/jelek).

Setiap transisi mencatat CapaStatusHistory (append-only, audit trail + timeline
SLA). submitted_at/closed_at/closed_by di-set pada transisi yang relevan.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CapaMedia, CapaStatusHistory, CapaTicket

# action -> status-status sumber yang legal
VALID_FROM: dict[str, frozenset[str]] = {
    "resolve": frozenset({"OPEN", "AWAITING_GM"}),
    "gm_approve": frozenset({"AWAITING_GM"}),
    "gm_reject": frozenset({"AWAITING_GM"}),
    "qa_close": frozenset({"AWAITING_QA"}),
    "qa_reopen": frozenset({"AWAITING_QA"}),
    "escalate": frozenset({"OPEN", "AWAITING_GM", "AWAITING_QA"}),
}

TO_STATUS: dict[str, str] = {
    "resolve": "AWAITING_GM",
    "gm_approve": "AWAITING_QA",
    "gm_reject": "OPEN",
    "qa_close": "CLOSED",
    "qa_reopen": "OPEN",
}

CLOSED = "CLOSED"

APPROVAL_ACTIONS = frozenset({"gm_approve", "qa_close"})


class InvalidTransitionError(ValueError):
    """Transisi tak legal untuk status tiket saat ini (atau gate approval gagal)."""


async def _count_verified_after(session: AsyncSession, ticket_id: int) -> int:
    """Banyaknya bukti AFTER terverifikasi (split-path 7c → upload_status VERIFIED)."""
    return int(await session.scalar(
        select(func.count()).select_from(CapaMedia).where(
            CapaMedia.ticket_id == ticket_id,
            CapaMedia.phase == "AFTER",
            CapaMedia.upload_status == "VERIFIED",
        )
    ) or 0)


async def assert_approval_gate(
    session: AsyncSession, ticket: CapaTicket, actor_id: uuid.UUID
) -> None:
    """Hierarki approval layer (task 7d, PRD-F-04).

    - Four-Eyes: approver ≠ assignee (yang kerjaannya diverifikasi).
    - Whistleblower: approver ≠ reporter (kanal pseudonim, A-004).
    - Evidence: minimal 1 bukti AFTER terverifikasi wajib ada sebelum approve.
    """
    if ticket.assigned_to is not None and ticket.assigned_to == actor_id:
        raise InvalidTransitionError(
            "Approver tidak boleh sama dengan assignee (Four-Eyes)"
        )
    if (
        ticket.origin == "WHISTLEBLOWER"
        and ticket.reporter_id is not None
        and ticket.reporter_id == actor_id
    ):
        raise InvalidTransitionError(
            "Approver tidak boleh sama dengan reporter laporan anonim"
        )
    if not await _count_verified_after(session, ticket.id):
        raise InvalidTransitionError(
            "Approval memerlukan minimal 1 bukti AFTER terverifikasi (PRD-F-04)"
        )


async def verify_ticket(
    session: AsyncSession,
    ticket: CapaTicket,
    action: str,
    actor_id: uuid.UUID,
    note: str | None = None,
) -> CapaTicket:
    """GM/QA decision — approve & close melewati gate hierarki approval."""
    if action in APPROVAL_ACTIONS:
        await assert_approval_gate(session, ticket, actor_id)
    return await transition_ticket(session, ticket, action, actor_id, note)


async def _history(
    session: AsyncSession,
    ticket: CapaTicket,
    from_status: str | None,
    to_status: str,
    actor_id: uuid.UUID,
    note: str | None,
) -> None:
    session.add(CapaStatusHistory(
        ticket_id=ticket.id,
        from_status=from_status,
        to_status=to_status,
        actor_id=actor_id,
        note=note,
    ))


async def transition_ticket(
    session: AsyncSession,
    ticket: CapaTicket,
    action: str,
    actor_id: uuid.UUID,
    note: str | None = None,
) -> CapaTicket:
    """Aplikasikan satu aksi lifecycle; history append-only + timestamp setup."""
    allowed = VALID_FROM.get(action)
    if allowed is None:
        raise InvalidTransitionError(f"unknown action: {action}")
    if ticket.status not in allowed:
        raise InvalidTransitionError(
            f"Action '{action}' tidak sah untuk status '{ticket.status}'"
        )
    now = datetime.now(UTC)
    prev_status = ticket.status
    to_status = TO_STATUS.get(action, ticket.status)

    if action == "resolve":
        if ticket.submitted_at is None:
            ticket.submitted_at = now
    elif action == "qa_close":
        ticket.closed_at = now
        ticket.closed_by = actor_id

    ticket.status = to_status
    await _history(session, ticket, prev_status, to_status, actor_id, note)
    return ticket


async def resolve_ticket(
    session: AsyncSession,
    ticket: CapaTicket,
    actor_id: uuid.UUID,
    note: str,
    media: list,
) -> CapaTicket:
    """Teknisi submit perbaikan (foto AFTER live-camera, C1/C2) → AWAITING_GM.

    Mendaftarkan CapaMedia PENDING (upload object-store & verify sha256 adalah
    integrasi split-path media ARD-005 yang di-wire di task media selanjutnya).
    """
    ticket = await transition_ticket(session, ticket, "resolve", actor_id, note)
    for i, m in enumerate(media, start=1):
        session.add(CapaMedia(
            ticket_id=ticket.id,
            phase=m.phase,
            source_camera=m.source_camera,
            object_key=f"capa/{str(ticket.uuid)}/{uuid.uuid4().hex}.webp",
            file_name=f"capa-{ticket.receipt_id}-after-{i}.webp",
            mime=m.mime,
            width=m.width,
            height=m.height,
            size_bytes=m.size_bytes,
            checksum_sha256=m.checksum_sha256.lower(),
            gps_lat=m.gps_lat,
            gps_lng=m.gps_lng,
            gps_valid=m.gps_valid,
            captured_at=m.captured_at,
            server_captured_at=datetime.now(UTC),
            upload_status="PENDING",
        ))
    return ticket


async def assign_ticket(
    session: AsyncSession,
    ticket: CapaTicket,
    assigned_to: int,
    actor_id: uuid.UUID,
    note: str | None = None,
) -> CapaTicket:
    """Assign ke resolver (HOD/EHK). Bukan transisi status — history dictatat
    dengan from == to agar jejak penugasan tetap (openapi /history)."""
    if ticket.status == CLOSED:
        raise InvalidTransitionError("Cannot assign a CLOSED ticket")
    ticket.assigned_to = assigned_to
    await _history(session, ticket, ticket.status, ticket.status, actor_id,
                   note or f"Assigned to {assigned_to}")
    return ticket


async def escalate_ticket(
    session: AsyncSession,
    ticket: CapaTicket,
    actor_id: uuid.UUID,
    note: str | None = None,
) -> CapaTicket:
    """Naikkan escalation_level (SLA breach trigger; alert receiver di-seed 7b)."""
    await transition_ticket(session, ticket, "escalate", actor_id, note)
    ticket.escalation_level += 1
    return ticket