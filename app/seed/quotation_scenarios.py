"""Quotation seeder — PRD-F-09 (task 8d) chained simulation (H2-H4).

Menggunakan service bisnis NYATA `create_quotation` (verifikasi pagu SBM F-09 +
snapshot E3) di atas lead hasil seeder 8a (CRM-8A-*) & 8c (RFP-CWS-8C-01) —
state identik dengan endpoint `POST /crm/quotations`. Progresi status
DRAFT → SENT / ACCEPTED + approval diskon GM disimulasikan langsung (belum ada
endpoint kontrak utk transisi status, openapi quotation hanya POST/GET/PDF).

Variasi (H3):
- Q-8D-CWS-01  GOV FULLBOARD dalam pagu → DRAFT (baru generate) tanpa diskon
- Q-8D-CWS-02  GOV diskon > 0 → PENDING approval GM → GM approve → SENT
- Q-8D-SQYO-03  GOV CONFIRMED → ACCEPTED (menang — utk linkage billing F-10)
- Q-8D-RFP-01  lead RFP intake (F-11) → SENT (alur RFP → quotation 1-klik)
Semua dalam pagu provinsi/tahun (tidak memicu 409).

Idempoten & deterministik (H4): `quotation_no` tetap; aktivitas leod dibersihkan
per `quotation_no` agar tidak menumpuk saat dijalankan ulang.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Hotel, Lead, LeadActivity
from app.services.quotation_pipeline import create_quotation

SYSTEM_INGEST_EMAIL = "system.ingest@ehos.local"

QUOTATION_SCENARIOS: list[dict] = [
    {
        "quotation_no": "Q-8D-CWS-01",
        "lead_no": "CRM-8A-CWS-03",
        "event_date": date(2027, 3, 10),
        "event_name": "Rapat Kerja Pemerintah Provinsi \u2014 Jawa Barat",
        "package_type": "FULLBOARD",
        "pax_count": 120,
        "gross_amount": 48_000_000,
        "discount_amount": 0,
        "status": "DRAFT",
        "approve_discount": None,
    },
    {
        "quotation_no": "Q-8D-CWS-02",
        "lead_no": "CRM-8A-CWS-02",
        "event_date": date(2027, 4, 18),
        "event_name": "Bimbingan Teknis Perpajakan DJP",
        "package_type": "FULLDAY",
        "pax_count": 80,
        "gross_amount": 24_000_000,
        "discount_amount": 3_200_000,
        "status": "SENT",
        "approve_discount": True,
    },
    {
        "quotation_no": "Q-8D-SQYO-03",
        "lead_no": "CRM-8A-SQYO-03",
        "event_date": date(2027, 6, 22),
        "event_name": "Kongres Pemerintah Daerah \u2014 Yogyakarta",
        "package_type": "FULLBOARD",
        "pax_count": 200,
        "gross_amount": 80_000_000,
        "discount_amount": 0,
        "status": "ACCEPTED",
        "approve_discount": None,
    },
    {
        "quotation_no": "Q-8D-RFP-01",
        "lead_no": "RFP-CWS-8C-01",
        "event_date": date(2027, 3, 8),
        "event_name": "Rakerkesnas Kemenkes \u2014 120 kamar + fullboard",
        "package_type": "FULLDAY",
        "pax_count": 120,
        "gross_amount": 42_000_000,
        "discount_amount": 0,
        "status": "SENT",
        "approve_discount": None,
    },
]


async def seed_quotation_scenarios(session: AsyncSession, resolved: dict[str, uuid.UUID]) -> dict:
    actor_id = resolved.get(SYSTEM_INGEST_EMAIL)
    if actor_id is None:
        actor_id = await session.scalar(select(Lead.owner_id).limit(1))

    leads = dict((await session.execute(select(Lead.lead_no, Lead))).all())

    details: dict[str, dict] = {}
    for cfg in QUOTATION_SCENARIOS:
        lead = leads.get(cfg["lead_no"])
        if lead is None:
            details[cfg["quotation_no"]] = {"error": f"lead {cfg['lead_no']} belum di-seed"}
            continue
        h = await session.get(Hotel, lead.hotel_id)
        # jaga aktivitas lead idempoten (H4): hapus jejak lama utk quotation_no ini
        await session.execute(
            delete(LeadActivity).where(
                LeadActivity.lead_id == lead.id,
                LeadActivity.note.like(f"Quotation {cfg['quotation_no']} dibuat%"),
            )
        )
        quotation = await create_quotation(
            session,
            lead=lead,
            hotel=h,
            event_date=cfg["event_date"],
            event_name=cfg["event_name"],
            package_type=cfg["package_type"],
            pax_count=cfg["pax_count"],
            gross_amount=cfg["gross_amount"],
            discount_amount=cfg["discount_amount"],
            actor_id=actor_id,
            quotation_no=cfg["quotation_no"],
        )
        if cfg["approve_discount"]:
            quotation.discount_approval_status = "APPROVED"
        quotation.status = cfg["status"]
        if cfg["status"] != "DRAFT":
            quotation.pdf_key = f"quotations/{cfg['quotation_no']}.pdf"
        details[cfg["quotation_no"]] = {
            "status": quotation.status,
            "discount_approval": quotation.discount_approval_status,
            "sbm": quotation.sbm_rate_value,
            "fiscal_year": quotation.sbm_fiscal_year,
            "final_amount": quotation.final_amount,
        }

    # verifikasi pagu tidak lolos ke seeder (semua harus dalam pagu)
    exceeded = sum(1 for v in details.values() if "sbm" in v and v["sbm"] is None)
    return {
        "quotations": len(details),
        "with_sbm_snapshot": sum(1 for v in details.values() if v.get("sbm") is not None),
        "partial": sum(1 for v in details.values() if "error" in v),
        "details": details,
        "sbm_missing_check": "OK" if exceeded == 0 else f"no-snapshot={exceeded}",
    }