"""CRM kanban pipeline service — PRD-F-07 (task 8a).

Status machine LEAD → CONTACTED → PROSPECT → CONFIRMED, dan sink menuju LOST
(terminal). Fitur wajib F-07:

- **Mandatory Lost Reason**: transisi ke `LOST` tanpa `lost_reason` → `MissingLostReasonError`
  (endpoint memetakan ke 422 sesuai openapi swagger: "lost_reason kosong saat LOST").
- **Follow-up reminder otomatis**: `crm_followup_reminders()` sweep lead dengan
  `next_followup_at` yang sudah lewat (non-terminal) → notifikasi `FOLLOWUP_CRM`
  ke owner (sales) via `create_notification` (idempoten, dedup per `reminder_key`).
- **Aktivitas kanban**: setiap transisi status di-log sebagai `LeadActivity`
  (type=NOTE) — jejak audit pipeline war room.

Lead selalu bertanda `hotel_id` (tenant isolation Golden Rule 3); endpoint
mem-filter scope; service murni mengelola entity + aturan bisnis.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Lead, LeadActivity, LeadReferral, User
from app.models.master import Hotel
from app.services.notifications import create_notification, human_due

LEAD_STATUSES = frozenset({"LEAD", "CONTACTED", "PROSPECT", "CONFIRMED", "LOST"})
TERMINAL_STATUSES = frozenset({"LOST"})
NO_FOLLOWUP_STATUSES = frozenset({"CONFIRMED", "LOST"})

# Kanban transitions (skip-forward diperbolehkan; LOST terminal).
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "LEAD": frozenset({"CONTACTED", "PROSPECT", "CONFIRMED", "LOST"}),
    "CONTACTED": frozenset({"PROSPECT", "CONFIRMED", "LOST"}),
    "PROSPECT": frozenset({"CONFIRMED", "LOST"}),
    "CONFIRMED": frozenset({"LOST"}),
    "LOST": frozenset(),  # terminal — tidak keluar (batal lewat status lain)
}


class InvalidTransitionError(ValueError):
    """Transisi status kanban ilegal → endpoint maps ke 409."""


class MissingLostReasonError(ValueError):
    """F-07: status=LOST wajib berisi lost_reason → endpoint maps ke 422."""


class ReferralError(ValueError):
    """Cross-property referral gagal (F-08) → endpoint maps ke 409."""


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES


def followup_due(lead: Lead, now: datetime | None = None) -> bool:
    """Lead non-terminal dgn jadwal follow-up yang sudah lewat → due reminder."""
    if lead.status in NO_FOLLOWUP_STATUSES or lead.next_followup_at is None:
        return False
    return lead.next_followup_at <= (now or datetime.now(UTC))


async def _log_activity(
    session: AsyncSession,
    lead: Lead,
    *,
    activity_type: str,
    note: str | None,
    actor_id: int,
    at: datetime | None = None,
) -> LeadActivity:
    activity = LeadActivity(
        lead_id=lead.id,
        type=activity_type,
        note=note,
        at=at or datetime.now(UTC),
        actor_id=actor_id,
    )
    session.add(activity)
    return activity


async def create_lead(
    session: AsyncSession,
    *,
    lead_no: str,
    hotel_id: int,
    source: str,
    institution_type: str,
    company_name: str,
    owner_id: int,
    pic_name: str | None = None,
    pic_phone: str | None = None,
    pic_email: str | None = None,
    province_id: int | None = None,
    amount_est: float | None = None,
    next_followup_at: datetime | None = None,
    created_by: int | None = None,
) -> Lead:
    lead = Lead(
        lead_no=lead_no,
        hotel_id=hotel_id,
        source=source,
        institution_type=institution_type,
        company_name=company_name,
        pic_name=pic_name,
        pic_phone=pic_phone,
        pic_email=pic_email,
        province_id=province_id,
        status="LEAD",
        next_followup_at=next_followup_at,
        amount_est=amount_est,
        owner_id=owner_id,
        created_by=created_by,
        updated_by=created_by,
    )
    session.add(lead)
    await session.flush()
    await _log_activity(
        session,
        lead,
        activity_type="NOTE",
        note=f"Lead dibuat (sumber {source})",
        actor_id=created_by or owner_id,
        at=lead.created_at or datetime.now(UTC),
    )
    return lead


async def change_status(
    session: AsyncSession,
    lead: Lead,
    *,
    status: str,
    lost_reason: str | None,
    actor_id: int,
) -> Lead:
    """Kanban transition + mandatory lost_reason (F-07).

    `status` sudah validasi pydantic (Literal); di sini guard legal transisi.
    """
    status = status.upper()
    if status == lead.status:
        # no-op / rekonfirmasi kolom pelengkap — tidak menulis history ganda.
        return lead
    allowed = ALLOWED_TRANSITIONS.get(lead.status, frozenset())
    if status not in allowed:
        raise InvalidTransitionError(
            f"Transisi {lead.status} → {status} tidak diizinkan kanban CRM"
        )
    if status == "LOST":
        if not (lost_reason or "").strip():
            raise MissingLostReasonError("lost_reason wajib diisi saat status=LOST (F-07)")
        lead.lost_reason = lost_reason.strip()
        lead.next_followup_at = None
    elif status == "CONFIRMED":
        lead.lost_reason = None
    lead.status = status
    lead.updated_by = actor_id
    await _log_activity(
        session,
        lead,
        activity_type="NOTE",
        note=f"Status kanban → {status}"
        + (f" (alasan: {lead.lost_reason})" if status == "LOST" and lead.lost_reason else ""),
        actor_id=actor_id,
    )
    return lead


async def add_activity(
    session: AsyncSession,
    lead: Lead,
    *,
    activity_type: str,
    note: str | None,
    actor_id: int,
    next_followup_at: datetime | None = None,
) -> LeadActivity:
    """Catat aktivitas follow-up (CALL/EMAIL/MEETING/NOTE) + set jadwal berikutnya.

    `next_followup_at` pada respons mencerminkan jadwal terbaru lead.
    """
    if lead.status == "LOST":
        raise InvalidTransitionError("Lead LOST terminal — tidak boleh aktivitas lanjutan")
    activity = await _log_activity(
        session,
        lead,
        activity_type=activity_type,
        note=note,
        actor_id=actor_id,
    )
    if next_followup_at is not None:
        lead.next_followup_at = next_followup_at
        lead.updated_by = actor_id
    return activity


# ─── Cross-property referral (F-08) ─────────────────────────────────────

REFERRED_STATUS = "REFERRED"


async def resolve_crm_owner(
    session: AsyncSession,
    hotel_id: int,
) -> User | None:
    """Owner aktif utk hotel tujuan — prefer HOTEL_SALES, fallback GM hotel.

    Keduanya punya `crm:manage` (seed rbac); filter user aktif + assignment
    hotel belum di-soft-delete. Dipakai saat referral memindah kepemilikan lead.
    """
    sales_id = await session.scalar(
        text(
            "SELECT u.id FROM users u "
            "JOIN user_roles ur ON ur.user_id = u.id "
            "JOIN roles r ON r.id = ur.role_id "
            "WHERE r.code='HOTEL_SALES' AND u.is_active IS TRUE AND u.deleted_at IS NULL "
            "AND u.id IN (SELECT uha.user_id FROM user_hotel_assignments uha "
            "             WHERE uha.hotel_id=:h AND uha.deleted_at IS NULL) "
            "ORDER BY u.created_at ASC LIMIT 1"
        ),
        {"h": hotel_id},
    )
    if sales_id is not None:
        return await session.get(User, sales_id)
    gm_id = await session.scalar(
        text(
            "SELECT h.gm_id FROM hotels h JOIN users u ON u.id = h.gm_id "
            "WHERE h.id=:h AND u.is_active IS TRUE AND u.deleted_at IS NULL "
            "AND EXISTS (SELECT 1 FROM user_roles ur "
            "            JOIN roles_permissions rp ON rp.role_id = ur.role_id "
            "            JOIN permissions p ON p.id = rp.permission_id "
            "            WHERE ur.user_id = u.id AND p.code='crm:manage')"
        ),
        {"h": hotel_id},
    )
    if gm_id is not None:
        return await session.get(User, gm_id)
    return None


async def refer_cross_property(
    session: AsyncSession,
    lead: Lead,
    *,
    to_hotel_id: int,
    commission_amount: float | None,
    note: str | None,
    actor_id: int,
    target: Hotel | None = None,
) -> tuple[LeadReferral, User]:
    """Refer cross-property (F-08): hotelT1 menyerahkan lead → hotelT2.

    Jalur legal antar unit (Golden Rule 3 pengecualian): lead DIPINDAH ke
    hotel tujuan (`hotel_id`), ditandai `source=REFERRAL` + `referred_from_hotel_id`
    asal untuk tracking komisi. Record `lead_referrals` immutable (jumlah komisi
    tidak berubah); transfer kepemilikan ke sales aktif hotel tujuan. Single-hop —
    lead hasil referral tidak bisa di-refer ulang (mencegah rantai komisi tak jelas).
    """
    if is_terminal(lead.status):
        raise ReferralError("Lead LOST terminal — tidak bisa di-refer")
    if lead.source == "REFERRAL":
        raise ReferralError("Lead sudah hasil referral (single-hop F-08) — tidak bisa di-refer ulang")
    if to_hotel_id == lead.hotel_id:
        raise ReferralError("Tujuan referral harus hotel lain (cross-property)")
    if commission_amount is not None and commission_amount < 0:
        raise ReferralError("commission_amount tidak boleh negatif")

    target = target or await session.get(Hotel, to_hotel_id)
    if target is None:
        raise ReferralError("Hotel tujuan tidak ditemukan")
    from_hotel = await session.get(Hotel, lead.hotel_id)
    new_owner = await resolve_crm_owner(session, to_hotel_id)
    if new_owner is None:
        raise ReferralError("Hotel tujuan belum punya sales/GM aktif untuk menerima lead")

    commission = (
        f" komisi Rp{commission_amount:,.0f}" if commission_amount else " tanpa komisi"
    )
    referral = LeadReferral(
        lead_id=lead.id,
        from_hotel_id=lead.hotel_id,
        to_hotel_id=to_hotel_id,
        commission_amount=commission_amount,
        status=REFERRED_STATUS,
        note=note,
    )
    session.add(referral)
    await _log_activity(
        session,
        lead,
        activity_type="NOTE",
        note=f"Direfer ke {target.code} ({commission}) — kepemilikan pindah",
        actor_id=actor_id,
    )
    lead.hotel_id = to_hotel_id
    lead.source = "REFERRAL"
    lead.referred_from_hotel_id = from_hotel.id
    lead.owner_id = new_owner.id
    lead.updated_by = actor_id
    await _log_activity(
        session,
        lead,
        activity_type="NOTE",
        note=f"Diterima via referral dari {from_hotel.code}",
        actor_id=actor_id,
    )
    return referral, new_owner


# ─── Follow-up reminder sweep (F-07) ────────────────────────────────────

def lead_context(lead: Lead) -> dict:
    return {
        "lead_no": lead.lead_no,
        "company_name": lead.company_name,
        "status": lead.status,
        "followup_at": lead.next_followup_at.isoformat() if lead.next_followup_at else None,
        "url": f"/crm/leads/{lead.uuid}",
    }


async def _followup_reminder_exists(session: AsyncSession, lead: Lead, reminder_key: str) -> bool:
    row = await session.scalar(
        text(
            "SELECT 1 FROM notifications "
            "WHERE entity_type='lead' AND entity_id=:lid AND type='FOLLOWUP_CRM' "
            "AND payload->>'reminder_key' = :rk LIMIT 1"
        ),
        {"lid": lead.uuid, "rk": reminder_key},
    )
    return bool(row)


async def crm_followup_reminders(
    session: AsyncSession,
    now: datetime | None = None,
    limit: int = 500,
) -> dict:
    """Sweep reminder follow-up: lead non-terminal dgn `next_followup_at <= now`.

    Idempoten — dedup per `reminder_key` (`{lead_no}:{due:%Y%m%d%H}`); jadwal
    berubah → key baru → reminder baru. Recipient: `owner_id` (sales lead).
    Dipanggil worker/scheduler dan runner seeder (awal jadwal pasti terkirim).
    """
    now = now or datetime.now(UTC)
    leads = list(
        (
            await session.scalars(
                select(Lead)
                .where(
                    Lead.status.not_in(NO_FOLLOWUP_STATUSES),
                    Lead.next_followup_at.is_not(None),
                    Lead.next_followup_at <= now,
                )
                .order_by(Lead.next_followup_at.asc())
                .limit(limit)
            )
        ).all()
    )

    reminded = refreshed = notifications = 0
    items: list[dict] = []
    for lead in leads:
        reminder_key = f"{lead.lead_no}:{lead.next_followup_at:%Y%m%d%H}"
        if await _followup_reminder_exists(session, lead, reminder_key):
            refreshed += 1
            continue
        owner = await session.get(User, lead.owner_id)
        if owner is None or not owner.is_active:
            continue
        context = lead_context(lead)
        context["followup_at_fmt"] = human_due(lead.next_followup_at)
        context["reminder_key"] = reminder_key
        rows = await create_notification(
            session,
            key="crm_followup",
            user=owner,
            context=context,
            entity_type="lead",
            entity_id=lead.uuid,
        )
        notifications += len(rows)
        reminded += 1
        items.append(
            {
                "lead_id": str(lead.uuid),
                "lead_no": lead.lead_no,
                "status": lead.status,
                "reminder_key": reminder_key,
                "recipient": str(owner.uuid),
            }
        )

    await session.commit()
    return {
        "scanned": len(leads),
        "reminded": reminded,
        "refreshed_intact": refreshed,
        "notifications": notifications,
        "items": items,
    }


# ─── Lead number generator ───────────────────────────────────────────────

LEAD_NO_PREFIXES = {
    "RFP_PORTAL": "RFP",
    "CROSS_SELLING": "XSELL",
    "REFERRAL": "REF",
    "MANUAL": "MAN",
}


def generate_lead_no(hotel_code: str, source: str) -> str:
    prefix = LEAD_NO_PREFIXES.get(source, "L")
    stamp = datetime.now(UTC).strftime("%y%m%d")
    suffix = uuid.uuid4().hex[:5].upper()
    return f"{prefix}-{hotel_code}-{stamp}-{suffix}"