"""CAPA hierarki approval (PRD-F-04, task 7d) — gate bukti + Four-Eyes.

Target spesifik STATE-SPECS §2.5:
- "approval perlu evidence After foto → tombol disabled"  → gm_approve/qa_close
  MEwajibkan ≥1 CapaMedia AFTER VERIFIED (split-path 7c). Gagal → 409.
- "approver ≠ assignee ≠ reporter" (Four-Eyes)            → approver dilarang sama
  dengan assignee; utk origin WHISTLEBLOWER juga ≠ reporter.
- REJECT/REOPEN sengaja tidak digate (menolak karena bukti kurang/jelek).

Berjalan terhadap seeded dev DB (template SECURITY_RISK, CWS — sama dgn
test_capa_lifecycle).
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from httpx import AsyncClient
from sqlalchemy import text

from app.db.session import SessionLocal

SEED_PASSWORD = "Ehos#2026!"
CORP_AUDITOR = "corp.auditor@ehos.local"
GM_CWS = "gm.cws@ehos.local"
HOD_CWS = "hod.srm.cws@ehos.local"

_DATEFIX_BASE = datetime(2021, 1, 1, tzinfo=UTC) + timedelta(days=uuid.uuid4().int % 20000)
_DATEFIX_SEQ = 0


def _unique_date() -> str:
    global _DATEFIX_SEQ
    _DATEFIX_SEQ += 1
    return (_DATEFIX_BASE + timedelta(days=_DATEFIX_SEQ)).date().isoformat()


async def _headers(client: AsyncClient, email: str) -> dict:
    r = await client.post("/auth/login",
                          json={"email": email, "password": SEED_PASSWORD, "remember_me": False})
    assert r.status_code == 200, (email, r.text)
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


async def _fixtures():
    async with SessionLocal() as s:
        hotel_id = await s.scalar(text("SELECT id FROM hotels WHERE code='CWS'"))
        template_id = (await s.execute(text(
            "SELECT id FROM checklist_templates "
            "WHERE department='SECURITY_RISK' AND status='LOCKED' "
            "ORDER BY locked_at DESC LIMIT 1"))).scalar_one()
        items = await s.execute(text(
            "SELECT i.id, i.code, i.rubric_type, i.max_score, i.is_life_safety "
            "FROM checklist_items i "
            "JOIN checklist_sections sec ON sec.id=i.section_id "
            "WHERE sec.template_id=:t ORDER BY i.sort_order, i.code"), {"t": template_id})
        return {"hotel_id": str(hotel_id), "template_id": str(template_id),
                "items": [dict(row) for row in items.mappings()]}


def _pass_value(it: dict) -> dict:
    if it["rubric_type"] == "MULTI_ROOM":
        return {"value": "YES"}
    if it["rubric_type"] == "NUMERIC_SCALE":
        return {"value": str(it["max_score"])}
    return {"value": "YES"}


def _fail_item(fx: dict) -> dict:
    for it in fx["items"]:
        if it["is_life_safety"] and it["rubric_type"] == "TRAFFIC_LIGHT":
            return it
    raise AssertionError("item life_safety TRAFFIC_LIGHT tidak ditemukan")


async def _seed_open_ticket(client: AsyncClient, fx: dict) -> str:
    h = await _headers(client, CORP_AUDITOR)
    r = await client.post("/audit/sessions", headers=h, json={
        "hotel_id": fx["hotel_id"], "department": "SECURITY_RISK", "date_start": _unique_date(),
        "template_id": fx["template_id"]})
    sess_id = r.json()["data"]["id"]
    now = datetime.now(UTC)
    scores = []
    for it in fx["items"]:
        v = _pass_value(it)
        if it["code"] == _fail_item(fx)["code"]:
            v = {"value": "NO"}
        scores.append({"item_id": str(it["id"]), **v, "is_na": False,
                       "scored_at": now.isoformat(), "updated_at": now.isoformat()})
    await client.post(f"/audit/sessions/{sess_id}/items", json={"scores": scores}, headers=h)
    await client.post(f"/audit/sessions/{sess_id}/submit", headers=h)
    r = await client.post(f"/audit/sessions/{sess_id}/publish", headers=h)
    assert r.status_code == 200, r.text
    async with SessionLocal() as s:
        return str(await s.scalar(text(
            "SELECT ct.uuid FROM capa_tickets ct JOIN findings f ON f.id=ct.finding_id "
            "WHERE f.session_id=(SELECT id FROM audit_sessions WHERE uuid::text=:s) LIMIT 1"), {"s": sess_id}))


def _after_media() -> list[dict]:
    return [{
        "phase": "AFTER", "source_camera": "LIVE_CAMERA", "mime": "image/webp",
        "width": 1280, "height": 720, "size_bytes": 180_000,
        "checksum_sha256": "ab" * 32, "gps_lat": -6.20, "gps_lng": 106.80,
        "gps_valid": True, "captured_at": datetime.now(UTC).isoformat(),
    }]


async def _resolve(client: AsyncClient, ticket_id: str, media: list[dict] | None = None) -> None:
    r = await client.post(f"/capa/tickets/{ticket_id}/resolve",
                          headers=await _headers(client, HOD_CWS),
                          json={"note": "Perbaikan terpasang", "media": media or []})
    assert r.status_code == 200, r.text


async def _confirm_after_media(client: AsyncClient, ticket_id: str, approver_email: str) -> dict:
    """Confirm semua bukti AFTER belum-verified → VERIFIED (split-path 7c, patch MinIO)."""
    h = await _headers(client, approver_email)
    r = await client.get(f"/capa/tickets/{ticket_id}/media", headers=h)
    assert r.status_code == 200, r.text
    for m in r.json()["data"]["items"]:
        if m["phase"] == "AFTER" and m["upload_status"] != "VERIFIED":
            with patch("app.services.capa_media.verify_object", return_value=True):
                resp = await client.post(f"/capa/tickets/{ticket_id}/media/{m['id']}/confirm", headers=h)
            assert resp.status_code == 200, resp.text
    return h


async def _rid(client: AsyncClient, email: str):
    async with SessionLocal() as s:
        return await s.scalar(text("SELECT uuid::text FROM users WHERE email=:e"), {"e": email})


async def _set_verified(client: AsyncClient, ticket_id: str, verified: bool) -> None:
    async with SessionLocal() as s:
        await s.execute(text(
            "UPDATE capa_media SET upload_status=:st WHERE ticket_id="
            "(SELECT id FROM capa_tickets WHERE uuid::text=:t) AND phase='AFTER'"),
            {"st": "VERIFIED" if verified else "PENDING", "t": ticket_id})
        await s.commit()


# ─── 1. GM first-approver WAJIB evidence After terverifikasi ────────────

async def test_gm_approve_requires_verified_after(client: AsyncClient) -> None:
    fx = await _fixtures()
    t = await _seed_open_ticket(client, fx)
    await _resolve(client, t, _after_media())  # bukti PENDING

    h_gm = await _headers(client, GM_CWS)
    r = await client.post(f"/capa/tickets/{t}/verify/gm", headers=h_gm,
                          json={"decision": "APPROVE", "note": "Ok"})
    assert r.status_code == 409, r.text
    assert "bukti AFTER terverifikasi" in r.json()["detail"]

    # bukti di-upload & diverifikasi → approve lolos
    await _confirm_after_media(client, t, GM_CWS)
    r = await client.post(f"/capa/tickets/{t}/verify/gm", headers=h_gm,
                          json={"decision": "APPROVE", "note": "Ok, pantau 7 hari"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "AWAITING_QA"


# ─── 2. QA final-approver WAJIB evidence After terverifikasi ─────────────

async def test_qa_close_requires_verified_after(client: AsyncClient) -> None:
    fx = await _fixtures()
    t = await _seed_open_ticket(client, fx)
    await _resolve(client, t, _after_media())
    await _confirm_after_media(client, t, GM_CWS)
    await client.post(f"/capa/tickets/{t}/verify/gm", headers=await _headers(client, GM_CWS),
                      json={"decision": "APPROVE"})

    # force evidence jadi belum-verified (simulasi bukti hilang/ditarik) → close 409
    await _set_verified(client, t, False)
    h_qa = await _headers(client, CORP_AUDITOR)
    r = await client.post(f"/capa/tickets/{t}/verify/qa", headers=h_qa,
                          json={"decision": "CLOSE"})
    assert r.status_code == 409, r.text
    assert "bukti AFTER terverifikasi" in r.json()["detail"]

    await _set_verified(client, t, True)
    r = await client.post(f"/capa/tickets/{t}/verify/qa", headers=h_qa,
                          json={"decision": "CLOSE", "note": "Valid, tutup"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "CLOSED"


# ─── 3. REJECT / REOPEN TIDAK digate evidence ────────────────────────────

async def test_reject_and_reopen_without_evidence_allowed(client: AsyncClient) -> None:
    fx = await _fixtures()
    t = await _seed_open_ticket(client, fx)

    # reject tanpa media sama sekali → boleh (justru menolak karena bukti kurang)
    await _resolve(client, t, [])  # media kosong PENDING-tanpa-barang
    r = await client.post(f"/capa/tickets/{t}/verify/gm", headers=await _headers(client, GM_CWS),
                          json={"decision": "REJECT", "note": "Bukti belum ada, ulangi"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "OPEN"

    # cycle penuh hingga AWAITING_QA lalu QA REOPEN tanpa kondisi evidence baru
    await _resolve(client, t, _after_media())
    await _confirm_after_media(client, t, GM_CWS)
    await client.post(f"/capa/tickets/{t}/verify/gm", headers=await _headers(client, GM_CWS),
                      json={"decision": "APPROVE"})
    r = await client.post(f"/capa/tickets/{t}/verify/qa",
                          headers=await _headers(client, CORP_AUDITOR),
                          json={"decision": "REOPEN", "note": "Rework"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "OPEN"


# ─── 4. Four-Eyes: approver ≠ assignee ───────────────────────────────────

async def test_gm_approve_blocked_when_gm_is_assignee(client: AsyncClient) -> None:
    fx = await _fixtures()
    t = await _seed_open_ticket(client, fx)

    gm_id = str(await _rid(client, GM_CWS))
    r = await client.post(f"/capa/tickets/{t}/assign", headers=await _headers(client, GM_CWS),
                          json={"assigned_to": gm_id, "note": "Ditangani GM sendiri"})
    assert r.status_code == 200, r.text
    assert str(r.json()["data"]["assigned_to"]) == gm_id

    await _resolve(client, t, _after_media())
    await _confirm_after_media(client, t, GM_CWS)
    r = await client.post(f"/capa/tickets/{t}/verify/gm", headers=await _headers(client, GM_CWS),
                          json={"decision": "APPROVE"})
    assert r.status_code == 409, r.text
    assert "Approver tidak boleh sama dengan assignee" in r.json()["detail"]


async def test_qa_close_blocked_when_auditor_is_assignee(client: AsyncClient) -> None:
    fx = await _fixtures()
    t = await _seed_open_ticket(client, fx)

    auditor_id = str(await _rid(client, CORP_AUDITOR))
    r = await client.post(f"/capa/tickets/{t}/assign", headers=await _headers(client, GM_CWS),
                          json={"assigned_to": auditor_id})
    assert r.status_code == 200

    await _resolve(client, t, _after_media())
    await _confirm_after_media(client, t, CORP_AUDITOR)  # auditor punya approve → bisa confirm
    await client.post(f"/capa/tickets/{t}/verify/gm", headers=await _headers(client, GM_CWS),
                      json={"decision": "APPROVE"})
    r = await client.post(f"/capa/tickets/{t}/verify/qa",
                          headers=await _headers(client, CORP_AUDITOR),
                          json={"decision": "CLOSE"})
    assert r.status_code == 409, r.text
    assert "Assign" not in r.json()["detail"]  # guard Four-Eyes, bukan 403 RBAC


# ─── 5. Whistleblower: approver ≠ reporter ───────────────────────────────

async def test_qa_close_blocked_when_origin_whistleblower_reporter(client: AsyncClient) -> None:
    fx = await _fixtures()
    t = await _seed_open_ticket(client, fx)

    auditor_id = await _rid(client, CORP_AUDITOR)
    async with SessionLocal() as s:
        await s.execute(text(
            "UPDATE capa_tickets SET origin='WHISTLEBLOWER', reporter_id="
            "(SELECT id FROM users WHERE uuid::text=:r) WHERE uuid::text=:t"),
            {"r": auditor_id, "t": t})
        await s.commit()

    await _resolve(client, t, _after_media())
    await _confirm_after_media(client, t, CORP_AUDITOR)
    await client.post(f"/capa/tickets/{t}/verify/gm", headers=await _headers(client, GM_CWS),
                      json={"decision": "APPROVE"})
    r = await client.post(f"/capa/tickets/{t}/verify/qa",
                          headers=await _headers(client, CORP_AUDITOR),
                          json={"decision": "CLOSE"})
    assert r.status_code == 409, r.text
    assert "reporter laporan anonim" in r.json()["detail"]


# ─── 6. Decision + note tercatat sebagai approval audit trail ────────────

async def test_approval_decisions_in_history_audit_trail(client: AsyncClient) -> None:
    fx = await _fixtures()
    t = await _seed_open_ticket(client, fx)
    await _resolve(client, t, _after_media())
    await _confirm_after_media(client, t, GM_CWS)
    await client.post(f"/capa/tickets/{t}/verify/gm", headers=await _headers(client, GM_CWS),
                      json={"decision": "APPROVE", "note": "GM setuju"})
    await client.post(f"/capa/tickets/{t}/verify/qa",
                      headers=await _headers(client, CORP_AUDITOR),
                      json={"decision": "CLOSE", "note": "QA verifikasi selesai"})

    h = await _headers(client, CORP_AUDITOR)
    hist = (await client.get(f"/capa/tickets/{t}/history", headers=h)).json()["data"]
    notes = {(x["from_status"], x["to_status"], x["note"]) for x in hist}
    assert ("AWAITING_GM", "AWAITING_QA", "GM setuju") in notes
    assert ("AWAITING_QA", "CLOSED", "QA verifikasi selesai") in notes