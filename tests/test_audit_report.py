"""Ekspor laporan audit PDF (PRD-F-02/F-22) — task 6f.

GET /audit/sessions/{id}/report.pdf → application/pdf, localized via
Accept-Language (id kanonikal, en fallback F3). Konten dibangun dari DB real
(agg 6b + verdict 6c + findings 6d + tiket CAPA 6e). RBAC: corporate global
atau hotel-scope (GM hotel sendiri boleh; GM hotel lain → 403).
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from io import BytesIO

from httpx import AsyncClient
from pypdf import PdfReader
from sqlalchemy import text

from app.db.session import SessionLocal

SEED_PASSWORD = "Ehos#2026!"
CORP_AUDITOR = "corp.auditor@ehos.local"
GM_CWS = "gm.cws@ehos.local"
GM_SQYO = "gm.sqyo@ehos.local"


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
                "items": [dict(row) for row in items.mappings()]}


def _pass_value(it: dict) -> dict:
    if it["rubric_type"] == "MULTI_ROOM":
        return {"value": "YES"}
    if it["rubric_type"] == "NUMERIC_SCALE":
        return {"value": str(it["max_score"])}
    return {"value": "YES"}


def _ls_item(fx: dict) -> dict:
    for it in fx["items"]:
        if it["is_life_safety"] and it["rubric_type"] == "TRAFFIC_LIGHT":
            return it
    raise AssertionError("item life-safety TRAFFIC_LIGHT tidak ditemukan")


async def _publish_session(client: AsyncClient, fx: dict, *, fail_code: str | None) -> str:
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
    await client.post(f"/audit/sessions/{sess_id}/items", json={"scores": scores}, headers=h)
    await client.post(f"/audit/sessions/{sess_id}/submit", headers=h)
    rp = await client.post(f"/audit/sessions/{sess_id}/publish", headers=h)
    assert rp.status_code == 200, rp.text
    return sess_id


async def _publish_session_dept(client: AsyncClient, department: str) -> str:
    """Sesi PASS (semua item lulus) utk departemen LOCKED template paling baru."""
    h = await _headers(client, CORP_AUDITOR)
    async with SessionLocal() as s:
        template_id = (await s.execute(text(
            "SELECT id FROM checklist_templates "
            "WHERE department=:d AND status='LOCKED' ORDER BY locked_at DESC LIMIT 1"
        ), {"d": department})).scalar_one()
        hotel_id = await s.scalar(text("SELECT id FROM hotels WHERE code='CWS'"))
        items = (await s.execute(text(
            "SELECT i.id, i.code, i.rubric_type, i.max_score, i.is_life_safety "
            "FROM checklist_items i JOIN checklist_sections sec ON sec.id=i.section_id "
            "WHERE sec.template_id=:t ORDER BY i.sort_order, i.code"
        ), {"t": template_id})).mappings().all()
    r = await client.post("/audit/sessions", headers=h, json={
        "hotel_id": str(hotel_id), "department": department,
        "date_start": _unique_date(), "template_id": str(template_id),
    })
    sess_id = r.json()["data"]["id"]
    now = datetime.now(UTC)
    scores = []
    for it in items:
        v = _pass_value(dict(it))
        scores.append({"item_id": str(it["id"]), **v, "is_na": False,
                       "scored_at": now.isoformat(), "updated_at": now.isoformat()})
    await client.post(f"/audit/sessions/{sess_id}/items", json={"scores": scores}, headers=h)
    await client.post(f"/audit/sessions/{sess_id}/submit", headers=h)
    rp = await client.post(f"/audit/sessions/{sess_id}/publish", headers=h)
    assert rp.status_code == 200, rp.text
    return sess_id


def _pdf_text(resp) -> str:
    data = resp.content
    assert data[:5] == b"%PDF-"
    return "\n".join((p.extract_text() or "") for p in PdfReader(BytesIO(data)).pages)


# ─── ekspor PDF ──────────────────────────────────────────────────────────

async def test_report_pdf_pass_export(client: AsyncClient) -> None:
    fx = await _fixtures()
    sess_id = await _publish_session(client, fx, fail_code=None)
    r = await client.get(f"/audit/sessions/{sess_id}/report.pdf", headers=await _headers(client, CORP_AUDITOR))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert f"audit-report-{sess_id}.pdf" in r.headers["content-disposition"]
    txt = _pdf_text(r)
    assert "CWS" in txt
    assert "SECURITY_RISK" in txt
    assert "PASS" in txt
    assert "100.0" in txt
    assert "Tidak ada temuan" in txt      # semua lulus


async def test_report_pdf_hazard_fail_with_capa(client: AsyncClient) -> None:
    fx = await _fixtures()
    sess_id = await _publish_session(
        client, fx, fail_code=_ls_item(fx)["code"])
    r = await client.get(f"/audit/sessions/{sess_id}/report.pdf", headers=await _headers(client, CORP_AUDITOR))
    assert r.status_code == 200
    txt = _pdf_text(r)
    assert "FAIL" in txt
    assert "HAZARD" in txt
    assert "CRITICAL" in txt
    assert "Tiket CAPA" in txt          # auto-CAPA 6e tercantum
    assert "Priority 1" in txt or "P1" in txt
    # finding life-safety ada (item yang digagalkan)
    assert _ls_item(fx)["code"] in txt


async def test_report_pdf_en_locale(client: AsyncClient) -> None:
    fx = await _fixtures()
    sess_id = await _publish_session(client, fx, fail_code=None)
    token = await _login(client, CORP_AUDITOR)
    r = await client.get(f"/audit/sessions/{sess_id}/report.pdf",
                         headers={"Authorization": f"Bearer {token}",
                                  "Accept-Language": "en-US,en;q=0.9"})
    assert r.status_code == 200
    txt = _pdf_text(r)
    assert "Hotel Audit Report" in txt
    assert "Audit Report" in txt

    # locale lain → fallback id (F3)
    r2 = await client.get(f"/audit/sessions/{sess_id}/report.pdf",
                          headers={"Authorization": f"Bearer {token}",
                                   "Accept-Language": "fr-FR,fr;q=0.9"})
    assert "Laporan Audit Hotel" in _pdf_text(r2)


async def test_report_pdf_en_translated_item_content(client: AsyncClient) -> None:
    """6h: konten item (question_text) di-terjemahkan dari tabel `translations`
    (real data, Constraint G) — bukan kamus hardcode. id → kanonikal; en → curated."""
    fx = await _fixtures()
    sess_id = await _publish_session(client, fx, fail_code=None)
    token = await _login(client, CORP_AUDITOR)
    async with SessionLocal() as s:
        canon, en = (await s.execute(text(
            "SELECT i.question_text, tr.value FROM checklist_items i "
            "JOIN checklist_sections sec ON sec.id=i.section_id "
            "JOIN translations tr ON tr.entity_id=i.uuid "
            "   AND tr.entity_type='checklist_item' AND tr.locale='en' AND tr.field='question_text' "
            "WHERE sec.template_id=:t AND tr.value <> i.question_text "
            "ORDER BY i.sort_order LIMIT 1"
        ), {"t": fx["template_id"]})).one()
    assert canon and en and en != canon

    r_en = await client.get(f"/audit/sessions/{sess_id}/report.pdf",
                            headers={"Authorization": f"Bearer {token}",
                                     "Accept-Language": "en-US,en;q=0.9"})
    txt_en = _pdf_text(r_en)
    assert en in txt_en, f"terjemahan EN tidak muncul di laporan EN: {en!r}"
    assert canon not in txt_en, f"teks kanonikal id bocor di laporan EN: {canon!r}"

    r_id = await client.get(f"/audit/sessions/{sess_id}/report.pdf",
                            headers={"Authorization": f"Bearer {token}"})
    txt_id = _pdf_text(r_id)
    assert canon in txt_id, f"kanonikal id tidak muncul di laporan id: {canon!r}"
    assert en not in txt_id, f"terjemahan EN bocor di laporan id: {en!r}"


async def test_report_pdf_en_translated_section(client: AsyncClient) -> None:
    """6h: nama section (HOUSEKEEPING) di-terjemahkan via `translations`
    — id 'Kebersihan Kamar' → en 'Room Cleanliness' (nilai real DB)."""
    sess_id = await _publish_session_dept(client, "HOUSEKEEPING")
    token = await _login(client, CORP_AUDITOR)
    async with SessionLocal() as s:
        canon, en = (await s.execute(text(
            "SELECT sec.name, tr.value FROM checklist_sections sec "
            "JOIN translations tr ON tr.entity_id=sec.uuid "
            "   AND tr.entity_type='checklist_section' AND tr.locale='en' AND tr.field='name' "
            "WHERE sec.template_id=(SELECT template_id FROM audit_sessions WHERE uuid::text=:sid) "
            "  AND tr.value <> sec.name LIMIT 1"
        ), {"sid": sess_id})).one()
    assert canon and en and en != canon

    r_en = await client.get(f"/audit/sessions/{sess_id}/report.pdf",
                            headers={"Authorization": f"Bearer {token}",
                                     "Accept-Language": "en-US,en;q=0.9"})
    txt_en = _pdf_text(r_en)
    assert en in txt_en, f"nama section EN tidak muncul di laporan EN: {en!r}"

    r_id = await client.get(f"/audit/sessions/{sess_id}/report.pdf",
                            headers={"Authorization": f"Bearer {token}"})
    assert canon in _pdf_text(r_id), f"nama section kanonikal id hilang: {canon!r}"


async def test_report_pdf_rbac_hotel_scope(client: AsyncClient) -> None:
    fx = await _fixtures()
    sess_id = await _publish_session(client, fx, fail_code=None)
    # GM SQYO tanpa scope CWS → 403
    r = await client.get(f"/audit/sessions/{sess_id}/report.pdf", headers=await _headers(client, GM_SQYO))
    assert r.status_code == 403
    # GM CWS (hotel sendiri) → 200
    r = await client.get(f"/audit/sessions/{sess_id}/report.pdf", headers=await _headers(client, GM_CWS))
    assert r.status_code == 200
    assert b"%PDF" in r.content