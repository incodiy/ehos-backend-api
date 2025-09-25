"""CAPA media split-path (task 7c, C2 / ARD-005) — presign → upload → confirm.

Presign mengeluarkan presigned PUT URL (expire 7 mnt, renew diizinkan),
confirm memverifikasi object (reachable + size + mime) → VERIFIED / FAILED.
Verifikasi di-patch agar tidak menyentuh jaringan saat unit-test; jalur storage
down dibuktikan 503 jujur (Constraint G4 — tidak pernah mengganti dengan
payload palsu). Komparasi BEFORE vs AFTER diuji dari data yang di-seed sendiri
(resolve → AFTER PENDING; BEFORE-VERIFIED di-insert langsung menandakan
four-eyes verification hub terhadap 2 fase).

Kir kerja terhadap seeded dev DB (template SECURITY_RISK, hotel CWS seperti
test_sla_escalation).
"""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from httpx import AsyncClient
from sqlalchemy import text

from app.db.session import SessionLocal
from app.models import CapaMedia
from app.services.media_storage import StorageUnavailableError

SEED_PASSWORD = "Ehos#2026!"
CORP_AUDITOR = "corp.auditor@ehos.local"
GM_CWS = "gm.cws@ehos.local"
GM_SQYO = "gm.sqyo@ehos.local"
HOD_SRM = "hod.srm.cws@ehos.local"

_DATEFIX_BASE = datetime(2021, 1, 1, tzinfo=UTC) + timedelta(days=uuid.uuid4().int % 20000)
_DATEFIX_SEQ = 0


def _unique_date() -> str:
    global _DATEFIX_SEQ
    _DATEFIX_SEQ += 1
    return (_DATEFIX_BASE + timedelta(days=_DATEFIX_SEQ)).date().isoformat()


async def _headers(client: AsyncClient, email: str) -> dict:
    r = await client.post(
        "/auth/login",
        json={"email": email, "password": SEED_PASSWORD, "remember_me": False},
    )
    assert r.status_code == 200, (email, r.text)
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


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


def _fail_item(fx: dict) -> dict:
    for it in fx["items"]:
        if it["is_life_safety"] and it["rubric_type"] == "TRAFFIC_LIGHT":
            return it
    raise AssertionError("item life_safety TRAFFIC_LIGHT tidak ditemukan")


async def _seed_open_ticket(client: AsyncClient, fx: dict) -> str:
    """Buat 1 tiket OPEN via audit fail — anti halusinasi (data nyata dari flow)."""
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


def _media_payload(count: int = 1, *, phase: str = "AFTER") -> list[dict]:
    now = datetime.now(UTC).isoformat()
    digest = hashlib.sha256(f"media-{uuid.uuid4()}".encode()).hexdigest()
    return [{
        "phase": phase,
        "source_camera": "LIVE_CAMERA",
        "object_key": f"capa/{uuid.uuid4().hex}/f-{i}.webp",
        "file_name": f"evidence-{i}.webp",
        "mime": "image/webp",
        "width": 1280,
        "height": 960,
        "size_bytes": 187_000 + i,
        "checksum_sha256": digest,
        "gps_lat": -6.200000,
        "gps_lng": 106.810000,
        "gps_valid": True,
        "captured_at": now,
    } for i in range(count)]


async def _resolve(client: AsyncClient, ticket_id: str, media: list[dict]) -> None:
    r = await client.post(f"/capa/tickets/{ticket_id}/resolve",
                          headers=await _headers(client, HOD_SRM),
                          json={"note": "Perbaikan terpasang", "media": media})
    assert r.status_code == 200, r.text


async def _resolve_one_media(client: AsyncClient, ticket_id: str) -> str:
    payload = _media_payload(1)
    await _resolve(client, ticket_id, payload)
    async with SessionLocal() as s:
        return str(await s.scalar(text(
            "SELECT id FROM capa_media WHERE ticket_id="
            "(SELECT id FROM capa_tickets WHERE uuid::text=:t) LIMIT 1"), {"t": ticket_id}))


# ─── 1. Presign PUT URL ───────────────────────────────────────────────────

async def test_presign_returns_presigned_put_url(client: AsyncClient) -> None:
    fx = await _fixtures()
    t = await _seed_open_ticket(client, fx)
    media_id = await _resolve_one_media(client, t)

    with patch("app.services.capa_media.presigned_put_url",
               return_value="https://minio/ehos-media/x?X-Amz-Signature=abc") as mock:
        r = await client.post(f"/capa/tickets/{t}/media/{media_id}/presign",
                              headers=await _headers(client, GM_CWS))
    assert r.status_code == 200, r.text
    mock.assert_called_once()
    data = r.json()["data"]
    assert data["presigned_url"].startswith("https://minio/ehos-media/")
    assert data["upload_status"] == "PRESIGNED"
    assert data["expires_in"] == 420


async def test_presigned_url_structure_is_sigv4(client: AsyncClient) -> None:
    from app.services.media_storage import presigned_put_url

    url = presigned_put_url("capa/unit-test/img.webp", content_type="image/webp")
    assert url.startswith("http://localhost:9000/ehos-media/capa/unit-test/img.webp?")
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url
    assert "X-Amz-Credential=" in url
    assert "X-Amz-SignedHeaders=host%3Bcontent-type" in url
    signature = url.split("X-Amz-Signature=")[1]
    assert len(signature) == 64
    assert all(c in "0123456789abcdef" for c in signature)


# ─── 2. Confirm: VERIFIED / FAILED / 503 — tanpa payload palsu ────────────

async def test_confirm_verified_then_idempotent(client: AsyncClient) -> None:
    fx = await _fixtures()
    t = await _seed_open_ticket(client, fx)
    media_id = await _resolve_one_media(client, t)

    with patch("app.services.capa_media.verify_object", return_value=True):
        r = await client.post(f"/capa/tickets/{t}/media/{media_id}/confirm",
                              headers=await _headers(client, GM_CWS))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["upload_status"] == "VERIFIED"

    # idempotent: confirm ulang di object VERIFIED tetap 200 VERIFIED
    r2 = await client.post(f"/capa/tickets/{t}/media/{media_id}/confirm",
                           headers=await _headers(client, GM_CWS))
    assert r2.status_code == 200
    assert r2.json()["data"]["upload_status"] == "VERIFIED"


async def test_confirm_failed_then_presign_retry(client: AsyncClient) -> None:
    fx = await _fixtures()
    t = await _seed_open_ticket(client, fx)
    media_id = await _resolve_one_media(client, t)

    with patch("app.services.capa_media.verify_object", return_value=False):
        r = await client.post(f"/capa/tickets/{t}/media/{media_id}/confirm",
                              headers=await _headers(client, GM_CWS))
    assert r.json()["data"]["upload_status"] == "FAILED"

    # retry diizinkan dari FAILED → presign ulang → PRESIGNED
    with patch("app.services.capa_media.presigned_put_url", return_value="https://minio/x"):
        r2 = await client.post(f"/capa/tickets/{t}/media/{media_id}/presign",
                               headers=await _headers(client, GM_CWS))
    assert r2.status_code == 200
    assert r2.json()["data"]["upload_status"] == "PRESIGNED"


async def test_confirm_storage_down_returns_503_honest(client: AsyncClient) -> None:
    fx = await _fixtures()
    t = await _seed_open_ticket(client, fx)
    media_id = await _resolve_one_media(client, t)

    with patch("app.services.capa_media.verify_object",
               side_effect=StorageUnavailableError("MinIO unreachable")):
        r = await client.post(f"/capa/tickets/{t}/media/{media_id}/confirm",
                              headers=await _headers(client, GM_CWS))
    assert r.status_code == 503
    assert r.json()["detail"] == "MinIO unreachable"


async def test_confirm_rejects_wrong_object_key_guard(client: AsyncClient) -> None:
    fx = await _fixtures()
    t = await _seed_open_ticket(client, fx)
    media_id = await _resolve_one_media(client, t)

    r = await client.post(f"/capa/tickets/{t}/media/{media_id}/confirm",
                          json={"object_key": "capa/wrong/path.bin"},
                          headers=await _headers(client, GM_CWS))
    assert r.status_code == 422


# ─── 3. State guard & RBAC scope ──────────────────────────────────────────

async def test_presign_verified_media_returns_409(client: AsyncClient) -> None:
    fx = await _fixtures()
    t = await _seed_open_ticket(client, fx)
    media_id = await _resolve_one_media(client, t)

    with patch("app.services.capa_media.verify_object", return_value=True):
        await client.post(f"/capa/tickets/{t}/media/{media_id}/confirm",
                          headers=await _headers(client, GM_CWS))

    r = await client.post(f"/capa/tickets/{t}/media/{media_id}/presign",
                          headers=await _headers(client, GM_CWS))
    assert r.status_code == 409


async def test_media_operations_reject_gm_outside_hotel_scope(client: AsyncClient) -> None:
    fx = await _fixtures()
    t = await _seed_open_ticket(client, fx)
    media_id = await _resolve_one_media(client, t)

    # gm.sqyo scoped SQYO — presign/confirm tiket CWS harus 403 (tenant isolation)
    h = await _headers(client, GM_SQYO)
    for path in (f"/capa/tickets/{t}/media/{media_id}/presign",
                 f"/capa/tickets/{t}/media/{media_id}/confirm"):
        r = await client.post(path, headers=h)
        assert r.status_code == 403, (path, r.text)


# ─── 4. List & Before/After comparison (four-eyes verification hub) ───────

async def test_media_list_summary_before_after(client: AsyncClient) -> None:
    fx = await _fixtures()
    t = await _seed_open_ticket(client, fx)

    # resolve → 2 AFTER PENDING; 1 diverifikasi (confirm)
    await _resolve(client, t, _media_payload(2))
    async with SessionLocal() as s:
        media_ids = (await s.scalars(text(
            "SELECT id FROM capa_media WHERE ticket_id="
            "(SELECT id FROM capa_tickets WHERE uuid::text=:t) ORDER BY captured_at"), {"t": t})).all()
    confirmed = str(media_ids[0])
    with patch("app.services.capa_media.verify_object", return_value=True):
        await client.post(f"/capa/tickets/{t}/media/{confirmed}/confirm",
                          headers=await _headers(client, GM_CWS))

    # BEFORE VERIFIED (setup langsung — simulates evidence before yang sudah
    # ter-verifikasi utk verification hub menampilkan 2 fase)
    async with SessionLocal() as s:
        internal_tid = await s.scalar(text("SELECT id FROM capa_tickets WHERE uuid::text=:u"), {"u": t})
        s.add(CapaMedia(ticket_id=internal_tid, phase="BEFORE", source_camera="LIVE_CAMERA",
                        object_key=f"capa/{uuid.uuid4().hex}/before.webp",
                        file_name="before-1.webp", mime="image/webp",
                        width=1280, height=960, size_bytes=150000,
                        checksum_sha256=hashlib.sha256(b"before").hexdigest(),
                        gps_valid=False, captured_at=datetime.now(UTC),
                        server_captured_at=datetime.now(UTC), upload_status="VERIFIED"))
        await s.commit()

    r = await client.get(f"/capa/tickets/{t}/media", headers=await _headers(client, GM_CWS))
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    summary = body["summary"]
    assert summary["before"] == {"count": 1, "verified": 1}
    assert summary["after"] == {"count": 2, "verified": 1}
    assert summary["has_verified_after"] is True and summary["ready"] is True
    after_statuses = sorted(m["upload_status"] for m in body["items"] if m["phase"] == "AFTER")
    assert after_statuses == ["PENDING", "VERIFIED"]
    before_statuses = sorted(m["upload_status"] for m in body["items"] if m["phase"] == "BEFORE")
    assert before_statuses == ["VERIFIED"]


async def test_detail_media_summary_visible(client: AsyncClient) -> None:
    fx = await _fixtures()
    t = await _seed_open_ticket(client, fx)
    await _resolve(client, t, _media_payload(1))

    r = await client.get(f"/capa/tickets/{t}", headers=await _headers(client, GM_CWS))
    assert r.status_code == 200
    summary = r.json()["data"]["media_summary"]
    assert summary["after"]["count"] == 1
    assert summary["after"]["verified"] == 0
    assert summary["has_verified_after"] is False