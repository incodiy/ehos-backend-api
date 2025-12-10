"""Audit session CRUD + offline sync + conflict resolution (F-02/F-05) — task 6d.

Berjalan terhadap seeded dev DB. Alur lengkap: create (bind template LOCKED /
brand-tier fallback) → bulk upsert nilai → submit → publish (verdict 6c +
findings) → offline sync push/pull + timestamp-merge conflict → resolve.

Pakai template SECURITY_RISK Universal (15 item, 9 is_life_safety) agar hazard
& CAPA-precursor teruji dengan data DB nyata (Aturan 2, H1-H4).
"""

import uuid
from datetime import UTC, date, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import text

from app.db.session import SessionLocal

SEED_PASSWORD = "Ehos#2026!"
CORP_AUDITOR = "corp.auditor@ehos.local"
GM_CWS = "gm.cws@ehos.local"
GM_SQYO = "gm.sqyo@ehos.local"


async def _login(client: AsyncClient, email: str) -> dict:
    r = await client.post(
        "/auth/login",
        json={"email": email, "password": SEED_PASSWORD, "remember_me": False},
    )
    assert r.status_code == 200, (email, r.text)
    return r.json()["data"]["access_token"]


async def _headers(client: AsyncClient, email: str) -> dict:
    return {"Authorization": f"Bearer {await _login(client, email)}"}


# ─── data fixtures (dev DB) ──────────────────────────────────────────────

_DATEFIX_BASE = date(2050, 1, 1) + timedelta(days=uuid.uuid4().int % 50000)
_DATEFIX_SEQ = 0


def _unique_date() -> str:
    """Tanggal unik anti-bentrok (uq hotel+dept+date_start): base acak per-run
    + counter monotonik agar antar-test dalam satu run selalu beda."""
    global _DATEFIX_SEQ
    _DATEFIX_SEQ += 1
    return (_DATEFIX_BASE + timedelta(days=_DATEFIX_SEQ)).isoformat()


async def _fixtures():
    """Hotel CWS, template SECURITY_RISK Universal (LOCKED) + item-nya."""
    async with SessionLocal() as s:
        hotel_id = await s.scalar(text("SELECT id FROM hotels WHERE code='CWS'"))
        tpl = await s.execute(text(
            "SELECT id, uuid::text, brand_tier FROM checklist_templates "
            "WHERE department='SECURITY_RISK' AND status='LOCKED' "
            "ORDER BY locked_at DESC LIMIT 1"
        ))
        template_id, template_uuid, brand_tier = tpl.first()
        items = await s.execute(text(
            "SELECT i.id, i.uuid::text, i.code, i.rubric_type, i.max_score, i.is_life_safety "
            "FROM checklist_items i "
            "JOIN checklist_sections sec ON sec.id=i.section_id "
            "WHERE sec.template_id=:t ORDER BY i.sort_order, i.code"
        ), {"t": template_id})
        return {"hotel_id": hotel_id, "template_id": template_id,
                "template_uuid": template_uuid,
                "brand_tier": brand_tier,
                "items": [dict(row) for row in items.mappings()]}


def _pass_value(it: dict) -> dict:
    """Nilai yang membuat item lulus (ratio 1.0)."""
    if it["rubric_type"] == "MULTI_ROOM":
        return {"value": "YES"}
    if it["rubric_type"] == "NUMERIC_SCALE":
        return {"value": str(it["max_score"])}
    return {"value": "YES"}


async def _score_all(client: AsyncClient, sess_id: str, fx: dict, *, fail_code: str | None = None,
                     timestamp: datetime | None = None) -> int:
    now = timestamp or datetime.now(UTC)
    scores = []
    for it in fx["items"]:
        v = _pass_value(it)
        if fail_code and it["code"] == fail_code and it["rubric_type"] == "TRAFFIC_LIGHT":
            v = {"value": "NO"}
        scores.append({"item_id": str(it["id"]), **v, "is_na": False,
                       "scored_at": now.isoformat(), "updated_at": now.isoformat()})
    h = {"Authorization": f"Bearer {await _login(client, CORP_AUDITOR)}"}
    r = await client.post(f"/audit/sessions/{sess_id}/items", json={"scores": scores}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["data"]


# ─── create ──────────────────────────────────────────────────────────────

async def test_create_session_explicit_locked_template(client: AsyncClient) -> None:
    fx = await _fixtures()
    h = await _headers(client, CORP_AUDITOR)
    r = await client.post("/audit/sessions", headers=h, json={
        "hotel_id": str(fx["hotel_id"]),
        "template_id": str(fx["template_id"]),
        "department": "SECURITY_RISK", "date_start": _unique_date(),
        "audit_type": "FULL",
    })
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert d["status"] == "DRAFT"
    assert d["template_id"] == fx["template_uuid"]
    assert d["department"] == "SECURITY_RISK"
    assert d["origin"] == "SYSTEM"
    assert d["total_score"] is None


async def test_create_session_auto_brand_tier_fallback(client: AsyncClient) -> None:
    """Tidak ada template Midscale (CWS) → turun ke universal (brand_tier=None)."""
    fx = await _fixtures()
    assert fx["brand_tier"] is None  # universal, bukan tier persis
    h = await _headers(client, CORP_AUDITOR)
    r = await client.post("/audit/sessions", headers=h, json={
        "hotel_id": str(fx["hotel_id"]), "department": "SECURITY_RISK", "date_start": _unique_date(),
    })
    assert r.status_code == 201, r.text
    assert r.json()["data"]["template_id"] == fx["template_uuid"]


async def test_create_rejects_unlocked_or_wrong_department(client: AsyncClient) -> None:
    fx = await _fixtures()
    h = await _headers(client, CORP_AUDITOR)
    # template ACTIVE (bukan LOCKED)
    async with SessionLocal() as s:
        draft_id = await s.scalar(text(
            "SELECT id FROM checklist_templates WHERE department='SECURITY_RISK' "
            "AND status!='LOCKED' LIMIT 1"
        ))
        if draft_id is None:
            row = await s.execute(text(
                "INSERT INTO checklist_templates (uuid, department, name, version, status, published_by, created_at) "
                "VALUES (gen_random_uuid(), 'SECURITY_RISK', 'Draft Test', 'vDraft.1', 'DRAFT', (SELECT id FROM users LIMIT 1), now()) RETURNING id"
            ))
            draft_id = row.scalar_one()
            await s.commit()
    r = await client.post("/audit/sessions", headers=h, json={
        "hotel_id": str(fx["hotel_id"]), "department": "SECURITY_RISK", "date_start": _unique_date(),
        "template_id": str(draft_id),
    })
    assert r.status_code == 422
    assert "LOCKED" in r.json()["detail"]

    # template department beda (HOUSEKEEPING luxury)
    async with SessionLocal() as s:
        hk_id = await s.scalar(text(
            "SELECT id FROM checklist_templates WHERE department='HOUSEKEEPING' "
            "AND status='LOCKED' LIMIT 1"
        ))
    r = await client.post("/audit/sessions", headers=h, json={
        "hotel_id": str(fx["hotel_id"]), "department": "SECURITY_RISK", "date_start": _unique_date(),
        "template_id": str(hk_id),
    })
    assert r.status_code == 422


async def test_create_requires_running_scope_for_hotel(client: AsyncClient) -> None:
    """GM SQYO tidak berhak run audit di CWS (scope lintas hotel)."""
    fx = await _fixtures()
    h = await _headers(client, GM_SQYO)
    r = await client.post("/audit/sessions", headers=h, json={
        "hotel_id": str(fx["hotel_id"]), "department": "SECURITY_RISK", "date_start": _unique_date(),
        "template_id": str(fx["template_id"]),
    })
    assert r.status_code == 403


async def test_client_id_dedupe_create(client: AsyncClient) -> None:
    fx = await _fixtures()
    h = await _headers(client, CORP_AUDITOR)
    cid = str(uuid.uuid4())
    body = {"hotel_id": str(fx["hotel_id"]), "department": "SECURITY_RISK", "date_start": _unique_date(),
            "template_id": str(fx["template_id"]), "client_id": cid}
    r1 = await client.post("/audit/sessions", headers=h, json=body)
    r2 = await client.post("/audit/sessions", headers=h, json=body)
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["data"]["id"] == r2.json()["data"]["id"]


# ─── scoring → submit → publish ──────────────────────────────────────────

async def test_bulk_upsert_submit_and_detail(client: AsyncClient) -> None:
    fx = await _fixtures()
    h = await _headers(client, CORP_AUDITOR)
    r = await client.post("/audit/sessions", headers=h, json={
        "hotel_id": str(fx["hotel_id"]), "department": "SECURITY_RISK", "date_start": _unique_date(),
        "template_id": str(fx["template_id"]),
    })
    sess_id = r.json()["data"]["id"]
    n = len(fx["items"])
    up = await _score_all(client, sess_id, fx)
    assert up["upserted"] == n
    assert up["conflicts"] == 0

    body = (await client.get(f"/audit/sessions/{sess_id}", headers=h)).json()
    assert len(body["data"]["items"]) == n
    assert body["data"]["session"]["status"] == "IN_PROGRESS"

    rs = await client.post(f"/audit/sessions/{sess_id}/submit", headers=h)
    assert rs.status_code == 200, rs.text
    assert rs.json()["data"]["status"] == "SUBMITTED"

    # Reopen test (SUBMITTED -> IN_PROGRESS)
    ro = await client.post(f"/audit/sessions/{sess_id}/reopen", headers=h)
    assert ro.status_code == 200, ro.text
    assert ro.json()["data"]["status"] == "IN_PROGRESS"

    # Re-submit
    rs2 = await client.post(f"/audit/sessions/{sess_id}/submit", headers=h)
    assert rs2.status_code == 200
    assert rs2.json()["data"]["status"] == "SUBMITTED"


async def test_publish_all_yes_pass(client: AsyncClient) -> None:
    fx = await _fixtures()
    h = await _headers(client, CORP_AUDITOR)
    r = await client.post("/audit/sessions", headers=h, json={
        "hotel_id": str(fx["hotel_id"]), "department": "SECURITY_RISK", "date_start": _unique_date(),
        "template_id": str(fx["template_id"]),
    })
    sess_id = r.json()["data"]["id"]
    await _score_all(client, sess_id, fx)
    await client.post(f"/audit/sessions/{sess_id}/submit", headers=h)
    rp = await client.post(f"/audit/sessions/{sess_id}/publish", headers=h)
    assert rp.status_code == 200, rp.text
    d = rp.json()["data"]
    assert d["pass_fail"] == "PASS"
    assert d["total_score"] == 100.0
    assert d["status"] == "PUBLISHED"
    assert d["published_at"] is not None
    # semua item lulus → tidak ada finding
    detail = (await client.get(f"/audit/sessions/{sess_id}", headers=h)).json()
    assert detail["data"]["findings"] == []
    # re-publish / re-score ditolak
    assert (await client.post(f"/audit/sessions/{sess_id}/publish", headers=h)).status_code == 409
    assert (await client.post(f"/audit/sessions/{sess_id}/items",
                              json={"scores": []}, headers=h)).status_code == 409


async def test_publish_life_safety_hazard_fail_and_finding(client: AsyncClient) -> None:
    fx = await _fixtures()
    async with SessionLocal() as s:
        ls_item = (await s.execute(text(
            "SELECT i.code FROM checklist_items i "
            "JOIN checklist_sections sec ON sec.id=i.section_id "
            "WHERE sec.template_id=:t AND i.is_life_safety AND i.rubric_type='TRAFFIC_LIGHT' "
            "LIMIT 1"
        ), {"t": fx["template_id"]})).first()[0]
    h = await _headers(client, CORP_AUDITOR)
    r = await client.post("/audit/sessions", headers=h, json={
        "hotel_id": str(fx["hotel_id"]), "department": "SECURITY_RISK", "date_start": _unique_date(),
        "template_id": str(fx["template_id"]),
    })
    sess_id = r.json()["data"]["id"]
    await _score_all(client, sess_id, fx, fail_code=ls_item)
    await client.post(f"/audit/sessions/{sess_id}/submit", headers=h)
    rp = await client.post(f"/audit/sessions/{sess_id}/publish", headers=h)
    assert rp.status_code == 200, rp.text
    d = rp.json()["data"]
    assert d["pass_fail"] == "FAIL"      # ≥80% tapi hazard life-safety menahan PASS
    assert d["total_score"] < 100.0
    detail = (await client.get(f"/audit/sessions/{sess_id}", headers=h)).json()
    findings = detail["data"]["findings"]
    assert len(findings) == 1
    assert findings[0]["is_life_safety"] is True
    assert findings[0]["severity"] == "CRITICAL"   # trigger CAPA P1 di 6e
    assert d["total_score"] > 80.0


async def test_publish_fail_when_unscored(client: AsyncClient) -> None:
    fx = await _fixtures()
    h = await _headers(client, CORP_AUDITOR)
    r = await client.post("/audit/sessions", headers=h, json={
        "hotel_id": str(fx["hotel_id"]), "department": "SECURITY_RISK", "date_start": _unique_date(),
        "template_id": str(fx["template_id"]),
    })
    sess_id = r.json()["data"]["id"]
    await _score_all(client, sess_id, fx)
    # hapus sebagian nilai → ada item miss
    detail = (await client.get(f"/audit/sessions/{sess_id}", headers=h)).json()
    victim = detail["data"]["items"][0]
    items = detail["data"]["items"][1:]
    scores = [{"item_id": it["item_id"], "value": it["value"], "is_na": it["is_na"],
               "scored_at": it["updated_at"], "updated_at": it["updated_at"]} for it in items]
    async with SessionLocal() as s:
        await s.execute(text(
            "DELETE FROM audit_item_scores WHERE uuid::text=:i"), {"i": victim["id"]}
        )
        await s.commit()
    await client.post(f"/audit/sessions/{sess_id}/submit", headers=h)
    rp = await client.post(f"/audit/sessions/{sess_id}/publish", headers=h)
    assert rp.status_code == 200, rp.text
    assert rp.json()["data"]["pass_fail"] == "FAIL"
    assert len(scores) == len(fx["items"]) - 1


# ─── offline sync + conflict resolution ──────────────────────────────────

async def test_offline_sync_push_conflict_and_resolve(client: AsyncClient) -> None:
    fx = await _fixtures()
    h = await _headers(client, CORP_AUDITOR)
    cid = str(uuid.uuid4())
    # 1) buat sesi offline (client tracking)
    r = await client.post("/audit/sessions", headers=h, json={
        "hotel_id": str(fx["hotel_id"]), "department": "SECURITY_RISK", "date_start": _unique_date(),
        "template_id": str(fx["template_id"]), "client_id": cid,
    })
    sess_id = r.json()["data"]["id"]

    # 2) device mensinkronkan nilai di T1 (via sync, semua YES)
    t1 = datetime(2026, 9, 1, 8, 0, 0, tzinfo=UTC)
    score_rows = []
    for it in fx["items"]:
        v = _pass_value(it)
        score_rows.append({"item_id": str(it["id"]), **v, "is_na": False,
                           "scored_at": t1.isoformat(), "updated_at": t1.isoformat()})
    rp = await client.post("/audit/sessions/sync", headers=h, json={
        "session_client_id": cid, "scores": score_rows,
        "media_events": [], "device_now": t1.isoformat(),
    })
    assert rp.status_code == 200, rp.text
    assert rp.json()["data"]["upserted"] == len(fx["items"])
    assert rp.json()["data"]["conflicts"] == 0

    # 3) server-side perbarui nilai item pertama jadi NO di T2 (lebih baru)
    target = _pass_value(fx["items"][0])
    t2 = t1 + timedelta(hours=2)
    first = fx["items"][0]
    server_val = "NO" if first["rubric_type"] == "TRAFFIC_LIGHT" else target["value"]
    first_row = {"item_id": str(first["id"]), "value": server_val,
                 "is_na": False, "scored_at": t2.isoformat(), "updated_at": t2.isoformat()}
    assert first["rubric_type"] == "TRAFFIC_LIGHT"  # universal: 9/15 traffic
    r = await client.post(f"/audit/sessions/{sess_id}/items",
                          json={"scores": [first_row]}, headers=h)
    assert r.json()["data"]["upserted"] == 1

    # 4) device push ulang nilai T1 yang lama → konflik (timestmap merge F-05)
    stale_row = {"item_id": str(first["id"]), "value": target["value"], "is_na": False,
                 "scored_at": t1.isoformat(), "updated_at": t1.isoformat()}
    rp = await client.post("/audit/sessions/sync", headers=h, json={
        "session_client_id": cid, "scores": [stale_row],
        "media_events": [], "device_now": t2.isoformat(),
    })
    body = rp.json()["data"]
    assert body["conflicts"] == 1
    assert len(body["conflict_ids"]) == 1
    assert body["session_id"] == sess_id

    # 5) sesi tandai PENDING_CONFLICT, daftar konflik terbuka
    det = (await client.get(f"/audit/sessions/{sess_id}", headers=h)).json()
    assert det["data"]["session"]["sync_status"] == "PENDING_CONFLICT"
    cl = (await client.get(f"/audit/sessions/{sess_id}/conflicts", headers=h)).json()
    assert len(cl["data"]) == 1
    conflict_id = cl["data"][0]["id"]
    assert cl["data"][0]["losing_value"] == target["value"]

    # 6) resolve pilih nilai server (NO) → SYNCED kembali
    rr = await client.post(
        f"/audit/sessions/{sess_id}/conflicts/{conflict_id}/resolve",
        headers=h, json={"winning_value": "NO"},
    )
    assert rr.status_code == 200, rr.text
    assert rr.json()["data"]["resolution"] == "MANUAL"
    det = (await client.get(f"/audit/sessions/{sess_id}", headers=h)).json()
    assert det["data"]["session"]["sync_status"] == "SYNCED"

    # 7) pull incremental: nilai resolving muncul sejak T2
    rp = (await client.get(f"/audit/sessions/{sess_id}/sync", headers=h,
                          params={"since": t2.isoformat()})).json()
    pulled = [s for s in rp["data"]["scores"] if s["item_id"] == first["uuid"]]
    assert pulled and pulled[-1]["value"] == "NO"


async def test_sync_wrong_client_id_404(client: AsyncClient) -> None:
    h = await _headers(client, CORP_AUDITOR)
    r = await client.post("/audit/sessions/sync", headers=h, json={
        "session_client_id": str(uuid.uuid4()), "scores": [],
        "media_events": [], "device_now": datetime.now(UTC).isoformat(),
    })
    assert r.status_code == 404


async def test_list_requires_hotel_for_gm(client: AsyncClient) -> None:
    h = await _headers(client, GM_CWS)
    r = await client.get("/audit/sessions", headers=h)
    assert r.status_code == 403
    fx = await _fixtures()
    r = await client.get("/audit/sessions", headers=h, params={"hotel_id": str(fx["hotel_id"])})
    assert r.status_code == 200
    assert r.json()["data"] is not None
    assert "meta" in r.json()


async def test_update_and_delete_draft_session(client: AsyncClient) -> None:
    """Verifikasi PATCH dan DELETE hanya berlaku pada sesi DRAFT."""
    fx = await _fixtures()
    h = await _headers(client, CORP_AUDITOR)
    # 1) Buat sesi DRAFT
    d_start = _unique_date()
    r = await client.post("/audit/sessions", headers=h, json={
        "hotel_id": str(fx["hotel_id"]), "department": "SECURITY_RISK", "date_start": d_start,
        "template_id": str(fx["template_id"]),
    })
    assert r.status_code == 201
    sess_id = r.json()["data"]["id"]

    # 2) Update saat DRAFT
    new_date = _unique_date()
    up_res = await client.patch(f"/audit/sessions/{sess_id}", headers=h, json={
        "date_start": new_date, "audit_type": "MICRO",
    })
    assert up_res.status_code == 200, up_res.text
    assert up_res.json()["data"]["audit_type"] == "MICRO"
    assert up_res.json()["data"]["date_start"] == new_date

    # 3) Delete saat DRAFT
    del_res = await client.delete(f"/audit/sessions/{sess_id}", headers=h)
    assert del_res.status_code == 200, del_res.text

    # Pastikan sudah terhapus
    get_res = await client.get(f"/audit/sessions/{sess_id}", headers=h)
    assert get_res.status_code == 404