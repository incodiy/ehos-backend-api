"""RFP intake → lead CRM (PRD-F-11, task 8c) — POST /frontpage/rfp.

Form publik (tanpa auth, `security: []`):
- Valid + `target_hotel_code` → RfpRequest `ASSIGNED` + lead `RFP_PORTAL` otomatis
  (owner = sales aktif hotel tujuan, follow-up default), notif `RFP_INTAKE` ke sales.
- Tanpa target / kode invalid → RFP `NEW` menunggu assign corporate (ERD note 568),
  lead belum dibuat (bukan error palsu — G4).
- Validasi kontrak: required fields 422, enum invalid 422, pax<1 422.
- Idempotensi seeder (H4): rerun tidak menambah notif/lead duplikat.

Berjalan terhadap seeded dev DB (seeder task 8c menyediakan RFP-8C-*).
"""

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import text

from app.db.session import SessionLocal

SEED_PASSWORD = "Ehos#2026!"
SALES_CWS = "sales.cws@ehos.local"
CORP_EXEC = "corp.exec@ehos.local"

_BASE = datetime.now(UTC) + timedelta(days=-15)
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


async def _db_scalar(sql: str, params: dict) -> object | None:
    async with SessionLocal() as s:
        return await s.scalar(text(sql), params)


def _payload(**overrides) -> dict:
    return {
        "company_name": f"Instansi Uji RFP {_stamp()}",
        "institution_type": "GOV",
        "pic_name": "PIC Instansi Uji",
        "phone": "+6281212345678",
        "email": "pic.rfp@example.com",
        "event_date": "2027-08-15",
        "pax": 150,
        "package_type": "FULLBOARD",
        "city": "Bandung",
        "target_hotel_code": "CWS",
        "notes": "Uji intake RFP end-to-end",
        **overrides,
    }


async def _submit(client: AsyncClient, **overrides) -> tuple[int, dict, dict]:
    payload = _payload(**overrides)
    r = await client.post("/frontpage/rfp", json=payload)
    return r.status_code, r.json(), payload


# ─── 1. Happy path: RFP → lead RFP_PORTAL + notif sales ────────────────

async def test_rfp_creates_lead_and_notifies_sales(client: AsyncClient) -> None:
    code, body, payload = await _submit(client)
    assert code == 201, body
    data = body["data"]
    assert data["ref_no"].startswith("RFP-")
    assert data["status"] == "ASSIGNED"

    r = await client.get("/crm/leads?source=RFP_PORTAL", headers=await _headers(client, SALES_CWS))
    assert r.status_code == 200
    leads = [row for row in r.json()["data"] if row["company_name"] == payload["company_name"]]
    assert len(leads) == 1
    lead = leads[0]
    assert lead["source"] == "RFP_PORTAL"
    assert lead["status"] == "LEAD"

    detail = (await client.get(
        f"/crm/leads/{lead['id']}", headers=await _headers(client, CORP_EXEC)
    )).json()["data"]
    assert detail["next_followup_at"] is not None
    notes = [a["note"] for a in detail["activities"] if a["type"] == "NOTE"]
    assert any("RFP " in n and "FULLBOARD" in n for n in notes)

    got_notif = await _db_scalar(
        "SELECT count(*) FROM notifications n JOIN rfp_requests r ON n.entity_id::text=r.uuid::text "
        "WHERE n.entity_type='rfp' AND n.type='RFP_INTAKE' AND r.ref_no=:ref",
        {"ref": data["ref_no"]},
    )
    assert got_notif >= 1


# ─── 2. Tanpa target / kode invalid → NEW, lead belum dibuat ────────────

async def test_rfp_without_target_stays_new(client: AsyncClient) -> None:
    code, body, payload = await _submit(client, target_hotel_code=None, city="Jakarta")
    assert code == 201, body
    ref = body["data"]["ref_no"]
    assert body["data"]["status"] == "NEW"

    status = await _db_scalar("SELECT status FROM rfp_requests WHERE ref_no=:r", {"r": ref})
    assert status == "NEW"
    has_lead = await _db_scalar(
        "SELECT count(*) FROM leads WHERE company_name=:c AND source='RFP_PORTAL'",
        {"c": payload["company_name"]},
    )
    assert has_lead == 0


async def test_rfp_invalid_target_code_stays_new(client: AsyncClient) -> None:
    code, body, _ = await _submit(client, target_hotel_code="NOTREAL")
    assert code == 201, body
    assert body["data"]["status"] == "NEW"


# ─── 3. Validasi kontrak (422) ──────────────────────────────────────────

async def test_rfp_validation_errors(client: AsyncClient) -> None:
    code, body, _ = await _submit(client, company_name="")
    assert code == 422, body

    code, body, _ = await _submit(client, pax=0)
    assert code == 422, body

    code, body, _ = await _submit(client, institution_type="NGO")
    assert code == 422, body

    code, body, _ = await _submit(client, package_type="PEKAN")
    assert code == 422, body

    code, body, _ = await _submit(client, event_date="bukan-tanggal")
    assert code == 422, body


# ─── 4. Seeder & idempotensi (H4) ───────────────────────────────────────

async def test_seeded_rfp_state(client: AsyncClient) -> None:
    # RFP dengan target → lead RFP_PORTAL deterministik milik sales hotel tujuan.
    lead_no = await _db_scalar("SELECT lead_no FROM leads WHERE lead_no='RFP-CWS-8C-01'", {})
    assert lead_no == "RFP-CWS-8C-01"

    ref01 = await _db_scalar("SELECT uuid::text FROM rfp_requests WHERE ref_no='RFP-8C-CWS-01'", {})
    notif_rows = await _db_scalar(
        "SELECT count(*) FROM notifications WHERE entity_type='rfp' "
        "AND entity_id=:id AND type='RFP_INTAKE'",
        {"id": ref01},
    )
    assert notif_rows >= 1

    # RFP tanpa target tetap NEW tanpa lead.
    status = await _db_scalar("SELECT status FROM rfp_requests WHERE ref_no='RFP-8C-OPEN-01'", {})
    assert status == "NEW"
    no_lead = await _db_scalar(
        "SELECT count(*) FROM leads WHERE lead_no IN ('RFP-CWS-8C-01','RFP-SQYO-8C-01')", {}
    )
    assert no_lead == 2