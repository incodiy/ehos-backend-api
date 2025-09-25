"""Auto-CAPA trigger dari temuan audit (PRD-F-03) — task 6e.

Saat sesi di-publish (6d), tiap Finding gagal otomatis membentuk CapaTicket:
- CRITICAL (life-safety) → Priority 1, SLA 1x24 jam (due_at = now + 24h)
- MAJOR                  → Priority 2, SLA 2x24 jam (due_at = now + 48h)
Origin AUDIT, status OPEN, mengikat finding/hotel/department + history OPEN.
Tidak ada tiket saat publish lulus penuh (PASS, tanpa finding).

Berjalan terhadap seeded dev DB (template SECURITY_RISK Universal).
"""

import uuid
from datetime import UTC, date, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.capa_trigger import auto_create_capa_ticket

SEED_PASSWORD = "Ehos#2026!"
CORP_AUDITOR = "corp.auditor@ehos.local"


async def _login(client: AsyncClient, email: str) -> str:
    r = await client.post(
        "/auth/login",
        json={"email": email, "password": SEED_PASSWORD, "remember_me": False},
    )
    assert r.status_code == 200, (email, r.text)
    return r.json()["data"]["access_token"]


async def _headers(client: AsyncClient, email: str) -> dict:
    return {"Authorization": f"Bearer {await _login(client, email)}"}


_DATEFIX_BASE = date(2020, 1, 1) + timedelta(days=uuid.uuid4().int % 20000)
_DATEFIX_SEQ = 0


def _unique_date() -> str:
    global _DATEFIX_SEQ
    _DATEFIX_SEQ += 1
    return (_DATEFIX_BASE + timedelta(days=_DATEFIX_SEQ)).isoformat()


async def _fixtures():
    async with SessionLocal() as s:
        hotel_id = await s.scalar(text("SELECT id FROM hotels WHERE code='CWS'"))
        dept_id = await s.scalar(text(
            "SELECT d.id FROM hotel_departments d "
            "JOIN hotels h ON h.id=d.hotel_id "
            "WHERE h.code='CWS' AND d.code='SECURITY_RISK'"
        ))
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
        return {"hotel_id": hotel_id, "template_id": template_id,
                "dept_id": str(dept_id),
                "items": [dict(row) for row in items.mappings()]}


def _pass_value(it: dict) -> dict:
    if it["rubric_type"] == "MULTI_ROOM":
        return {"value": "YES"}
    if it["rubric_type"] == "NUMERIC_SCALE":
        return {"value": str(it["max_score"])}
    return {"value": "YES"}


async def _create_scored_session(client: AsyncClient, fx: dict, *, fail_code: str | None) -> str:
    h = await _headers(client, CORP_AUDITOR)
    r = await client.post("/audit/sessions", headers=h, json={
        "hotel_id": str(fx["hotel_id"]), "department": "SECURITY_RISK", "date_start": _unique_date(),
        "template_id": str(fx["template_id"]),
    })
    sess_id = r.json()["data"]["id"]
    now = datetime.now(UTC)
    scores = []
    for it in fx["items"]:
        v = _pass_value(it)
        if fail_code and it["code"] == fail_code and it["rubric_type"] == "TRAFFIC_LIGHT":
            v = {"value": "NO"}
        scores.append({"item_id": str(it["id"]), **v, "is_na": False,
                       "scored_at": now.isoformat(), "updated_at": now.isoformat()})
    r = await client.post(f"/audit/sessions/{sess_id}/items", json={"scores": scores}, headers=h)
    assert r.status_code == 200, r.text
    await client.post(f"/audit/sessions/{sess_id}/submit", headers=h)
    return sess_id


async def _publish(client: AsyncClient, sess_id: str) -> dict:
    h = await _headers(client, CORP_AUDITOR)
    resp = await client.post(f"/audit/sessions/{sess_id}/publish", headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _tickets(sess_id: str) -> list[dict]:
    async with SessionLocal() as s:
        rows = await s.execute(text(
            "SELECT ct.uuid, ct.priority, ct.sla_hours, ct.due_at, ct.status, ct.title, "
            "       ct.origin, ct.escalation_level, ct.hotel_id, ct.department_id, "
            "       ct.finding_id, ct.receipt_id, ct.reporter_id, ct.created_by, "
            "       f.severity, f.is_life_safety "
            "FROM capa_tickets ct JOIN findings f ON f.id=ct.finding_id "
            "WHERE f.session_id=(SELECT id FROM audit_sessions WHERE uuid::text=:s) ORDER BY ct.priority"
        ), {"s": sess_id})
        return [dict(r) for r in rows.mappings()]


def _pick_item(fx: dict, *, life_safety: bool) -> dict:
    for it in fx["items"]:
        if it["is_life_safety"] == life_safety and it["rubric_type"] == "TRAFFIC_LIGHT":
            return it
    raise AssertionError(f"item life_safety={life_safety} TRAFFIC_LIGHT tidak ditemukan")


# ─── auto-CAPA trigger saat publish ──────────────────────────────────────

async def test_publish_all_yes_produces_no_capa(client: AsyncClient) -> None:
    """Lulus penuh (PASS) → tanpa finding → tanpa tiket CAPA (F-03 negatif)."""
    fx = await _fixtures()
    sess_id = await _create_scored_session(client, fx, fail_code=None)
    d = await _publish(client, sess_id)
    assert d["pass_fail"] == "PASS"
    assert await _tickets(sess_id) == []


async def test_publish_life_safety_auto_capa_p1_sla_24h(client: AsyncClient) -> None:
    """Finding life-safety CRITICAL → CapaTicket P1, SLA 1x24 jam, origin AUDIT."""
    fx = await _fixtures()
    sess_id = await _create_scored_session(
        client, fx, fail_code=_pick_item(fx, life_safety=True)["code"])
    await _publish(client, sess_id)
    tickets = await _tickets(sess_id)
    assert len(tickets) == 1
    t = tickets[0]
    assert t["severity"] == "CRITICAL"
    assert t["is_life_safety"] is True
    assert t["priority"] == 1
    assert t["sla_hours"] == 24
    assert t["origin"] == "AUDIT"
    assert t["status"] == "OPEN"
    assert t["escalation_level"] == 0
    assert str(t["hotel_id"]) == str(fx["hotel_id"])
    assert str(t["department_id"]) == fx["dept_id"]
    assert t["receipt_id"].startswith("CAPA-") and len(t["receipt_id"]) == 12
    assert timedelta(hours=23) <= t["due_at"] - datetime.now(UTC) <= timedelta(hours=25)

    async with SessionLocal() as s:
        hists = (await s.execute(text(
            "SELECT from_status, to_status FROM capa_status_histories WHERE ticket_id="
            "(SELECT id FROM capa_tickets WHERE uuid::text=:id)"
        ), {"id": str(t["uuid"])})).all()
        auditor_id = await s.scalar(text("SELECT id FROM users WHERE email=:e"), {"e": CORP_AUDITOR})
    assert any(h.to_status == "OPEN" and h.from_status is None for h in hists)
    assert str(t["reporter_id"]) == str(auditor_id)
    assert str(t["created_by"]) == str(auditor_id)


async def test_publish_major_non_life_safety_p2_sla_48h(client: AsyncClient) -> None:
    """Finding MAJOR (bukan life-safety) → CapaTicket P2, SLA 2x24 jam."""
    fx = await _fixtures()
    sess_id = await _create_scored_session(
        client, fx, fail_code=_pick_item(fx, life_safety=False)["code"])
    await _publish(client, sess_id)
    tickets = await _tickets(sess_id)
    assert len(tickets) == 1
    assert tickets[0]["severity"] == "MAJOR"
    assert tickets[0]["is_life_safety"] is False
    assert tickets[0]["priority"] == 2
    assert tickets[0]["sla_hours"] == 48
    assert timedelta(hours=47) <= tickets[0]["due_at"] - datetime.now(UTC) <= timedelta(hours=49)


async def test_capa_trigger_service_idempotent(client: AsyncClient) -> None:
    """auto_create_capa_ticket idempoten per finding (aman retry/re-publish)."""
    fx = await _fixtures()
    sess_id = await _create_scored_session(
        client, fx, fail_code=_pick_item(fx, life_safety=True)["code"])
    await _publish(client, sess_id)

    async with SessionLocal() as s:
        from app.models import Finding

        row = (await s.execute(text(
            "SELECT * FROM findings WHERE session_id=(SELECT id FROM audit_sessions WHERE uuid::text=:s) LIMIT 1"
        ), {"s": sess_id})).mappings().one()
        actor_id = await s.scalar(text("SELECT id FROM users WHERE email=:e"), {"e": CORP_AUDITOR})
        fobj = await s.get(Finding, row["id"])
        assert fobj is not None
        ticket = await auto_create_capa_ticket(s, fobj, actor_id=actor_id, department="SECURITY_RISK")
        await s.commit()
        assert ticket.finding_id == row["id"]
    # panggil service lagi → tetap satu tiket (tidak duplikat receipt)
    tickets = await _tickets(sess_id)
    assert len(tickets) == 1