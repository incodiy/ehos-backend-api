"""Document & billing milestone tracker — PRD-F-10 / task 8f.

Endpoints medan uji:
- GET   /crm/billing                     (scoped finance, RBAC `crm:read`)
- POST  /crm/billing/milestones          (RBAC `billing:manage`, quotation ACCEPTED)
- PATCH /crm/billing/milestones/{id}     (lampirkan dokumen / tandai PAID terminal)
- POST  /notifications/remind-billing    (sweep BILLING_REMINDER jelang tutup TA)

Seeder `billing_scenarios` menyimulasikan alur dinas nyata (H2/H3):
SQYO-03 ACCEPTED → SPK/NPWP UPLOADED, BAST PAID, LPJ EXPECTED(due +5) dan
Q-8F-CWS-01 ACCEPTED → SPK OVERDUE(-2), NPWP EXPECTED(+7). Kandidat reminder:
5 non-PAID dgn due ≤ hari+14. Tenant scope + imutabilitas finansial diuji.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.db.session import SessionLocal
from app.seed.billing_scenarios import seed_billing_milestones

SEED_PASSWORD = "Ehos#2026!"
CORP_EXEC = "corp.exec@ehos.local"
SALES_CWS = "sales.cws@ehos.local"
FINANCE_CWS = "finance.cws@ehos.local"
CLIENT_PUBLIC = "client.public@ehos.local"


async def _login(client: AsyncClient, email: str) -> str:
    r = await client.post(
        "/auth/login",
        json={"email": email, "password": SEED_PASSWORD, "remember_me": False},
    )
    assert r.status_code == 200, (email, r.text)
    return r.json()["data"]["access_token"]


async def _headers(client: AsyncClient, email: str) -> dict:
    return {"Authorization": f"Bearer {await _login(client, email)}"}


async def _sql_as_uuid(query: str, **params) -> str:
    async with SessionLocal() as s:
        row = await s.execute(text(query), params or {})
        r = row.first()
        assert r is not None, f"tidak ditemukan: {query}"
        return str(r[0])


@pytest.fixture(autouse=True)
async def _ensure_seeded():
    async with SessionLocal() as s:
        actor = await s.scalar(text("SELECT id FROM users WHERE email='corp.exec@ehos.local'"))
        assert actor is not None
        await seed_billing_milestones(s, {"root.admin@ehos.local": actor, "corp.exec@ehos.local": actor})
        await s.commit()
    yield


async def _quotation_id(quotation_no: str) -> str:
    """Public uuid quotation (apa yang dipakai API/response)."""
    return await _sql_as_uuid("SELECT uuid::text FROM quotations WHERE quotation_no=:q", q=quotation_no)


# ── Create ────────────────────────────────────────────────────────────────


async def test_create_milestone_201(client: AsyncClient) -> None:
    """BAST baru di Q-8F-CWS-01 (ACCEPTED, belum ada BAST) → 201 EXPECTED."""
    tok = await _headers(client, CORP_EXEC)
    qid = await _quotation_id("Q-8F-CWS-01")
    # hermetik: buang BAST test-artefak run sebelumnya (di luar set kanonikal seeder).
    async with SessionLocal() as s:
        await s.execute(
            text(
                "DELETE FROM billing_milestones WHERE quotation_id="
                "(SELECT id FROM quotations WHERE uuid::text=:q) AND milestone_type='BAST' AND doc_no IS NULL"
            ),
            {"q": qid},
        )
        await s.commit()
    r = await client.post(
        "/crm/billing/milestones",
        headers=tok,
        json={"quotation_id": qid, "milestone_type": "BAST", "due_date": "2027-06-10", "amount": 30_000_000},
    )
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert d["milestone_type"] == "BAST"
    assert d["status"] == "EXPECTED"
    assert d["due_date"] == "2027-06-10"
    assert d["quotation_id"] == qid


async def test_create_milestone_409_not_accepted(client: AsyncClient) -> None:
    """Quotation belum ACCEPTED (DRAFT) → 409 (F-10 hanya alur menang)."""
    tok = await _headers(client, CORP_EXEC)
    qid = await _quotation_id("Q-8D-CWS-01")
    r = await client.post(
        "/crm/billing/milestones",
        headers=tok,
        json={"quotation_id": qid, "milestone_type": "SPK", "due_date": "2027-06-10"},
    )
    assert r.status_code == 409, r.text


async def test_create_milestone_duplicate_409(client: AsyncClient) -> None:
    """SPK sudah diseed utk Q-8F-CWS-01 → 409 (1 dokumen per jenis, immutable)."""
    tok = await _headers(client, CORP_EXEC)
    qid = await _quotation_id("Q-8F-CWS-01")
    r = await client.post(
        "/crm/billing/milestones",
        headers=tok,
        json={"quotation_id": qid, "milestone_type": "SPK", "due_date": "2027-06-10"},
    )
    assert r.status_code == 409, r.text


async def test_create_milestone_422_unknown_type(client: AsyncClient) -> None:
    tok = await _headers(client, CORP_EXEC)
    qid = await _quotation_id("Q-8F-CWS-01")
    r = await client.post(
        "/crm/billing/milestones",
        headers=tok,
        json={"quotation_id": qid, "milestone_type": "KONTRAK", "due_date": "2027-06-10"},
    )
    assert r.status_code == 422, r.text


async def test_create_billing_rbac_denied_missing_manage(client: AsyncClient) -> None:
    """sales.cws punya crm:read tapi bukan billing:manage → 403."""
    tok = await _headers(client, SALES_CWS)
    qid = await _quotation_id("Q-8F-CWS-01")
    r = await client.post(
        "/crm/billing/milestones",
        headers=tok,
        json={"quotation_id": qid, "milestone_type": "LPJ", "due_date": "2027-06-10"},
    )
    assert r.status_code == 403, r.text


async def test_create_billing_scope_isolated(client: AsyncClient) -> None:
    """finance.cws (scope CWS) membuat milestone utk SQYO → 403 (tenant)."""
    tok = await _headers(client, FINANCE_CWS)
    qid = await _quotation_id("Q-8D-SQYO-03")
    r = await client.post(
        "/crm/billing/milestones",
        headers=tok,
        json={"quotation_id": qid, "milestone_type": "SPK", "due_date": "2027-06-10"},
    )
    assert r.status_code == 403, r.text  # scope CWS ≠ SQYO — ditolak sebelum validasi bisnis


# ── Update / transisi ─────────────────────────────────────────────────────


async def test_transition_expected_to_uploaded(client: AsyncClient) -> None:
    """LPJ SQYO (EXPECTED) → UPLOADED wajib doc_key."""
    tok = await _headers(client, CORP_EXEC)
    mid = await _sql_as_uuid(
        "SELECT b.id FROM billing_milestones b JOIN quotations q ON b.quotation_id=q.id "
        "WHERE q.quotation_no='Q-8D-SQYO-03' AND b.milestone_type='LPJ'"
    )
    r = await client.patch(
        f"/crm/billing/milestones/{mid}",
        headers=tok,
        json={"status": "UPLOADED", "doc_key": "billing/Q-8D-SQYO-03/LPJ.pdf"},
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["status"] == "UPLOADED"
    assert d["doc_key"] == "billing/Q-8D-SQYO-03/LPJ.pdf"


async def test_uploaded_without_doc_key_422(client: AsyncClient) -> None:
    """Transisi ke UPLOADED tanpa bukti dokumen → 422 (fixture reset → kanonikal)."""
    tok = await _headers(client, CORP_EXEC)
    mid = await _sql_as_uuid(
        "SELECT b.id FROM billing_milestones b JOIN quotations q ON b.quotation_id=q.id "
        "WHERE q.quotation_no='Q-8F-CWS-01' AND b.milestone_type='NPWP'"
    )
    r = await client.patch(f"/crm/billing/milestones/{mid}", headers=tok, json={"status": "UPLOADED"})
    assert r.status_code == 422, r.text


async def test_uploaded_to_paid_terminal(client: AsyncClient) -> None:
    """NPWP SQYO (UPLOADED) → PAID → PAID tidak bisa ditarik mundur (409)."""
    tok = await _headers(client, CORP_EXEC)
    mid = await _sql_as_uuid(
        "SELECT b.id FROM billing_milestones b JOIN quotations q ON b.quotation_id=q.id "
        "WHERE q.quotation_no='Q-8D-SQYO-03' AND b.milestone_type='NPWP'"
    )
    paid = await client.patch(f"/crm/billing/milestones/{mid}", headers=tok, json={"status": "PAID"})
    assert paid.status_code == 200, paid.text
    assert paid.json()["data"]["status"] == "PAID"
    assert paid.json()["data"]["paid_at"] is not None

    revert = await client.patch(f"/crm/billing/milestones/{mid}", headers=tok, json={"status": "UPLOADED"})
    assert revert.status_code == 409, revert.text


async def test_update_scope_isolated(client: AsyncClient) -> None:
    """finance.cws (scope CWS) update milestone SQYO → 403."""
    tok = await _headers(client, FINANCE_CWS)
    mid = await _sql_as_uuid(
        "SELECT b.id FROM billing_milestones b JOIN quotations q ON b.quotation_id=q.id "
        "WHERE q.quotation_no='Q-8D-SQYO-03' AND b.milestone_type='LPJ'"
    )
    r = await client.patch(
        f"/crm/billing/milestones/{mid}",
        headers=tok,
        json={"doc_no": "BM-8F-SQYO-LPJ"},
    )
    assert r.status_code == 403, r.text


# ── List ──────────────────────────────────────────────────────────────────


async def test_list_billing_sql(client: AsyncClient) -> None:
    """GET /crm/billing → data nyata dari DB (G2), mencakup PAID+OVERDUE seeder."""
    tok = await _headers(client, CORP_EXEC)
    r = await client.get("/crm/billing", headers=tok)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert isinstance(d, list) and len(d) >= 6
    statuses = {m["status"] for m in d}
    assert {"PAID", "OVERDUE", "EXPECTED", "UPLOADED"} <= statuses


async def test_list_filter_by_status_paid(client: AsyncClient) -> None:
    tok = await _headers(client, CORP_EXEC)
    r = await client.get("/crm/billing", params={"status": "PAID"}, headers=tok)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d and all(m["status"] == "PAID" for m in d)


async def test_list_filter_invalid_status_422(client: AsyncClient) -> None:
    tok = await _headers(client, CORP_EXEC)
    r = await client.get("/crm/billing", params={"status": "FOO"}, headers=tok)
    assert r.status_code == 422, r.text


async def test_list_finance_scoped_hotel(client: AsyncClient) -> None:
    """finance.cws scope CWS → hanya melihat CWS (tenant isolation), Q-8D-SQYO-03 tidak boleh muncul."""
    tok = await _headers(client, FINANCE_CWS)
    r = await client.get("/crm/billing", headers=tok)
    assert r.status_code == 200, r.text
    sqyo = await _quotation_id("Q-8D-SQYO-03")
    shown_qids = {m["quotation_id"] for m in r.json()["data"]}
    assert sqyo not in shown_qids
    cws_qid = await _quotation_id("Q-8F-CWS-01")
    assert cws_qid in shown_qids


async def test_list_public_403(client: AsyncClient) -> None:
    """PUBLIC_CLIENT tanpa crm:read → 403."""
    tok = await _headers(client, CLIENT_PUBLIC)
    r = await client.get("/crm/billing", headers=tok)
    assert r.status_code == 403, r.text


# ── Auto-reminder (F-10) ──────────────────────────────────────────────────


async def test_remind_billing_sweep_and_dedup(client: AsyncClient) -> None:
    """Sweep: semua milestone non-PAID due ≤ hari+14 di-remind (finance+GM); idempoten."""
    tok = await _headers(client, CORP_EXEC)
    first = await client.post("/notifications/remind-billing", params={"days_before": 14}, headers=tok)
    assert first.status_code == 200, first.text
    d1 = first.json()["data"]
    assert d1["reminded"] >= 5, d1
    assert d1["notifications"] >= 5, d1
    # Semua recipient di-seed (finance/GM) mendapat notif — per milestone ≥ 1.
    assert d1["recipients"] >= d1["reminded"]

    second = await client.post("/notifications/remind-billing", params={"days_before": 14}, headers=tok)
    assert second.status_code == 200, second.text
    d2 = second.json()["data"]
    assert d2["reminded"] == 0, "sweep harus idempoten (dedup reminder_key)"
    assert d2["refreshed_intact"] == d1["reminded"]


async def test_reminder_notifications_created(client: AsyncClient) -> None:
    """Notif BILLING_REMINDER benar terjadwal utk milestone OVERDUE LPJ nakal."""
    tok = await _headers(client, CORP_EXEC)
    await client.post("/notifications/remind-billing", params={"days_before": 14}, headers=tok)
    async with SessionLocal() as s:
        n = await s.scalar(
            text("SELECT count(*) FROM notifications WHERE type='BILLING_REMINDER'")
        )
        assert n >= 5, n
        types = await s.execute(
            text("SELECT DISTINCT type FROM notifications WHERE type='BILLING_REMINDER'")
        )
        assert types.first() is not None


async def test_reminder_rbac_requires_admin(client: AsyncClient) -> None:
    tok = await _headers(client, CLIENT_PUBLIC)
    r = await client.post("/notifications/remind-billing", params={"days_before": 14}, headers=tok)
    assert r.status_code in (401, 403), r.text


async def test_seeder_idempotent() -> None:
    """H4: seed ulang tidak menggandakan milestone — set kanonikal 6 (SQYO 4 + CWS 2)."""
    async with SessionLocal() as s:
        actor = await s.scalar(text("SELECT id FROM users WHERE email='corp.exec@ehos.local'"))
        args = {"root.admin@ehos.local": actor, "corp.exec@ehos.local": actor}
        await seed_billing_milestones(s, args)
        await s.commit()
        after1 = await s.scalar(
            text("SELECT count(*) FROM billing_milestones WHERE doc_no LIKE 'BM-8F-%%'")
        )
        await seed_billing_milestones(s, args)
        await s.commit()
        after2 = await s.scalar(
            text("SELECT count(*) FROM billing_milestones WHERE doc_no LIKE 'BM-8F-%%'")
        )
        qids = await s.execute(
            text("SELECT DISTINCT quotation_id FROM billing_milestones WHERE doc_no LIKE 'BM-8F-%%'")
        )
    assert after2 == after1 == 6, "seeder harus stabil: 6 milestone kanonikal"
    assert len([r[0] for r in qids]) == 2, "milestone milik 2 quotation ACCEPTED (SQYO + CWS)"