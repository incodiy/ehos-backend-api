"""Aggregation pipeline bottom-up weighted (PRD-F-02, task 6b).

Menggabungkan evaluasi per-item (task 6a) menjadi struktur berjenjang:
item → subkategori (section, mendukung nesting parent_id) → total departemen.

Formula (F-02 weighted):
- ratio item  = skor item / max item  (N/A excluded → None, di-skip)
- ratio node  = Σ(weight_i × ratio_i) / Σ(weight_i)  atas seluruh descendant item
  (weight = checklist_items.weight; fallback count bila total weight 0)
- total_score = round(total_ratio × 100, 2)  → threshold PASS ≥ 80% (task 6c)
- Item yang belum di-score (missing) dilaporkan, tidak menekan rasio.

Murni & deterministik: fungsi `compute` menerima data domain apa pun (duck-typed),
loader `aggregate_session` membaca dari DB.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

from app.services.scoring import (
    InvalidNA,
    InvalidScoreValue,
    ItemEvaluation,
    RubricType,
    evaluate_item,
    evaluate_multi_room,
)


class AggregationError(ValueError):
    """Data template/sesi tidak konsisten untuk diagregasi."""


# ─── output structures ───────────────────────────────────────────────────

class ItemScore:
    __slots__ = ("item_id", "code", "question_text", "rubric_type", "max_score",
                 "weight", "ratio", "achieved", "is_na", "missing", "is_life_safety")

    def __init__(self, item_id, code, question_text, rubric_type, max_score,
                 weight, ratio, achieved=0.0, is_na=False, missing=False,
                 is_life_safety=False):
        self.item_id = item_id
        self.code = code
        self.question_text = question_text
        self.rubric_type = rubric_type
        self.max_score = float(max_score or 0)
        self.weight = float(weight or 0)
        self.ratio = ratio
        self.achieved = float(achieved)
        self.is_na = is_na
        self.missing = missing
        self.is_life_safety = is_life_safety


class SectionScore:
    def __init__(self, section_id, code, name, sort_order=0):
        self.section_id = section_id
        self.code = code
        self.name = name
        self.sort_order = sort_order
        self.ratio: float | None = None
        self.achieved_points = 0.0
        self.max_points = 0.0
        self.items_scored = 0
        self.items_missed = 0
        self.items_na = 0
        self.items: list[ItemScore] = []
        self.children: list[SectionScore] = []


class SessionScore:
    def __init__(self, session_id, department, template_id):
        self.session_id = session_id
        self.department = department
        self.template_id = template_id
        self.total_ratio: float | None = None
        self.total_score: float | None = None
        self.achieved_points = 0.0
        self.max_points = 0.0
        self.items_scored = 0
        self.items_missed = 0
        self.items_na = 0
        self.all_items: list[ItemScore] = []
        self.sections: list[SectionScore] = []

    def to_dict(self) -> dict:
        return {
            "session_id": str(self.session_id),
            "department": self.department,
            "template_id": str(self.template_id),
            "total_ratio": self.total_ratio,
            "total_score": self.total_score,
            "achieved_points": self.achieved_points,
            "max_points": self.max_points,
            "items_scored": self.items_scored,
            "items_missed": self.items_missed,
            "items_na": self.items_na,
            "sections": [_section_to_dict(s) for s in self.sections],
        }


def _section_to_dict(node: SectionScore) -> dict:
    return {
        "code": node.code,
        "name": node.name,
        "ratio": node.ratio,
        "achieved_points": node.achieved_points,
        "max_points": node.max_points,
        "items_scored": node.items_scored,
        "items_missed": node.items_missed,
        "items_na": node.items_na,
        "children": [_section_to_dict(c) for c in node.children],
    }


# ─── per-item evaluation ─────────────────────────────────────────────────

def _evaluate(item, rows) -> ItemScore:
    base = dict(
        item_id=item.id, code=item.code, question_text=item.question_text,
        rubric_type=item.rubric_type, max_score=item.max_score, weight=item.weight,
        is_life_safety=item.is_life_safety,
    )
    if not rows:
        return ItemScore(**base, ratio=None, missing=True)

    try:
        if RubricType(item.rubric_type) is RubricType.MULTI_ROOM:
            ev = evaluate_multi_room(
                max_score=item.max_score,
                na_allowed=item.na_allowed,
                rooms=[{"value": r.value, "is_na": r.is_na} for r in rows],
            )
        else:
            latest = max(rows, key=lambda r: r.updated_at)
            if latest.value is None and getattr(latest, "score", None) is not None:
                # legacy/4d: ingest menulis skor langsung tanpa value → pakai skor tersimpan
                if item.max_score > 0:
                    ev = ItemEvaluation(
                        achieved=round(min(max(float(latest.score), 0.0), float(item.max_score)), 2),
                        max_possible=float(item.max_score),
                    )
                else:
                    raise AggregationError(f"Item {item.code!r} max_score 0 dgn value kosong")
            else:
                ev = evaluate_item(
                    rubric_type=item.rubric_type,
                    value=latest.value,
                    max_score=item.max_score,
                    na_allowed=item.na_allowed,
                    is_na=latest.is_na,
                )
    except (InvalidScoreValue, InvalidNA) as exc:
        raise AggregationError(
            f"Item {item.code!r}: {exc}"
        ) from None

    return ItemScore(**base, ratio=ev.ratio, achieved=ev.achieved, is_na=ev.is_na)


# ─── weighting ───────────────────────────────────────────────────────────

def _weighted_ratio(ratios_and_weights: list[tuple[float, float]]) -> float | None:
    """Rata-rata tertimbang rasio; fallback rata-rata sederhana bila Σw = 0."""
    total_w = sum(w for _, w in ratios_and_weights)
    if total_w <= 0:
        if not ratios_and_weights:
            return None
        return round(sum(r for r, _ in ratios_and_weights) / len(ratios_and_weights), 6)
    return round(sum(r * w for r, w in ratios_and_weights) / total_w, 6)


# ─── tree + aggregation (pure) ───────────────────────────────────────────

def _descendant_items(node, items_by_section) -> list[ItemScore]:
    """Item di node ini + semua descendant (section bersarang)."""
    out = list(items_by_section.get(node.section_id, []))
    for child in node.children:
        out.extend(_descendant_items(child, items_by_section))
    return out


def _finalize_node(node: SectionScore, items_by_section) -> SectionScore:
    for child in node.children:
        _finalize_node(child, items_by_section)
    items = _descendant_items(node, items_by_section)
    scored = [i for i in items if not i.missing and not i.is_na]
    node.items = items_by_section.get(node.section_id, [])
    node.ratio = _weighted_ratio([(i.ratio, i.weight) for i in scored])
    node.achieved_points = round(sum(i.achieved for i in scored), 2)
    node.max_points = round(sum(i.max_score for i in scored if i.ratio is not None), 2)
    node.items_scored = len(scored)
    node.items_missed = sum(1 for i in items if i.missing)
    node.items_na = sum(1 for i in items if i.is_na)
    return node


def compute(items_by_section: dict, sections: list, scores: dict) -> SessionScore:
    """Agregasi bottom-up dari data in-memory. Return SessionScore.

    items_by_section: {section_id: [ChecklistItem-like,...]}
    sections:         [section-like (id, parent_id, code, name, sort_order)]
    scores:           {item_id: [score-like (value, is_na, updated_at)]}
    """
    section_ids = {s.id for s in sections}
    item_lists = list(items_by_section.values())
    all_items = [it for lst in item_lists for it in lst]

    evaluated = {
        it.id: _evaluate(it, scores.get(it.id, []))
        for it in all_items
    }

    # evaluasi item yang punya score tapi tidak ada di template (invalid data)
    for item_id, _rows in scores.items():
        if item_id not in evaluated:
            raise AggregationError(f"Score mengacu item {item_id} yang tidak ada di template")

    # bangun pohon section
    node_by_id: dict = {}
    roots: list[SectionScore] = []
    for s in sorted(sections, key=lambda s: (s.sort_order, s.code)):
        node = SectionScore(s.id, s.code, s.name, sort_order=s.sort_order)
        node_by_id[s.id] = node
        if s.parent_id and s.parent_id in section_ids:
            continue  # akan di-attach anak
        roots.append(node)
    for s in sections:
        if s.parent_id and s.parent_id in section_ids:
            node_by_id[s.parent_id].children.append(node_by_id[s.id])
    for root in roots:
        _sort_children(root)

    # item → section node miliknya
    items_slot: dict = {sid: [] for sid in section_ids}
    for it in all_items:
        items_slot.setdefault(it.section_id, []).append(evaluated[it.id])

    # finalisasi bottom-up + total
    total_scored: list[ItemScore] = []
    for root in roots:
        _finalize_node(root, items_slot)
    for it in evaluated.values():
        if not it.missing and not it.is_na:
            total_scored.append(it)

    session = SessionScore(session_id=None, department=None, template_id=None)
    session.sections = roots
    session.total_ratio = _weighted_ratio([(i.ratio, i.weight) for i in total_scored])
    session.total_score = round(session.total_ratio * 100, 2) if session.total_ratio is not None else None
    session.achieved_points = round(sum(i.achieved for i in total_scored), 2)
    session.max_points = round(sum(i.max_score for i in total_scored if i.ratio is not None), 2)
    session.items_scored = len(total_scored)
    session.items_missed = sum(1 for i in evaluated.values() if i.missing)
    session.items_na = sum(1 for i in evaluated.values() if i.is_na)
    session.all_items = list(evaluated.values())
    return session


def _sort_children(node: SectionScore) -> None:
    for child in node.children:
        _sort_children(child)
    node.children.sort(key=lambda c: (c.sort_order, c.code))


# ─── DB loader ───────────────────────────────────────────────────────────

async def aggregate_session(session: AsyncSession, session_id: int) -> SessionScore:
    """Load dari DB lalu agregasi: session → template items/sections → scores."""
    from sqlalchemy import select

    from app.models.audit import AuditItemScore, AuditSession
    from app.models.checklist import ChecklistItem, ChecklistSection

    sess_row = await session.get(AuditSession, session_id)
    if sess_row is None:
        raise AggregationError(f"Audit session {session_id} tidak ditemukan")

    sections = list(
        (await session.scalars(
            select(ChecklistSection).where(ChecklistSection.template_id == sess_row.template_id)
        )).all()
    )
    items = list(
        (await session.scalars(
            select(ChecklistItem)
            .where(ChecklistItem.section_id.in_([s.id for s in sections]))
        )).all()
    )
    score_rows = (await session.scalars(
        select(AuditItemScore).where(AuditItemScore.session_id == session_id)
    )).all()

    items_by_section: dict = {}
    for it in items:
        items_by_section.setdefault(it.section_id, []).append(it)
    scores: dict = {}
    for row in score_rows:
        scores.setdefault(row.item_id, []).append(row)

    result = compute(items_by_section, sections, scores)
    result.session_id = session_id
    result.department = sess_row.department
    result.template_id = sess_row.template_id
    return result