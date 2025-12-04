"""Checklist bank seeder (PRD-F-01) — Constraint H1-H4.

Adds tier-qualified templates to the checklist bank (the phase-4d templates stay
untouched because historical audit sessions snapshot them). Rubric conventions:
- TRAFFIC_LIGHT  → max_score 90 (90/45/0)
- NUMERIC_SCALE  → measured vs reference, max_score 100
- MULTI_ROOM     → room sampling, max_score = 90 x sample count
- BINARY_COUNT   → present/absent, max_score 1.0

brand_tier filter: audit sessions for a hotel pick template by
(department, brand_tier) — e.g. MĀUA (Luxury) vs Zest (Budget) differ here.
Idempotent: UPSERT on unique (department, name, version) / (template_id, code).
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChecklistItem, ChecklistSection, ChecklistTemplate

VERSION = "v2026.1"


# ---------------------------------------------------------------------------
# BANK DATA — department → templates → sections → items
# ---------------------------------------------------------------------------
BANK: list[dict[str, Any]] = [
    {
        "department": "GM",
        "name": "GM Checklist — Luxury Tier",
        "brand_tier": "Luxury",
        "sections": [
            {
                "code": "G1",
                "name": "Strategic Leadership",
                "items": [
                    (
                        "G1.01",
                        "GM memiliki KPI tahunan yang disepakati pemilik dan ditinjau tiap kuartal",
                        "TRAFFIC_LIGHT",
                        90,
                        1.0,
                        False,
                        False,
                    ),
                    (
                        "G1.02",
                        "Rencana perbaikan berkelanjutan terdokumentasi dan dieksekusi",
                        "TRAFFIC_LIGHT",
                        90,
                        1.0,
                        False,
                        False,
                    ),
                    (
                        "G1.03",
                        "Struktur organisasi lengkap dengan job description seluruh key position",
                        "BINARY_COUNT",
                        1.0,
                        1.0,
                        False,
                        False,
                    ),
                ],
            },
            {
                "code": "G2",
                "name": "Guest Experience",
                "items": [
                    (
                        "G2.01",
                        "Skor ulasan eksternal (GSS/TripAdvisor) di atas ambang pasar premium",
                        "TRAFFIC_LIGHT",
                        90,
                        1.0,
                        False,
                        False,
                    ),
                    (
                        "G2.02",
                        "GM melakukan daily brief dan hadir pada peak-hour lobby/resto",
                        "TRAFFIC_LIGHT",
                        90,
                        1.0,
                        False,
                        False,
                    ),
                    (
                        "G2.03",
                        "Contoh tamu diundang untuk guest journey review skala penuh (3 sample)",
                        "MULTI_ROOM",
                        270.0,
                        1.0,
                        False,
                        False,
                    ),
                ],
            },
            {
                "code": "G3",
                "name": "Commercial & Finance",
                "items": [
                    (
                        "G3.01",
                        "Tingkat okupansi dan RevPAR sesuai target tahunan",
                        "NUMERIC_SCALE",
                        100,
                        1.0,
                        False,
                        False,
                    ),
                    (
                        "G3.02",
                        "Tidak ada audit hotel corporate yang terulang (repeat finding)",
                        "BINARY_COUNT",
                        1.0,
                        1.0,
                        False,
                        False,
                    ),
                    (
                        "G3.03",
                        "RKF/RMA (revenue & yield) di-review GM tiap minggu",
                        "BINARY_COUNT",
                        1.0,
                        1.0,
                        False,
                        False,
                    ),
                ],
            },
        ],
    },
    {
        "department": "GM",
        "name": "GM Checklist — Budget Tier",
        "brand_tier": "Budget",
        "sections": [
            {
                "code": "G1",
                "name": "Manajemen Operasional",
                "items": [
                    (
                        "G1.01",
                        "GM terlibat dalam rotasi pelayanan dan daily briefing departemen",
                        "TRAFFIC_LIGHT",
                        90,
                        1.0,
                        False,
                        False,
                    ),
                    (
                        "G1.02",
                        "Target finansial (okupansi, GOP) dikomunikasikan ke seluruh staf",
                        "BINARY_COUNT",
                        1.0,
                        1.0,
                        False,
                        False,
                    ),
                ],
            },
            {
                "code": "G2",
                "name": "Standar Layanan",
                "items": [
                    (
                        "G2.01",
                        "SOP layanan front office & housekeeping tersedia dan dijalankan",
                        "TRAFFIC_LIGHT",
                        90,
                        1.0,
                        False,
                        False,
                    ),
                    (
                        "G2.02",
                        "Penanganan komplain tamu terdokumentasi dan ditindaklanjuti SSC",
                        "TRAFFIC_LIGHT",
                        90,
                        1.0,
                        False,
                        False,
                    ),
                    (
                        "G2.03",
                        "Rasio staf per kamar sesuai standar segmen budget",
                        "NUMERIC_SCALE",
                        100,
                        1.0,
                        False,
                        False,
                    ),
                ],
            },
        ],
    },
    {
        "department": "HOUSEKEEPING",
        "name": "Housekeeping Checklist — Luxury Tier",
        "brand_tier": "Luxury",
        "sections": [
            {
                "code": "HK1",
                "name": "Room Product & Amenities",
                "items": [
                    (
                        "HK1.01",
                        "Luxury amenity kit premium tersedia lengkap semua tipe kamar (3 sample)",
                        "MULTI_ROOM",
                        270.0,
                        1.0,
                        False,
                        False,
                    ),
                    (
                        "HK1.02",
                        "Kebersihan spot-check menyeluruh kamar kelas suite (3 sample)",
                        "MULTI_ROOM",
                        270.0,
                        1.0,
                        False,
                        False,
                    ),
                    (
                        "HK1.03",
                        "Linen dan bedding bebas stain, sesuai standar kerapian brand",
                        "TRAFFIC_LIGHT",
                        90,
                        1.0,
                        False,
                        False,
                    ),
                    (
                        "HK1.04",
                        "Setrikaan seragam karyawan & linen VIP memenuhi standar",
                        "BINARY_COUNT",
                        1.0,
                        1.0,
                        False,
                        False,
                    ),
                ],
            },
            {
                "code": "HK2",
                "name": "Environment & Hygiene",
                "items": [
                    (
                        "HK2.01",
                        "Tingkat pencahayaan koridor kamar sesuai standar (lux reading)",
                        "NUMERIC_SCALE",
                        100,
                        1.0,
                        False,
                        False,
                    ),
                    (
                        "HK2.02",
                        "Suhu & kelembapan kamar pada setpoint premium",
                        "NUMERIC_SCALE",
                        100,
                        1.0,
                        False,
                        False,
                    ),
                    ("HK2.03", "Program pest control rutin terdokumentasi", "TRAFFIC_LIGHT", 90, 1.0, False, False),
                ],
            },
            {
                "code": "HK3",
                "name": "Linen & Laundry",
                "items": [
                    (
                        "HK3.01",
                        "Sirkulasi par stock linen mencukupi peak occupancy (1:3)",
                        "NUMERIC_SCALE",
                        100,
                        1.0,
                        False,
                        False,
                    ),
                    (
                        "HK3.02",
                        "Laundry outsourcing punya SLA dan verifikasi kualitas mingguan",
                        "BINARY_COUNT",
                        1.0,
                        1.0,
                        False,
                        False,
                    ),
                ],
            },
            {
                "code": "HK4",
                "name": "Lost & Found & Inventory",
                "items": [
                    (
                        "HK4.01",
                        "Lost & found terkelola sistemik, direkonsiliasi bulanan",
                        "TRAFFIC_LIGHT",
                        90,
                        1.0,
                        True,
                        False,
                    ),
                    (
                        "HK4.02",
                        "Inventaris fixed asset kamar dihitung ulang per semester",
                        "BINARY_COUNT",
                        1.0,
                        1.0,
                        False,
                        False,
                    ),
                ],
            },
        ],
    },
    {
        "department": "HOUSEKEEPING",
        "name": "Housekeeping Checklist — Budget Tier",
        "brand_tier": "Budget",
        "sections": [
            {
                "code": "HK1",
                "name": "Kebersihan Kamar",
                "items": [
                    ("HK1.01", "Spot-check kebersihan kamar (3 sample)", "MULTI_ROOM", 270.0, 1.0, False, False),
                    ("HK1.02", "Linen lengkap & layak pakai", "TRAFFIC_LIGHT", 90, 1.0, False, False),
                ],
            },
            {
                "code": "HK2",
                "name": "Tamu Transit & Umum",
                "items": [
                    ("HK2.01", "Waktu turnaround kamar sesuai standar segmen", "NUMERIC_SCALE", 100, 1.0, False, False),
                    ("HK2.02", "Area koridor & lobby dalam kondisi bersih", "TRAFFIC_LIGHT", 90, 1.0, False, False),
                ],
            },
            {
                "code": "HK3",
                "name": "Linen & Supplies",
                "items": [
                    ("HK3.01", "Par stock linen cukup untuk okupansi penuh", "BINARY_COUNT", 1.0, 1.0, False, False),
                    (
                        "HK3.02",
                        "Persediaan amenities (bubble set) sesuai brand spec",
                        "BINARY_COUNT",
                        1.0,
                        1.0,
                        False,
                        False,
                    ),
                ],
            },
        ],
    },
    {
        "department": "HOUSEKEEPING",
        "name": "Housekeeping Checklist — Eco-Resort Tier",
        "brand_tier": "Eco-Resort",
        "sections": [
            {
                "code": "HK1",
                "name": "Sustainable Procurement",
                "items": [
                    (
                        "HK1.01",
                        "Amenity refill & bulk dispenser (zero single-use plastic)",
                        "TRAFFIC_LIGHT",
                        90,
                        1.0,
                        True,
                        False,
                    ),
                    ("HK1.02", "Eco-label linen & detergent tersertifikasi", "BINARY_COUNT", 1.0, 1.0, False, False),
                ],
            },
            {
                "code": "HK2",
                "name": "Waste & Water",
                "items": [
                    (
                        "HK2.01",
                        "Program penghematan air (linen reuse) aktif setiap kamar okupasi >1 malam",
                        "TRAFFIC_LIGHT",
                        90,
                        1.0,
                        True,
                        False,
                    ),
                    (
                        "HK2.02",
                        "Pemilahan sampah organik/non-organik di area housekeeping",
                        "BINARY_COUNT",
                        1.0,
                        1.0,
                        False,
                        False,
                    ),
                ],
            },
            {
                "code": "HK3",
                "name": "Kawasan Alami",
                "items": [
                    (
                        "HK3.01",
                        "Kamar villa/kabana dalam kondisi alami & bebas bahan kimia keras",
                        "TRAFFIC_LIGHT",
                        90,
                        1.0,
                        False,
                        False,
                    ),
                    ("HK3.02", "Lampu exterior hemat energi dan terjadwal", "BINARY_COUNT", 1.0, 1.0, False, False),
                ],
            },
        ],
    },
    {
        "department": "KITCHEN_FB",
        "name": "Kitchen & F&B Checklist — Luxury Tier",
        "brand_tier": "Luxury",
        "sections": [
            {
                "code": "KF1",
                "name": "Food Safety & Hygiene",
                "items": [
                    (
                        "KF1.01",
                        "Suhu inti masakan sesuai HACCP saat plating (core temp)",
                        "NUMERIC_SCALE",
                        100,
                        1.5,
                        False,
                        False,
                    ),
                    (
                        "KF1.02",
                        "Suhu chiller & freezer sesuai standar penyimpanan",
                        "NUMERIC_SCALE",
                        100,
                        1.5,
                        False,
                        False,
                    ),
                    (
                        "KF1.03",
                        "Sanitasi peralatan dapur — test swab & chlorine ppm",
                        "NUMERIC_SCALE",
                        100,
                        1.5,
                        False,
                        False,
                    ),
                    (
                        "KF1.04",
                        "Pemisahan area proses mentah vs matang terjaga",
                        "TRAFFIC_LIGHT",
                        90,
                        1.0,
                        False,
                        False,
                    ),
                    (
                        "KF1.05",
                        "Label FIFO & tanggal kedaluwarsa seluruh bahan",
                        "TRAFFIC_LIGHT",
                        90,
                        1.5,
                        False,
                        False,
                    ),
                ],
            },
            {
                "code": "KF2",
                "name": "Quality & Consistency",
                "items": [
                    ("KF2.01", "Recipe standardization seluruh menu signature", "BINARY_COUNT", 1.0, 1.0, False, False),
                    ("KF2.02", "Tasting panel bulanan oleh chef exco & GM", "BINARY_COUNT", 1.0, 1.0, False, False),
                    ("KF2.03", "Guest feedback F&B di-review mingguan", "TRAFFIC_LIGHT", 90, 1.0, False, False),
                ],
            },
            {
                "code": "KF3",
                "name": "Special Event & Buffet",
                "items": [
                    (
                        "KF3.01",
                        "Buffet mise-en-place tahan suhu (chafing terukur)",
                        "TRAFFIC_LIGHT",
                        90,
                        1.0,
                        False,
                        False,
                    ),
                    ("KF3.02", "GM/Owner's table service meetup dijalankan", "BINARY_COUNT", 1.0, 1.0, True, False),
                ],
            },
        ],
    },
    {
        "department": "KITCHEN_FB",
        "name": "Kitchen & F&B Checklist — Budget Tier",
        "brand_tier": "Budget",
        "sections": [
            {
                "code": "KF1",
                "name": "Keamanan Pangan",
                "items": [
                    ("KF1.01", "Suhu chiller sesuai standar penyimpanan", "NUMERIC_SCALE", 100, 1.5, False, False),
                    ("KF1.02", "Suhu inti masakan saat plating sesuai HACCP", "NUMERIC_SCALE", 100, 1.5, False, False),
                    ("KF1.03", "Pemisahan bahan mentah & matang terjaga", "TRAFFIC_LIGHT", 90, 1.0, False, False),
                ],
            },
            {
                "code": "KF2",
                "name": "Layanan & Menu",
                "items": [
                    ("KF2.01", "Menu & harga sesuai spesifikasi segmen", "BINARY_COUNT", 1.0, 1.0, False, False),
                    ("KF2.02", "Breakfast operational readiness tiap hari", "TRAFFIC_LIGHT", 90, 1.0, False, False),
                ],
            },
            {
                "code": "KF3",
                "name": "Perlengkapan",
                "items": [
                    ("KF3.01", "Perlengkapan dapur dalam kondisi laik pakai", "BINARY_COUNT", 1.0, 1.0, False, False),
                    (
                        "KF3.02",
                        "Log kontaminasi ulang permukaan kerja terdokumentasi",
                        "TRAFFIC_LIGHT",
                        90,
                        1.0,
                        False,
                        False,
                    ),
                ],
            },
        ],
    },
    {
        "department": "SECURITY_RISK",
        "name": "Security Risk Checklist — Universal",
        "brand_tier": None,
        "sections": [
            {
                "code": "SR1",
                "name": "Fire & Life Safety",
                "items": [
                    (
                        "SR1.01",
                        "Fire alarm panel dalam mode AUTOMATIC dan bebas trouble/alarm palsu",
                        "TRAFFIC_LIGHT",
                        90,
                        2.0,
                        False,
                        True,
                    ),
                    (
                        "SR1.02",
                        "Alat pemadam (APAR) terisi, ter-seal, tekanan sesuai PSI, uji berkala",
                        "NUMERIC_SCALE",
                        100,
                        2.0,
                        False,
                        True,
                    ),
                    (
                        "SR1.03",
                        "Jalur evakuasi & pintu darurat bebas hambatan, terbuka ke luar",
                        "TRAFFIC_LIGHT",
                        90,
                        2.0,
                        False,
                        True,
                    ),
                    (
                        "SR1.04",
                        "Evakuasi drill dan inspeksi fasilitas kebakaran sesuai jadwal",
                        "TRAFFIC_LIGHT",
                        90,
                        1.5,
                        False,
                        True,
                    ),
                    (
                        "SR1.05",
                        "Ruang utilitas listrik (MDP/panel) bebas material mudah terbakar",
                        "BINARY_COUNT",
                        1.0,
                        1.5,
                        False,
                        True,
                    ),
                    (
                        "SR1.06",
                        "Detektor asap per zona kamar berfungsi (3 sample)",
                        "MULTI_ROOM",
                        270.0,
                        1.5,
                        False,
                        True,
                    ),
                ],
            },
            {
                "code": "SR2",
                "name": "Security Intelligence & Surveillance",
                "items": [
                    (
                        "SR2.01",
                        "CCTV berfungsi & rekaman arsip 30 hari (akses terbatas)",
                        "TRAFFIC_LIGHT",
                        90,
                        1.5,
                        False,
                        False,
                    ),
                    (
                        "SR2.02",
                        "Pos security & patrol schedule berjalan sesuai SOP",
                        "TRAFFIC_LIGHT",
                        90,
                        1.0,
                        False,
                        False,
                    ),
                    (
                        "SR2.03",
                        "Skrining tamu & barang (penanganan paket mencurigakan) terjaga",
                        "TRAFFIC_LIGHT",
                        90,
                        1.0,
                        True,
                        False,
                    ),
                ],
            },
            {
                "code": "SR3",
                "name": "Emergency Response",
                "items": [
                    (
                        "SR3.01",
                        "Kesiapan tanggap gempa pertama (gathering point, drill)",
                        "TRAFFIC_LIGHT",
                        90,
                        1.5,
                        False,
                        True,
                    ),
                    ("SR3.02", "P3K & defibrillator tersedia di area publik", "BINARY_COUNT", 1.0, 1.0, False, True),
                    (
                        "SR3.03",
                        "Sirkulasi evakuasi difabel (ramp) menuju titik kumpul berfungsi",
                        "BINARY_COUNT",
                        1.0,
                        1.0,
                        False,
                        True,
                    ),
                ],
            },
            {
                "code": "SR4",
                "name": "Access & Key Control",
                "items": [
                    (
                        "SR4.01",
                        "Kartu/akses tingkat master & sub-master direkonsiliasi bulanan",
                        "BINARY_COUNT",
                        1.0,
                        1.0,
                        False,
                        False,
                    ),
                    (
                        "SR4.02",
                        "Kunci/akses log lewat tim keamanan, non-anak duplikasi",
                        "TRAFFIC_LIGHT",
                        90,
                        1.0,
                        False,
                        False,
                    ),
                    (
                        "SR4.03",
                        "Backup room access alternatif tersedia saat outage",
                        "TRAFFIC_LIGHT",
                        90,
                        1.0,
                        True,
                        False,
                    ),
                ],
            },
        ],
    },
]


def _section_items(section: dict) -> list[dict]:
    return [
        {
            "code": row[0],
            "question_text": row[1],
            "rubric_type": row[2],
            "max_score": row[3],
            "weight": row[4],
            "na_allowed": row[5],
            "is_life_safety": row[6],
        }
        for row in section["items"]
    ]


async def seed_checklist_bank(session: AsyncSession, published_by: uuid.UUID) -> None:
    """Upsert tier-qualified checklist templates + sections + items (idempotent)."""

    for entry in BANK:
        department = entry["department"]
        name = entry["name"]
        brand_tier = entry["brand_tier"]
        tpl = pg_insert(ChecklistTemplate).values(
            department=department,
            name=name,
            version=VERSION,
            brand_tier=brand_tier,
            status="LOCKED",
            locked_at=datetime.now(UTC),
            published_by=published_by,
        )
        tpl = tpl.on_conflict_do_update(
            index_elements=[ChecklistTemplate.department, ChecklistTemplate.name, ChecklistTemplate.version],
            set_={"brand_tier": brand_tier, "status": "LOCKED"},
        )
        await session.execute(tpl)
        template_id = await session.scalar(
            select(ChecklistTemplate.id).where(
                ChecklistTemplate.department == department,
                ChecklistTemplate.name == name,
                ChecklistTemplate.version == VERSION,
            )
        )

        for position, section in enumerate(entry["sections"], start=1):
            sec = pg_insert(ChecklistSection).values(
                template_id=template_id,
                code=section["code"],
                name=section["name"],
                sort_order=position * 10,
            )
            sec = sec.on_conflict_do_update(
                index_elements=[ChecklistSection.template_id, ChecklistSection.code],
                set_={"name": section["name"], "sort_order": position * 10},
            )
            await session.execute(sec)
            section_id = await session.scalar(
                select(ChecklistSection.id).where(
                    ChecklistSection.template_id == template_id,
                    ChecklistSection.code == section["code"],
                )
            )

            for rank, item in enumerate(_section_items(section), start=1):
                item["sort_order"] = rank * 10
                it = pg_insert(ChecklistItem).values(
                    section_id=section_id,
                    code=item["code"],
                    question_text=item["question_text"],
                    rubric_type=item["rubric_type"],
                    max_score=item["max_score"],
                    weight=item["weight"],
                    na_allowed=item["na_allowed"],
                    is_life_safety=item["is_life_safety"],
                    sort_order=item["sort_order"],
                )
                it = it.on_conflict_do_update(
                    index_elements=[ChecklistItem.section_id, ChecklistItem.code],
                    set_={
                        "question_text": item["question_text"],
                        "rubric_type": item["rubric_type"],
                        "max_score": item["max_score"],
                        "weight": item["weight"],
                        "na_allowed": item["na_allowed"],
                        "is_life_safety": item["is_life_safety"],
                        "sort_order": item["sort_order"],
                    },
                )
                await session.execute(it)

    # -----------------------------------------------------------------------
    # ARCHIVED, DRAFT, AND SOFT-DELETED SCENARIOS (Constraint H1-H4)
    # -----------------------------------------------------------------------
    additional_templates = [
        {
            "department": "HOUSEKEEPING",
            "name": "Housekeeping — Standard Room v2024 (Legacy)",
            "version": "v2024.1",
            "brand_tier": None,
            "status": "ARCHIVED",
            "deleted_at": None,
            "sections": [
                {
                    "code": "HK-LEG-01",
                    "name": "Legacy Room Cleanliness",
                    "items": [
                        ("HK-L1", "Kerapihan tempat tidur dan linen bersih tanpa noda", "TRAFFIC_LIGHT", 90, 1.0, False, False),
                    ],
                }
            ],
        },
        {
            "department": "GM",
            "name": "General Manager Annual Audit v2025 (Arsip)",
            "version": "v2025.1",
            "brand_tier": None,
            "status": "ARCHIVED",
            "deleted_at": None,
            "sections": [
                {
                    "code": "GM-LEG-01",
                    "name": "Legacy Operations & Compliance",
                    "items": [
                        ("GM-L1", "Kepatuhan SOP perizinan operasional hotel", "BINARY_COUNT", 1.0, 1.0, False, False),
                    ],
                }
            ],
        },
        {
            "department": "KITCHEN_FB",
            "name": "Kitchen & FB Hygiene Draft v2027",
            "version": "v2027.1",
            "brand_tier": "Upscale",
            "status": "DRAFT",
            "deleted_at": None,
            "sections": [
                {
                    "code": "FB-DRF-01",
                    "name": "Cold Storage & HACCP",
                    "items": [
                        ("FB-D1", "Suhu chiller daging berada di antara 0-4 derajat Celsius", "NUMERIC_SCALE", 100, 1.5, False, True),
                    ],
                }
            ],
        },
        {
            "department": "SECURITY_RISK",
            "name": "Security Risk Obsolete Protocol v2023",
            "version": "v2023.1",
            "brand_tier": None,
            "status": "ARCHIVED",
            "deleted_at": datetime(2025, 1, 1, tzinfo=UTC),
            "sections": [
                {
                    "code": "SEC-OBS-01",
                    "name": "Obsolete Patrol Procedure",
                    "items": [
                        ("SEC-O1", "Logbook pos satpam manual", "BINARY_COUNT", 1.0, 1.0, False, False),
                    ],
                }
            ],
        },
    ]

    for extra in additional_templates:
        tpl = pg_insert(ChecklistTemplate).values(
            department=extra["department"],
            name=extra["name"],
            version=extra["version"],
            brand_tier=extra["brand_tier"],
            status=extra["status"],
            locked_at=datetime.now(UTC) if extra["status"] != "DRAFT" else None,
            published_by=published_by,
            deleted_at=extra["deleted_at"],
        )
        tpl = tpl.on_conflict_do_update(
            index_elements=[ChecklistTemplate.department, ChecklistTemplate.name, ChecklistTemplate.version],
            set_={
                "brand_tier": extra["brand_tier"],
                "status": extra["status"],
                "deleted_at": extra["deleted_at"],
            },
        )
        await session.execute(tpl)
        template_id = await session.scalar(
            select(ChecklistTemplate.id).where(
                ChecklistTemplate.department == extra["department"],
                ChecklistTemplate.name == extra["name"],
                ChecklistTemplate.version == extra["version"],
            )
        )
        for position, sec_data in enumerate(extra["sections"], start=1):
            sec = pg_insert(ChecklistSection).values(
                template_id=template_id,
                code=sec_data["code"],
                name=sec_data["name"],
                sort_order=position * 10,
            )
            sec = sec.on_conflict_do_update(
                index_elements=[ChecklistSection.template_id, ChecklistSection.code],
                set_={"name": sec_data["name"], "sort_order": position * 10},
            )
            await session.execute(sec)
            section_id = await session.scalar(
                select(ChecklistSection.id).where(
                    ChecklistSection.template_id == template_id,
                    ChecklistSection.code == sec_data["code"],
                )
            )
            for rank, item_tuple in enumerate(sec_data["items"], start=1):
                code, q_text, r_type, max_s, w, na_a, is_ls = item_tuple
                it = pg_insert(ChecklistItem).values(
                    section_id=section_id,
                    code=code,
                    question_text=q_text,
                    rubric_type=r_type,
                    max_score=max_s,
                    weight=w,
                    na_allowed=na_a,
                    is_life_safety=is_ls,
                    sort_order=rank * 10,
                )
                it = it.on_conflict_do_update(
                    index_elements=[ChecklistItem.section_id, ChecklistItem.code],
                    set_={
                        "question_text": q_text,
                        "rubric_type": r_type,
                        "max_score": max_s,
                        "weight": w,
                        "na_allowed": na_a,
                        "is_life_safety": is_ls,
                        "sort_order": rank * 10,
                    },
                )
                await session.execute(it)

