"""CRM kanban pipeline (PRD-F-07, task 8a) — status machine + Lost Reason + follow-up.

Alur: LEAD → CONTACTED → PROSPECT → CONFIRMED serta sink ke LOST (terminal).
F-07 wajib: `lost_reason` saat LOST (422 tanpa alasan), transisi ilegal → 409,
`next_followup_at` → reminder otomatis `FOLLOWUP_CRM` (sweep idempoten).

Berjalan terhadap seeded dev DB: user sales.cws (read+manage CWS), gm.sqyo
(SQYO), finance.cws (read saja), corp.exec (read global), client.public (tanpa
permission CRM). Seeder task 8a menyediakan leads `CRM-8A-*` lintas status.
"""

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.crm_pipeline import crm_followup_reminders

SEED_PASSWORD = "Ehos#2026!"
SALES_CWS = "sales.cws@ehos.local"
GM_SQYO = "gm.sqyo@ehos.local"
FINANCE_CWS = "finance.cws@ehos.local"
PUBLIC_CLIENT = "client.public@ehos.local"
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


async def _hotel_id(code: str) -> str:
    async with SessionLocal() as s:
        return str(await s.scalar(text("SELECT uuid FROM hotels WHERE code=:c"), {"c": code}))


async def _create_lead(
    client: AsyncClient,
    *,
    hotel: str,
    company: str = "Uji Kompetensi 8A",
    status: str | None = None,
    lost_reason: str | None = None,
    next_followup_at: str | None = None,
    source: str = "MANUAL",
) -> tuple[int, dict]:
    cws = await _hotel_id(hotel)
    payload = {
        "hotel_id": cws,
        "source": source,
        "institution_type": "PRIVATE",
        "company_name": f"{company} {_stamp()}",
        "pic_name": "PIC Uji",
        "pic_phone": "+6281234567890",
        "pic_email": "pic.uji@example.com",
        "amount_est": 125_000_000,
    }
    if status is not None:
        payload["status"] = status
    if lost_reason is not None:
        payload["lost_reason"] = lost_reason
    if next_followup_at is not None:
        payload["next_followup_at"] = next_followup_at
    r = await client.post("/crm/leads", headers=await _headers(client, SALES_CWS), json=payload)
    return r.status_code, r.json()


# ─── 1. Create + kanban happy path ───────────────────────────────────────

async def test_create_lead_and_kanban_flow(client: AsyncClient) -> None:
    code, body = await _create_lead(client, hotel="CWS", source="CROSS_SELLING")
    assert code == 201, body
    lead = body["data"]
    assert lead["status"] == "LEAD"
    assert lead["lead_no"].startswith("XSELL-CWS-")

    lead_id = lead["id"]
    for target in ("CONTACTED", "PROSPECT", "CONFIRMED"):
        r = await client.patch(
            f"/crm/leads/{lead_id}",
            headers=await _headers(client, SALES_CWS),
            json={"status": target},
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["status"] == target

    # Aktivitas status otomatis tercatat (jejak kanban) + detail menampilkannya
    r = await client.get(f"/crm/leads/{lead_id}", headers=await _headers(client, CORP_EXEC))
    assert r.status_code == 200
    det = r.json()["data"]
    notes = [a["note"] for a in det["activities"] if a["type"] == "NOTE"]
    assert any("→ PROSPECT" in n for n in notes)
    assert det["quotations"] == []

    # CONFIRMED → LOST dengan alasan tersimpan
    r = await client.patch(
        f"/crm/leads/{lead_id}",
        headers=await _headers(client, SALES_CWS),
        json={"status": "LOST", "lost_reason": "Anggaran diblokir keputusan pimpinan"},
    )
    assert r.status_code == 200, r.text
    lost = r.json()["data"]
    assert lost["status"] == "LOST"
    assert lost["lost_reason"] == "Anggaran diblokir keputusan pimpinan"
    assert lost["next_followup_at"] is None


# ─── 2. Lost Reason mandatori (F-07) ─────────────────────────────────────

async def test_lost_without_reason_422(client: AsyncClient) -> None:
    code, body = await _create_lead(client, hotel="CWS")
    assert code == 201
    lead_id = body["data"]["id"]

    r = await client.patch(
        f"/crm/leads/{lead_id}", headers=await _headers(client, SALES_CWS),
        json={"status": "LOST"},
    )
    assert r.status_code == 422, r.text
    assert "lost_reason" in r.json()["detail"]

    # alasan kosong / whitespace juga ditolak
    r = await client.patch(
        f"/crm/leads/{lead_id}", headers=await _headers(client, SALES_CWS),
        json={"status": "LOST", "lost_reason": "   "},
    )
    assert r.status_code == 422

    # tetap LEAD — tidak berubah status gagal
    r = await client.get(f"/crm/leads/{lead_id}", headers=await _headers(client, SALES_CWS))
    assert r.json()["data"]["status"] == "LEAD"


# ─── 3. Transisi ilegal → 409 ────────────────────────────────────────────

async def test_invalid_transitions_conflict_409(client: AsyncClient) -> None:
    code, body = await _create_lead(client, hotel="CWS")
    assert code == 201
    lead_id = body["data"]["id"]

    # LOST terminal: tidak bisa keluar ke status lain
    r = await client.patch(
        f"/crm/leads/{lead_id}", headers=await _headers(client, SALES_CWS),
        json={"status": "LOST", "lost_reason": "Harga tidak kompetitif"},
    )
    assert r.status_code == 200
    r = await client.patch(
        f"/crm/leads/{lead_id}", headers=await _headers(client, SALES_CWS),
        json={"status": "CONFIRMED"},
    )
    assert r.status_code == 409

    # Aktivitas lanjutan pada LOST dilarang
    r = await client.post(
        f"/crm/leads/{lead_id}/activities", headers=await _headers(client, SALES_CWS),
        json={"type": "CALL", "note": "Menawarkan paket baru"},
    )
    assert r.status_code == 409

    # status invalid → 422 (pydantic Literal)
    r = await client.patch(
        f"/crm/leads/{lead_id}", headers=await _headers(client, SALES_CWS),
        json={"status": "WON"},
    )
    assert r.status_code == 422


# ─── 4. Aktivitas + jadwal follow-up ─────────────────────────────────────

async def test_activity_updates_followup(client: AsyncClient) -> None:
    future = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    code, body = await _create_lead(client, hotel="CWS")
    assert code == 201
    lead_id = body["data"]["id"]

    r = await client.post(
        f"/crm/leads/{lead_id}/activities", headers=await _headers(client, SALES_CWS),
        json={"type": "CALL", "note": "Telepon survey kebutuhan", "next_followup_at": future},
    )
    assert r.status_code == 201, r.text
    act = r.json()["data"]
    assert act["type"] == "CALL"
    assert act["next_followup_at"] is not None

    r = await client.get(f"/crm/leads/{lead_id}", headers=await _headers(client, SALES_CWS))
    det = r.json()["data"]
    assert det["next_followup_at"] is not None
    assert any(a["type"] == "CALL" for a in det["activities"])


# ─── 5. Filter followup_due ──────────────────────────────────────────────

async def test_followup_due_filter(client: AsyncClient) -> None:
    past = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    future = (datetime.now(UTC) + timedelta(days=4)).isoformat()
    _, a = await _create_lead(client, hotel="CWS", next_followup_at=past)
    _, b = await _create_lead(client, hotel="CWS", next_followup_at=future)

    r = await client.get("/crm/leads?followup_due=true", headers=await _headers(client, CORP_EXEC))
    assert r.status_code == 200, r.text
    due_ids = {x["id"] for x in r.json()["data"]}
    assert a["data"]["id"] in due_ids
    assert b["data"]["id"] not in due_ids

    # terminal status dengan followup lewat TIDAK muncul di followup_due
    r = await client.patch(
        f"/crm/leads/{b['data']['id']}", headers=await _headers(client, SALES_CWS),
        json={"status": "CONFIRMED"},
    )
    assert r.status_code == 200
    r = await client.patch(
        f"/crm/leads/{b['data']['id']}", headers=await _headers(client, SALES_CWS),
        json={"next_followup_at": past},
    )
    assert r.status_code == 200
    r = await client.get("/crm/leads?followup_due=true", headers=await _headers(client, CORP_EXEC))
    due_ids = {x["id"] for x in r.json()["data"]}
    assert b["data"]["id"] not in due_ids

    # restore lead masa depan agar tidak menumpuk due antar-run
    await client.patch(
        f"/crm/leads/{b['data']['id']}", headers=await _headers(client, SALES_CWS),
        json={"next_followup_at": future},
    )


# ─── 6. RBAC & tenant isolation ──────────────────────────────────────────

async def test_rbac_guards(client: AsyncClient) -> None:
    # finance: read boleh, manage tidak
    r = await client.get("/crm/leads", headers=await _headers(client, FINANCE_CWS))
    assert r.status_code == 200
    r = await client.post(
        "/crm/leads", headers=await _headers(client, FINANCE_CWS), json={
            "hotel_id": await _hotel_id("CWS"), "company_name": "Finance Tidak Boleh",
            "pic_name": "X", "source": "MANUAL", "institution_type": "PRIVATE",
            "pic_email": "x@example.com",
        },
    )
    assert r.status_code == 403

    # public client tanpa permission CRM → 403
    r = await client.get("/crm/leads", headers=await _headers(client, PUBLIC_CLIENT))
    assert r.status_code == 403

    # tenant isolation: sales CWS tidak bisa akses lead SQYO (seeder 8a)
    async with SessionLocal() as s:
        sqyo_lead_id = await s.scalar(
            text("SELECT id FROM leads WHERE lead_no='CRM-8A-SQYO-01'")
        )
    assert sqyo_lead_id is not None
    r = await client.get(f"/crm/leads/{sqyo_lead_id}", headers=await _headers(client, SALES_CWS))
    assert r.status_code == 403
    r = await client.patch(
        f"/crm/leads/{sqyo_lead_id}", headers=await _headers(client, SALES_CWS),
        json={"amount_est": 1},
    )
    assert r.status_code == 403

    # corp exec (global) bisa baca lead unit lain + list global
    r = await client.get(f"/crm/leads/{sqyo_lead_id}", headers=await _headers(client, CORP_EXEC))
    assert r.status_code == 200
    assert r.json()["data"]["company_name"]

    # sales list hanya hotel scope-nya (CWS) — semua hasil hotel_id = CWS
    r = await client.get("/crm/leads", headers=await _headers(client, SALES_CWS))
    cws = await _hotel_id("CWS")
    assert r.status_code == 200
    assert r.json()["data"], "sales.cws harus punya lead (seeder 8a CWS)"
    assert all(x["hotel_id"] == cws for x in r.json()["data"])


# ─── 7. Follow-up reminder sweep (F-07) ──────────────────────────────────

async def test_crm_followup_reminders_idempotent(client: AsyncClient) -> None:
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    code, body = await _create_lead(client, hotel="CWS", next_followup_at=past)
    assert code == 201
    lead_id = body["data"]["id"]

    async with SessionLocal() as s:
        first = await crm_followup_reminders(s, now=datetime.now(UTC))
        count1 = await s.scalar(text(
            "SELECT count(*) FROM notifications "
            "WHERE entity_type='lead' AND entity_id=:lid AND type='FOLLOWUP_CRM'"
        ), {"lid": lead_id})
        assert count1 >= 2, first  # PUSH + EMAIL (sales.cws tanpa phone → 2 channel)
        assert first["scanned"] >= 1

        second = await crm_followup_reminders(s, now=datetime.now(UTC))
        count2 = await s.scalar(text(
            "SELECT count(*) FROM notifications "
            "WHERE entity_type='lead' AND entity_id=:lid AND type='FOLLOWUP_CRM'"
        ), {"lid": lead_id})
        assert count1 == count2, (second, "sweep harus idempoten (tidak duplikat)")

        payload = await s.scalar(text(
            "SELECT payload->>'reminder_key' FROM notifications "
            "WHERE entity_type='lead' AND entity_id=:lid AND type='FOLLOWUP_CRM' LIMIT 1"
        ), {"lid": lead_id})
        assert payload and payload.startswith(body["data"]["lead_no"] + ":")