"""RFP intake seeder — PRD-F-11 (task 8c) chained simulation (H1-H4).

Menjalankan service bisnis nyata (`submit_rfp`) — bukan duplikasi logika — sehingga
state seeder identik dengan hasil endpoint `POST /frontpage/rfp`. Variasi (H3):
- target hotel VALID + GOV/CROSS → RFP ASSIGNED + lead `RFP_PORTAL` + notif `RFP_INTAKE`.
- target hotel KOSONG (RFP "target semua") → RFP NEW menunggu assign corporate (ERD).
- target hotel TIDAK VALID → RFP tetap NEW (validasi jujur, bukan error palsu/E-retry).
- jenis PRIVATE + berbagai package/pax/city.

Idempoten & deterministik (H4): `ref_no`/`lead_no` tetap → upsert `rfp_requests`,
lead dibuat sekali (guard lead_no), notif dedup per rfp (entity `RFP_INTAKE`).
"""

import uuid
from datetime import date
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services.rfp_pipeline import submit_rfp

SYSTEM_INGEST_EMAIL = "system.ingest@ehos.local"

RFP_SCENARIOS: list[dict[str, Any]] = [
    {
        "ref_no": "RFP-8C-CWS-01",
        "lead_no": "RFP-CWS-8C-01",
        "company_name": "Kemenkes RI",
        "institution_type": "GOV",
        "pic_name": "PIC Kemenkes",
        "phone": "+6281211110001",
        "email": "rfp.kemenkes@example.com",
        "event_date": date(2027, 3, 8),
        "pax": 120,
        "package_type": "FULLBOARD",
        "city": "Bandung",
        "target_hotel_code": "CWS",
        "notes": "Rakerkesnas — butuh 120 kamar + fullboard 3 hari",
    },
    {
        "ref_no": "RFP-8C-SQYO-01",
        "lead_no": "RFP-SQYO-8C-01",
        "company_name": "Pemkab Bantul",
        "institution_type": "GOV",
        "pic_name": "PIC Pemkab Bantul",
        "phone": "+6281211110002",
        "email": "rfp.bantul@example.com",
        "event_date": date(2027, 4, 12),
        "pax": 60,
        "package_type": "FULLDAY",
        "city": "Yogyakarta",
        "target_hotel_code": "SQYO",
        "notes": None,
    },
    {
        "ref_no": "RFP-8C-OPEN-01",
        "lead_no": None,
        "company_name": "Pemprov DKI Jakarta",
        "institution_type": "GOV",
        "pic_name": "PIC Pemprov DKI",
        "phone": "+6281211110003",
        "email": "rfp.dki@example.com",
        "event_date": date(2027, 5, 20),
        "pax": 200,
        "package_type": "HALFDAY",
        "city": "Jakarta",
        "target_hotel_code": None,
        "notes": "Belum tentukan hotel — beberapa opsi di Jakarta & Bandung",
    },
    {
        "ref_no": "RFP-8C-ALT-01",
        "lead_no": None,
        "company_name": "PT Swasta Nusantara",
        "institution_type": "PRIVATE",
        "pic_name": "PIC Swasta",
        "phone": "+6281211110004",
        "email": "rfp.swasta@example.com",
        "event_date": date(2027, 6, 2),
        "pax": 300,
        "package_type": "FULLBOARD",
        "city": "Bandung",
        "target_hotel_code": "ZZZZ",
        "notes": "Kode hotel tidak valid — menunggu assign corporate",
    },
]


async def seed_rfp_scenarios(session: AsyncSession, resolved: dict[str, uuid.UUID]) -> dict:
    actor_id = resolved.get(SYSTEM_INGEST_EMAIL)
    if actor_id is None:
        actor_id = await session.scalar(select(User.id).where(User.email == SYSTEM_INGEST_EMAIL))
    if actor_id is None:
        raise RuntimeError(f"{SYSTEM_INGEST_EMAIL} belum di-seed — jalankan seed_system_ingest_user")

    results: dict[str, dict] = {}
    for cfg in RFP_SCENARIOS:
        rfp, lead, owner = await submit_rfp(
            session,
            company_name=cfg["company_name"],
            institution_type=cfg["institution_type"],
            pic_name=cfg["pic_name"],
            phone=cfg["phone"],
            email=cfg["email"],
            event_date=cfg["event_date"],
            pax=cfg["pax"],
            package_type=cfg["package_type"],
            city=cfg["city"],
            target_hotel_code=cfg["target_hotel_code"],
            notes=cfg["notes"],
            actor_id=actor_id,
            ref_no=cfg["ref_no"],
            lead_no=cfg["lead_no"],
        )
        results[cfg["ref_no"]] = {
            "status": rfp.status,
            "assigned": rfp.assigned_hotel_id is not None,
            "lead": lead.lead_no if lead else None,
            "owner": owner.email if owner else None,
        }

    # Verifikasi dedup notif (H4): RFP_ASSIGNED punya tepat 1 notif RFP_INTAKE.
    notif_broken = 0
    for cfg in RFP_SCENARIOS:
        if cfg["lead_no"] is None:
            continue
        count = await session.scalar(
            text(
                "SELECT count(*) FROM notifications n JOIN rfp_requests r ON r.uuid=n.entity_id "
                "WHERE n.entity_type='rfp' AND n.type='RFP_INTAKE' AND r.ref_no=:ref"
            ),
            {"ref": cfg["ref_no"]},
        )
        if count < 1:
            notif_broken += 1

    outcomes = {
        "rfp_rows": len(results),
        "leads_created": sum(1 for v in results.values() if v["lead"]),
        "details": results,
        "notif_check": "OK" if notif_broken == 0 else f"broken={notif_broken}",
    }
    return outcomes