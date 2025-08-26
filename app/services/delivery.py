"""Delivery notifikasi — Email (SMTP aiosmtplib) & WhatsApp API, + sweep worker.

Task 7e / SYSTEM §5.2: worker menyalurkan notifikasi QUEUED (channel EMAIL/WA)
menjadi SENT (sent_at) dengan status jujur:

- Provider tidak dikonfigurasi (EHOS_SMTP_HOST / EHOS_WA_API_URL kosong di dev)
  → `ProviderNotConfiguredError` → NOTIF tetap QUEUED dgn `attempts` bertambah
  (retry backoff) dan `last_error` ter-record — TIDAK pernah SENT palsu (Constraint
  G4: error ≠ fake data).
- Gagal ≥ `MAX_ATTEMPTS` → status FAILED (terminal, jujur). Sisa tetap QUEUED
  utk sweep berikutnya.
- PUSH (inbox) tidak dikirim via provider — dibaca client (polling inbox).

Sweep dipanggil worker RQ / endpoint `POST /notifications/sweep` (ROOT/CORP_EXEC).
"""

from __future__ import annotations

from datetime import UTC, datetime
from email.message import EmailMessage

import aiosmtplib
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Notification, User

MAX_ATTEMPTS = 3
DELIVERY_CHANNELS = ("EMAIL", "WA")


class DeliveryError(RuntimeError):
    """Kegagalan pengiriman provider (SMTP/WA)."""


class ProviderNotConfiguredError(DeliveryError):
    """Provider belum dikonfigurasi (dev) — pengiriman tidak mungkin dilakukan."""


async def send_email(to: str, subject: str, body: str) -> None:
    """Kirim email via SMTP (aiosmtplib). Naikkan DeliveryError bila gagal."""
    if not settings.smtp_host:
        raise ProviderNotConfiguredError("EHOS_SMTP_HOST belum dikonfigurasi")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from
    message["To"] = to
    message.set_content(body)
    try:
        await aiosmtplib.send(
            message,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username or None,
            password=settings.smtp_password or None,
            start_tls=bool(settings.smtp_username),
        )
    except Exception as exc:  # noqa: BLE001 — naikkan jenis error provider
        raise DeliveryError(f"SMTP: {exc}") from exc


async def send_whatsapp(phone: str, message: str) -> None:
    """Kirim WA text via HTTP API. Naikkan DeliveryError bila gagal."""
    if not settings.wa_api_url:
        raise ProviderNotConfiguredError("EHOS_WA_API_URL belum dikonfigurasi")
    headers = {"Authorization": f"Bearer {settings.wa_token}"} if settings.wa_token else {}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                settings.wa_api_url,
                headers=headers,
                json={"to": phone, "type": "text", "text": {"body": message}},
            )
            response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise DeliveryError(f"WhatsApp: {exc}") from exc


async def _deliver_one(notification: Notification, user: User) -> str:
    """Kirim benar-benar utk satu notif. Return status: SENT / QUEUED / FAILED."""
    payload = dict(notification.payload or {})
    attempts = int(payload.get("attempts", 0)) + 1
    payload["attempts"] = attempts

    title = payload.get("title") or notification.type
    body = payload.get("body") or payload.get("note") or ""
    try:
        if notification.channel == "EMAIL":
            await send_email(user.email, title, body)
        elif notification.channel == "WA":
            message = f"{title}\n\n{body}" if body else title
            await send_whatsapp(user.phone, message)
        else:
            raise DeliveryError(f"channel PUSH tidak via provider: {notification.channel}")
        payload.pop("last_error", None)
        notification.payload = payload
        notification.status = "SENT"
        notification.sent_at = datetime.now(UTC)
        return "SENT"
    except (DeliveryError, Exception) as exc:  # noqa: BLE001 — retry jujur
        payload["last_error"] = str(exc)[:500]
        notification.payload = payload
        if attempts >= MAX_ATTEMPTS:
            notification.status = "FAILED"
            return "FAILED"
        notification.status = "QUEUED"
        return "QUEUED"


async def run_delivery_sweep(session: AsyncSession, limit: int = 200) -> dict:
    """Sweep QUEUED (EMAIL/WA) → kirim + set SENT/FAILED/retry backoff."""
    limit = max(1, limit)
    rows = list(
        (
            await session.scalars(
                select(Notification)
                .where(
                    Notification.status == "QUEUED",
                    Notification.channel.in_(DELIVERY_CHANNELS),
                )
                .order_by(Notification.created_at.asc())
                .limit(limit)
            )
        ).all()
    )

    users: dict = {}
    sent = retry = failed = scanned = 0
    items: list[dict] = []
    for notification in rows:
        scanned += 1
        user = users.get(notification.user_id)
        if user is None:
            user = await session.get(User, notification.user_id)
            users[notification.user_id] = user
        if user is None or not user.is_active:
            notification.status = "FAILED"
            failed += 1
            items.append({"id": str(notification.id), "result": "FAILED", "error": "User tidak aktif"})
            continue
        result = await _deliver_one(notification, user)
        if result == "SENT":
            sent += 1
        elif result == "FAILED":
            failed += 1
        else:
            retry += 1
        items.append(
            {
                "id": str(notification.id),
                "channel": notification.channel,
                "result": result,
                "attempts": (notification.payload or {}).get("attempts", 1),
            }
        )
    await session.commit()
    return {
        "scanned": scanned,
        "sent": sent,
        "retry_backoff": retry,
        "failed": failed,
        "items": items,
    }
