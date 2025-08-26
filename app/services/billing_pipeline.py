"""Document & billing milestone pipeline — PRD-F-10 (task 8f).

Melengkapi quotation 8d untuk dinas: **milestone SPK / NPWP / BAST / LPJ**
mengikuti format resmi dokumen dinas (Constraint §5 F-10). Tiap milestone 1
dokumen berstatus `EXPECTED/UPLOADED/PAID/OVERDUE`:

- EXPECTED  → dibuat saat quotation ACCEPTED (belum ada dokumen).
- UPLOADED  → dokumen (`doc_key` object store / `doc_no`) sudah dilampirkan.
- OVERDUE   → belum terpenuhi & lewat `due_date` (ditandai finance/GM).
- PAID      → pelunasan tercatat (`paid_at`); **terminal** — financial
  immutability (ERD v1.3): milestone finansial tidak di-hard/soft-delete,
  pembatalan lewat status; PAID tidak bisa ditarik mundur.

Auto-reminder `remind_billing_milestones()` menyapu milestone **belum PAID**
yang `due_date` sudah lewat / masuk window jelang jatuh tempo → notifikasi
`BILLING_REMINDER` ke Finance + GM hotel quotation (dedup per milestone+due).
F-10: "auto-reminder sebelum tutup tahun anggaran" — cegah kebocoran tagihan
APBD/APBN.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BillingMilestone, Quotation, User
from app.models.master import Hotel
from app.services.notifications import create_notification

MILESTONE_TYPES = frozenset({"SPK", "NPWP", "BAST", "LPJ"})
MILESTONE_STATUSES = frozenset({"EXPECTED", "UPLOADED", "PAID", "OVERDUE"})

# Legal forward transitions — PAID terminal (imutabilitas finansial).
BILLING_TRANSITIONS: dict[str, frozenset[str]] = {
    "EXPECTED": frozenset({"UPLOADED", "OVERDUE"}),
    "OVERDUE": frozenset({"UPLOADED", "PAID"}),
    "UPLOADED": frozenset({"PAID"}),
    "PAID": frozenset(),  # terminal
}


class BillingMilestoneError(ValueError):
    """Validasi milestone ilegal (type/status tak dikenal, kurang dokumen) → 422."""


class BillingTransitionError(BillingMilestoneError):
    """Transisi status ilegal (PAID terminal, mundur) → endpoint maps ke 409."""


class BillingQuotationError(ValueError):
    """Quotation tidak dalam status ACCEPTED (F-10: billing hanya utk menang)."""


class BillingDuplicateError(BillingMilestoneError):
    """Milestone jenis yg sama sudah ada utk quotation tsb (imutabilitas) → 409."""


async def create_billing_milestone(
    session: AsyncSession,
    *,
    quotation: Quotation,
    milestone_type: str,
    due_date: date,
    amount: float | None = None,
    doc_no: str | None = None,
    actor_id: uuid.UUID,
) -> BillingMilestone:
    """Buat milestone dokumen dinas utk quotation ACCEPTED (F-10).

    Approval F-09 → status ACCEPTED = "menang" → milestone billing dibuat
    (SPK/NPWP/BAST/LPJ). Duplikat `(quotation_id, milestone_type)` → 409
    (mengikuti imutabilitas: tidak ada delete, pastikan 1 dokumen per jenis).
    """
    milestone_type = milestone_type.upper()
    if milestone_type not in MILESTONE_TYPES:
        raise BillingMilestoneError(
            f"milestone_type tidak dikenal: {milestone_type} (legal: {sorted(MILESTONE_TYPES)})"
        )
    if quotation.status != "ACCEPTED":
        raise BillingQuotationError(
            "Milestone billing hanya dibuat utk quotation ACCEPTED (F-10 alur menang → dokumen dinas)"
        )
    if amount is not None and amount < 0:
        raise BillingMilestoneError("amount tidak boleh negatif")
    dup = await session.scalar(
        select(BillingMilestone.id).where(
            BillingMilestone.quotation_id == quotation.id,
            BillingMilestone.milestone_type == milestone_type,
        )
    )
    if dup is not None:
        raise BillingDuplicateError(
            f"Milestone {milestone_type} utk quotation {quotation.quotation_no} sudah ada"
        )
    milestone = BillingMilestone(
        quotation_id=quotation.id,
        milestone_type=milestone_type,
        doc_no=doc_no,
        status="EXPECTED",
        amount=amount,
        due_date=due_date,
        updated_by=actor_id,
    )
    session.add(milestone)
    await session.flush()
    return milestone


async def update_billing_milestone(
    session: AsyncSession,
    milestone: BillingMilestone,
    *,
    status: str | None = None,
    doc_key: str | None = None,
    doc_no: str | None = None,
    paid_at: datetime | None = None,
    actor_id: uuid.UUID,
) -> BillingMilestone:
    """Update milestone: lampirkan dokumen (`doc_key`/`doc_no`) / tandai PAID.

    - Transisi ke `UPLOADED` wajib disertai `doc_key` (bukti lampiran nyata).
    - Transisi ke `PAID` otomatis mengisi `paid_at` (payload `paid_at` bila
      ada, default now) dan bersifat terminal (searah).
    - `doc_key`/`doc_no` bisa di-update di status EXPECTED/OVERDUE tanpa
      mengubah status (melampirkan lampiran saat belum selesai).
    """
    target = status.upper() if status else milestone.status
    if target not in MILESTONE_STATUSES:
        raise BillingMilestoneError(
            f"status tidak dikenal: {target} (legal: {sorted(MILESTONE_STATUSES)})"
        )
    if target != milestone.status:
        allowed = BILLING_TRANSITIONS.get(milestone.status, frozenset())
        if target not in allowed:
            raise BillingTransitionError(
                f"Transisi {milestone.status} → {target} tidak diizinkan (PAID terminal F-10)"
            )
    if target == "UPLOADED" and not (doc_key or milestone.doc_key):
        raise BillingMilestoneError("Transisi ke UPLOADED wajib menyertakan doc_key (bukti dokumen)")

    if doc_key is not None:
        milestone.doc_key = doc_key
    if doc_no is not None:
        milestone.doc_no = doc_no
    if target == "PAID":
        milestone.paid_at = paid_at or datetime.now(UTC)
    milestone.status = target
    milestone.updated_by = actor_id
    return milestone


# ─── Auto-reminder billing (F-10) ────────────────────────────────────────

def _billing_reminder_key(milestone: BillingMilestone) -> str:
    return f"bm:{milestone.uuid}:{milestone.due_date:%Y%m%d}"


async def _billing_reminder_exists(session: AsyncSession, milestone: BillingMilestone) -> bool:
    row = await session.scalar(
        text(
            "SELECT 1 FROM notifications "
            "WHERE entity_type='billing_milestone' AND entity_id=:mid AND type='BILLING_REMINDER' "
            "AND payload->>'reminder_key' = :rk LIMIT 1"
        ),
        {"mid": milestone.uuid, "rk": _billing_reminder_key(milestone)},
    )
    return bool(row)


async def resolve_billing_recipients(session: AsyncSession, hotel_id: uuid.UUID) -> dict[uuid.UUID, User]:
    """Finance + GM hotel quotation — penerima reminder tagihan dinas (F-10)."""
    rows = await session.execute(
        text(
            "SELECT DISTINCT u.id FROM users u "
            "JOIN user_roles ur ON ur.user_id = u.id "
            "JOIN roles r ON r.id = ur.role_id "
            "JOIN user_hotel_assignments uha ON uha.user_id = u.id "
            "WHERE r.code IN ('HOTEL_FINANCE','HOTEL_GM') "
            "AND u.is_active IS TRUE AND u.deleted_at IS NULL "
            "AND uha.hotel_id = :h AND uha.deleted_at IS NULL"
        ),
        {"h": hotel_id},
    )
    recipients: dict[uuid.UUID, User] = {}
    for uid, in rows:
        user = await session.get(User, uid)
        if user is not None:
            recipients[user.id] = user
    return recipients


async def remind_billing_milestones(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    days_before: int = 14,
    limit: int = 500,
) -> dict:
    """Sweep auto-reminder tagihan (F-10): milestone belum PAID dan jatuh tempo
    ≤ `now + days_before` → notifikasi `BILLING_REMINDER` ke finance+GM hotel.

    Termasuk yang **sudah lewat** (`OVERDUE`, status non-PAID) — risiko
    kebocoran dana tertinggi. Idempoten: dedup per `(milestone, due_date)`;
    due_date bergeser → reminder baru.
    """
    now = now or datetime.now(UTC)
    cutoff = now + timedelta(days=days_before)
    milestones = list(
        (
            await session.scalars(
                select(BillingMilestone)
                .where(
                    BillingMilestone.status != "PAID",
                    BillingMilestone.due_date <= cutoff.date(),
                )
                .order_by(BillingMilestone.due_date.asc())
                .limit(limit)
            )
        ).all()
    )

    reminded = refreshed = notifications = recipients_total = 0
    items: list[dict] = []
    for milestone in milestones:
        if await _billing_reminder_exists(session, milestone):
            refreshed += 1
            continue
        quotation = await session.get(Quotation, milestone.quotation_id)
        if quotation is None:
            continue
        hotel = await session.get(Hotel, quotation.hotel_id)
        recipients = await resolve_billing_recipients(session, quotation.hotel_id)
        if not recipients:
            continue
        context = {
            "quotation_no": quotation.quotation_no,
            "milestone_type": milestone.milestone_type,
            "doc_no": milestone.doc_no or "-",
            "amount": f"Rp{milestone.amount:,.0f}" if milestone.amount else "-",
            "due_date": milestone.due_date.isoformat(),
            "hotel_code": hotel.code if hotel else "-",
            "status": milestone.status,
            "reminder_key": _billing_reminder_key(milestone),
            "url": f"/crm/billing/milestones/{milestone.uuid}",
        }
        for recipient in recipients.values():
            rows = await create_notification(
                session,
                key="billing_reminder",
                user=recipient,
                context=context,
                entity_type="billing_milestone",
                entity_id=milestone.uuid,
            )
            notifications += len(rows)
        reminded += 1
        recipients_total += len(recipients)
        items.append(
            {
                "milestone_id": str(milestone.uuid),
                "quotation_no": quotation.quotation_no,
                "milestone_type": milestone.milestone_type,
                "due_date": milestone.due_date.isoformat(),
                "status": milestone.status,
                "recipients": len(recipients),
                "reminder_key": _billing_reminder_key(milestone),
            }
        )

    await session.commit()
    return {
        "scanned": len(milestones),
        "reminded": reminded,
        "refreshed_intact": refreshed,
        "recipients": recipients_total,
        "notifications": notifications,
        "items": items,
    }