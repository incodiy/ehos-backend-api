"""Lost Reason Analytics seeder (PRD-F-07, task 8e).

Mensimulasikan lead LOST historis tersebar multi-kuartal + multi-hotel/region
(H2/H3) agar endpoint `/crm/analytics/lost-reasons` punya trend per kuartal
yang bermakna sejak task pertama — sekaligus melatih empty-state (H3) bila
period tanpa LOST.

Lead dibuat langsung `status=LOST` dengan `lost_reason` dari master kanonikal
`LOST_REASONS`; `created_at`/`updated_at` di-set historis agar:
- `created_at` = awal pipeline (3 bulan sebelum LOST),
- `updated_at` = tanggal ditetapkan LOST (dasar agregasi kuartal F-07).

Idempoten & deterministik (H4): upsert `lead_no` `CRM-8E-{code}-{Q}{i}` dan
aktivitas awal di-rebuild bersih per run (delete → insert).
"""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Hotel, Lead, LeadActivity

SOURCES = ("MANUAL", "CROSS_SELLING")

# (hotel_code, kuartal_seeded) — kuartal 2026-Q1..Q3 (live, sebelum 2027).
QUARTERS = [
    (date(2026, 2, 15), "Q1"),
    (date(2026, 5, 20), "Q2"),
    (date(2026, 8, 15), "Q3"),
]

LOST_REASONS = [
    "Anggaran tidak tersedia",
    "Memilih kompetitor",
    "Event dibatalkan",
    "Proses lelang gagal",
    "Dana pindah ke pos lain",
]

# amount_est memutar variasi nilai MICE (juta-an) + variasi GOV/PRIVATE.
AMOUNTS = [35_000_000, 68_000_000, 120_000_000, 45_000_000, 210_000_000]


async def seed_lost_reason_scenarios(session: AsyncSession, resolved: dict) -> dict:
    actor_id = resolved.get("root.admin@ehos.local")
    corp = resolved.get("corp.exec@ehos.local")
    if actor_id is None or corp is None:
        raise RuntimeError("root.admin/corp.exec belum di-seed — jalankan seed_users dulu")

    hotels = dict((await session.execute(select(Hotel.code, Hotel))).all())
    codes = [c for c in ("CWS", "SBAI", "ZHBA", "SQYO") if c in hotels]
    if not codes:
        return {"seeded": 0, "hotels": []}

    created: list[str] = []
    for hi, code in enumerate(codes):
        hotel = hotels[code]
        owner_id = resolved.get(
            "sales.cws@ehos.local" if code == "CWS" else "sales.tele@ehos.local"
        ) or actor_id
        for qi, (lost_on, quarter) in enumerate(QUARTERS, start=1):
            created_on = lost_on - timedelta(days=75)
            idx = (hi * 7 + qi * 3) % len(LOST_REASONS)
            reason = LOST_REASONS[idx]
            amount = AMOUNTS[(idx + qi) % len(AMOUNTS)]
            instype = "GOV" if (idx + qi) % 2 else "PRIVATE"
            source = SOURCES[(hi + qi) % len(SOURCES)]
            lead_no = f"CRM-8E-{code}-{quarter}{qi}"
            stmt = (
                pg_insert(Lead)
                .values(
                    lead_no=lead_no,
                    hotel_id=hotel.id,
                    source=source,
                    institution_type=instype,
                    company_name=f"Anorganis {code} {quarter}{qi}",
                    pic_name=f"PIC {code}",
                    pic_phone="+62812200" + f"{qi:03d}{hi}",
                    pic_email=f"pic.8e.{code.lower()}.{quarter}{qi}@mail.test",
                    province_id=hotel.province_id,
                    status="LOST",
                    lost_reason=reason,
                    next_followup_at=None,
                    amount_est=amount,
                    owner_id=owner_id,
                    referred_from_hotel_id=None,
                    created_by=corp,
                    updated_by=corp,
                    created_at=datetime.combine(created_on, datetime.min.time(), tzinfo=UTC),
                    updated_at=datetime.combine(lost_on, datetime.min.time(), tzinfo=UTC),
                )
                .on_conflict_do_update(
                    index_elements=[Lead.lead_no],
                    set_={
                        "hotel_id": hotel.id,
                        "source": source,
                        "status": "LOST",
                        "lost_reason": reason,
                        "amount_est": amount,
                        "owner_id": owner_id,
                        "created_by": corp,
                        "updated_by": corp,
                        "created_at": datetime.combine(created_on, datetime.min.time(), tzinfo=UTC),
                        "updated_at": datetime.combine(lost_on, datetime.min.time(), tzinfo=UTC),
                    },
                )
            )
            await session.execute(stmt)
            lead_id = await session.scalar(select(Lead.id).where(Lead.lead_no == lead_no))
            if lead_id is not None:
                # Rebuild aktivitas awal (idempoten): hapus lama → insert baru.
                await session.execute(delete(LeadActivity).where(LeadActivity.lead_id == lead_id))
                session.add(
                    LeadActivity(
                        lead_id=lead_id,
                        type="NOTE",
                        note=f"Lead dibuat (sumber {source})",
                        at=datetime.combine(created_on + timedelta(hours=10), datetime.min.time(), tzinfo=UTC),
                        actor_id=corp,
                    )
                )
                session.add(
                    LeadActivity(
                        lead_id=lead_id,
                        type="EMAIL",
                        note="Follow-up terakhir sebelum dinyatakan LOST.",
                        at=datetime.combine(lost_on - timedelta(days=9), datetime.min.time(), tzinfo=UTC),
                        actor_id=owner_id,
                    )
                )
            created.append(lead_no)

    return {"seeded": len(created), "hotels": codes, "lead_nos": created}