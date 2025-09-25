"""Dynamic Scoring Engine tests (PRD-F-01) — task 6a.

Unit tests cover rubric evaluation (TRAFFIC_LIGHT/BINARY_COUNT/NUMERIC_SCALE/
MULTI_ROOM), N/A exclusion guard, and brand_tier key resolution. One
integration test resolves a LOCKED template against the seeded dev DB.
"""

import pytest

from app.services.scoring import (
    InvalidNA,
    InvalidScoreValue,
    evaluate_item,
    evaluate_multi_room,
    find_locked_template,
    resolve_tier_key,
)

# ── TRAFFIC_LIGHT ─────────────────────────────────────────────────────────

def test_traffic_light_yes_review_no() -> None:
    assert evaluate_item(rubric_type="TRAFFIC_LIGHT", value="YES", max_score=90).achieved == 90
    assert evaluate_item(rubric_type="TRAFFIC_LIGHT", value="REVIEW", max_score=90).achieved == 45
    assert evaluate_item(rubric_type="TRAFFIC_LIGHT", value="NO", max_score=90).achieved == 0
    # max_possible tidak berubah oleh nilai
    assert evaluate_item(rubric_type="TRAFFIC_LIGHT", value="YES", max_score=90).max_possible == 90


def test_traffic_light_accepts_pass_fail_aliases() -> None:
    assert evaluate_item(rubric_type="TRAFFIC_LIGHT", value="PASS", max_score=90).achieved == 90
    assert evaluate_item(rubric_type="TRAFFIC_LIGHT", value="FAIL", max_score=90).achieved == 0


def test_traffic_light_invalid_value_raises() -> None:
    with pytest.raises(InvalidScoreValue):
        evaluate_item(rubric_type="TRAFFIC_LIGHT", value="MAYBE", max_score=90)


# ── BINARY_COUNT ─────────────────────────────────────────────────────────

def test_binary_count_yes_no() -> None:
    assert evaluate_item(rubric_type="BINARY_COUNT", value="YES", max_score=1.0).achieved == 1.0
    assert evaluate_item(rubric_type="BINARY_COUNT", value="NO", max_score=1.0).achieved == 0


def test_binary_count_numeric_aliases() -> None:
    assert evaluate_item(rubric_type="BINARY_COUNT", value="1", max_score=1.0).achieved == 1.0
    assert evaluate_item(rubric_type="BINARY_COUNT", value="0", max_score=1.0).achieved == 0


def test_binary_count_invalid_raises() -> None:
    with pytest.raises(InvalidScoreValue):
        evaluate_item(rubric_type="BINARY_COUNT", value="SOMETIMES", max_score=1.0)


# ── NUMERIC_SCALE ────────────────────────────────────────────────────────

def test_numeric_scale_within_cap() -> None:
    assert evaluate_item(rubric_type="NUMERIC_SCALE", value="85", max_score=100).achieved == 85
    assert evaluate_item(rubric_type="NUMERIC_SCALE", value="85", max_score=100).max_possible == 100


def test_numeric_scale_clamped_at_max() -> None:
    assert evaluate_item(rubric_type="NUMERIC_SCALE", value="120", max_score=100).achieved == 100


def test_numeric_scale_invalid_values_raise() -> None:
    with pytest.raises(InvalidScoreValue):
        evaluate_item(rubric_type="NUMERIC_SCALE", value="N/A", max_score=100)
    with pytest.raises(InvalidScoreValue):
        evaluate_item(rubric_type="NUMERIC_SCALE", value="-5", max_score=100)


# ── N/A EXCLUSION (F-01: cegah N/A palsu) ────────────────────────────────

def test_na_allowed_excludes_item() -> None:
    res = evaluate_item(rubric_type="TRAFFIC_LIGHT", value="NO", max_score=90, na_allowed=True, is_na=True)
    assert res.is_na is True
    assert res.max_possible == 0
    assert res.ratio is None  # signal skip pada aggregation (6b)


def test_na_without_allowance_raises() -> None:
    with pytest.raises(InvalidNA):
        evaluate_item(rubric_type="TRAFFIC_LIGHT", value="YES", max_score=90, na_allowed=False, is_na=True)


# ── MULTI_ROOM ───────────────────────────────────────────────────────────

def test_multi_room_all_yes() -> None:
    res = evaluate_multi_room(
        max_score=270,
        rooms=[{"value": "YES"}, {"value": "YES"}, {"value": "YES"}],
    )
    assert res.achieved == 270
    assert res.max_possible == 270
    assert res.ratio == 1.0


def test_multi_room_mixed() -> None:
    res = evaluate_multi_room(
        max_score=270,
        rooms=[{"value": "YES"}, {"value": "NO"}, {"value": "NO"}],
    )
    assert res.achieved == 90
    assert res.max_possible == 270
    assert res.ratio == round(90 / 270, 6)


def test_multi_room_na_room_shrinks_denominator() -> None:
    # 3 kamar, 1 N/A (na_allowed): max turun ke 90 × 2 = 180
    res = evaluate_multi_room(
        max_score=270,
        na_allowed=True,
        rooms=[{"value": "YES"}, {"value": "NO"}, {"value": "NO", "is_na": True}],
    )
    assert res.max_possible == 180
    assert res.achieved == 90
    assert res.ratio == 0.5


def test_multi_room_na_without_allowance_raises() -> None:
    with pytest.raises(InvalidNA):
        evaluate_multi_room(
            max_score=270,
            na_allowed=False,
            rooms=[{"value": "YES"}, {"value": "NO", "is_na": True}, {"value": "NO"}],
        )


def test_multi_room_all_na_excluded() -> None:
    res = evaluate_multi_room(
        max_score=270,
        na_allowed=True,
        rooms=[{"value": "NO", "is_na": True}, {"value": "NO", "is_na": True}],
    )
    assert res.is_na is True
    assert res.ratio is None


def test_multi_room_empty_raises() -> None:
    with pytest.raises(InvalidScoreValue):
        evaluate_multi_room(max_score=270, rooms=[])


def test_multi_room_invalid_value_raises() -> None:
    with pytest.raises(InvalidScoreValue):
        evaluate_multi_room(max_score=270, rooms=[{"value": "MAYBE"}])


# ── brand_tier-aware resolution ─────────────────────────────────────────

def test_resolve_tier_key_exact_then_universal() -> None:
    assert list(resolve_tier_key("GM", "Luxury")) == [("GM", "Luxury"), ("GM", None)]
    assert list(resolve_tier_key("GM", None)) == [("GM", None)]


async def test_find_locked_template_brand_tier_aware() -> None:
    from app.db.session import SessionLocal

    async with SessionLocal() as session:
        eco = await find_locked_template(session, "HOUSEKEEPING", "Eco-Resort")
        assert eco is not None
        assert eco.status == "LOCKED"
        assert eco.brand_tier == "Eco-Resort"

        lux = await find_locked_template(session, "HOUSEKEEPING", "Luxury")
        assert lux is not None and lux.brand_tier == "Luxury"

        # Midscale tidak punya template persis GM → fallback universal (tidak ada) → None
        nodata = await find_locked_template(session, "GM", "Midscale")
        assert nodata is None

        # SECURITY_RISK universal dipakai utk tier apa pun
        sr = await find_locked_template(session, "SECURITY_RISK", "Budget")
        assert sr is not None and sr.brand_tier is None