"""SLA class & auto-escalation CAPA (PRD-F-03, task 7b) — GM → ROM → VP.

SLA class berjenjang (1x24h / 2x24h / 7d), sweep auto-escalation saat due_at
lewat, cap di CORP_EXEC (level 3), dan Notification CAPA_ESCALATE (QUEUED) ke
penerima tier. Menguji service `sla_escalation` + endpoint `/escalate` yang
telah di-upgrade (notifikasi + cap).

Berjalan terhadap seeded dev DB (template SECURITY_RISK Universal, hotel CWS;
CWS region JATIM → ROM yang berhak = rom.jawa; CORP_EXEC = corp.exec).
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from httpx import AsyncClient
from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.sla_escalation import (
    MAX_ESCALATION_LEVEL,
    SLA_CLASS_HOURS,
    sla_class_hours,
    sla_status,
)

SEED_PASSWORD = "Ehos#2026!"
CORP_AUDITOR = "corp.auditor@ehos.local"
CORP_EXEC = "corp.exec@ehos.local"
GM_CWS = "gm.cws@ehos.local"
ROM_JAWA = "rom.jawa@ehos.local"

_DATEFIX_BASE = datetime(2021, 1, 1) + timedelta(days=uuid.uuid4().int % 20000)
_DATEFIX_SEQ = 0


def _unique_date() -> str:
    global _DATEFIX_SEQ
    _DATEFIX_SEQ += 1
    return (_DATEFIX_BASE + timedelta(days=_DATEFIX_SEQ)).date().isoformat()


async def _login(client: AsyncClient, email: str) -> str:
    r = await client.post(
        "/auth/login",
        json={"email": email, "password": SEED_PASSWORD, "remember_me": False},
    )
    assert r.status_code == 200, (email, r.text)
    return r.json()["data"]["access_token"]


async def _headers(client: AsyncClient, email: str) -> dict:
    return {"Authorization": f"Bearer {await _login(client, email)}"}


async def _fixtures():
    async with SessionLocal() as s:
        hotel_id = await s.scalar(text("SELECT id FROM hotels WHERE code='CWS'"))
        template_id = (await s.execute(text(
            "SELECT id FROM checklist_templates "
            "WHERE department='SECURITY_RISK' AND status='LOCKED' "
            "ORDER BY locked_at DESC LIMIT 1"
        ))).scalar_one()
        items = await s.execute(text(
            "SELECT i.id, i.code, i.rubric_type, i.max_score, i.is_life_safety "
            "FROM checklist_items i "
            "JOIN checklist_sections sec ON sec.id=i.section_id "
            "WHERE sec.template_id=:t ORDER BY i.sort_order, i.code"
        ), {"t": template_id})
        return {"hotel_id": str(hotel_id), "template_id": str(template_id),
                "items": [dict(row) for row in items.mappings()]}


def _pass_value(it: dict) -> dict:
    if it["rubric_type"] == "MULTI_ROOM":
        return {"value": "YES"}
    if it["rubric_type"] == "NUMERIC_SCALE":
        return {"value": str(it["max_score"])}
    return {"value": "YES"}


def _pick_item(fx: dict, *, life_safety: bool) -> dict:
    for it in fx["items"]:
        if it["is_life_safety"] == life_safety and it["rubric_type"] == "TRAFFIC_LIGHT":
            return it
    raise AssertionError(f"item life_safety={life_safety} TRAFFIC_LIGHT tidak ditemukan")


async def _rid(client: AsyncClient, email: str):
    async with SessionLocal() as s:
        return await s.scalar(text("SELECT id FROM users WHERE email=:e"), {"e": email})


async def _seed_one_ticket(client: AsyncClient, fx: dict, *, fail_code: str) -> str:
    """Buat sesi audit fail → publish → 1 auto-CAPA ticket OPEN. Return id tiket."""
    h = await _headers(client, CORP_AUDITOR)
    r = await client.post("/audit/sessions", headers=h, json={
        "hotel_id": fx["hotel_id"], "department": "SECURITY_RISK", "date_start": _unique_date(),
        "template_id": fx["template_id"],
    })
    sess_id = r.json()["data"]["id"]
    now = datetime.now(UTC)
    scores = []
    for it in fx["items"]:
        v = _pass_value(it)
        if it["code"] == fail_code:
            v = {"value": "NO"}
        scores.append({"item_id": str(it["id"]), **v, "is_na": False,
                       "scored_at": now.isoformat(), "updated_at": now.isoformat()})
    await client.post(f"/audit/sessions/{sess_id}/items", json={"scores": scores}, headers=h)
    await client.post(f"/audit/sessions/{sess_id}/submit", headers=h)
    r = await client.post(f"/audit/sessions/{sess_id}/publish", headers=h)
    assert r.status_code == 200, r.text
    async with SessionLocal() as s:
        ticket_id = await s.scalar(text(
            "SELECT ct.uuid FROM capa_tickets ct JOIN findings f ON f.id=ct.finding_id "
            "WHERE f.session_id=(SELECT id FROM audit_sessions WHERE uuid::text=:s) LIMIT 1"
        ), {"s": sess_id})
    return str(ticket_id)


async def _ticket_notifications(ticket_id: str) -> list[dict]:
    async with SessionLocal() as s:
        rows = await s.execute(text(
            "SELECT n.user_id, n.type, n.channel, n.entity_type, n.payload, n.status "
            "FROM notifications n WHERE n.entity_type='capa_ticket' AND "
            "n.entity_id=(SELECT ct.uuid FROM capa_tickets ct WHERE ct.uuid::text=:id) "
            "ORDER BY n.created_at"
        ), {"id": ticket_id})
        return [dict(r) for r in rows.mappings()]


def _after_media() -> list[dict]:
    return [{
        "phase": "AFTER", "source_camera": "LIVE_CAMERA", "mime": "image/webp",
        "width": 1280, "height": 720, "size_bytes": 180_000,
        "checksum_sha256": "ab" * 32, "gps_lat": -6.20, "gps_lng": 106.80,
        "gps_valid": True, "captured_at": datetime.now(UTC).isoformat(),
    }]


async def _confirm_after_media(client: AsyncClient, ticket_id: str) -> None:
    """Confirm bukti AFTER → VERIFIED (task 7d gate: approval wajib evidence)."""
    h = await _headers(client, "hod.srm.cws@ehos.local")
    r = await client.get(f"/capa/tickets/{ticket_id}/media", headers=await _headers(client, CORP_EXEC))
    assert r.status_code == 200, r.text
    for m in r.json()["data"]["items"]:
        if m["phase"] == "AFTER" and m["upload_status"] != "VERIFIED":
            with patch("app.services.capa_media.verify_object", return_value=True):
                resp = await client.post(f"/capa/tickets/{ticket_id}/media/{m['id']}/confirm", headers=h)
            assert resp.status_code == 200, resp.text


# ─── 1. SLA class mapping & sla_status (pure, no DB) ─────────────────────

async def test_sla_class_mapping() -> None:
    assert SLA_CLASS_HOURS == {1: 24, 2: 48, 3: 168}  # 1x24 / 2x24 / 7 hari
    assert sla_class_hours(1) == 24 and sla_class_hours(2) == 48 and sla_class_hours(3) == 168
    assert sla_class_hours(99) == 168  # fallback MINOR
    assert MAX_ESCALATION_LEVEL == 3  # GM → ROM → VP


async def test_sla_status_matrix() -> None:
    now = datetime.now(UTC)
    base = SimpleNamespace(priority=1, sla_hours=24, status="OPEN", closed_at=None)
    on = SimpleNamespace(**{**base.__dict__, "due_at": now + timedelta(hours=20)})
    risk = SimpleNamespace(**{**base.__dict__, "due_at": now + timedelta(hours=1)})
    overdue = SimpleNamespace(**{**base.__dict__, "due_at": now - timedelta(hours=1)})
    closed_ok = SimpleNamespace(
        **{**base.__dict__, "status": "CLOSED", "due_at": now - timedelta(hours=1),
           "closed_at": now - timedelta(hours=2)})
    closed_late = SimpleNamespace(
        **{**base.__dict__, "status": "CLOSED", "due_at": now - timedelta(hours=4),
           "closed_at": now - timedelta(hours=2)})
    assert sla_status(on, now) == "ON_TRACK"
    assert sla_status(risk, now) == "AT_RISK"
    assert sla_status(overdue, now) == "OVERDUE"
    assert sla_status(closed_ok, now) == "ON_TRACK"
    assert sla_status(closed_late, now) == "OVERDUE"


# ─── 2. Endpoint escalate: chain penuh GM→ROM→VP + cap 409 + notif ────────

async def test_endpoint_escalate_chain_caps_and_notifies(client: AsyncClient) -> None:
    fx = await _fixtures()
    ticket_id = await _seed_one_ticket(client, fx, fail_code=_pick_item(fx, life_safety=True)["code"])

    h_gm = await _headers(client, GM_CWS)
    expect_tier = {
        1: await _rid(client, GM_CWS),
        2: await _rid(client, ROM_JAWA),
        3: await _rid(client, CORP_EXEC),
    }
    for level in (1, 2, 3):
        r = await client.post(f"/capa/tickets/{ticket_id}/escalate", headers=h_gm)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["escalation_level"] == level

    # level 4 → cap (409)
    r = await client.post(f"/capa/tickets/{ticket_id}/escalate", headers=h_gm)
    assert r.status_code == 409
    assert "maksimal" in r.json()["detail"]

    # notification CAPA_ESCALATE (QUEUED) ke tiap tier receiver sekali per channel (7e)
    notifs = await _ticket_notifications(ticket_id)
    esc = [n for n in notifs if n["type"] == "CAPA_ESCALATE"]
    tiers = {str(v) for v in expect_tier.values()}
    assert {str(n["user_id"]) for n in esc} == tiers
    channels = {uid: {n["channel"] for n in esc if str(n["user_id"]) == uid} for uid in tiers}
    assert all({"PUSH", "EMAIL"} <= chans <= {"PUSH", "EMAIL", "WA"} for chans in channels.values())
    assert len(esc) == sum(len(c) for c in channels.values())
    assert all(n["status"] == "QUEUED" for n in esc)
    lvl1 = [n for n in esc if str(n["user_id"]) == str(expect_tier[1])]
    assert all(n["payload"]["escalation_level"] == 1 for n in lvl1)
    assert all(n["payload"]["receipt_id"] is not None for n in lvl1)


# ─── 3. Auto-escalation sweep service (due_at lewat) ──────────────────────

async def test_run_sla_escalation_auto_escalates_overdue(client: AsyncClient) -> None:
    from app.services.sla_escalation import run_sla_escalation

    fx = await _fixtures()
    ticket_id = await _seed_one_ticket(client, fx, fail_code=_pick_item(fx, life_safety=False)["code"])
    async with SessionLocal() as s:
        await s.execute(text(
            "UPDATE capa_tickets SET due_at = now() - interval '30 days' WHERE uuid::text=:id"
        ), {"id": ticket_id})
        await s.commit()

    async with SessionLocal() as s:
        res = await run_sla_escalation(s)
    assert res["escalated"] >= 1
    assert any(i["ticket_id"] == ticket_id for i in res["items"])

    async with SessionLocal() as s:
        level_after = await s.scalar(text(
            "SELECT escalation_level FROM capa_tickets WHERE uuid::text=:id"), {"id": ticket_id})
        assert level_after == 1
    notifs = await _ticket_notifications(ticket_id)
    gm_id = str(await _rid(client, GM_CWS))
    assert any(n["type"] == "CAPA_ESCALATE" and str(n["user_id"]) == gm_id for n in notifs)

    # tiket masih overdue → run berikutnya rombong naik (GM → ROM → VP)
    async with SessionLocal() as s:
        await run_sla_escalation(s)
    async with SessionLocal() as s:
        assert await s.scalar(text(
            "SELECT escalation_level FROM capa_tickets WHERE uuid::text=:id"), {"id": ticket_id}) == 2
    notifs = await _ticket_notifications(ticket_id)
    rom_id = str(await _rid(client, ROM_JAWA))
    assert any(str(n["user_id"]) == rom_id for n in notifs)

    async with SessionLocal() as s:
        await run_sla_escalation(s)
    async with SessionLocal() as s:
        assert await s.scalar(text(
            "SELECT escalation_level FROM capa_tickets WHERE uuid::text=:id"), {"id": ticket_id}) == 3
    notifs = await _ticket_notifications(ticket_id)
    corp_id = str(await _rid(client, CORP_EXEC))
    assert any(str(n["user_id"]) == corp_id for n in notifs)

    # cap: run berikutnya tiket tak berubah (level 3 = CORP_EXEC maksimal)
    async with SessionLocal() as s:
        await run_sla_escalation(s)
    async with SessionLocal() as s:
        assert await s.scalar(text(
            "SELECT escalation_level FROM capa_tickets WHERE uuid::text=:id"), {"id": ticket_id}) == 3


# ─── 4. Sweep command/service tidak menyentuh non-kandidat ────────────────

async def test_escalate_one_level_closed_and_future_guards(client: AsyncClient) -> None:
    from app.models import CapaTicket
    from app.services.capa_lifecycle import InvalidTransitionError
    from app.services.sla_escalation import escalate_one_level, run_sla_escalation

    fx = await _fixtures()
    # tiket 1: genuine CLOSED lalu dimundurkan due_at-nya (overdue, tapi sudah tutup)
    t_closed = await _seed_one_ticket(client, fx, fail_code=_pick_item(fx, life_safety=True)["code"])
    h_hod = await _headers(client, "hod.srm.cws@ehos.local")
    await client.post(f"/capa/tickets/{t_closed}/resolve", headers=h_hod,
                      json={"note": "Fix", "media": _after_media()})
    await _confirm_after_media(client, t_closed)
    await client.post(f"/capa/tickets/{t_closed}/verify/gm", headers=await _headers(client, GM_CWS),
                      json={"decision": "APPROVE"})
    await client.post(f"/capa/tickets/{t_closed}/verify/qa",
                      headers=await _headers(client, CORP_AUDITOR), json={"decision": "CLOSE"})
    # tiket 2: still OPEN, due future (bukan kandidat sweep)
    t_future = await _seed_one_ticket(client, fx, fail_code=_pick_item(fx, life_safety=False)["code"])
    async with SessionLocal() as s:
        await s.execute(text(
            "UPDATE capa_tickets SET due_at = now() - interval '2 hours' WHERE uuid::text=:id"
        ), {"id": t_closed})
        await s.commit()

    # CLOSED tidak bisa di-eskalasi manual (guard service)
    async with SessionLocal() as s:
        from app.core.identity import get_by_uuid
        closed = await get_by_uuid(s, CapaTicket, t_closed)
        try:
            await escalate_one_level(s, closed, closed.created_by)
            raise AssertionError("CLOSED ticket tereskalasi — harusnya InvalidTransitionError")
        except InvalidTransitionError:
            pass
        assert closed.escalation_level == 0

    # sweep tidak menyentuh CLOSED maupun yang belum jatuh tempo
    async with SessionLocal() as s:
        await run_sla_escalation(s)
    async with SessionLocal() as s:
        assert await s.scalar(text(
            "SELECT escalation_level FROM capa_tickets WHERE uuid::text=:id"), {"id": t_closed}) == 0
        assert await s.scalar(text(
            "SELECT escalation_level FROM capa_tickets WHERE uuid::text=:id"), {"id": t_future}) == 0
    assert await _ticket_notifications(t_closed) == []
    assert await _ticket_notifications(t_future) == []


# ─── 5. Notification CAPA_ESCALATE tampil via detail/status + queue ───────

async def test_sla_status_in_detail_and_notification_payload(client: AsyncClient) -> None:
    fx = await _fixtures()
    ticket_id = await _seed_one_ticket(client, fx, fail_code=_pick_item(fx, life_safety=True)["code"])

    async with SessionLocal() as s:
        await s.execute(text(
            "UPDATE capa_tickets SET due_at = now() - interval '3 hours' WHERE uuid::text=:id"
        ), {"id": ticket_id})
        await s.commit()

    h = await _headers(client, GM_CWS)
    r = await client.get(f"/capa/tickets/{ticket_id}", headers=h)
    assert r.status_code == 200, r.text
    det = r.json()["data"]
    assert det["sla_status"] == "OVERDUE" and det["overdue"] is True

    r = await client.post(f"/capa/tickets/{ticket_id}/escalate", headers=h)
    assert r.status_code == 200
    notifs = await _ticket_notifications(ticket_id)
    n = next(x for x in notifs if x["type"] == "CAPA_ESCALATE")
    p = n["payload"]
    assert p["escalation_level"] == 1
    assert p["priority"] == 1 and p["sla_hours"] == 24
    assert p["status"] == "OPEN" and p["due_at"]
    assert p["url"] == f"/capa/tickets/{ticket_id}"


# ─── 9c — field SLA di list & presign-GET bukti (verification hub) ────────

async def test_list_tickets_sla_status_and_overdue_fields(client: AsyncClient) -> None:
    fx = await _fixtures()
    ticket_id = await _seed_one_ticket(client, fx, fail_code=_pick_item(fx, life_safety=True)["code"])
    async with SessionLocal() as s:
        await s.execute(text(
            "UPDATE capa_tickets SET due_at = now() - interval '30 days' WHERE uuid::text=:id"
        ), {"id": ticket_id})
        await s.commit()

    r = await client.get(f"/capa/tickets?hotel_id={fx['hotel_id']}&status=OPEN", headers=await _headers(client, GM_CWS))
    assert r.status_code == 200, r.text
    rows = r.json()["data"]
    row = next(x for x in rows if str(x["id"]) == ticket_id)
    assert row["sla_status"] == "OVERDUE" and row["overdue"] is True

    # status chip non-DB (OVERDUE dan COMPLETED bukan status DB legal, harus 422)
    for bad in ("COMPLETED", "OVERDUE"):
        nr = await client.get(f"/capa/tickets?status={bad}", headers=await _headers(client, CORP_EXEC))
        assert nr.status_code == 422, (bad, nr.text)


async def test_presign_get_media_requires_verified(client: AsyncClient) -> None:
    fx = await _fixtures()
    ticket_id = await _seed_one_ticket(client, fx, fail_code=_pick_item(fx, life_safety=False)["code"])
    h_read = await _headers(client, CORP_EXEC)

    # resolve → media AFTER PENDING (belum dikonfirmasi) → presign-GET harus 409 (G4 tanpa stub)
    await client.post(f"/capa/tickets/{ticket_id}/resolve", headers=await _headers(client, "hod.srm.cws@ehos.local"),
                      json={"note": "Fix", "media": _after_media()})
    r = await client.get(f"/capa/tickets/{ticket_id}/media", headers=h_read)
    assert r.status_code == 200, r.text
    items = r.json()["data"]["items"]
    pend = next(m for m in items if m["phase"] == "AFTER")
    rr = await client.get(f"/capa/tickets/{ticket_id}/media/{pend['id']}/presign-get", headers=h_read)
    assert rr.status_code == 409, rr.text

    # konfirmasi → VERIFIED → presign-GET valid
    await _confirm_after_media(client, ticket_id)
    ok = await client.get(f"/capa/tickets/{ticket_id}/media/{pend['id']}/presign-get", headers=h_read)
    assert ok.status_code == 200, ok.text
    got = ok.json()["data"]
    assert got["media_id"] == pend["id"]
    assert got["presigned_url"].startswith("http") and got["expires_in"] == 300