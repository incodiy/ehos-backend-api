"""Quotation generator + pagu SBM + PDF (PRD-F-09, task 8d).

Alur: POST /crm/quotations (generate + snapshot E3 + cek pagu SBM F-09) →
GET list/detail → POST /pdf (ber-kop + barcode, simpan object store).
GOV lead dgn rate efektif > pagu provinsi/tahun → 409; PRIVATE bebas pagu
(SBM hanya mengatur belanja pemerintah); diskon GOV → PENDING approval GM.
SBM master (E1/E2): GET /crm/sbm-rates + PATCH (hanya corporate).

Berjalan terhadap seeded dev DB (DB harus di-seed dulu: seed_sbm_rates +
seed_quotation_scenarios di-runner). sales.cws = read+manage CWS; corp.exec =
global.
"""

import zlib
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.db.session import SessionLocal
from app.seed.quotation_scenarios import seed_quotation_scenarios
from app.seed.sbm_rates import seed_sbm_rates

SEED_PASSWORD = "Ehos#2026!"
SALES_CWS = "sales.cws@ehos.local"
CORP_EXEC = "corp.exec@ehos.local"

_BASE = datetime.now(UTC) + timedelta(minutes=-200)
_SEQ = 0


def _stamp() -> str:
    global _SEQ
    _SEQ += 1
    return (_BASE + timedelta(minutes=_SEQ)).strftime("%y%m%d-%H%M%S")


async def _login(client: AsyncClient, email: str) -> str:
    r = await client.post(
        "/auth/login",
        json={"email": email, "password": SEED_PASSWORD, "remember_me": False},
    )
    assert r.status_code == 200, (email, r.text)
    return r.json()["data"]["access_token"]


async def _headers(client: AsyncClient, email: str) -> dict:
    return {"Authorization": f"Bearer {await _login(client, email)}"}


async def _lead_id(lead_no: str) -> str:
    async with SessionLocal() as s:
        row = await s.execute(text("SELECT id, hotel_id FROM leads WHERE lead_no=:l"), {"l": lead_no})
        r = row.first()
        assert r is not None, f"lead {lead_no} belum di-seed"
        return str(r[0]), str(r[1])


@pytest.fixture(autouse=True)
async def _ensure_seeded():
    async with SessionLocal() as s:
        actor = await s.scalar(text("SELECT id FROM users WHERE email='corp.exec@ehos.local'"))
        await seed_sbm_rates(s, actor)  # upsert deterministik — reset master PMK
        await s.commit()
    yield


async def _create_quote(
    client: AsyncClient,
    token: str,
    lead_id: str,
    *,
    event_date: str = "2027-05-01",
    package_type: str = "FULLDAY",
    pax: int = 100,
    gross: int = 30_000_000,
    discount: int = 0,
) -> tuple[int, dict]:
    payload = {
        "lead_id": lead_id,
        "event_date": event_date,
        "event_name": "Uji Quotation 8D",
        "package_type": package_type,
        "pax_count": pax,
        "gross_amount": gross,
        "discount_amount": discount,
    }
    r = await client.post("/crm/quotations", json=payload, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, r.json()


async def test_create_quotation_gov_snapshot_e3(client: AsyncClient) -> None:
    token = await _login(client, SALES_CWS)
    lead_id, _ = await _lead_id("CRM-8A-CWS-01")
    status, body = await _create_quote(client, token, lead_id)
    assert status == 201, body
    d = body["data"]
    assert d["status"] == "DRAFT"
    assert d["sbm_fiscal_year"] == 2027
    assert d["sbm_rate_value"] == pytest.approx(400_000)
    assert d["sbm_rate_id"] is not None, "snapshot E3 harus mengunci sbm_rate_id"
    assert d["final_amount"] == d["gross_amount"]
    assert d["discount_approval_status"] == "APPROVED"  # tanpa diskon
    # detail quotation → milestones kosong (F-10 belum), pdf_url null di awal
    detail = await client.get(
        f"/crm/quotations/{d['id']}", headers={"Authorization": f"Bearer {token}"}
    )
    assert detail.status_code == 200
    assert detail.json()["data"]["milestones"] == []
    assert detail.json()["data"]["pdf_url"] is None


async def test_quotation_gov_pagu_exceeded_409(client: AsyncClient) -> None:
    token = await _login(client, SALES_CWS)
    lead_id, _ = await _lead_id("CRM-8A-CWS-01")  # GOV
    status, body = await _create_quote(client, token, lead_id, pax=50, gross=60_000_000)  # 1.2jt/pax
    assert status == 409, body
    assert "pagu SBM" in body["detail"]


async def test_quotation_private_bebas_pagu(client: AsyncClient) -> None:
    token = await _login(client, SALES_CWS)
    lead_id, _ = await _lead_id("CRM-8A-CWS-04")
    async with SessionLocal() as s:
        it = await s.scalar(text("SELECT institution_type FROM leads WHERE id=:i"), {"i": int(lead_id)})
        assert it == "PRIVATE"
    status, body = await _create_quote(client, token, lead_id, pax=50, gross=120_000_000)  # 2.4jt/pax >> pagu
    assert status == 201, body
    assert body["data"]["sbm_rate_value"] is not None, "snapshot tetap (referensi)"
    assert body["data"]["final_amount"] == 120_000_000


async def test_quotation_gov_missing_sbm_409(client: AsyncClient) -> None:
    token = await _login(client, SALES_CWS)
    lead_id, _ = await _lead_id("CRM-8A-CWS-01")
    status, body = await _create_quote(client, token, lead_id, event_date="2030-09-01")
    assert status == 409, body
    assert "SBM rate belum tersedia" in body["detail"]


async def test_quotation_gov_discount_pending_approval(client: AsyncClient) -> None:
    token = await _login(client, SALES_CWS)
    lead_id, _ = await _lead_id("CRM-8A-CWS-01")
    status, body = await _create_quote(
        client, token, lead_id, pax=100, gross=30_000_000, discount=5_000_000
    )
    assert status == 201, body
    assert body["data"]["discount_approval_status"] == "PENDING"
    assert body["data"]["final_amount"] == pytest.approx(25_000_000)


async def test_quotation_list_scoped_and_filtered(client: AsyncClient) -> None:
    token = await _login(client, SALES_CWS)
    r = await client.get("/crm/quotations?status=SENT", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert all(q["status"] == "SENT" for q in r.json()["data"])
    # finance.cws read-only boleh dgn scope; client.public tanpa crm:read → 403
    pub = await _headers(client, "client.public@ehos.local")
    denied = await client.get("/crm/quotations", headers=pub)
    assert denied.status_code == 403


async def test_generate_quotation_pdf_with_barcode(client: AsyncClient) -> None:
    token = await _login(client, SALES_CWS)
    lead_id, _ = await _lead_id("CRM-8A-CWS-01")
    status, body = await _create_quote(client, token, lead_id)
    assert status == 201, body
    qid = body["data"]["id"]
    # patch eksternal storage — tanpa jaringan nyata
    with patch("app.api.endpoints.crm.upload_bytes") as up, patch(
        "app.api.endpoints.crm.presigned_get_url",
        return_value="https://minio/ehos-media/quotations/x.pdf?X-Amz-Signature=abc",
    ):
        r = await client.post(
            f"/crm/quotations/{qid}/pdf", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200, r.text
        up.assert_called_once()
        pdf_key = up.call_args.args[0]
        assert pdf_key.endswith(".pdf") and pdf_key.startswith("quotations/")
        assert r.json()["data"]["pdf_url"] == "https://minio/ehos-media/quotations/x.pdf?X-Amz-Signature=abc"
        # detail dalam konteks patch — pdf_url dari snapshot
        detail = await client.get(f"/crm/quotations/{qid}", headers={"Authorization": f"Bearer {token}"})
        assert detail.json()["data"]["pdf_url"] == "https://minio/ehos-media/quotations/x.pdf?X-Amz-Signature=abc"


async def test_render_quotation_pdf_bytes() -> None:
    from datetime import date

    from app.services.pdf_quotation import render_quotation_pdf

    async with SessionLocal() as s:
        q = await s.execute(text("SELECT quotation_no FROM quotations WHERE quotation_no='Q-8D-CWS-02'"))
        assert q.first() is not None, "seeder quotation belum jalan"
    # build objek ringan utk render murni (tanpa session)
    lead = type("Lead", (), {
        "company_name": "Kemenkeu DJP",
        "pic_name": "PIC Kemenkeu",
        "pic_email": "pic@mail.test",
        "pic_phone": "+62812",
    })()
    hotel = type("Hotel", (), {"name": "Hotel Cibubur Jakarta", "code": "CWS", "city": "Cibubur"})()
    quotation = type("Quotation", (), {
        "quotation_no": "Q-8D-CWS-02",
        "event_date": date(2027, 4, 18),
        "event_name": "Bimtek Perpajakan",
        "package_type": "FULLDAY",
        "pax_count": 80,
        "gross_amount": 24_000_000,
        "discount_amount": 3_200_000,
        "final_amount": 20_800_000,
        "sbm_rate_value": 400_000,
        "sbm_fiscal_year": 2027,
        "discount_approval_status": "APPROVED",
        "status": "SENT",
        "created_at": None,
    })()
    pdf = render_quotation_pdf(lead, hotel, quotation)
    assert pdf[:5] == b"%PDF-"
    # isi stream PDF ter-FlateDecode → decompress utk verifikasi teks (barcode + snapshot)
    head, _, rest = pdf.partition(b"stream\n")
    assert rest, "harus ada content stream"
    content = zlib.decompress(rest.split(b"endstream")[0])
    assert b"Q-8D-CWS-02" in content  # quotation_no tampil (teks + barcode)
    assert b"400,000" in content or b"400.000" in content  # snapshot SBM ter-render


async def test_sbm_rates_list_and_corporate_patch(client: AsyncClient) -> None:
    corp = await _headers(client, CORP_EXEC)
    r = await client.get("/crm/sbm-rates?province_code=31&package_type=FULLDAY&fiscal_year=2027", headers=corp)
    assert r.status_code == 200, r.text
    rows = r.json()["data"]
    assert rows, "seeder SBM harus non-empty utk DKI Jakarta 2027"
    assert rows[0]["max_rate_per_pax"] == 400_000
    assert rows[0]["province_code"] == "31"

    # sales (punya scope hotel) dilarang ubah master PMK → 403
    sales = await _headers(client, SALES_CWS)
    denied = await client.patch(
        f"/crm/sbm-rates/{rows[0]['id']}", json={"max_rate_per_pax": 450_000}, headers=sales
    )
    assert denied.status_code == 403

    # corp.exec update + restore (jaga determinisme lintas run)
    rate_id = rows[0]["id"]
    updated = await client.patch(
        f"/crm/sbm-rates/{rate_id}", json={"max_rate_per_pax": 405_000}, headers=corp
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["max_rate_per_pax"] == pytest.approx(405_000)
    await client.patch(f"/crm/sbm-rates/{rate_id}", json={"max_rate_per_pax": 400_000}, headers=corp)


async def test_seeder_quotation_idempotent() -> None:
    async with SessionLocal() as s:
        actor = await s.scalar(text("SELECT id FROM users WHERE email='system.ingest@ehos.local'"))
        assert actor is not None
        before = await s.scalar(text("SELECT count(*) FROM quotations"))
        await seed_quotation_scenarios(s, {})
        await s.commit()
        after = await s.scalar(text("SELECT count(*) FROM quotations"))
        assert after == before, "seeder quotation harus idempoten (upsert quotation_no)"
        # aktivitas lead per quotation_no tepat 1 (cleanup H4)
        dup = await s.scalar(
            text(
                "SELECT count(*) FROM lead_activities WHERE note LIKE 'Quotation Q-8D-%%dibuat%%' "
                "GROUP BY lead_id HAVING count(*) > 1"
            )
        )
        assert dup is None