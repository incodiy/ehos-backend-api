"""Notifikasi domain — template kanonikal, render multi-locale, inbox & reminder SLA.

Task 7e (PRD-F-03 / F-22, ERD §9 `notifications` + `translations` registry
`notification_template`):

- **Template kanonikal**: setiap `key` punya konten `title`/`body` bahasa id (kanonik).
  Terjemahan `en` (dan salinan kanonik) disimpan di tabel `translations`
  (entity_type=`notification_template`, entity_id=UUID v5 stabil dari key,
  field=`title`|`body`, locale=`id`|`en`) — content master di DB (Constraint H1).
  Fallback terakhir ke konstanta kode (F-22 graceful degradation; F3 locale-fallback
  tetap konten nyata dari DB, bukan mock).
- **Renderer**: interpolasi `{var}` dari payload; variabel yang hilang dibiarkan
  apa adanya (jujur, tidak menelan data).
- **create_notification**: 1 baris per channel — PUSH (inbox selalu), EMAIL
  (selalu, user punya email), WA (hanya bila user.phone terisi). Semua QUEUED;
  pengiriman nyata oleh `app.services.delivery` (sweep worker, SYSTEM §5.2).
- **remind_sla**: sweep reminder SLA → tipe CAPA_SLA (dedup via payload
  `reminder_key` = `{receipt}:{bucket-jam-due}`).

Payload notifikasi menyimpan snapshot: `template_key`, `locale`, `title`, `body`
(render hasil), plus konteks (`receipt_id`, `priority`, `sla_hours`, `due_at`,
`escalation_level`, `origin`, `status`, `hotel_id`, `url`, dst) — inilah "kolom
kanonikal" yang dibaca inbox & dipakai delivery.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CapaTicket, Notification, User
from app.models.cross import Translation
from app.services.sla_escalation import resolve_escalation_receivers

# ─── Template registry (kanonik id; en di DB translations) ─────────────────

NOTIFICATION_TEMPLATES: dict[str, dict[str, dict[str, str]]] = {
    "capa_escalate": {
        "title": {
            "id": "CAPA Tereskalasi: {receipt_id}",
            "en": "CAPA Escalated: {receipt_id}",
        },
        "body": {
            "id": (
                "Tiket {receipt_id} — “{ticket_title}” (prioritas {priority}) "
                "melampaui SLA dan tereskalasi ke level {escalation_level}. "
                "Batas waktu: {due_at_fmt}. {url}"
            ),
            "en": (
                "Ticket {receipt_id} — “{ticket_title}” (priority {priority}) "
                "exceeded SLA and escalated to level {escalation_level}. "
                "Due: {due_at_fmt}. {url}"
            ),
        },
    },
    "capa_sla_reminder": {
        "title": {
            "id": "Reminder SLA: {receipt_id}",
            "en": "SLA Reminder: {receipt_id}",
        },
        "body": {
            "id": (
                "Tiket {receipt_id} — “{ticket_title}” jatuh tempo {due_at_fmt} "
                "(kurang dari {window_hours} jam). Prioritaskan penyelesaian. {url}"
            ),
            "en": (
                "Ticket {receipt_id} — “{ticket_title}” is due {due_at_fmt} "
                "(less than {window_hours} hours left). Prioritize completion. {url}"
            ),
        },
    },
    "crm_followup": {
        "title": {
            "id": "Follow-up CRM: {lead_no}",
            "en": "CRM Follow-up: {lead_no}",
        },
        "body": {
            "id": (
                "Lead {lead_no} — “{company_name}” terjadwal follow-up "
                "tanggal {followup_at_fmt}. Progres: {status}. {url}"
            ),
            "en": (
                "Lead {lead_no} — “{company_name}” follow-up is due "
                "on {followup_at_fmt}. Pipeline stage: {status}. {url}"
            ),
        },
    },
    "crm_referral": {
        "title": {
            "id": "Lead Referral Diterima: {lead_no}",
            "en": "Cross-Property Referral Received: {lead_no}",
        },
        "body": {
            "id": (
                "Lead {lead_no} — “{company_name}” direfer dari {from_hotel_code} "
                "ke {to_hotel_code}. Sekarang milik unit Anda — kelola & tindak lanjuti. {url}"
            ),
            "en": (
                "Lead {lead_no} — “{company_name}” was referred from "
                "{from_hotel_code} to {to_hotel_code}. It now belongs to your unit — "
                "please manage and follow up. {url}"
            ),
        },
    },
    "rfp_new": {
        "title": {
            "id": "RFP Baru: {ref_no}",
            "en": "New RFP: {ref_no}",
        },
        "body": {
            "id": (
                "RFP {ref_no} — “{company_name}” ({event_date}, {pax} pax, {package_type}) "
                "masuk via form publik dan menjadi lead CRM Anda. Segera tindak lanjuti. {url}"
            ),
            "en": (
                "RFP {ref_no} — “{company_name}” ({event_date}, {pax} pax, {package_type}) "
                "came in via the public form and is now your CRM lead. Please follow up soon. {url}"
            ),
        },
    },
    "billing_reminder": {
        "title": {
            "id": "Reminder Tagihan Dinas: {quotation_no} ({milestone_type})",
            "en": "GoV Billing Reminder: {quotation_no} ({milestone_type})",
        },
        "body": {
            "id": (
                "Milestone {milestone_type} utk quotation {quotation_no} ({hotel_code}) "
                "jatuh tempo {due_date} — {status}. Nilai {amount}. "
                "Tindak lanjuti sebelum tutup tahun anggaran. {url}"
            ),
            "en": (
                "Milestone {milestone_type} for quotation {quotation_no} ({hotel_code}) "
                "is due {due_date} — {status}. Amount {amount}. "
                "Follow up before fiscal year closing. {url}"
            ),
        },
    },
}

TEMPLATE_TYPE: dict[str, str] = {
    "capa_escalate": "CAPA_ESCALATE",
    "capa_sla_reminder": "CAPA_SLA",
    "crm_followup": "FOLLOWUP_CRM",
    "crm_referral": "CRM_REFERRAL",
    "rfp_new": "RFP_INTAKE",
    "billing_reminder": "BILLING_REMINDER",
}

# Daftar type notification yang didefinisikan ERD §9 (digunakan inbox/labeling).
KNOWN_TYPES = {"CAPA_SLA", "CAPA_ESCALATE", "FOLLOWUP_CRM", "CRM_REFERRAL", "RFP_INTAKE", "BILLING_REMINDER", "REPORT"}

REMINDEABLE_STATUSES = (
    "OPEN",
    "IN_PROGRESS",
    "ASSIGNED",
    "AWAITING_HOD",
    "AWAITING_GM",
    "AWAITING_QA",
    "REJECTED",
    "REOPENED",
)


def template_uuid(key: str) -> uuid.UUID:
    """UUID v5 stabil untuk key template — dipakai sbg entity_id translations."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"ehos:notification_template:{key}")


class _SafeFormat(dict):
    """dict yang tidak melempar KeyError — variabel hilang tetap sebagai '{var}'."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_text(template: str, context: dict) -> str:
    ctx = _SafeFormat((k, str(v)) for k, v in (context or {}).items())
    return template.format_map(ctx)


def _template_default(key: str, field: str, locale: str) -> str:
    entry = NOTIFICATION_TEMPLATES[key][field]
    return entry.get(locale) or entry["id"]


async def load_template_locales(session: AsyncSession, key: str) -> dict[tuple[str, str], str]:
    tid = template_uuid(key)
    rows = (
        await session.scalars(
            select(Translation).where(
                Translation.entity_type == "notification_template",
                Translation.entity_id == tid,
                Translation.field.in_(["title", "body"]),
            )
        )
    ).all()
    return {(r.field, r.locale): r.value for r in rows}


async def render_notification(
    session: AsyncSession,
    key: str,
    context: dict,
    locale: str = "id",
) -> dict[str, str]:
    """Render title/body utk notifikasi — terjemahan DB (default id) → fallback kode."""
    overrides = await load_template_locales(session, key)

    def _field(field: str) -> str:
        canonical = (
            overrides.get((field, locale)) or overrides.get((field, "id")) or _template_default(key, field, locale)
        )
        return render_text(canonical, context)

    return {"title": _field("title"), "body": _field("body")}


def ticket_context(
    ticket: CapaTicket,
    *,
    escalation_level: int | None = None,
    window_hours: int | None = None,
    reminder_key: str | None = None,
) -> dict:
    ctx = {
        "receipt_id": ticket.receipt_id or "",
        "ticket_title": ticket.title,
        "title": ticket.title,
        "priority": ticket.priority,
        "sla_hours": ticket.sla_hours,
        "due_at": ticket.due_at.isoformat() if ticket.due_at else None,
        "escalation_level": ticket.escalation_level if escalation_level is None else escalation_level,
        "origin": ticket.origin,
        "status": ticket.status,
        "hotel_id": str(ticket.hotel.uuid) if ticket.hotel_id else None,
        "url": f"/capa/tickets/{ticket.uuid}",
    }
    if window_hours is not None:
        ctx["window_hours"] = window_hours
    if reminder_key is not None:
        ctx["reminder_key"] = reminder_key
    return ctx


def human_due(dt: datetime) -> str:
    return dt.strftime("%d %b %Y %H:%M")


async def create_notification(
    session: AsyncSession,
    *,
    key: str,
    user: User,
    context: dict,
    entity_type: str,
    entity_id: uuid.UUID,
    locale: str | None = None,
) -> list[Notification]:
    """Buat Notification per channel (PUSH selalu, EMAIL selalu, WA bila user.phone)."""
    locale = locale or user.preferred_locale or "id"
    rendered = await render_notification(session, key, context, locale)
    channels = ["PUSH", "EMAIL"] + (["WA"] if user.phone else [])
    rows: list[Notification] = []
    for channel in channels:
        payload = {**context, "template_key": key, "locale": locale, **rendered}
        notify = Notification(
            user_id=user.id,
            type=TEMPLATE_TYPE[key],
            channel=channel,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
            status="QUEUED",
        )
        session.add(notify)
        rows.append(notify)
    return rows


async def _reminder_exists(session: AsyncSession, ticket_id: uuid.UUID, reminder_key: str) -> bool:
    row = await session.scalar(
        text(
            "SELECT 1 FROM notifications "
            "WHERE entity_type='capa_ticket' AND entity_id=:tid AND type='CAPA_SLA' "
            "AND payload->>'reminder_key' = :rk LIMIT 1"
        ),
        {"tid": ticket_id, "rk": reminder_key},
    )
    return bool(row)


async def remind_sla(
    session: AsyncSession,
    actor_id: uuid.UUID | None = None,
    now: datetime | None = None,
    window_hours: int = 24,
    limit: int = 500,
) -> dict:
    """Sweep reminder SLA: tiket terbuka dgn due_at dalam [now, now+window].

    Idempoten — dedup per `reminder_key` (bucket jam due). Recipient: assignee
    (jika ada) + tier receiver level saat ini (GM→ROM→VP, skala tereskalasi).
    Dipanggil worker/scheduler (SYSTEM §5.2).
    """
    now = now or datetime.now(UTC)
    cutoff = now + timedelta(hours=window_hours)
    tickets = list(
        (
            await session.scalars(
                select(CapaTicket)
                .where(
                    CapaTicket.status.in_(REMINDEABLE_STATUSES),
                    CapaTicket.due_at.is_not(None),
                    CapaTicket.due_at >= now,
                    CapaTicket.due_at <= cutoff,
                )
                .order_by(CapaTicket.due_at.asc())
                .limit(limit)
            )
        ).all()
    )

    reminded = refreshed = notifications = recipients_total = 0
    items: list[dict] = []
    for ticket in tickets:
        reminder_key = f"{ticket.receipt_id}:{ticket.due_at:%Y%m%d%H}"
        if await _reminder_exists(session, ticket.uuid, reminder_key):
            refreshed += 1
            continue

        recipients: dict[uuid.UUID, User] = {}
        if ticket.assigned_to:
            assignee = await session.get(User, ticket.assigned_to)
            if assignee is not None and assignee.is_active:
                recipients[assignee.id] = assignee
        tier_users = await resolve_escalation_receivers(session, ticket, max(ticket.escalation_level or 1, 1))
        for u in tier_users:
            if u.is_active:
                recipients[u.id] = u
        if not recipients:
            continue

        context = ticket_context(
            ticket,
            escalation_level=ticket.escalation_level,
            window_hours=window_hours,
            reminder_key=reminder_key,
        )
        context["due_at_fmt"] = human_due(ticket.due_at)
        for recipient in recipients.values():
            rows = await create_notification(
                session,
                key="capa_sla_reminder",
                user=recipient,
                context=context,
                entity_type="capa_ticket",
                entity_id=ticket.uuid,
            )
            notifications += len(rows)
        reminded += 1
        recipients_total += len(recipients)
        items.append(
            {
                "ticket_id": str(ticket.uuid),
                "receipt_id": ticket.receipt_id,
                "status": ticket.status,
                "reminder_key": reminder_key,
                "recipients": len(recipients),
            }
        )

    await session.commit()
    return {
        "scanned": len(tickets),
        "reminded": reminded,
        "refreshed_intact": refreshed,
        "recipients": recipients_total,
        "notifications": notifications,
        "items": items,
    }


def inbox_item(notification: Notification) -> dict:
    payload = notification.payload or {}
    body = payload.get("body") or payload.get("note") or "No content"
    title = payload.get("title") or notification.type
    return {
        "id": str(notification.uuid),
        "type": notification.type,
        "channel": notification.channel,
        "title": title,
        "body": body,
        "url": payload.get("url"),
        "template_key": payload.get("template_key"),
        "entity_type": notification.entity_type,
        "entity_id": str(notification.entity_id) if notification.entity_id else None,
        "read_at": notification.read_at.isoformat() if notification.read_at else None,
        "created_at": notification.created_at.isoformat() if notification.created_at else None,
    }


async def list_user_notifications(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    unread_only: bool = False,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict], dict]:
    """Inbox user — data nyata dari notifications (G2/G4), terbaru di atas."""
    page = max(page or 1, 1)
    per_page = min(max(per_page or 50, 1), 100)
    filters = [Notification.user_id == user_id]
    if unread_only:
        filters.append(Notification.read_at.is_(None))
    total = (await session.scalar(select(func.count()).select_from(Notification).where(*filters))) or 0
    rows = list(
        (
            await session.scalars(
                select(Notification)
                .where(*filters)
                .order_by(Notification.created_at.desc())
                .offset((page - 1) * per_page)
                .limit(per_page)
            )
        ).all()
    )
    items = [inbox_item(n) for n in rows]
    last_page = max((total + per_page - 1) // per_page, 1) if total else 1
    meta = {
        "current_page": page,
        "per_page": per_page,
        "total": total,
        "last_page": last_page,
    }
    return items, meta
