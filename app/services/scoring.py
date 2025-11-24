"""Dynamic Scoring Engine — rubric evaluator (PRD-F-01, task 6a).

Pure & deterministic: no DB dependency for per-item evaluation. Template
selection is brand_tier-aware and resolves against LOCKED templates only
(snapshot semantics B3).

Rubric models:
- TRAFFIC_LIGHT  value YES → 1.0·max, REVIEW → 0.5·max, NO → 0.0     (90/45/0)
- BINARY_COUNT   value YES|1 → max, NO|0 → 0                          (present/absent)
- NUMERIC_SCALE  value measured (number) → min(measured, max)          (vs reference, cap)
- MULTI_ROOM     per-room rows (room_ref), ratio mean × max; N/A room excluded from
                 BOTH numerator and denominator (max shrinks).          (90 × room count)

N/A exclusion (F-01 "cegah N/A palsu"):
- is_na=True hanya valid bila item.na_allowed=True; selain itu raise InvalidNA.
- item yang di-exclude menghasilkan achieved=max=0 → skipped di aggregation (6b).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RubricType(StrEnum):
    TRAFFIC_LIGHT = "TRAFFIC_LIGHT"
    BINARY_COUNT = "BINARY_COUNT"
    NUMERIC_SCALE = "NUMERIC_SCALE"
    MULTI_ROOM = "MULTI_ROOM"


class InvalidScoreValue(ValueError):
    """Rubrik mendapat value yang tidak selaras dengan model skor."""


class InvalidNA(ValueError):
    """is_na digunakan pada item yang na_allowed=False (N/A palsu, F-01)."""


@dataclass(frozen=True)
class ItemEvaluation:
    achieved: float
    max_possible: float
    is_na: bool = False

    @property
    def ratio(self) -> float | None:
        """Normalized 0..1 (None bila item di-exclude N/A — skip di aggregation)."""
        if self.max_possible <= 0:
            return None
        return round(self.achieved / self.max_possible, 6)


def _traffic_ratio(value: str) -> float:
    v = (value or "").strip().upper()
    if v in ("YES", "PASS"):
        return 1.0
    if v in ("REVIEW", "REVISIT", "PARTIAL", "NEED REVIEW", "NEEDREVIEW"):
        return 0.5
    if v in ("NO", "FAIL"):
        return 0.0
    raise InvalidScoreValue(f"TRAFFIC_LIGHT value tidak dikenal: {value!r}")


def _binary_ratio(value: str) -> float:
    v = (value or "").strip().upper()
    if v in ("YES", "PASS", "1", "TRUE"):
        return 1.0
    if v in ("NO", "FAIL", "0", "FALSE"):
        return 0.0
    raise InvalidScoreValue(f"BINARY_COUNT value tidak dikenal: {value!r}")


def _numeric_value(value: str, max_score: float) -> float:
    try:
        measured = float(value)
    except (TypeError, ValueError):
        raise InvalidScoreValue(f"NUMERIC_SCALE value harus angka, dapat {value!r}") from None
    if measured < 0:
        raise InvalidScoreValue(f"NUMERIC_SCALE tidak boleh negatif: {measured}")
    return min(round(measured, 2), max_score)


def _check_na(allowed: bool, is_na: bool) -> None:
    if is_na and not allowed:
        raise InvalidNA("N/A dilarang untuk item ini (na_allowed=False)")


def evaluate_item(
    *,
    rubric_type: str,
    value: str | None,
    max_score: float,
    na_allowed: bool = False,
    is_na: bool = False,
) -> ItemEvaluation:
    """Evaluasi satu baris nilai terhadap rubrik.

    max_score diterima dari checklist_items.max_score (Numeric). Ratio
    dihitung lalu dikalikan max_score sehingga skala rubric apa pun
    (90, 100, 1.0, 90×N) menghasilkan skor yang konsisten.
    """
    max_score = float(max_score) if max_score is not None else 0.0
    if is_na:
        _check_na(na_allowed, True)
        return ItemEvaluation(achieved=0.0, max_possible=0.0, is_na=True)

    rubric = RubricType(rubric_type)
    if rubric is RubricType.TRAFFIC_LIGHT:
        ratio = _traffic_ratio(value or "")
    elif rubric is RubricType.BINARY_COUNT:
        ratio = _binary_ratio(value or "")
    elif rubric is RubricType.NUMERIC_SCALE:
        achieved = _numeric_value(value or "", max_score)
        return ItemEvaluation(achieved=achieved, max_possible=max_score)
    else:  # MULTI_ROOM handled by evaluate_multi_room
        raise InvalidScoreValue("MULTI_ROOM item butuh evaluasi per ruangan")

    return ItemEvaluation(achieved=round(ratio * max_score, 2), max_possible=max_score)


def evaluate_multi_room(
    *,
    max_score: float,
    na_allowed: bool = False,
    rooms: list[dict],
) -> ItemEvaluation:
    """Evaluasi MULTI_ROOM dari list baris per ruangan (room_ref).

    rooms: [{"value": str, "is_na": bool}]. Per-row dievaluasi dengan skala
    TRAFFIC_LIGHT (per ruangan: 90/45/0). Room yang di-exclude (N/A valid)
    dikurangi dari pembilang DAN penyebut: max = per_room_max × count(non-NA).
    """
    if not rooms:
        raise InvalidScoreValue("MULTI_ROOM tanpa baris ruangan")
    max_score = float(max_score)
    total_rooms = len(rooms)
    per_room_max = max_score / total_rooms

    achieved = 0.0
    active = 0
    for row in rooms:
        if row.get("is_na"):
            _check_na(na_allowed, True)
            continue
        ratio = _traffic_ratio(row.get("value") or "")
        achieved += ratio * per_room_max
        active += 1

    if active == 0:
        return ItemEvaluation(achieved=0.0, max_possible=0.0, is_na=True)
    return ItemEvaluation(
        achieved=round(achieved, 2),
        max_possible=round(per_room_max * active, 2),
    )


def resolve_tier_key(department: str, brand_tier: str | None):
    """Iterasi kunci lookup template: brand_tier persis -> variasi kapitalisasi -> fallback universal.

    Contract (F-01): hotel bertier memakai template dengan brand_tier yang
    sama; bila tidak ada, turun ke template universal (brand_tier=None).
    Yields tuple (department, tier) dari spesifik ke generik.
    """
    if brand_tier:
        yield (department, brand_tier)
        if brand_tier.capitalize() != brand_tier:
            yield (department, brand_tier.capitalize())
        if brand_tier.upper() != brand_tier:
            yield (department, brand_tier.upper())
        if brand_tier.lower() != brand_tier:
            yield (department, brand_tier.lower())
    yield (department, None)


async def find_locked_template(session, department: str, brand_tier: str | None):
    """Ambil template LOCKED terbaru untuk (department, brand_tier).

    Prioritas: brand_tier persis → universal (None). Hanya template berstatus
    LOCKED (immutable snapshot B3). Mengembalikan row atau None.
    """
    from sqlalchemy import select

    from app.models.checklist import ChecklistTemplate

    for key_department, key_tier in resolve_tier_key(department, brand_tier):
        stmt = (
            select(ChecklistTemplate)
            .where(
                ChecklistTemplate.department == key_department,
                ChecklistTemplate.status == "LOCKED",
            )
            .order_by(ChecklistTemplate.locked_at.desc())
        )
        if key_tier is not None:
            stmt = stmt.where(ChecklistTemplate.brand_tier == key_tier)
        else:
            stmt = stmt.where(ChecklistTemplate.brand_tier.is_(None))
        tmpl = await session.scalar(stmt)
        if tmpl is not None:
            return tmpl
    return None