"""PASS/FAIL verdict + Life-Safety Hazard Flag tests (PRD-F-02/F-03) — task 6c."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.services.scoring import RubricType
from app.services.verdict import Hazard, compute_verdict


@dataclass
class Section:
    id: uuid.UUID
    parent_id: uuid.UUID | None
    code: str
    name: str
    sort_order: int = 0


@dataclass
class Item:
    id: uuid.UUID
    section_id: uuid.UUID
    code: str
    question_text: str
    rubric_type: str
    max_score: float
    weight: float = 1.0
    na_allowed: bool = False
    is_life_safety: bool = False
    sort_order: int = 0


@dataclass
class ScoreRow:
    value: str | None
    is_na: bool = False
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    score: float | None = None


def _it(sec, code, rubric=RubricType.TRAFFIC_LIGHT, max_score=90, weight=1.0,
        na_allowed=False, is_life_safety=False) -> Item:
    return Item(
        id=uuid.uuid4(), section_id=sec.id, code=code, question_text=code,
        rubric_type=rubric, max_score=max_score, weight=weight,
        na_allowed=na_allowed, is_life_safety=is_life_safety,
    )


def _score(value=None, is_na=False, score=None) -> ScoreRow:
    return ScoreRow(value=value, is_na=is_na, score=score)


def _run(items, sections, scores):
    from app.services.aggregation import compute

    items_by_section: dict = {}
    for it in items:
        items_by_section.setdefault(it.section_id, []).append(it)
    return compute(items_by_section, sections, scores)


def test_pass_above_threshold() -> None:
    sec = Section(id=__import__("uuid").uuid4(), parent_id=None, code="S", name="S")
    a = _it(sec, "A")
    b = _it(sec, "B")
    res = _run([a, b], [sec], {a.id: [_score("YES")], b.id: [_score("YES")]})
    status = compute_verdict(res)
    assert status.pass_fail == "PASS"
    assert status.total_score == 100.0
    assert status.hazard is False
    assert status.blocking_reasons == []


def test_fail_below_threshold() -> None:
    sec = Section(id=__import__("uuid").uuid4(), parent_id=None, code="S", name="S")
    a = _it(sec, "A")
    b = _it(sec, "B")
    res = _run([a, b], [sec], {a.id: [_score("YES")], b.id: [_score("NO")]})
    status = compute_verdict(res)
    assert status.pass_fail == "FAIL"
    assert "below_threshold" in status.blocking_reasons


def test_exactly_at_threshold_passes() -> None:
    sec = Section(id=__import__("uuid").uuid4(), parent_id=None, code="S", name="S")
    it = _it(sec, "8", rubric=RubricType.NUMERIC_SCALE, max_score=100)
    res = _run([it], [sec], {it.id: [_score("80")]})
    status = compute_verdict(res, threshold=0.80)
    assert status.pass_fail == "PASS"
    assert status.total_score == 80.0


def test_life_safety_hazard_blocks_pass() -> None:
    sec = Section(id=__import__("uuid").uuid4(), parent_id=None, code="S", name="S")
    normal = _it(sec, "OK", weight=1.0)
    hazard_item = _it(sec, "FIRE", is_life_safety=True)
    # semua YES (harusnya 100%) tapi life-safety FIRE gagal → PASS diblokir
    res = _run([normal, hazard_item], [sec], {
        normal.id: [_score("YES")],
        hazard_item.id: [_score("NO")],
    })
    status = compute_verdict(res)
    assert status.pass_fail == "FAIL"
    assert status.hazard is True
    assert "life_safety_hazard" in status.blocking_reasons
    assert [h.code for h in status.hazards] == ["FIRE"]
    assert isinstance(status.hazards[0], Hazard)


def test_life_safety_partial_is_not_hazard() -> None:
    sec = Section(id=__import__("uuid").uuid4(), parent_id=None, code="S", name="S")
    it = _it(sec, "EVAC", is_life_safety=True)
    res = _run([it], [sec], {it.id: [_score("REVIEW")]})
    status = compute_verdict(res)
    assert status.hazard is False
    assert status.pass_fail == "FAIL"  # 50% < 80%
    assert status.blocking_reasons == ["below_threshold"]


def test_unscored_items_block_pass() -> None:
    sec = Section(id=__import__("uuid").uuid4(), parent_id=None, code="S", name="S")
    a = _it(sec, "A")
    b = _it(sec, "B")
    res = _run([a, b], [sec], {a.id: [_score("YES")]})
    status = compute_verdict(res)
    assert status.pass_fail == "FAIL"
    assert "has_unscored_items" in status.blocking_reasons


def test_na_excluded_from_hazard() -> None:
    sec = Section(id=__import__("uuid").uuid4(), parent_id=None, code="S", name="S")
    it = _it(sec, "P3K", is_life_safety=True, na_allowed=True)
    res = _run([it], [sec], {it.id: [_score(is_na=True)]})
    status = compute_verdict(res)
    assert status.pass_fail is None  # tidak ada item ter-score
    assert status.hazard is False


def test_no_scored_items_returns_none() -> None:
    sec = Section(id=__import__("uuid").uuid4(), parent_id=None, code="S", name="S")
    res = _run([_it(sec, "A")], [sec], {})
    status = compute_verdict(res)
    assert status.pass_fail is None
    assert status.blocking_reasons == ["no_scored_items"]


def test_custom_threshold() -> None:
    sec = Section(id=__import__("uuid").uuid4(), parent_id=None, code="S", name="S")
    a = _it(sec, "A")
    b = _it(sec, "B")
    res = _run([a, b], [sec], {a.id: [_score("YES")], b.id: [_score("NO")]})
    status = compute_verdict(res, threshold=0.4)
    assert status.pass_fail == "PASS"  # 50% >= threshold 40%