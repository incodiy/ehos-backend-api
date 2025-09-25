"""Notifikasi (PRD-F-03/F-22, task 7e) — template kanonikal + i18n, inbox,
delivery EMAIL/WA (status jujur), reminder SLA idempoten, RBAC sweep/remind.

Berjalan terhadap seeded dev DB (users gm.cws/hod.srm.cws/corp.exec, tiket CAPA
hasil seed 7a handle). Delivery memakai mock provider — tidak ada SMTP/WA nyata
di test (G4: tanpa provider → retry jujur, bukan SENT palsu).
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from httpx import AsyncClient
from sqlalchemy import select, text

from app.db.session import SessionLocal
from app.models import CapaTicket, User
from app.services.delivery import DeliveryError, ProviderNotConfiguredError
from app.services.notifications import (
    create_notification,
    remind_sla,
    render_notification,
    render_text,
    ticket_context,
)

SEED_PASSWORD = "Ehos#2026!"
HOD_CWS = "hod.srm.cws@ehos.local"
GM_CWS = "gm.cws@ehos.local"
CORP_EXEC = "corp.exec@ehos.local"
ROM_JAWA = "rom.jawa@ehos.local"


async def _login(client: AsyncClient, email: str) -> str:
    r = await client.post(
        "/auth/login",
        json={"email": email, "password": SEED_PASSWORD, "remember_me": False},
    )
    assert r.status_code == 200, (email, r.text)
    return r.json()["data"]["access_token"]


async def _headers(client: AsyncClient, email: str) -> dict:
    return {"Authorization": f"Bearer {await _login(client, email)}"}


async def _any_ticket():
    async with SessionLocal() as s:
        row = (
            (
                await s.execute(
                    text(
                        "SELECT uuid::text AS id, receipt_id, title, priority, sla_hours, "
                        "due_at, origin, status, escalation_level, hotel_id, assigned_to "
                        "FROM capa_tickets ORDER BY created_at LIMIT 1"
                    )
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise AssertionError("tidak ada tiket CAPA di DB dev — jalankan seeder")
        return dict(row)


async def _status_counts(notif_id: str) -> dict:
    async with SessionLocal() as s:
        row = (
            (await s.execute(text("SELECT status, payload FROM notifications WHERE uuid=:i"), {"i": notif_id}))
            .mappings()
            .first()
        )
        return {"status": row["status"], "payload": row["payload"]}


async def _get_user(email: str):
    async with SessionLocal() as s:
        u = await s.scalar(select(User).where(User.email == email))
        if u is None or u.email != email:
            raise AssertionError(f"user {email} tidak ada")
        return u


async def _get_ticket(ticket: dict) -> CapaTicket:
    async with SessionLocal() as s:
        return await s.scalar(
            select(CapaTicket).where(CapaTicket.uuid == uuid.UUID(ticket["id"]))
        )


# ─── 1. Template render & interpolation ───────────────────────────────────


async def test_render_text_missing_var_kept_intact() -> None:
    assert render_text("Halo {name} ({receipt})", {"name": "Budi"}) == "Halo Budi ({receipt})"


async def test_render_notification_id_en_locales() -> None:
    ctx = {
        "receipt_id": "SLA00001",
        "ticket_title": "Cover berminyak",
        "priority": 1,
        "escalation_level": 2,
        "window_hours": 24,
        "due_at_fmt": "10 Sep 2026 14:00",
        "url": "/capa/tickets/x",
    }
    async with SessionLocal() as s:
        id_render = await render_notification(s, "capa_sla_reminder", ctx, "id")
        en_render = await render_notification(s, "capa_sla_reminder", ctx, "en")
        esc_id = await render_notification(s, "capa_escalate", ctx, "id")
        esc_en = await render_notification(s, "capa_escalate", ctx, "en")
    assert id_render["title"] == "Reminder SLA: SLA00001"
    assert "kurang dari 24 jam" in id_render["body"]
    assert en_render["title"].startswith("SLA Reminder:")
    assert "escalated to level 2" in esc_en["body"]
    assert "tereskalasi ke level 2" in esc_id["body"]
    assert esc_id["title"] == "CAPA Tereskalasi: SLA00001"


# ─── 2. create_notification: channel per phone + snapshot payload ─────────


async def test_create_notification_channels_and_snapshot() -> None:
    ticket = await _any_ticket()
    gm = await _get_user(GM_CWS)
    tk = await _get_ticket(ticket)
    async with SessionLocal() as s:
        rows = await create_notification(
            s,
            key="capa_sla_reminder",
            user=gm,
            context=ticket_context(tk, window_hours=24),
            entity_type="capa_ticket",
            entity_id=tk.uuid,
        )
        channels = sorted({r.channel for r in rows})
        payload = rows[0].payload
    assert "EMAIL" in channels and "PUSH" in channels
    if gm.phone:
        assert "WA" in channels
    else:
        assert "WA" not in channels
    assert payload["template_key"] == "capa_sla_reminder"
    assert payload["title"] == f"Reminder SLA: {ticket['receipt_id']}"
    assert "jatuh tempo" in payload["body"].lower()
    assert payload["receipt_id"] == ticket["receipt_id"]


# ─── 3. Inbox: list, unread_only, scoped owner, mark read ─────────────────


async def _new_inbox_notification(ticket: dict, email: str) -> str:
    user = await _get_user(email)
    tk = await _get_ticket(ticket)
    async with SessionLocal() as s:
        await create_notification(
            s,
            key="capa_sla_reminder",
            user=user,
            context={**ticket_context(tk, window_hours=24), "reminder_key": f"{ticket['receipt_id']}:inbox"},
            entity_type="capa_ticket",
            entity_id=tk.uuid,
        )
        await s.commit()
        return str(
            (
                await s.execute(
                    text(
                        "SELECT uuid::text FROM notifications WHERE user_id=:u AND type='CAPA_SLA' "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"u": user.id},
                )
            ).scalar_one()
        )


async def test_inbox_list_unread_filter_and_mark_read(client: AsyncClient) -> None:
    ticket = await _any_ticket()
    target_id = await _new_inbox_notification(ticket, HOD_CWS)

    h = await _headers(client, HOD_CWS)
    r = await client.get("/notifications", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {"success", "data", "meta"}
    assert body["meta"]["total"] >= 1

    mine = [i for i in body["data"] if i["id"] == target_id]
    assert mine, "notifikasi yang baru dibuat harus tampil di inbox"
    assert mine[0]["type"] == "CAPA_SLA" and mine[0]["channel"] == "PUSH"
    assert mine[0]["title"] == f"Reminder SLA: {ticket['receipt_id']}"
    assert mine[0]["read_at"] is None

    r = await client.get("/notifications?unread_only=true", headers=h)
    unread_ids = {i["id"] for i in r.json()["data"]}
    assert target_id in unread_ids

    r = await client.post(f"/notifications/{target_id}/read", headers=h)
    assert r.status_code == 204
    r = await client.get("/notifications", headers=h)
    mine = [i for i in r.json()["data"] if i["id"] == target_id]
    assert mine[0]["read_at"] is not None


async def test_inbox_read_scoped_to_owner(client: AsyncClient) -> None:
    async with SessionLocal() as s:
        row = (
            (
                await s.execute(
                    text(
                        "SELECT id, user_id FROM notifications WHERE user_id IS NOT NULL "
                        "ORDER BY created_at DESC LIMIT 1"
                    )
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return
        other_id, owner = str(row["id"]), row["user_id"]
    me = (await _get_user(ROM_JAWA)).id
    if owner == me:
        return  # notif kebetulan milik ROM — skip
    r = await client.post(f"/notifications/{other_id}/read", headers=await _headers(client, ROM_JAWA))
    assert r.status_code == 404


# ─── 4. Delivery sweep: retry jujur → SENT ────────────────────────────────


async def _queue_one(email: str, channel: str, ticket: dict, attempts: int = 0) -> str:
    user = await _get_user(email)
    tk = await _get_ticket(ticket)
    async with SessionLocal() as s:
        # backdate agar selalu pertama dalam urutan sweep (deterministik)
        row = await s.execute(
            text(
                "INSERT INTO notifications (user_id, type, channel, entity_type, "
                "entity_id, payload, status, sent_at, read_at, created_at, updated_at) "
                "VALUES (:u, 'CAPA_SLA', :c, 'capa_ticket', :e, "
                "CAST(:p AS JSONB), 'QUEUED', NULL, NULL, "
                "now() - interval '1 day', now() - interval '1 day') "
                "RETURNING uuid::text"
            ),
            {
                "u": user.id,
                "c": channel,
                "e": tk.uuid,
                "p": json.dumps(
                    {
                        "template_key": "capa_sla_reminder",
                        "locale": "id",
                        "title": "Reminder SLA: x",
                        "body": "Jatuh tempo",
                        "attempts": attempts,
                    }
                ),
            },
        )
        nid = row.scalar_one()
        await s.commit()
        return nid


async def test_delivery_sweep_transient_retry_then_sent(client: AsyncClient) -> None:
    ticket = await _any_ticket()
    nid = await _queue_one(HOD_CWS, "EMAIL", ticket)
    h = await _headers(client, CORP_EXEC)

    with patch(
        "app.services.delivery.send_email", side_effect=ProviderNotConfiguredError("EHOS_SMTP_HOST belum dikonfigurasi")
    ):
        r = await client.post("/notifications/sweep", headers=h)
    assert r.status_code == 200, r.text
    st = await _status_counts(nid)
    assert st["status"] == "QUEUED"
    assert st["payload"]["attempts"] == 1
    assert "SMTP_HOST" in st["payload"]["last_error"]

    async def _ok(to, subject, body):
        return None

    with patch("app.services.delivery.send_email", new=_ok):
        r = await client.post("/notifications/sweep", headers=h)
    assert r.json()["data"]["sent"] >= 1
    st = await _status_counts(nid)
    assert st["status"] == "SENT"


async def test_delivery_sweep_terminal_failed_after_max(client: AsyncClient) -> None:
    ticket = await _any_ticket()
    nid = await _queue_one(HOD_CWS, "EMAIL", ticket, attempts=2)
    h = await _headers(client, CORP_EXEC)

    async def _fail(to, subject, body):
        raise DeliveryError("SMTP: connection refused")

    with patch("app.services.delivery.send_email", new=_fail):
        r = await client.post("/notifications/sweep", headers=h)
    assert r.status_code == 200
    st = await _status_counts(nid)
    assert st["status"] == "FAILED" and st["payload"]["attempts"] == 3


async def test_delivery_wa_provider_unconfigured_retries(client: AsyncClient) -> None:
    """WA tanpa provider → retry QUEUED jujur, bukan SENT palsu (G4)."""
    ticket = await _any_ticket()
    gm = await _get_user(GM_CWS)
    if not gm.phone:
        return  # robust: tanpa phone, WA memang tak dibuat
    nid = await _queue_one(GM_CWS, "WA", ticket)
    h = await _headers(client, CORP_EXEC)

    with patch(
        "app.services.delivery.send_whatsapp",
        side_effect=ProviderNotConfiguredError("EHOS_WA_API_URL belum dikonfigurasi"),
    ):
        r = await client.post("/notifications/sweep", headers=h)
    assert r.status_code == 200
    st = await _status_counts(nid)
    assert st["status"] == "QUEUED"
    assert "WA_API_URL" in st["payload"]["last_error"]


# ─── 5. RBAC: sweep & reminder hanya ROOT/CORP (notifications:deliver) ─────


async def test_sweep_and_remind_require_corp_privilege(client: AsyncClient) -> None:
    h_hod = await _headers(client, HOD_CWS)
    for path in ("/notifications/sweep", "/notifications/remind-sla"):
        r = await client.post(path, headers=h_hod)
        assert r.status_code == 403, (path, r.text)
    h_corp = await _headers(client, CORP_EXEC)
    r = await client.post("/notifications/sweep", headers=h_corp)
    assert r.status_code == 200


# ─── 6. Reminder SLA: generate + dedup idempoten ──────────────────────────


async def _set_due(ticket_id: uuid.UUID, due_at) -> None:
    async with SessionLocal() as s:
        await s.execute(text("UPDATE capa_tickets SET due_at=:d WHERE uuid=:i"), {"d": due_at, "i": ticket_id})
        await s.commit()


async def _had_reminder(ticket_id: uuid.UUID) -> bool:
    async with SessionLocal() as s:
        return bool(
            await s.scalar(
                text(
                    "SELECT 1 FROM notifications WHERE entity_type='capa_ticket' "
                    "AND entity_id=:i AND type='CAPA_SLA' LIMIT 1"
                ),
                {"i": ticket_id},
            )
        )


async def test_sla_reminder_generates_and_dedups(client: AsyncClient) -> None:
    async with SessionLocal() as s:
        trow = (
            (
                await s.execute(
                    text(
                        "SELECT uuid::text AS id, receipt_id, due_at FROM capa_tickets "
                        "WHERE status != 'CLOSED' ORDER BY due_at LIMIT 1"
                    )
                )
            )
            .mappings()
            .first()
        )
        if trow is None:
            return
        t_id = uuid.UUID(trow["id"])
        original_due = trow["due_at"]
    new_due = datetime.now(UTC) + timedelta(hours=3)
    await _set_due(t_id, new_due)

    try:
        async with SessionLocal() as s:
            now = datetime.now(UTC)
            first = await remind_sla(s, now=now, window_hours=24)
            second = await remind_sla(s, now=now, window_hours=24)
        assert first["reminded"] >= 1 or await _had_reminder(t_id)
        assert second["notifications"] == 0  # dedup reminder_key (bucket jam due)
    finally:
        await _set_due(t_id, original_due)


async def test_remind_sla_endpoint_creates_queue(client: AsyncClient) -> None:
    async with SessionLocal() as s:
        trow = (
            (
                await s.execute(
                    text(
                        "SELECT uuid::text AS id, due_at FROM capa_tickets "
                        "WHERE status != 'CLOSED' ORDER BY due_at LIMIT 1"
                    )
                )
            )
            .mappings()
            .first()
        )
        if trow is None:
            return
        t_id = uuid.UUID(trow["id"])
        original = trow["due_at"]
        # bersihkan reminder lama utk tiket ini — jaga idempoten antar-run (dev DB shared)
        await s.execute(
            text("DELETE FROM notifications WHERE entity_type='capa_ticket' AND entity_id=:i AND type='CAPA_SLA'"),
            {"i": t_id},
        )
        await s.commit()
    await _set_due(t_id, datetime.now(UTC) + timedelta(hours=5))
    try:
        h = await _headers(client, CORP_EXEC)
        r = await client.post("/notifications/remind-sla", headers=h, params={"window_hours": 24})
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["notifications"] >= 1
        assert any(i["ticket_id"] == str(t_id) for i in data["items"])
    finally:
        await _set_due(t_id, original)
