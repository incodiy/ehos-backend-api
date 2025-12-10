"""CRM War Room kanban (PRD-F-07, task 9d) — row kanban + filter hotel + gating transisi.

Validasi list `/crm/leads` menyajikan kolom kanban dari server (`followup_due`,
`hotel_code`, `owner_name`) tanpa N+1 di klien; filter `hotel_id` utk user global;
guard transisi terminal LOST (409) & lost_reason wajib (422).

Berjalan terhadap seeded dev DB (app/seed/crm_scenarios.py): leads `CRM-8A-*`
lintas status multi-hotel (CWS/SBAI/ZHBA/SQYO) — lihat docstring test_crm_pipeline.
"""

from httpx import AsyncClient
from sqlalchemy import text

from app.db.session import SessionLocal

SEED_PASSWORD = "Ehos#2026!"
SALES_CWS = "sales.cws@ehos.local"
CORP_EXEC = "corp.exec@ehos.local"
PUBLIC_CLIENT = "client.public@ehos.local"


async def _login(client: AsyncClient, email: str) -> str:
    r = await client.post(
        "/auth/login",
        json={"email": email, "password": SEED_PASSWORD, "remember_me": False},
    )
    assert r.status_code == 200, (email, r.text)
    return r.json()["data"]["access_token"]


async def _hdr(client: AsyncClient, email: str) -> dict:
    return {"Authorization": f"Bearer {await _login(client, email)}"}


async def _hotel_id(code: str) -> str:
    async with SessionLocal() as s:
        return str(await s.scalar(text("SELECT uuid FROM hotels WHERE code=:c"), {"c": code}))


async def _create_lead(client: AsyncClient, hotel: str = "CWS") -> dict:
    cws = await _hotel_id(hotel)
    r = await client.post(
        "/crm/leads",
        headers=await _hdr(client, SALES_CWS),
        json={
            "hotel_id": cws,
            "source": "MANUAL",
            "institution_type": "GOV",
            "company_name": "Kanban Test 9D",
            "pic_name": "PIC 9D",
            "pic_email": "pic9d@example.com",
            "amount_est": 99_000_000,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]


# ─── 1. Row kanban membawa field server-side (followup_due/hotel_code/owner_name) ──

async def test_list_kanban_row_fields(client: AsyncClient) -> None:
    r = await client.get("/crm/leads", headers=await _hdr(client, SALES_CWS))
    assert r.status_code == 200, r.text
    rows = r.json()["data"]
    assert rows, "seeder harus mengisi leads CWS"
    for row in rows:
        assert row["hotel_code"] == "CWS", row
        assert row["owner_name"], row  # non-empty; bisa sales CWS atau tele cross-sell
        assert isinstance(row["followup_due"], bool), row
        assert row["source"] in {"RFP_PORTAL", "CROSS_SELLING", "REFERRAL", "MANUAL"}

    # CRM-8A-CWS-03 = variant 2 (CONTACTED, follow-up 26 jam lalu) → due True.
    # List membesar oleh RFP ingest, jadi cari via pagination (max 10 halaman).
    found = None
    for page in range(1, 11):
        p = await client.get(f"/crm/leads?page={page}", headers=await _hdr(client, SALES_CWS))
        assert p.status_code == 200, p.text
        batch = p.json()["data"]
        if not batch:
            break
        found = next((x for x in batch if x["lead_no"] == "CRM-8A-CWS-03"), found)
        if found:
            break
    assert found is not None, "seeder variant2 (overdue) hilang"
    assert found["status"] == "CONTACTED"
    assert found["owner_name"] == "Sales CWS"
    assert found["followup_due"] is True, found


# ─── 2. Filter hotel_id utk user korporat global ──────────────────────────────

async def test_list_hotel_filter_global(client: AsyncClient) -> None:
    sqyo = await _hotel_id("SQYO")
    r = await client.get(f"/crm/leads?hotel_id={sqyo}", headers=await _hdr(client, CORP_EXEC))
    assert r.status_code == 200, r.text
    rows = r.json()["data"]
    codes = {x["hotel_code"] for x in rows}
    assert codes == {"SQYO"}, codes


# ─── 3. Guard transisi kanban (gating terminal + lost_reason wajib) ───────────

async def test_kanban_lost_requires_reason_then_terminal(client: AsyncClient) -> None:
    lead = await _create_lead(client, "CWS")

    # LEAD → LOST tanpa lost_reason → 422 (F-07)
    r = await client.patch(
        f"/crm/leads/{lead['id']}",
        headers=await _hdr(client, SALES_CWS),
        json={"status": "LOST"},
    )
    assert r.status_code == 422, r.text

    # LEAD → LOST dengan alasan → 200 & terminal
    r = await client.patch(
        f"/crm/leads/{lead['id']}",
        headers=await _hdr(client, SALES_CWS),
        json={"status": "LOST", "lost_reason": "Anggaran diblokir"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["lost_reason"] == "Anggaran diblokir"

    # LOST terminal → transisi keluar → 409
    r = await client.patch(
        f"/crm/leads/{lead['id']}",
        headers=await _hdr(client, SALES_CWS),
        json={"status": "CONTACTED"},
    )
    assert r.status_code == 409, r.text


# ─── 4. RBAC: user tanpa permission CRM → 403 ────────────────────────────────

async def test_list_requires_crm_read(client: AsyncClient) -> None:
    r = await client.get("/crm/leads", headers=await _hdr(client, PUBLIC_CLIENT))
    assert r.status_code == 403, r.text