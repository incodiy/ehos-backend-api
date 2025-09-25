"""Parser tests for the legacy dm_audit_ops ingestion (Task 4f, ADR-008).

Locks file dimension, year/hotel payload, totals snapshot and period-date
normalization against the real legacy workbook.
"""

from pathlib import Path

import pytest

from app.seed.legacy_ingest import _totals, parse_workbook

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent.parent / "audit" / "dm_audit_ops.xlsx"

pytestmark = pytest.mark.skipif(not SOURCE.exists(), reason="Legacy source file not present")


def test_parse_dimensions() -> None:
    rows, sessions = parse_workbook(str(SOURCE))
    assert len(rows) == 11_032
    assert sessions == 210


@pytest.mark.parametrize("year,count", [(2024, 4256), (2025, 4200), (2026, 2576)])
def test_parse_year_distribution(year: int, count: int) -> None:
    rows, _ = parse_workbook(str(SOURCE))
    got = sum(1 for r in rows if r["years"] == year)
    assert got == count


def test_parse_sanitizes_types() -> None:
    rows, _ = parse_workbook(str(SOURCE))
    sample = rows[0]
    assert isinstance(sample["period_date"], str) and sample["period_date"].startswith("2024")
    assert isinstance(sample["hotel_code"], str)
    assert sample["years"] in (2024, 2025, 2026)
    assert "tot_sc" in sample and "status_housekeeping" in sample


def test_totals_snapshot_picks_summary_fields() -> None:
    rows, _ = parse_workbook(str(SOURCE))
    totals = _totals(rows[0])
    assert totals["total_score"] == rows[0]["total_score"]
    assert "tot_sc" in totals and "status_total_score" in totals
    assert "hotel_name" not in totals
    assert "process_date" in totals