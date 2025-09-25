"""Cross-property referral & komisi (PRD-F-08, task 8b).

POST /crm/leads/{id}/refer — hotelT1 menyerahkan lead legal ke hotelT2:
- Record `lead_referrals` immutable (from/to/komisi/status REFERRED).
- Ownership pindah nyata: `hotel_id` → tujuan, `source=REFERRAL`,
  `referred_from_hotel_id`=asal (tracking komisi F-08), owner → sales hotel tujuan.
- Guard F-08: single-hop (lead refer di-refer ulang → 409), terminal LOST → 409,
  self-refer → 409, komisi negatif → 422, hotel tujuan tak ada → 404.
- Notifikasi formal handover `CRM_REFERRAL` (id/en) ke penerima baru.

Berjalan terhadap seeded dev DB (seeder task 8b menyediakan `CRM-8B-REF-*`).
"""

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import text

from app.db.session import SessionLocal

SEED_PASSWORD = "Ehos#2026!"
SALES_CWS = "sales.cws@ehos.local"
SALES_TELE = "sales.tele@ehos.local"
FINANCE_CWS = "finance.cws@ehos.local"
CORP_EXEC = "corp.exec@ehos.local"

_BASE = datetime.now(UTC) + timedelta(days=-10)
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


async def _db_scalar(sql: str, params: dict) -> str | None:
    async with SessionLocal() as s:
        val = await s.scalar(text(sql), params)
        return str(val) if val is not None else None


async def _hotel_id(code: str) -> str:
    return await _db_scalar("SELECT uuid FROM hotels WHERE code=:c", {"c": code})


async def _lead_id(lead_no: str) -> str | None:
    return await _db_scalar("SELECT id FROM leads WHERE lead_no=:l", {"l": lead_no})


async def _create_lead(client: AsyncClient, *, hotel: str = "CWS") -> dict:
    cws = await _hotel_id(hotel)
    payload = {
        "hotel_id": cws,
        "source": "MANUAL",
        "institution_type": "PRIVATE",
        "company_name": f"Rujukan Lintas Unit {_stamp()}",
        "pic_name": "PIC Rujukan",
        "pic_phone": "+6281234567891",
        "pic_email": "pic.rujukan@example.com",
        "amount_est": 140_000_000,
    }
    r = await client.post("/crm/leads", headers=await _headers(client, SALES_CWS), json=payload)
    assert r.status_code == 201, r.text
    return r.json()["data"]


async def _refer(client: AsyncClient, lead_id: str, to_hotel: str, **extra) -> tuple[int, dict]:
    payload = {"to_hotel_id": await _hotel_id(to_hotel), **extra}
    r = await client.post(
        f"/crm/leads/{lead_id}/refer", headers=await _headers(client, SALES_CWS), json=payload
    )
    return r.status_code, r.json()


# ─── 1. Happy path: transfer + komisi + notifikasi ─────────────────────

async def test_refer_transfers_and_tracks_commission(client: AsyncClient) -> None:
    lead = await _create_lead(client, hotel="CWS")
    cws = await _hotel_id("CWS")
    sqyo = await _hotel_id("SQYO")

    code, body = await _refer(
        client, lead["id"], "SQYO",
        commission_amount=7_500_000,
        note="Konfirmasi MICE BUMN — terima kasih atas rujukan",
    )
    assert code == 201, body
    ref = body["data"]
    assert ref["status"] == "REFERRED"
    assert ref["from_hotel_id"] == cws
    assert ref["to_hotel_id"] == sqyo
    assert ref["commission_amount"] == 7_500_000
    assert "rujukan" in (ref["note"] or "")

    det = (await client.get(
        f"/crm/leads/{lead['id']}", headers=await _headers(client, CORP_EXEC)
    )).json()["data"]
    assert det["hotel_id"] == sqyo
    assert det["source"] == "REFERRAL"
    assert det["referred_from_hotel_id"] == cws
    assert det["referrals"] and det["referrals"][0]["commission_amount"] == 7_500_000
    notes = [a["note"] for a in det["activities"] if a["type"] == "NOTE"]
    assert any("Direfer ke SQYO" in n for n in notes)
    assert any("Diterima via referral dari CWS" in n for n in notes)

    # Tenant isolation berpindah: owner lama 403, owner baru bisa akses+manage.
    r = await client.get(f"/crm/leads/{lead['id']}", headers=await _headers(client, SALES_CWS))
    assert r.status_code == 403, r.text
    r = await client.patch(
        f"/crm/leads/{lead['id']}",
        headers=await _headers(client, SALES_TELE),
        json={"status": "CONTACTED"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["hotel_id"] == sqyo

    # Notifikasi formal handover CRM_REFERRAL ke penerima baru.
    n = await _db_scalar(
        "SELECT id FROM notifications WHERE entity_type='lead' AND entity_id=:l "
        "AND type='CRM_REFERRAL' AND user_id=(SELECT id FROM users WHERE email=:e)",
        {"l": lead["id"], "e": SALES_TELE},
    )
    assert n, "notifikasi CRM_REFERRAL tidak dibuat untuk owner baru"


# ─── 2. Guard F-08: situasi ilegal ──────────────────────────────────────

async def test_refer_illegal_situations(client: AsyncClient) -> None:
    lead = await _create_lead(client, hotel="CWS")

    code, body = await _refer(client, lead["id"], "CWS")
    assert code == 409 and "hotel lain" in body["detail"], body

    r = await client.post(
        f"/crm/leads/{lead['id']}/refer",
        headers=await _headers(client, SALES_CWS),
        json={"to_hotel_id": "11111111-2222-4333-8444-555555555555"},
    )
    assert r.status_code == 404, r.text

    code, body = await _refer(
        client, lead["id"], "SQYO", commission_amount=-50_000
    )
    assert code == 422, body


async def test_refer_single_hop_terminal(client: AsyncClient) -> None:
    ref01 = await _lead_id("CRM-8B-REF-01")
    assert ref01, "Seeder referral 8b belum jalan — jalankan seeder dulu"
    sqyo = await _hotel_id("SQYO")
    zhba = await _hotel_id("ZHBA")

    # Lead hasil referral (source=REFERRAL) tidak bisa di-refer ulang.
    r = await client.get(f"/crm/leads/{ref01}", headers=await _headers(client, SALES_TELE))
    assert r.status_code == 200
    assert r.json()["data"]["source"] == "REFERRAL"
    r = await client.post(
        f"/crm/leads/{ref01}/refer",
        headers=await _headers(client, SALES_TELE),
        json={"to_hotel_id": zhba},
    )
    assert r.status_code == 409 and "single-hop" in r.json()["detail"]

    # Lead terminal LOST tidak bisa direfer.
    dead = await _create_lead(client, hotel="CWS")
    r = await client.patch(
        f"/crm/leads/{dead['id']}",
        headers=await _headers(client, SALES_CWS),
        json={"status": "LOST", "lost_reason": "Event digelar di hotel pesaing"},
    )
    assert r.status_code == 200
    r = await client.post(
        f"/crm/leads/{dead['id']}/refer",
        headers=await _headers(client, SALES_CWS),
        json={"to_hotel_id": sqyo, "commission_amount": 1_000_000},
    )
    assert r.status_code == 409 and "terminal" in r.json()["detail"]


async def test_refer_requires_manage_scope(client: AsyncClient) -> None:
    # Finance memiliki crm:read saja → tidak boleh menyerahkan lead.
    lead = await _create_lead(client, hotel="CWS")
    r = await client.post(
        f"/crm/leads/{lead['id']}/refer",
        headers=await _headers(client, FINANCE_CWS),
        json={"to_hotel_id": await _hotel_id("SQYO"), "commission_amount": 500_000},
    )
    assert r.status_code == 403, r.text


# ─── 3. Detail menampilkan riwayat referral ─────────────────────────────

async def test_refer_detail_keeps_referrals_history(client: AsyncClient) -> None:
    lead = await _create_lead(client, hotel="CWS")
    code, body = await _refer(client, lead["id"], "SBAI", commission_amount=2_000_000)
    assert code == 201, body

    det = (await client.get(
        f"/crm/leads/{lead['id']}", headers=await _headers(client, SALES_TELE)
    )).json()["data"]
    assert len(det["referrals"]) == 1
    assert det["referrals"][0]["to_hotel_id"] == await _hotel_id("SBAI")
    assert det["referrals"][0]["commission_amount"] == 2_000_000
    assert det["referrals"][0]["status"] == "REFERRED"