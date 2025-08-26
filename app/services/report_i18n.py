"""Localisasi konten laporan audit PDF (PRD-F-22, task 6h).

Konten laporan (question_text item, nama section) diambil dari tabel
`translations` (seeded 5f, entity_type `checklist_item`/`checklist_section`,
field `question_text`/`name`, locale `en`). Fallback: nilai kanonikal (id) bila
terjemahan belum ada — F3 (locale-fallback tetap konten nyata dari DB, bukan
mock). Sesuai Constraint G (real-data-only): tidak ada string hardcode di render.

Dipakai oleh GET /audit/sessions/{id}/report.pdf (Accept-Language).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checklist import ChecklistItem, ChecklistSection
from app.models.cross import Translation


async def load_report_translations(
    session: AsyncSession,
    template_id: int,
    locale: str = "en",
) -> dict:
    """Peta terjemahan konten laporan utk satu template sesi.

    Returns:
        {"items": {item_id: translated_question_text},
         "sections": {section_id: translated_name}}
    """
    sections = list(
        (await session.scalars(
            select(ChecklistSection).where(ChecklistSection.template_id == template_id)
        )).all()
    )
    if not sections:
        return {"items": {}, "sections": {}}
    section_ids = [s.id for s in sections]
    items = list(
        (await session.scalars(
            select(ChecklistItem).where(ChecklistItem.section_id.in_(section_ids))
        )).all()
    )
    item_ids = [i.uuid for i in items]
    section_uuids = [s.uuid for s in sections]

    rows = list(
        (await session.scalars(
            select(Translation).where(
                Translation.locale == locale,
                ((Translation.entity_type == "checklist_item")
                 & (Translation.field == "question_text")
                 & (Translation.entity_id.in_(item_ids)))
                | ((Translation.entity_type == "checklist_section")
                   & (Translation.field == "name")
                   & (Translation.entity_id.in_(section_uuids))),
            )
        )).all()
    )

    items_out: dict[str, str] = {}
    sections_out: dict[str, str] = {}
    for tr in rows:
        if tr.entity_type == "checklist_item":
            items_out[str(tr.entity_id)] = tr.value
        elif tr.entity_type == "checklist_section":
            sections_out[str(tr.entity_id)] = tr.value
    return {"items": items_out, "sections": sections_out}