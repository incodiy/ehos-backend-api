"""Legacy `dm_audit_ops` ingestion (ADR-008 dual-layer historical audit).

Reads `audit/dm_audit_ops.xlsx` (11.033 rows, 2024-26, 37 cols) — one row per
audit *item* across HOUSEKEEPING / KITCHEN & FB / SECURITY RISK MANAGEMENT
sessions — and writes it to `legacy_score_rows` under a fresh
`legacy_ingestion_batches` batch.

Deterministic & idempotent (Constraint H1-H4): the batch owns a stable
`file_key`; re-running deletes prior rows of that batch (append-only rows are
owned by batch) and re-inserts, so total counts stay stable across runs.

Source of truth: `audit/dm_audit_ops.xlsx`.
"""

import datetime as _dt
import os
import uuid

import openpyxl
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Hotel, LegacyIngestionBatch, LegacyScoreRow

SOURCE_FILE = "dm_audit_ops.xlsx"
SOURCE_DM_OPS = "DM_AUDIT_OPS"

TOTAL_KEYS = (
    "total_housekeeping",
    "total_room_checklist",
    "total_kitchenfnb",
    "total_securityrisk",
    "total_score",
    "tot_sc",
    "tot_hk",
    "tot_rc",
    "tot_kfb",
    "tot_sr",
    "status_total_score",
    "status_housekeeping",
    "status_room_checklist",
    "status_kitchenfb",
    "status_securityrisk",
)

PROCESS_DATE_KEY = "process_date"


def _norm(v: object) -> object:
    if isinstance(v, _dt.datetime):
        return v.isoformat()
    if isinstance(v, _dt.date):
        return v.isoformat()
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v) if isinstance(v, float) and v.is_integer() else v
    return v


def _totals(raw: dict) -> dict:
    out = {}
    for key in TOTAL_KEYS:
        if raw.get(key) is not None:
            out[key] = _norm(raw[key])
    if raw.get(PROCESS_DATE_KEY) is not None:
        out[PROCESS_DATE_KEY] = _norm(raw[PROCESS_DATE_KEY])
    return out


def parse_workbook(path: str) -> tuple[list[dict], int]:
    """Return `(raw_rows, session_count)` — pure parser, no DB access.

    Each raw row is a dict keyed by the legacy header names; cell values are
    normalized (datetime/date → ISO, numeric floats left as-is).
    """
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    rows: list[dict] = []
    sessions: set[tuple[str, str, str]] = set()
    try:
        ws = wb[wb.sheetnames[0]]
        for i, raw in enumerate(ws.iter_rows(values_only=True)):
            if i == 0:
                continue
            if all(c is None for c in raw):
                continue
            row = {name: _norm(value) for name, value in zip(HEADERS, raw, strict=False)}
            rows.append(row)
            sessions.add(
                (
                    str(row.get("hotel_code") or ""),
                    str(row.get("period_date") or ""),
                    str(row.get("date_audit") or ""),
                )
            )
    finally:
        wb.close()
    return rows, len(sessions)


HEADERS = [
    "years",
    "date_audit",
    "main_category",
    "category",
    "sub_category",
    "sub_category_child",
    "period_date",
    "hotel_code",
    "score",
    "total_housekeeping",
    "total_room_checklist",
    "total_kitchenfnb",
    "total_securityrisk",
    "total_score",
    "hotel_name",
    "rom",
    "rom_email",
    "gm_email",
    "gm_name",
    "brand",
    "longitude",
    "latitude",
    "region",
    "focus",
    "status",
    "year_terminated",
    "process_date",
    "tot_sc",
    "tot_hk",
    "tot_rc",
    "tot_kfb",
    "tot_sr",
    "status_total_score",
    "status_housekeeping",
    "status_room_checklist",
    "status_kitchenfb",
    "status_securityrisk",
]


async def seed_legacy_ops(session: AsyncSession, data_dir: str, owner_id: uuid.UUID) -> dict:
    path = os.path.join(data_dir, SOURCE_FILE)
    rows, session_count = parse_workbook(path)

    hotel_rows = (await session.execute(select(Hotel.code, Hotel.id))).all()
    hotel_ids = {code: hid for code, hid in hotel_rows}
    missing = sorted({r["hotel_code"] for r in rows} - set(hotel_ids))
    if missing:
        raise ValueError(f"Undefined legacy hotel codes: {missing}")

    existing_batch = (
        await session.execute(
            select(LegacyIngestionBatch).where(
                LegacyIngestionBatch.file_key == SOURCE_FILE,
                LegacyIngestionBatch.source == SOURCE_DM_OPS,
            )
        )
    ).scalar_one_or_none()
    if existing_batch is not None:
        await session.execute(delete(LegacyScoreRow).where(LegacyScoreRow.batch_id == existing_batch.id))
        await session.execute(delete(LegacyIngestionBatch).where(LegacyIngestionBatch.id == existing_batch.id))

    batch = LegacyIngestionBatch(
        source=SOURCE_DM_OPS,
        file_key=SOURCE_FILE,
        row_count=len(rows),
        status="COMPLETED",
        imported_by=owner_id,
        imported_at=func.now(),
    )
    session.add(batch)
    await session.flush()

    values = []
    for row in rows:
        raw_period = row.get("period_date")
        period_date = None
        if isinstance(raw_period, str) and raw_period:
            try:
                period_date = _dt.date.fromisoformat(raw_period[:10])
            except ValueError:
                period_date = None
        values.append(
            {
                "batch_id": batch.id,
                "hotel_id": hotel_ids[row["hotel_code"]],
                "years": int(row["years"]) if row.get("years") is not None else None,
                "period_date": period_date,
                "main_category": row.get("main_category"),
                "category": row.get("category"),
                "sub_category": row.get("sub_category"),
                "sub_category_child": row.get("sub_category_child"),
                "score": row.get("score"),
                "totals": _totals(row),
                "raw_csv": row,
            }
        )

    for chunk in range(0, len(values), 1000):
        stmt = pg_insert(LegacyScoreRow).values(values[chunk : chunk + 1000])
        await session.execute(stmt)

    return {
        "rows": len(values),
        "sessions": session_count,
        "batch_id": str(batch.id),
        "years": {int(y): n for y, n in _year_counts(values).items()} if values else {},
        "hotels": len({v["hotel_id"] for v in values}),
    }


def _year_counts(values: list[dict]) -> dict:
    out: dict[int, int] = {}
    for v in values:
        if v["years"] is not None:
            out[v["years"]] = out.get(v["years"], 0) + 1
    return out
