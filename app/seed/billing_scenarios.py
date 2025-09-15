"""Billing milestone seeder — PRD-F-10 (task 8f) chained simulation (H2-H4).

Berantai dari quotation (leads → quotations → billing_milestones): memakai
service NYATA `create_quotation` (verifikasi pagu SBM F-09 + snapshot E3) utk
satu quotation tambahan ACCEPTED di CWS + milestone di quotation ACCEPTED 8d
(SQYO). Flow dinas realistis: quotation menang (ACCEPTED) → SPK → NPWP → BAST
→ LPJ (dokumen format resmi, Constraint §5 F-10).

Variasi situasi dinamis (H3):
- Q-8F-CWS-01  CWS ACCEPTED → SPK **OVERDUE** (due kemarin — risiko dana) + NPWP **EXPECTED**
- Q-8D-SQYO-03 SQYO ACCEPTED → SPK **UPLOADED**, NPWP **UPLOADED**,
  BAST **PAID** (terminal, paid_at di masa lalu), LPJ **EXPECTED** (due +5 hari)

Milestone dgn due_date ≤ hari ini+14 hari → dipanggil `remind_billing_milestones`
(runner) → notifikasi **BILLING_REMINDER** ke finance+GM hotel (F-10
auto-reminder jelang tutup TA / cekal kebocoran APBD/APBN).

Idempoten & deterministik (H4): quotation `Q-8F-CWS-01` upsert stabil;
milestone seeder di-rebuild bersih per run via `doc_no` ber-prefix `BM-8F-`
(finansial immutable — tidak ada delete data bisnis; ini hanya reset baris
milik seeder utk menghindari duplikat saat rerun).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BillingMilestone, Hotel, Lead, Quotation
from app.services.billing_pipeline import create_billing_milestone
from app.services.quotation_pipeline import create_quotation

SYSTEM_INGEST_EMAIL = "system.ingest@ehos.local"

# (quotation_no, milestone_type, status, due_delta_days, amount, doc_no, doc_key)
BILLING_SCENARIOS: list[dict] = [
    # --- CWS (quotation dibuat seeder ini, ACCEPTED) -------------------------
    {
        "quotation_no": "Q-8F-CWS-01",
        "lead_no": "CRM-8A-CWS-01",
        "event_date": date(2027, 5, 20),
        "event_name": "Rapat Koordinasi Vertikal \u2014 Kemenkeu",
        "package_type": "FULLDAY",
        "pax_count": 100,
        "gross_amount": 30_000_000,
        "milestones": [
            {"milestone_type": "SPK", "status": "OVERDUE", "due_days": -2, "amount": None,
             "doc_no": "BM-8F-CWS-SPK-2027", "doc_key": None},
            {"milestone_type": "NPWP", "status": "EXPECTED", "due_days": 7, "amount": None,
             "doc_no": "BM-8F-CWS-NPWP", "doc_key": None},
        ],
    },
    # --- SQYO (quotation ACCEPTED milik 8d — linkage billing F-10) -----------
    {
        "quotation_no": "Q-8D-SQYO-03",
        "lead_no": "CRM-8A-SQYO-03",
        "milestones": [
            {"milestone_type": "SPK", "status": "UPLOADED", "due_days": 12, "amount": None,
             "doc_no": "BM-8F-SQYO-SPK-2027",
             "doc_key": "billing/Q-8D-SQYO-03/SPK.pdf"},
            {"milestone_type": "NPWP", "status": "UPLOADED", "due_days": 10, "amount": None,
             "doc_no": "BM-8F-SQYO-NPWP",
             "doc_key": "billing/Q-8D-SQYO-03/NPWP.pdf"},
            {"milestone_type": "BAST", "status": "PAID", "due_days": -5,
             "amount": 80_000_000,
             "doc_no": "BM-8F-SQYO-BAST-2027",
             "doc_key": "billing/Q-8D-SQYO-03/BAST.pdf"},
            {"milestone_type": "LPJ", "status": "EXPECTED", "due_days": 5,
             "amount": 80_000_000,
             "doc_no": "BM-8F-SQYO-LPJ-2027", "doc_key": None},
        ],
    },
]


async def seed_billing_milestones(session: AsyncSession, resolved: dict) -> dict:
    actor_id = resolved.get("root.admin@ehos.local") or resolved.get(SYSTEM_INGEST_EMAIL)
    if actor_id is None:
        actor_id = await session.scalar(select(Quotation.created_by).limit(1))
    if actor_id is None:
        raise RuntimeError("tidak ada actor — jalankan seed_users dulu")

    leads = dict((await session.execute(select(Lead.lead_no, Lead))).all())
    details: dict[str, dict] = {}

    for cfg in BILLING_SCENARIOS:
        lead = leads.get(cfg["lead_no"])
        if lead is None:
            details[cfg["quotation_no"]] = {"error": f"lead {cfg['lead_no']} belum di-seed"}
            continue

        if cfg.get("event_date") is not None:
            # quotation tambahan milik seeder ini (service nyata, idempoten).
            hotel = await session.get(Hotel, lead.hotel_id)
            quotation = await create_quotation(
                session,
                lead=lead,
                hotel=hotel,
                event_date=cfg["event_date"],
                event_name=cfg["event_name"],
                package_type=cfg["package_type"],
                pax_count=cfg["pax_count"],
                gross_amount=cfg["gross_amount"],
                discount_amount=0,
                actor_id=actor_id,
                quotation_no=cfg["quotation_no"],
            )
            quotation.status = "ACCEPTED"
            quotation.pdf_key = f"quotations/{cfg['quotation_no']}.pdf"
        else:
            quotation = await session.scalar(
                select(Quotation).where(Quotation.quotation_no == cfg["quotation_no"])
            )
            if quotation is None:
                details[cfg["quotation_no"]] = {"error": "quotation 8d belum di-seed"}
                continue

        # Rebuild milestone seeder (H4): hapus baris milik seed ini dulu.
        seed_docs = [m["doc_no"] for m in cfg["milestones"]]
        await session.execute(
            delete(BillingMilestone).where(
                BillingMilestone.quotation_id == quotation.id,
                BillingMilestone.doc_no.in_(seed_docs),
            )
        )

        today = date.today()
        now = datetime.now(UTC)
        milestones: list[dict] = []
        for m in cfg["milestones"]:
            due = today + timedelta(days=m["due_days"])
            milestone = await create_billing_milestone(
                session,
                quotation=quotation,
                milestone_type=m["milestone_type"],
                due_date=due,
                amount=m["amount"],
                doc_no=m["doc_no"],
                actor_id=actor_id,
            )
            milestone.status = m["status"]
            milestone.doc_key = m["doc_key"]
            if m["status"] == "PAID":
                milestone.paid_at = now - timedelta(days=1)
            milestone.updated_by = actor_id
            milestones.append(
                {
                    "milestone_type": m["milestone_type"],
                    "status": milestone.status,
                    "due_date": due.isoformat(),
                }
            )
        details[cfg["quotation_no"]] = {
            "status": quotation.status,
            "milestones": milestones,
        }

    return {"quotations": len(details), "details": details}