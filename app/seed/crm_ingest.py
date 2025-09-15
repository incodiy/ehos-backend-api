"""CRM cross-sell leads ingestion: crm_leads.

Reads the SBII cross-selling telemarketing worksheet (GOV) and maps each
account row into a `Lead` row. Each regional sheet contains up to three
DAY blocks, each with its own No/Sales Incharge/Account Name/PIC/... header.

Source of truth: `crm/_SBII - Cross Selling Tele (Work sheet GOV) 18-21 Aug 2026.xlsx`.

Deterministic & idempotent (Constraint H1-H4): `lead_no` unique key.
"""

import os
import re
import uuid
from collections import Counter

import openpyxl
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Hotel, Lead

SOURCE_FILE = "_SBII - Cross Selling Tele (Work sheet GOV) 18-21 Aug 2026.xlsx"

SOURCE_TELE = "TELEMARKETING"
INSTITUTION_GOV = "GOVERNMENT"

MONTH_RE = re.compile(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b")

SKIP_SHEETS = {
    "Business Leads",
    "SBM 2026",
    "Data Based & USP",
    "SBII Hotel List",
    "List Hotel Join",
    "Sheet20",
    "Sheet19",
    "Sheet18",
    "Sheet17",
    "Sheet23",
    "Introduction",
}


def _is_no(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return value.is_integer()
    return isinstance(value, str) and bool(re.fullmatch(r"\d+", value.strip()))


def _header_blocks(header: tuple) -> list[int]:
    blocks = []
    for index, cell in enumerate(header):
        if index + 10 > len(header):
            continue
        if isinstance(cell, str) and cell.strip().lower() in ("no", "no "):
            nxt = str(header[index + 1] or "").upper()
            if "SALES" in nxt:
                blocks.append(index)
    return blocks


def parse_workbook(path: str) -> tuple[list[tuple[int, dict]], dict]:
    """Return `(rows, per_sheet_counts)` — pure parser, no DB access.

    Each row tuple is `(sheet_index_offset, lead_dict)` where lead_dict has
    fixed keys; used both by the seeder and by tests.
    """
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    rows: list[tuple[int, dict]] = []
    per_sheet: Counter = Counter()
    try:
        for sheet_index, sheet_name in enumerate(wb.sheetnames):
            if sheet_name.strip() in SKIP_SHEETS:
                continue
            ws = wb[sheet_name]
            header = None
            header_row = None
            for i, raw in enumerate(ws.iter_rows(values_only=True)):
                if header_row is None and any(isinstance(c, str) and "ACCOUNT NAME" in c.upper() for c in raw):
                    header = raw
                    header_row = i
                    break
            if header is None:
                continue
            blocks = _header_blocks(header)
            if not blocks:
                continue

            for raw in ws.iter_rows(min_row=header_row + 2, values_only=True):
                for offset in blocks:
                    if raw[offset] is None or not _is_no(raw[offset]):
                        continue
                    if not (raw[offset + 1] or raw[offset + 2]):
                        continue
                    lead = {
                        "sales_incharge": str(raw[offset + 1] or "").strip(),
                        "company_name": str(raw[offset + 2] or "").strip(),
                        "pic_name": str(raw[offset + 3] or "").strip() or None,
                        "pic_phone": str(raw[offset + 4] or "").strip() or None,
                        "position": str(raw[offset + 5] or "").strip() or None,
                        "address": str(raw[offset + 6] or "").strip() or None,
                        "pic_email": str(raw[offset + 7] or "").strip() or None,
                        "result": str(raw[offset + 8] or "").strip() or None,
                        "remarks": str(raw[offset + 9] or "").strip() or None,
                    }
                    if not any(lead[k] for k in ("company_name", "pic_name", "pic_phone", "pic_email")):
                        continue
                    rows.append((sheet_index, lead))
                    per_sheet[sheet_name] += 1
    finally:
        wb.close()
    return rows, dict(per_sheet)


def _hotel_code(sales_incharge: str, known: set[str]) -> str:
    tokens = re.split(r"[^A-Za-z]", sales_incharge.upper())
    tokens = [t for t in tokens if t]
    for token in tokens:
        if token in known:
            return token
        if token in CODE_ALIASES and CODE_ALIASES[token] in known:
            return CODE_ALIASES[token]
    for token in tokens:
        for width in (4, 3):
            if token[:width] in known:
                return token[:width]
            if token[:width] in CODE_ALIASES and CODE_ALIASES[token[:width]] in known:
                return CODE_ALIASES[token[:width]]
    raise ValueError(f"Hotel code not resolved from sales incharge `{sales_incharge}`")


CODE_ALIASES = {"LSHB": "LHSB"}


def _status(result: str | None, remarks: str | None) -> tuple[str, str | None]:
    text = " ".join(x or "" for x in (result, remarks)).lower()
    if re.search(r"\blost\b", text):
        return "LOST", (remarks or result)
    if re.search(r"\bconfirm|\bwon\b", text):
        return "CONFIRMED", None  # WON legacy dinormalkan ke CONFIRMED (enum F-07)
    if re.search(r"\b(potential|requirement|require|meeting|need|need a|travel)", text):
        return "PROSPECT", None
    return "LEAD", None


async def seed_crm_leads(session: AsyncSession, data_dir: str, owner_id: uuid.UUID) -> dict:
    path = os.path.join(data_dir, SOURCE_FILE)
    rows, per_sheet = parse_workbook(path)

    known_codes = set((await session.execute(select(Hotel.code))).scalars().all())
    hotel_ids = dict((await session.execute(select(Hotel.code, Hotel.id))).all())

    lead_values = []
    for sequence, (_, raw) in enumerate(rows, start=1):
        code = _hotel_code(raw["sales_incharge"], known_codes)
        status, lost_reason = _status(raw["result"], raw["remarks"])
        phone = raw["pic_phone"]
        if phone:
            phone = phone[:20]
        email = raw["pic_email"]
        if email:
            email = email[:255]
        lead_values.append(
            {
                "lead_no": f"XSELL-GOV-2026-{sequence:04d}",
                "hotel_id": hotel_ids[code],
                "source": SOURCE_TELE,
                "institution_type": INSTITUTION_GOV,
                "company_name": raw["company_name"],
                "pic_name": raw["pic_name"],
                "pic_phone": phone,
                "pic_email": email,
                "status": status,
                "lost_reason": lost_reason,
                "owner_id": owner_id,
                "created_by": owner_id,
                "updated_by": owner_id,
            }
        )

    if lead_values:
        from sqlalchemy import delete

        await session.execute(delete(Lead).where(Lead.lead_no.like("XSELL-GOV-2026%")))
        for chunk in range(0, len(lead_values), 1000):
            stmt = (
                pg_insert(Lead)
                .values(lead_values[chunk : chunk + 1000])
                .on_conflict_do_nothing(index_elements=[Lead.lead_no])
            )
            await session.execute(stmt)

    return {"total_rows": len(lead_values), "per_sheet": per_sheet}
