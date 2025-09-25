"""Aggregation pipeline bottom-up weighted tests (PRD-F-02) — task 6b."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from app.services.aggregation import AggregationError, compute
from app.services.scoring import RubricType


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


def _sec(code="SEC", parent=None, order=0) -> Section:
    return Section(id=uuid.uuid4(), parent_id=parent, code=code, name=code, sort_order=order)


def _it(section, code, rubric=RubricType.TRAFFIC_LIGHT, max_score=90, weight=1.0,
        na_allowed=False, is_life_safety=False) -> Item:
    return Item(
        id=uuid.uuid4(), section_id=section.id, code=code, question_text=code,
        rubric_type=rubric, max_score=max_score, weight=weight,
        na_allowed=na_allowed, is_life_safety=is_life_safety,
    )


def _score(value=None, is_na=False, score=None) -> ScoreRow:
    return ScoreRow(value=value, is_na=is_na, score=score)


def _run(items, sections, scores):
    items_by_section: dict = {}
    for it in items:
        items_by_section.setdefault(it.section_id, []).append(it)
    return compute(items_by_section, sections, scores)


# ── weighted bottom-up ───────────────────────────────────────────────────

def test_weighted_mean_across_items() -> None:
    sec = _sec("GM-01")
    i1 = _it(sec, "A", weight=1.0)
    i2 = _it(sec, "B", weight=2.0)
    res = _run([i1, i2], [sec], {i1.id: [_score("YES")], i2.id: [_score("NO")]})
    assert res.sections[0].ratio == round(1 / 3, 6)
    assert res.total_ratio == round(1 / 3, 6)
    assert res.total_score == round(1 / 3 * 100, 2)
    assert res.items_scored == 2


def test_section_breakdown_and_total() -> None:
    sa, sb = _sec("A"), _sec("B")
    ia = _it(sa, "A1")
    ib = _it(sb, "B1")
    res = _run([ia, ib], [sa, sb], {ia.id: [_score("YES")], ib.id: [_score("NO")]})
    assert res.sections[0].ratio == 1.0
    assert res.sections[1].ratio == 0.0
    assert res.total_score == 50.0


def test_na_item_excluded_from_denominator() -> None:
    sec = _sec("S")
    a = _it(sec, "A", na_allowed=True)
    b = _it(sec, "B")
    c = _it(sec, "C")
    res = _run([a, b, c], [sec], {a.id: [_score(is_na=True)], b.id: [_score("YES")], c.id: [_score("NO")]})
    assert res.total_ratio == 0.5
    assert res.items_scored == 2
    assert res.items_na == 1
    assert res.sections[0].ratio == 0.5


def test_missing_item_reported_not_depressing() -> None:
    sec = _sec("S")
    a = _it(sec, "A")
    b = _it(sec, "B")
    res = _run([a, b], [sec], {a.id: [_score("YES")]})
    assert res.total_ratio == 1.0
    assert res.total_score == 100.0
    assert res.items_missed == 1
    assert res.sections[0].items_missed == 1


def test_multirooms_na_room_via_pipeline() -> None:
    sec = _sec("HK-ROOM")
    it = _it(sec, "ROOM", rubric=RubricType.MULTI_ROOM, max_score=270, na_allowed=True)
    res = _run([it], [sec], {
        it.id: [_score("YES"), _score("NO"), _score(is_na=True)],
    })
    assert res.sections[0].ratio == 0.5
    assert res.total_score == 50.0


def test_legacy_score_fallback_without_value() -> None:
    sec = _sec("S")
    it = _it(sec, "LEG")
    res = _run([it], [sec], {it.id: [_score(score=75)]})
    assert res.total_ratio == round(75 / 90, 6)
    assert res.total_score == round(75 / 90 * 100, 2)


def test_weight_zero_fallback_to_simple_mean() -> None:
    sec = _sec("S")
    a = _it(sec, "A", weight=0.0)
    b = _it(sec, "B", weight=0.0)
    res = _run([a, b], [sec], {a.id: [_score("YES")], b.id: [_score("NO")]})
    assert res.total_ratio == 0.5


def test_nested_section_aggregates_descendants() -> None:
    parent = _sec("ROOT")
    child = _sec("CHILD", parent=parent.id)
    a = _it(child, "A")
    b = _it(child, "B")
    res = _run([a, b], [parent, child], {a.id: [_score("YES")], b.id: [_score("YES")]})
    root = res.sections[0]
    assert len(root.children) == 1
    assert root.children[0].ratio == 1.0
    assert root.ratio == 1.0
    assert root.items_scored == 2  # item descendant ikut dihitung


def test_invalid_value_raises_aggregation_error() -> None:
    sec = _sec("S")
    it = _it(sec, "A")
    with pytest.raises(AggregationError):
        _run([it], [sec], {it.id: [_score("MAYBE")]})


def test_orphan_score_raises() -> None:
    sec = _sec("S")
    it = _it(sec, "A")
    orphan = uuid.uuid4()
    with pytest.raises(AggregationError):
        _run([it], [sec], {orphan: [_score("YES")]})


# ── integration: dev DB (data 4d legacy real) ───────────────────────────

async def test_aggregate_session_real_legacy_data() -> None:
    from sqlalchemy import text

    from app.db.session import SessionLocal
    from app.services.aggregation import aggregate_session

    async with SessionLocal() as session:
        sess_id = await session.scalar(
            text(
                "SELECT s.id FROM audit_sessions s "
                "WHERE EXISTS (SELECT 1 FROM audit_item_scores sc WHERE sc.session_id = s.id) "
                "ORDER BY s.date_start DESC LIMIT 1"
            )
        )
        assert sess_id is not None

        res = await aggregate_session(session, sess_id)
        assert 0.0 <= res.total_score <= 100.0
        assert res.total_ratio is not None
        assert res.items_scored > 0
        # invarian: total items = scored + missing + na
        total_items = res.items_scored + res.items_missed + res.items_na
        assert total_items > 0
        # semua item dievaluasi
        assert len(res.all_items) == total_items
        # breakdown section tidak kosong dan konvergen
        assert len(res.sections) > 0
        sec_total_score = sum(s.items_scored for s in res.sections)
        assert sec_total_score > 0
        # to_dict serializable
        d = res.to_dict()
        assert d["total_score"] == res.total_score
        assert isinstance(d["sections"], list)