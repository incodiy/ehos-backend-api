"""CAPA ticket lifecycle (PRD-F-03, task 7a) — Four-Eyes status machine + timeline SLA.

Alur: OPEN → (resolve) AWAITING_GM → (verify/gm APPROVE) AWAITING_QA →
(verify/qa CLOSE) CLOSED. REJECT/REOPEN kembali ke OPEN untuk rework oleh
teknisi. Setiap transisi me-record CapaStatusHistory (audit trail).

Berjalan terhadap seeded dev DB (template SECURITY_RISK Universal, hotel CWS).
RBAC: GM (manage+read), HOD_TECH (resolve+read), CORP_AUDITOR (approve+read global),
CORP_EXEC (read global) — seed RBAC v2 (capa:read:global ditambahkan).
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

from httpx import AsyncClient
from sqlalchemy import text

from app.db.session import SessionLocal

SEED_PASSWORD = "Ehos#2026!"
CORP_AUDITOR = "corp.auditor@ehos.local"
CORP_EXEC = "corp.exec@ehos.local"
GM_CWS = "gm.cws@ehos.local"
HOD_CWS = "hod.srm.cws@ehos.local"
SALES_CWS = "sales.cws@ehos.local"

_DATEFIX_BASE = date(2020, 1, 1) + timedelta(days=uuid.uuid4().int % 20000)
_DATEFIX_SEQ = 0


def _unique_date() -> str:
    global _DATEFIX_SEQ
    _DATEFIX_SEQ += 1
    return (_DATEFIX_BASE + timedelta(days=_DATEFIX_SEQ)).isoformat()


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
    r = await client.post(f"/audit/sessions/{sess_id}/items", json={"scores": scores}, headers=h)
    assert r.status_code == 200, r.text
    await client.post(f"/audit/sessions/{sess_id}/submit", headers=h)
    r = await client.post(f"/audit/sessions/{sess_id}/publish", headers=h)
    assert r.status_code == 200, r.text
    async with SessionLocal() as s:
        ticket_id = await s.scalar(text(
            "SELECT ct.uuid FROM capa_tickets ct JOIN findings f ON f.id=ct.finding_id "
            "WHERE f.session_id=(SELECT id FROM audit_sessions WHERE uuid::text=:s) LIMIT 1"
        ), {"s": sess_id})
    return str(ticket_id)


def _after_media() -> dict:
    return [{
        "phase": "AFTER", "source_camera": "LIVE_CAMERA", "mime": "image/webp",
        "width": 1280, "height": 720, "size_bytes": 180_000,
        "checksum_sha256": "ab" * 32, "gps_lat": -6.20, "gps_lng": 106.80,
        "gps_valid": True, "captured_at": datetime.now(UTC).isoformat(),
    }]


async def _history(client: AsyncClient, ticket_id: str) -> list[dict]:
    h = await _headers(client, CORP_EXEC)
    r = await client.get(f"/capa/tickets/{ticket_id}/history", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["data"]


async def _confirm_after_media(client: AsyncClient, ticket_id: str) -> None:
    """Confirm bukti AFTER → VERIFIED (task 7d gate: approval wajib evidence)."""
    h = await _headers(client, GM_CWS)
    r = await client.get(f"/capa/tickets/{ticket_id}/media", headers=await _headers(client, CORP_EXEC))
    for m in r.json()["data"]["items"]:
        if m["phase"] == "AFTER" and m["upload_status"] != "VERIFIED":
            with patch("app.services.capa_media.verify_object", return_value=True):
                resp = await client.post(f"/capa/tickets/{ticket_id}/media/{m['id']}/confirm", headers=h)
            assert resp.status_code == 200, resp.text


# ─── 1. Four-Eyes lengkap (happy path) ───────────────────────────────────

async def test_full_four_eyes_flow_with_media(client: AsyncClient) -> None:
    fx = await _fixtures()
    ticket_id = await _seed_one_ticket(client, fx, fail_code=_pick_item(fx, life_safety=True)["code"])

    h_hod = await _headers(client, HOD_CWS)
    r = await client.post(f"/capa/tickets/{ticket_id}/resolve", headers=h_hod,
                          json={"note": "Fire extinguisher dipasang ulang & diuji", "media": _after_media()})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["ticket"]["status"] == "AWAITING_GM"
    assert d["ticket"]["submitted_at"] is not None
    assert len(d["media"]) == 1
    m = d["media"][0]
    assert m["phase"] == "AFTER" and m["upload_status"] == "PENDING"
    assert m["object_key"].startswith(f"capa/{ticket_id}/") and m["object_key"].endswith(".webp")

    # 7d gate: GM tidak approve tanpa bukti AFTER terverifikasi
    h_gm = await _headers(client, GM_CWS)
    r = await client.post(f"/capa/tickets/{ticket_id}/verify/gm", headers=h_gm,
                          json={"decision": "APPROVE", "note": "Ok, pantau 7 hari"})
    assert r.status_code == 409 and "bukti AFTER terverifikasi" in r.json()["detail"]
    await _confirm_after_media(client, ticket_id)

    r = await client.post(f"/capa/tickets/{ticket_id}/verify/gm", headers=h_gm,
                          json={"decision": "APPROVE", "note": "Ok, pantau 7 hari"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "AWAITING_QA"

    r = await client.post(f"/capa/tickets/{ticket_id}/verify/qa", headers=await _headers(client, CORP_AUDITOR),
                          json={"decision": "CLOSE", "note": "Bukti valid, kapasitas OK"})
    assert r.status_code == 200, r.text
    closed = r.json()["data"]
    assert closed["status"] == "CLOSED"
    assert closed["closed_at"] is not None and closed["closed_by"] is not None

    hist = await _history(client, ticket_id)
    transitions = [(h["from_status"], h["to_status"]) for h in hist]
    assert ("OPEN", "AWAITING_GM") in transitions
    assert ("AWAITING_GM", "AWAITING_QA") in transitions
    assert ("AWAITING_QA", "CLOSED") in transitions
    assert len(hist) == 4  # OPEN(auto) → resolve → gm → qa

    # detail menampilkan media + history + overdue false pada CLOSED
    r = await client.get(f"/capa/tickets/{ticket_id}", headers=await _headers(client, GM_CWS))
    assert r.status_code == 200
    det = r.json()["data"]
    assert det["overdue"] is False and len(det["media"]) == 1 and len(det["history"]) == 4


# ─── 2. GM REJECT → rework teknisi ───────────────────────────────────────

async def test_gm_reject_returns_to_open_for_rework(client: AsyncClient) -> None:
    fx = await _fixtures()
    ticket_id = await _seed_one_ticket(client, fx, fail_code=_pick_item(fx, life_safety=True)["code"])

    await client.post(f"/capa/tickets/{ticket_id}/resolve", headers=await _headers(client, HOD_CWS),
                      json={"note": "Perbaikan awal", "media": []})
    r = await client.post(f"/capa/tickets/{ticket_id}/verify/gm", headers=await _headers(client, GM_CWS),
                          json={"decision": "REJECT", "note": "Foto belum jalan, ulangi"})
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "OPEN"

    # teknisi boleh resolve lagi dari OPEN (rework)
    r = await client.post(f"/capa/tickets/{ticket_id}/resolve", headers=await _headers(client, HOD_CWS),
                          json={"note": "Perbaikan lengkap", "media": _after_media()})
    assert r.status_code == 200
    assert r.json()["data"]["ticket"]["status"] == "AWAITING_GM"

    hist = await _history(client, ticket_id)
    assert ("AWAITING_GM", "OPEN") in [(h["from_status"], h["to_status"]) for h in hist]


# ─── 3. QA REOPEN → cycle penuh lagi ─────────────────────────────────────

async def test_qa_reopen_returns_to_open_and_first_submitted_at_preserved(client: AsyncClient) -> None:
    fx = await _fixtures()
    ticket_id = await _seed_one_ticket(client, fx, fail_code=_pick_item(fx, life_safety=True)["code"])

    h_hod = await _headers(client, HOD_CWS)
    await client.post(f"/capa/tickets/{ticket_id}/resolve", headers=h_hod,
                      json={"note": "Perbaikan", "media": _after_media()})
    first_submitted = (await client.get(f"/capa/tickets/{ticket_id}",
                                        headers=await _headers(client, CORP_EXEC))).json()["data"]["submitted_at"]
    await _confirm_after_media(client, ticket_id)
    await client.post(f"/capa/tickets/{ticket_id}/verify/gm", headers=await _headers(client, GM_CWS),
                      json={"decision": "APPROVE"})
    r = await client.post(f"/capa/tickets/{ticket_id}/verify/qa", headers=await _headers(client, CORP_AUDITOR),
                          json={"decision": "REOPEN", "note": "Alat uji tak dilampirkan"})
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "OPEN"

    # cycle kedua → closed
    await client.post(f"/capa/tickets/{ticket_id}/resolve", headers=h_hod,
                      json={"note": "Kelengkapan terpasang", "media": _after_media()})
    await _confirm_after_media(client, ticket_id)
    await client.post(f"/capa/tickets/{ticket_id}/verify/gm", headers=await _headers(client, GM_CWS),
                      json={"decision": "APPROVE"})
    r = await client.post(f"/capa/tickets/{ticket_id}/verify/qa", headers=await _headers(client, CORP_AUDITOR),
                          json={"decision": "CLOSE"})
    assert r.status_code == 200 and r.json()["data"]["status"] == "CLOSED"

    # submitted_at = first submission (timeline SLA stabil)
    det = (await client.get(f"/capa/tickets/{ticket_id}",
                            headers=await _headers(client, CORP_EXEC))).json()["data"]
    assert det["submitted_at"] == first_submitted

    hist = await _history(client, ticket_id)
    assert ("AWAITING_QA", "OPEN") in [(h["from_status"], h["to_status"]) for h in hist]


# ─── 4. Transisi ilegal → 409, filter invalid → 422 ──────────────────────

async def test_invalid_transitions_conflict_409(client: AsyncClient) -> None:
    fx = await _fixtures()
    ticket_id = await _seed_one_ticket(client, fx, fail_code=_pick_item(fx, life_safety=True)["code"])

    h_hod = await _headers(client, HOD_CWS)
    h_gm = await _headers(client, GM_CWS)

    # resolve dari OPEN legal; verify/gm dari OPEN ilegal
    r = await client.post(f"/capa/tickets/{ticket_id}/verify/gm", headers=h_gm, json={"decision": "APPROVE"})
    assert r.status_code in (409, 403)
    r = await client.post(f"/capa/tickets/{ticket_id}/verify/qa", headers=await _headers(client, CORP_AUDITOR),
                          json={"decision": "CLOSE"})
    assert r.status_code in (409, 403)

    await client.post(f"/capa/tickets/{ticket_id}/resolve", headers=h_hod,
                      json={"note": "Fix", "media": _after_media()})
    await _confirm_after_media(client, ticket_id)
    await client.post(f"/capa/tickets/{ticket_id}/verify/gm", headers=h_gm,
                      json={"decision": "APPROVE"})
    # qa close → CLOSED
    r = await client.post(f"/capa/tickets/{ticket_id}/verify/qa", headers=await _headers(client, CORP_AUDITOR),
                          json={"decision": "CLOSE"})
    assert r.status_code == 200

    # CLOSED: resolve / assign / gm → 409
    r = await client.post(f"/capa/tickets/{ticket_id}/resolve", headers=h_hod,
                          json={"note": "Fix lanjutan", "media": []})
    assert r.status_code == 409
    r = await client.post(f"/capa/tickets/{ticket_id}/assign", headers=h_gm,
                          json={"assigned_to": str(await _rid(client, HOD_CWS))})
    assert r.status_code == 409
    r = await client.post(f"/capa/tickets/{ticket_id}/verify/gm", headers=h_gm, json={"decision": "APPROVE"})
    assert r.status_code == 409
    r = await client.post(f"/capa/tickets/{ticket_id}/escalate", headers=h_gm)
    assert r.status_code == 409

    # filter invalid
    r = await client.get("/capa/tickets?priority=7", headers=await _headers(client, CORP_EXEC))
    assert r.status_code == 422
    r = await client.get("/capa/tickets?status=NOPE", headers=await _headers(client, CORP_EXEC))
    assert r.status_code == 422


async def _rid(client: AsyncClient, email: str):
    async with SessionLocal() as s:
        return await s.scalar(text("SELECT uuid::text FROM users WHERE email=:e"), {"e": email})


# ─── 5. RBAC guards ──────────────────────────────────────────────────────

async def test_rbac_guards(client: AsyncClient) -> None:
    fx = await _fixtures()
    ticket_id = await _seed_one_ticket(client, fx, fail_code=_pick_item(fx, life_safety=True)["code"])

    # HOD tidak boleh verify/gm (perlu capa:manage:hotel)
    r = await client.post(f"/capa/tickets/{ticket_id}/verify/gm", headers=await _headers(client, HOD_CWS),
                          json={"decision": "APPROVE"})
    assert r.status_code == 403

    # GM tidak boleh resolve (perlu capa:resolve:hotel)
    r = await client.post(f"/capa/tickets/{ticket_id}/resolve", headers=await _headers(client, GM_CWS),
                          json={"note": "Fix", "media": []})
    assert r.status_code == 403

    # GM tidak boleh verify/qa (perlu capa:approve)
    r = await client.post(f"/capa/tickets/{ticket_id}/verify/qa", headers=await _headers(client, GM_CWS),
                          json={"decision": "CLOSE"})
    assert r.status_code == 403

    # Sales (tanpa capa perm) tidak boleh baca detail
    r = await client.get(f"/capa/tickets/{ticket_id}", headers=await _headers(client, SALES_CWS))
    assert r.status_code == 403

    # GM tidak boleh list tanpa filter hotel (perlu capa:read:global)
    r = await client.get("/capa/tickets", headers=await _headers(client, GM_CWS))
    assert r.status_code == 403

    # CORP_EXEC & CORP_AUDITOR bisa list global + baca detail
    for email in (CORP_EXEC, CORP_AUDITOR):
        r = await client.get("/capa/tickets", headers=await _headers(client, email))
        assert r.status_code == 200
    r = await client.get(f"/capa/tickets/{ticket_id}", headers=await _headers(client, CORP_AUDITOR))
    assert r.status_code == 200


# ─── 6. assign + list filters + overdue ──────────────────────────────────

async def test_assign_and_list_filters(client: AsyncClient) -> None:
    fx = await _fixtures()
    t1 = await _seed_one_ticket(client, fx, fail_code=_pick_item(fx, life_safety=True)["code"])
    t2 = await _seed_one_ticket(client, fx, fail_code=_pick_item(fx, life_safety=False)["code"])

    # assign oleh GM → assigned_to + history from==to
    hod_id = str(await _rid(client, HOD_CWS))
    r = await client.post(f"/capa/tickets/{t1}/assign", headers=await _headers(client, GM_CWS),
                          json={"assigned_to": hod_id, "note": "Handle oleh security"})
    assert r.status_code == 200, r.text
    assert str(r.json()["data"]["assigned_to"]) == hod_id

    # filter priority: semua hasil = priority yg diminta (WHERE clause deterministik;
    # membership absolut tidak dipakai karena DB dev persistent + pagination 50)
    r = await client.get("/capa/tickets?hotel_id={}&priority=1".format(fx["hotel_id"]),
                         headers=await _headers(client, CORP_EXEC))
    assert r.status_code == 200
    assert r.json()["data"] and all(x["priority"] == 1 for x in r.json()["data"])
    r = await client.get("/capa/tickets?hotel_id={}&priority=2".format(fx["hotel_id"]),
                         headers=await _headers(client, CORP_EXEC))
    assert r.status_code == 200 and all(x["priority"] == 2 for x in r.json()["data"])

    # filter status (semua OPEN karena belum di-resolve)
    r = await client.get("/capa/tickets?status=OPEN", headers=await _headers(client, CORP_AUDITOR))
    assert r.status_code == 200 and all(x["status"] == "OPEN" for x in r.json()["data"])

    # overdue: t1 di-set due_at masa lalu → utk milik test ini hanya t1.
    # Perbandingan relatif (DB dev persistent + pagination 50), lalu restore.
    r = await client.get("/capa/tickets?only_overdue=true", headers=await _headers(client, CORP_AUDITOR))
    before = {x["id"] for x in r.json()["data"]}
    assert before & {t1, t2} == set()  # t1 & t2 belum overdue
    async with SessionLocal() as s:
        await s.execute(text(
            "UPDATE capa_tickets SET due_at=now() - interval '2 hours' WHERE uuid::text=:id"), {"id": t1})
        await s.commit()
    r = await client.get("/capa/tickets?only_overdue=true", headers=await _headers(client, CORP_AUDITOR))
    ids = {x["id"] for x in r.json()["data"]}
    assert ids & {t1, t2} == {t1}  # hanya t1 yang overdue
    async with SessionLocal() as s:  # restore agar tidak menumpuk antar-run
        await s.execute(text(
            "UPDATE capa_tickets SET due_at=now() + interval '24 hours' WHERE uuid::text=:id"), {"id": t1})
        await s.commit()


# ─── 7. create manual + media validation + escalate ──────────────────────

async def test_create_ticket_manual_idempotent_and_media_validation(client: AsyncClient) -> None:
    fx = await _fixtures()
    # butuh 1 finding real: publish life-safety fail tapi hapus tiket auto → buat manual
    h = await _headers(client, CORP_AUDITOR)
    r = await client.post("/audit/sessions", headers=h, json={
        "hotel_id": fx["hotel_id"], "department": "SECURITY_RISK", "date_start": _unique_date(),
        "template_id": fx["template_id"],
    })
    sess_id = r.json()["data"]["id"]
    now = datetime.now(UTC)
    fail_code = _pick_item(fx, life_safety=True)["code"]
    scores = []
    for it in fx["items"]:
        v = {"value": "NO"} if it["code"] == fail_code else _pass_value(it)
        scores.append({"item_id": str(it["id"]), **v, "is_na": False,
                       "scored_at": now.isoformat(), "updated_at": now.isoformat()})
    await client.post(f"/audit/sessions/{sess_id}/items", json={"scores": scores}, headers=h)
    await client.post(f"/audit/sessions/{sess_id}/submit", headers=h)
    await client.post(f"/audit/sessions/{sess_id}/publish", headers=h)

    # hapus tiket auto agar bisa uji create manual terpisah
    async with SessionLocal() as s:
        finding_id = await s.scalar(text(
            "SELECT f.id FROM findings f WHERE f.session_id="
            "(SELECT id FROM audit_sessions WHERE uuid::text=:s) LIMIT 1"), {"s": sess_id})
        await s.execute(text("DELETE FROM capa_tickets WHERE finding_id=:f"), {"f": finding_id})
        await s.commit()

    r = await client.post("/capa/tickets", headers=await _headers(client, GM_CWS),
                          json={"finding_id": str(finding_id)})
    assert r.status_code == 201, r.text
    ticket = r.json()["data"]
    assert ticket["status"] == "OPEN" and ticket["priority"] == 1 and ticket["sla_hours"] == 24
    assert ticket["receipt_id"].startswith("CAPA-")

    # idempoten: create ulang finding sama → tiket sama (bukan duplikat)
    r = await client.post("/capa/tickets", headers=await _headers(client, GM_CWS),
                          json={"finding_id": str(finding_id)})
    assert r.status_code == 201 and r.json()["data"]["id"] == ticket["id"]

    # media validation: BEFORE → 422; size melebihi 400KB → 422; checksum invalid → 422
    bad_phase = _after_media()[0] | {"phase": "BEFORE"}
    r = await client.post(f"/capa/tickets/{ticket['id']}/resolve", headers=await _headers(client, HOD_CWS),
                          json={"note": "Fix", "media": [bad_phase]})
    assert r.status_code == 422
    big = _after_media()[0] | {"size_bytes": 999_999_999}
    r = await client.post(f"/capa/tickets/{ticket['id']}/resolve", headers=await _headers(client, HOD_CWS),
                          json={"note": "Fix", "media": [big]})
    assert r.status_code == 422
    bad_checksum = _after_media()[0] | {"checksum_sha256": "zz"}
    r = await client.post(f"/capa/tickets/{ticket['id']}/resolve", headers=await _headers(client, HOD_CWS),
                          json={"note": "Fix", "media": [bad_checksum]})
    assert r.status_code == 422

    # escalate: level naik, openapi /escalate (alert receiver di-seed 7b)
    r = await client.post(f"/capa/tickets/{ticket['id']}/escalate", headers=await _headers(client, GM_CWS))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["escalation_level"] == 1
    hist = await _history(client, ticket['id'])
    assert any(h["to_status"] == h["from_status"] and "Escalation" in (h["note"] or "") for h in hist)