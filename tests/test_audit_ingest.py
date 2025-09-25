"""Parser mapping tests for the audit Excel ingest engine (Task 4d).

Locks the per-format mapper (TRAFFIC_LIGHT vs BINARY_COUNT), idempotent
file resolution and date-range extraction against the real 2026 workbooks.
"""

import os
from pathlib import Path

import pytest

from app.seed.audit_ingest import (
    FAMILY_GM,
    FAMILY_HK,
    FAMILY_KFB,
    FAMILY_SRM,
    parse_workbook,
    resolve_files,
)

HERE = Path(__file__).resolve().parent
AUDIT_DIR = HERE.parent.parent / "audit"

CODES = {"CWS", "SQYO", "ZHBA", "MANP", "SBAI"}

pytestmark = pytest.mark.skipif(not AUDIT_DIR.exists(), reason="audit/ source files not present")


@pytest.mark.parametrize(
    "filename,department,items,rating",
    [
        ("General Manager Check List Audit - 2026-CWS.xlsx", FAMILY_GM, 36, "TRAFFIC_LIGHT"),
        ("Housekeeping Audit - 2026-CWS.xlsx", FAMILY_HK, 68, "TRAFFIC_LIGHT"),
        ("Kitchen FB Audit Check List - CWS - 2026.xlsx", FAMILY_KFB, 86, "BINARY_COUNT"),
        ("Security Risk Management Audit - 2026-CWS.xlsx", FAMILY_SRM, 164, "TRAFFIC_LIGHT"),
    ],
)
def test_parse_workbook_per_format(filename: str, department: str, items: int, rating: str) -> None:
    parsed = parse_workbook(str(AUDIT_DIR / filename), CODES)
    assert parsed.department == department
    assert parsed.hotel_code == "CWS"
    assert parsed.audit_type == "FULL"
    assert parsed.date_start.year == 2026
    assert len(parsed.items) == items
    assert all(item.section_code for item in parsed.items)

    allowed = {0.0, 45.0, 90.0} if rating == "TRAFFIC_LIGHT" else {0.0, 1.0}
    assert {item.score for item in parsed.items} <= allowed
    assert parsed.items


def test_duplicate_kfb_copy_is_dropped() -> None:
    paths = resolve_files(str(AUDIT_DIR), CODES)
    names = {os.path.basename(p) for p in paths}
    assert not any("(1)" in name for name in names)
    assert len([n for n in names if "KITCHEN" in n.upper() and "FOLLOW" not in n.upper()]) == 5


def test_follow_up_workbooks_excluded() -> None:
    names = {os.path.basename(p) for p in resolve_files(str(AUDIT_DIR), CODES)}
    assert not any("FOLLOW UP" in n.upper() for n in names)


@pytest.mark.parametrize(
    "filename,expected_span",
    [
        ("General Manager Check List Audit - 2026-ZHBA.xlsx", (11, 13)),
        ("Housekeeping Audit-2026-SQYO.xlsx", (3, 5)),
    ],
)
def test_date_range_extraction(filename: str, expected_span: tuple[int, int]) -> None:
    parsed = parse_workbook(str(AUDIT_DIR / filename), CODES)
    assert (parsed.date_start.day, parsed.date_end.day) == expected_span
    assert parsed.date_start.month == parsed.date_end.month