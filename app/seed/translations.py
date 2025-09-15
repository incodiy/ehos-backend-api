"""Seed translations table — locale='en' for all translatable entities.

Scope (PRD-F-22, clarified): ALL system-generated & master data translated to EN.
hotel_departments excluded (all English proper nouns, F3 fallback suffices).
User-generated content (findings/capa/free-text) excluded (F-15 auto-translate, Could).

Seed approach: query existing rows → value = CURATED_EN[text] (if present), else
identity (text already English). Deterministic, idempotent via UPSERT ON CONFLICT.
Estimated ~650 rows (442 items + 82 sections + 28 templates + 29 master attrs + 30 provinces).
"""

from __future__ import annotations

import uuid as _uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checklist import (
    ChecklistItem,
    ChecklistSection,
    ChecklistTemplate,
)
from app.models.cross import Translation
from app.models.master import Brand, Province, Region
from app.models.users import Permission, Role
from app.services.notifications import NOTIFICATION_TEMPLATES, template_uuid

# ─── CURATED EN OVERRIDES ────────────────────────────────────────────────
# Only entries where canonical text is Indonesian need explicit EN.
# English text items fall through to identity (value = canonical).

PROVINCE_EN: dict[str, str] = {
    "Sumatera Utara": "North Sumatra",
    "Riau": "Riau",
    "Jambi": "Jambi",
    "Lampung": "Lampung",
    "Kepulauan Bangka Belitung": "Bangka Belitung Islands",
    "Kepulauan Riau": "Riau Islands",
    "DKI Jakarta": "Special Capital Region of Jakarta",
    "Jawa Barat": "West Java",
    "Jawa Tengah": "Central Java",
    "DI Yogyakarta": "Special Region of Yogyakarta",
    "Jawa Timur": "East Java",
    "Banten": "Banten",
    "Nusa Tenggara Barat": "West Nusa Tenggara",
    "Nusa Tenggara Timur": "East Nusa Tenggara",
    "Kalimantan Barat": "West Kalimantan",
    "Kalimantan Tengah": "Central Kalimantan",
    "Kalimantan Selatan": "South Kalimantan",
    "Kalimantan Timur": "East Kalimantan",
    "Kalimantan Utara": "North Kalimantan",
    "Sulawesi Utara": "North Sulawesi",
    "Sulawesi Tengah": "Central Sulawesi",
    "Sulawesi Selatan": "South Sulawesi",
    "Sulawesi Tenggara": "Southeast Sulawesi",
    "Papua Barat": "West Papua",
    "Papua Barat Daya": "Southwest Papua",
    "Papua Tengah": "Central Papua",
    "Papua Selatan": "South Papua",
}

SECTION_EN: dict[str, str] = {
    "Kawasan Alami": "Natural Area",
    "Keamanan Pangan": "Food Safety",
    "Kebersihan Kamar": "Room Cleanliness",
    "Layanan & Menu": "Service & Menu",
    "Manajemen Operasional": "Operational Management",
    "Perlengkapan": "Equipment & Supplies",
    "Standar Layanan": "Service Standards",
    "Tamu Transit & Umum": "Transit & General Guests",
}

ITEM_EN: dict[str, str] = {
    "Alat pemadam (APAR) terisi, ter-seal, tekanan sesuai PSI, uji berkala": (
        "Fire extinguishers (APAR) filled, sealed, correct PSI pressure, periodically tested"
    ),
    "Area koridor & lobby dalam kondisi bersih": ("Corridor & lobby areas are clean and well-maintained"),
    "Backup room access alternatif tersedia saat outage": ("Backup room key access available during system outage"),
    "Buffet mise-en-place tahan suhu (chafing terukur)": (
        "Buffet mise-en-place temperature-stable (chafing dishes calibrated)"
    ),
    "Detektor asap per zona kamar berfungsi (3 sample)": ("Smoke detectors per room zone operational (3 samples)"),
    "Eco-label linen & detergent tersertifikasi": ("Eco-label certified linen & detergent"),
    "Evakuasi drill dan inspeksi fasilitas kebakaran sesuai jadwal": (
        "Evacuation drills and fire facility inspections conducted per schedule"
    ),
    "Fire alarm panel dalam mode AUTOMATIC dan bebas trouble/alarm palsu": (
        "Fire alarm panel in AUTOMATIC mode, free of trouble/false alarms"
    ),
    "GM melakukan daily brief dan hadir pada peak-hour lobby/resto": (
        "GM conducts daily briefing and present at lobby/restaurant during peak hours"
    ),
    "GM memiliki KPI tahunan yang disepakati pemilik dan ditinjau tiap kuartal": (
        "GM has annual KPIs agreed by owner, reviewed each quarter"
    ),
    "GM/Owner's table service meetup dijalankan": ("GM/Owner table service meetup conducted"),
    "GM terlibat dalam rotasi pelayanan dan daily briefing departemen": (
        "GM participates in service rotation and department daily briefings"
    ),
    "Guest feedback F&B di-review mingguan": ("Guest F&B feedback reviewed weekly"),
    "Inventaris fixed asset kamar dihitung ulang per semester": ("Room fixed asset inventory recounted each semester"),
    "Lampu exterior hemat energi dan terjadwal": ("Exterior lighting energy-efficient and on schedule"),
    "Laundry outsourcing punya SLA dan verifikasi kualitas mingguan": (
        "Outsourced laundry has SLA and weekly quality verification"
    ),
    "Linen dan bedding bebas stain, sesuai standar kerapian brand": (
        "Linen and bedding stain-free, matching brand presentation standards"
    ),
    "Penanganan komplain tamu terdokumentasi dan ditindaklanjuti SSC": (
        "Guest complaint handling documented and follow-up via SSC"
    ),
    "Rasio staf per kamar sesuai standar segmen budget": ("Staff-to-room ratio aligned with budget segment standards"),
    "Rencana perbaikan berkelanjutan terdokumentasi dan dieksekusi": (
        "Continuous improvement plan documented and executed"
    ),
    "RKF/RMA (revenue & yield) di-review GM tiap minggu": ("RKF/RMA (revenue & yield) reviewed by GM weekly"),
    "SOP layanan front office & housekeeping tersedia dan dijalankan": (
        "Front office & housekeeping service SOP available and followed"
    ),
    "Tingkat okupansi dan RevPAR sesuai target tahunan": ("Occupancy rate and RevPAR aligned with annual targets"),
}


# ─── ENTITY_TYPE FIELD MAPPING ────────────────────────────────────────────


async def seed_translations(session: AsyncSession, created_by) -> int:
    """Seed EN translations for all translatable master content. Row count returned."""
    if isinstance(created_by, str):
        uid = _uuid.UUID(created_by)
    elif hasattr(created_by, "id"):
        uid = created_by.id
    else:
        uid = created_by

    rows: list[dict] = []

    def _add(entity_type: str, entity_id, field: str, value: str) -> None:
        rows.append(
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "field": field,
                "locale": "en",
                "value": value,
                "created_by": uid,
                "updated_by": uid,
            }
        )

    # ── Checklist Templates ──
    stmt = select(ChecklistTemplate)
    for t in (await session.scalars(stmt)).all():
        en_name = t.name  # all English already
        _add("checklist_template", t.uuid, "name", en_name)

    # ── Checklist Sections ──
    stmt = select(ChecklistSection)
    for s in (await session.scalars(stmt)).all():
        en_name = SECTION_EN.get(s.name, s.name)
        _add("checklist_section", s.uuid, "name", en_name)

    # ── Checklist Items ──
    stmt = select(ChecklistItem)
    for item in (await session.scalars(stmt)).all():
        en_text = ITEM_EN.get(item.question_text, item.question_text)
        _add("checklist_item", item.uuid, "question_text", en_text)

    # ── Brands ──
    stmt = select(Brand)
    for b in (await session.scalars(stmt)).all():
        _add("brand", b.uuid, "name", b.name)

    # ── Regions (name + sales_region) ──
    stmt = select(Region)
    for r in (await session.scalars(stmt)).all():
        en_name = PROVINCE_EN.get(r.name, r.name)  # some region names match province patterns
        _add("region", r.uuid, "name", en_name)
        _add("region", r.uuid, "sales_region", r.sales_region)

    # ── Provinces ──
    stmt = select(Province)
    for p in (await session.scalars(stmt)).all():
        en_name = PROVINCE_EN.get(p.name, p.name)
        _add("province", p.uuid, "name", en_name)

    # ── Roles ──
    stmt = select(Role)
    for role in (await session.scalars(stmt)).all():
        _add("role", role.uuid, "name", role.name)

    # ── Permissions ──
    stmt = select(Permission)
    for perm in (await session.scalars(stmt)).all():
        en_desc = perm.description or perm.code
        _add("permission", perm.uuid, "description", en_desc)

    # ── Notification templates (task 7e — F-22, ERD §9 registry) ──
    def _add_lang(entity_type: str, entity_id, field: str, value: str, locale: str) -> None:
        rows.append(
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "field": field,
                "locale": locale,
                "value": value,
                "created_by": uid,
                "updated_by": uid,
            }
        )

    for key, fields in NOTIFICATION_TEMPLATES.items():
        for field, by_locale in fields.items():
            for locale, text_value in by_locale.items():
                _add_lang("notification_template", template_uuid(key), field, text_value, locale)

    # ── Bulk Upsert ──
    if not rows:
        return 0

    await session.execute(
        pg_insert(Translation)
        .values(rows)
        .on_conflict_do_update(
            index_elements=["entity_type", "entity_id", "field", "locale"],
            set_={"value": pg_insert(Translation).excluded.value, "updated_by": uid},
        )
    )
    await session.commit()
    return len(rows)
