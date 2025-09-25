"""Parser mapping tests for the CRM cross-sell lead ingestion (Task 4e).

Locks row discovery (DAY blocks + `No` numeric-string), hotel resolution and
status heuristics against the real SBII GOV worksheet.
"""

from pathlib import Path

import pytest

from app.seed.crm_ingest import (
    _hotel_code,
    _status,
    parse_workbook,
)

HERE = Path(__file__).resolve().parent
CRM_DIR = HERE.parent.parent / "crm"
SOURCE = CRM_DIR / "_SBII - Cross Selling Tele (Work sheet GOV) 18-21 Aug 2026.xlsx"

pytestmark = pytest.mark.skipif(not SOURCE.exists(), reason="CRM source file not present")

KNOWN = {
    "CWS",
    "SQYO",
    "ZHBA",
    "MANP",
    "SBAI",
    "SBPI",
    "SBBO",
    "ZHAM",
    "SIMC",
    "SBB",
}


def test_crm_row_discovery() -> None:
    rows, per_sheet = parse_workbook(str(SOURCE))
    assert len(rows) >= 1_000
    assert len(per_sheet) >= 10

    first = rows[0][1]
    assert first["company_name"]
    assert first["sales_incharge"]


def test_hotel_resolution_from_incharge() -> None:
    assert _hotel_code("SBPI (Anggun)", KNOWN) == "SBPI"
    assert _hotel_code("ZHBA", KNOWN) == "ZHBA"
    assert _hotel_code("SBB", KNOWN) == "SBB"
    assert _hotel_code("RSO (NICKY)", {"RSO", "SBB"}) == "RSO"


@pytest.mark.parametrize(
    "result,remarks,status",
    [
        ("- Potential client for meeting", None, "PROSPECT"),
        (None, "Account lost the tender", "LOST"),
        ("Confirmed booking for Q4", None, "CONFIRMED"),
        (None, None, "LEAD"),
    ],
)
def test_status_heuristics(result: str | None, remarks: str | None, status: str) -> None:
    got, _ = _status(result, remarks)
    assert got == status


def test_no_e_g_sample_row_mapped() -> None:
    rows, _ = parse_workbook(str(SOURCE))
    sample_phones = {r[1]["pic_phone"] for r in rows}
    assert "62 815 6945481" not in sample_phones