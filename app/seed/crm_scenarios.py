"""CRM kanban pipeline seeder — PRD-F-07 (task 8a) chained simulation (H2).

Mensimulasikan pipeline sales ujung-ke-ujung yang realistis (Constraint H1-H4):
- lead → aktivitas (CALL/EMAIL/MEETING/NOTE) → follow-up reminder `FOLLOWUP_CRM`.
- Variasi status kanban: LEAD / CONTACTED / PROSPECT / CONFIRMED / LOST (dgn
  `lost_reason` variatif), multi-region/multi-hotel, sumber MANUAL/CROSS_SELLING,
  owner sales per hotel & cross-sell tele.
- SLA follow-up dinamis: overdue / due-segera / future / tanpa jadwal — melatih
  filter `followup_due` + sweep reminder.

Idempoten & deterministik (H4): upsert `lead_no` (`CRM-8A-{code}-{NN}`), aktivitas
di-rebuild bersih per run. Juga menormalkan legacy status `WON` → `CONFIRMED`
agar DB selaras enum kontrak (F-07).
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Hotel, Lead, LeadActivity, LeadReferral
from app.services.crm_pipeline import refer_cross_property
from app.services.notifications import create_notification

SOURCES = ("MANUAL", "CROSS_SELLING", "REFERRAL")

# (hotel_code, owner_email, lead_variant, ...) — variant: 0..6
# Variant → 0 LEAD·no-followup | 1 LEAD·due-segera | 2 CONTACTED·overdue |
# 3 CONTACTED·future | 4 PROSPECT·beraktivitas+future | 5 CONFIRMED | 6 LOST
VARIANTS = {
    "CWS": (0, 1, 2, 3, 4, 5, 6),
    "SBAI": (1, 2, 4, 5),
    "ZHBA": (2, 3, 6),
    "SQYO": (0, 1, 5),
}

LOST_REASONS = [
    "Anggaran tidak tersedia",
    "Memilih kompetitor",
    "Event dibatalkan",
    "Proses lelang gagal",
    "Dana pindah ke pos lain",
]

COMPANY_NAMES = [
    "Kemenparekraf", "Pemprov Jawa Barat", "BUMN Jasa Konstruksi", "Kemenkeu DJP",
    "Pemkot Bandung", "Polri DivHumas", "Kementerian PUPR", "BPJS Ketenagakerjaan",
]


async def _upsert_lead(
    session: AsyncSession,
    *,
    lead_no: str,
    hotel: Hotel,
    owner_id: uuid.UUID,
    actor_id: uuid.UUID,
    source: str,
    institution_type: str,
    company_name: str,
    status: str,
    lost_reason: str | None,
    next_followup_at: datetime | None,
    amount_est: float | None,
    variant: int,
) -> uuid.UUID:
    stmt = (
        pg_insert(Lead)
        .values(
            lead_no=lead_no,
            hotel_id=hotel.id,
            source=source,
            institution_type=institution_type,
            company_name=company_name,
            pic_name=f"PIC {company_name.split()[-1]}",
            pic_phone="+62812" + f"{variant:03d}000{variant:02d}",
            pic_email=f"pic.{'gov' if institution_type == 'GOV' else 'biz'}.{variant}@mail.test",
            province_id=hotel.province_id,
            status=status,
            lost_reason=lost_reason,
            next_followup_at=next_followup_at,
            amount_est=amount_est,
            owner_id=owner_id,
            created_by=actor_id,
            updated_by=actor_id,
        )
        .on_conflict_do_update(
            index_elements=[Lead.lead_no],
            set_={
                "hotel_id": hotel.id,
                "source": source,
                "institution_type": institution_type,
                "company_name": company_name,
                "status": status,
                "lost_reason": lost_reason,
                "next_followup_at": next_followup_at,
                "amount_est": amount_est,
                "owner_id": owner_id,
                "updated_by": actor_id,
            },
        )
    )
    await session.execute(stmt)
    lead_id = await session.scalar(select(Lead.id).where(Lead.lead_no == lead_no))
    return lead_id


async def _build_activities(
    session: AsyncSession,
    lead_id: uuid.UUID,
    owner_id: uuid.UUID,
    now: datetime,
    variant: int,
) -> None:
    actors = []
    if variant in (2, 3, 4):
        actors.append(("CALL", "Telepon awal — sambungan baik, jelaskan paket MICE.", now - timedelta(days=6)))
    if variant == 4:
        actors.append(("MEETING", "Presentasi paket fullboard + survey ballroom.", now - timedelta(days=2)))
    if variant == 6:
        actors.append(("EMAIL", "Tindak lanjut terakhir sebelum dinyatakan LOST.", now - timedelta(days=9)))
    if not actors:
        return
    for atype, note, at in actors:
        session.add(
            LeadActivity(
                lead_id=lead_id, type=atype, note=note, at=at, actor_id=owner_id,
            )
        )


async def seed_crm_scenarios(session: AsyncSession, resolved: dict[str, uuid.UUID]) -> dict:
    now = datetime.now(UTC)
    actor_id = resolved.get("root.admin@ehos.local")
    if actor_id is None:
        raise RuntimeError("root.admin@ehos.local belum di-seed — jalankan seed_users dulu")

    # Normalisasi legacy enum: WON → CONFIRMED (F-07 kontrak 5 status)
    legacy_normalized = await session.execute(
        text("UPDATE leads SET status='CONFIRMED', lost_reason=NULL WHERE status='WON'")
    )

    hotels = dict((await session.execute(select(Hotel.code, Hotel))).all())
    used_codes = set(VARIANTS) & set(hotels)

    created: dict[str, int] = {}
    lead_ids: list[uuid.UUID] = []
    for code in sorted(used_codes):
        hotel = hotels[code]
        owner_email = "sales.cws@ehos.local" if code == "CWS" else "sales.tele@ehos.local"
        owner_id = resolved[owner_email]
        variants = VARIANTS[code]
        created[code] = 0
        for i, variant in enumerate(variants, start=1):
            status, lost_reason, followup, amount = (
                (
                    "LEAD", None, None,
                    [90_000_000, 150_000_000, 210_000_000, 75_000_000][i % 4],
                )
                if variant == 0 else
                (
                    "LEAD", None, now + timedelta(hours=2),
                    120_000_000,
                )
                if variant == 1 else
                (
                    "CONTACTED", None, now - timedelta(hours=26),
                    165_000_000,
                )
                if variant == 2 else
                (
                    "CONTACTED", None, now + timedelta(days=3),
                    85_000_000,
                )
                if variant == 3 else
                (
                    "PROSPECT", None, now + timedelta(days=5),
                    310_000_000,
                )
                if variant == 4 else
                (
                    "CONFIRMED", None, None,
                    420_000_000,
                )
                if variant == 5 else
                (
                    "LOST", LOST_REASONS[i % len(LOST_REASONS)], None,
                    60_000_000,
                )
            )
            instype = "GOV" if i % 2 else "PRIVATE"
            source = SOURCES[(i * 2) % len(SOURCES)]
            company = COMPANY_NAMES[(i + hash(code)) % len(COMPANY_NAMES)]
            lead_no = f"CRM-8A-{code}-{i:02d}"
            lead_id = await _upsert_lead(
                session,
                lead_no=lead_no,
                hotel=hotel,
                owner_id=owner_id,
                actor_id=actor_id,
                source=source,
                institution_type=instype,
                company_name=company,
                status=status,
                lost_reason=lost_reason,
                next_followup_at=followup,
                amount_est=amount,
                variant=variant,
            )
            lead_ids.append(lead_id)
            created[code] += 1

    # Rebuild activities (deterministik & idempoten): hapus lama → insert baru.
    if lead_ids:
        await session.execute(
            delete(LeadActivity).where(LeadActivity.lead_id.in_(lead_ids))
        )
        for code in sorted(used_codes):
            owner_email = "sales.cws@ehos.local" if code == "CWS" else "sales.tele@ehos.local"
            owner_id = resolved[owner_email]
            for i, variant in enumerate(VARIANTS[code], start=1):
                lead_id = (await session.scalar(
                    select(Lead.id).where(Lead.lead_no == f"CRM-8A-{code}-{i:02d}")
                ))
                if lead_id:
                    await _build_activities(session, lead_id, owner_id, now, variant)

    return {
        "per_hotel": created,
        "leads": len(lead_ids),
        "legacy_won_normalized": legacy_normalized.rowcount or 0,
    }


# ─── Cross-property referral & komisi (F-08, task 8b) ───────────────────

REFERRAL_SCENARIOS = [
    {
        "lead_no": "CRM-8B-REF-01",
        "from_code": "CWS",
        "to_code": "SQYO",
        "commission": 5_000_000,
        "company": "Kemenparekraf",
        "note": "Permintaan event resmi di Yogyakarta — diserahkan ke SQYO",
    },
    {
        "lead_no": "CRM-8B-REF-02",
        "from_code": "ZHBA",
        "to_code": "SBAI",
        "commission": None,
        "company": "Pemprov Jawa Barat",
        "note": "Butuh kapasitas ballroom lebih besar — serah tangan antar unit tanpa komisi",
    },
]


async def seed_crm_referrals(session: AsyncSession, resolved: dict[str, uuid.UUID]) -> dict:
    """Chained F-08: lead CWS/ZHBA → di-refer cross-property (H2/H4).

    Menjalankan service bisnis nyata (`refer_cross_property`) — bukan duplikasi
    logika — sehingga state seeder identik dgn hasil endpoint (ownership pindah,
    `source=REFERRAL`, `referred_from_hotel_id`, record `lead_referrals` + komisi).
    Idempoten: referral & aktivitas lama dihapus lalu dibangun ulang; notifikasi
    handover dibuat sekali per lead (dedup per entity).
    """
    now = datetime.now(UTC)
    actor_id = resolved.get("root.admin@ehos.local")
    if actor_id is None:
        raise RuntimeError("root.admin@ehos.local belum di-seed — jalankan seed_users dulu")

    hotels = dict((await session.execute(select(Hotel.code, Hotel))).all())
    outcomes: dict[str, dict] = {}
    notified = 0
    for cfg in REFERRAL_SCENARIOS:
        from_hotel = hotels[cfg["from_code"]]
        to_hotel = hotels[cfg["to_code"]]
        owner_id = (
            resolved["sales.cws@ehos.local"]
            if cfg["from_code"] == "CWS"
            else resolved["sales.tele@ehos.local"]
        )
        lead_id = await _upsert_lead(
            session,
            lead_no=cfg["lead_no"],
            hotel=from_hotel,
            owner_id=owner_id,
            actor_id=actor_id,
            source="MANUAL",
            institution_type="GOV",
            company_name=cfg["company"],
            status="CONTACTED",
            lost_reason=None,
            next_followup_at=now + timedelta(days=2),
            amount_est=90_000_000,
            variant=3,
        )
        lead = await session.get(Lead, lead_id)
        # Rebuild idempoten: hapus referral & aktivitas lama → service ulang.
        await session.execute(delete(LeadReferral).where(LeadReferral.lead_id == lead_id))
        await session.execute(delete(LeadActivity).where(LeadActivity.lead_id == lead_id))
        await session.flush()

        referral, new_owner = await refer_cross_property(
            session,
            lead,
            to_hotel_id=to_hotel.id,
            commission_amount=cfg["commission"],
            note=cfg["note"],
            actor_id=actor_id,
            target=to_hotel,
        )
        exists = await session.scalar(
            text(
                "SELECT 1 FROM notifications WHERE entity_type='lead' "
                "AND entity_id=:l AND type='CRM_REFERRAL' LIMIT 1"
            ),
            {"l": lead.uuid},
        )
        if not exists:
            await create_notification(
                session,
                key="crm_referral",
                user=new_owner,
                context={
                    "lead_no": lead.lead_no,
                    "company_name": lead.company_name,
                    "from_hotel_code": cfg["from_code"],
                    "to_hotel_code": cfg["to_code"],
                    "url": f"/crm/leads/{lead.uuid}",
                },
                entity_type="lead",
                entity_id=lead.uuid,
            )
            notified += 1
        outcomes[cfg["lead_no"]] = {
            "from": cfg["from_code"],
            "to": cfg["to_code"],
            "commission_amount": cfg["commission"],
            "owner": new_owner.email,
        }
    return {"referral_leads": outcomes, "notified": notified}