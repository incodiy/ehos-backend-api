"""Audit session lifecycle management service.

Enkapsulasi alur state machine sesi audit:
DRAFT -> IN_PROGRESS -> SUBMITTED -> PUBLISHED (+ REOPEN ke IN_PROGRESS).
Mematuhi Constraint B1-B3 (template snapshot terkunci), Constraint C1-C4 (offline media),
Constraint G1-G4 (real data only), dan PRD-F-01/02/03/05.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identity import get_by_uuid
from app.models import (
    AuditItemScore,
    AuditSession,
    ChecklistItem,
    ChecklistSection,
    ChecklistTemplate,
    Finding,
    SyncConflictLog,
    User,
)
from app.models.master import Hotel
from app.schemas.audit import ScoreUpsert, SessionCreateRequest, SessionUpdateRequest
from app.schemas.common import HybridId
from app.services.aggregation import AggregationError, aggregate_session
from app.services.capa_trigger import auto_create_capa_ticket
from app.services.scoring import (
    InvalidNA,
    InvalidScoreValue,
    RubricType,
    evaluate_item,
    find_locked_template,
)
from app.services.verdict import compute_verdict


async def create_session(
    session: AsyncSession,
    payload: SessionCreateRequest,
    current: User,
) -> AuditSession:
    """Buat sesi audit baru dengan bind template snapshot terkunci (B3)."""
    hotel = await get_by_uuid(session, Hotel, payload.hotel_id)
    if hotel is None:
        raise HTTPException(404, "Hotel tidak ditemukan")

    if payload.template_id is not None:
        template = await get_by_uuid(session, ChecklistTemplate, payload.template_id)
        if template is None:
            raise HTTPException(404, "Template tidak ditemukan")
        if template.department != payload.department:
            raise HTTPException(422, "Template department ≠ sesi department")
        if template.status != "LOCKED":
            raise HTTPException(422, "Template belum LOCKED (B3 — harus snapshot terkunci)")
    else:
        brand = hotel.brand
        template = await find_locked_template(
            session, payload.department, brand.tier if brand else None
        )
        if template is None:
            raise HTTPException(
                422, "Tidak ada template LOCKED utk (department, brand_tier) hotel ini"
            )

    # dedupe offline (F-05): client_id sama -> kembalikan sesi yang ada
    if payload.client_id is not None:
        existing = await session.scalar(
            select(AuditSession).where(AuditSession.client_id == payload.client_id)
        )
        if existing is not None:
            return existing

    new_session = AuditSession(
        hotel_id=hotel.id,
        template_id=template.id,
        department=payload.department,
        audit_type=payload.audit_type,
        status="DRAFT",
        auditor_id=current.id,
        date_start=payload.date_start or date.today(),
        date_end=payload.date_end,
        origin="SYSTEM",
        sync_status="SYNCED",
        client_id=payload.client_id,
        created_by=current.id,
        updated_by=current.id,
    )
    session.add(new_session)
    await session.commit()
    await session.refresh(new_session)
    new_session.hotel = hotel
    new_session.template = template
    new_session.auditor = current
    return new_session


async def _template_items(
    session: AsyncSession, template_id: int
) -> dict[uuid.UUID, ChecklistItem]:
    rows = (
        await session.scalars(
            select(ChecklistItem)
            .join(ChecklistSection)
            .where(ChecklistSection.template_id == template_id)
        )
    ).all()
    return {i.uuid: i for i in rows}


async def _compute_stored_score(
    item: ChecklistItem, value: str | None, is_na: bool
) -> float | None:
    """Hitung skor tersimpan per-butir."""
    if RubricType(item.rubric_type) is RubricType.MULTI_ROOM:
        return None
    try:
        ev = evaluate_item(
            rubric_type=item.rubric_type,
            value=value or "",
            max_score=item.max_score,
            na_allowed=item.na_allowed,
            is_na=is_na,
        )
    except (InvalidScoreValue, InvalidNA) as exc:
        raise HTTPException(422, f"Item {item.code}: {exc}") from None
    if ev.is_na:
        return None
    return ev.achieved


async def _log_conflict(
    session: AsyncSession,
    sess: AuditSession,
    item: ChecklistItem,
    room_ref: str | None,
    server_value: str | None,
    device_value: str | None,
) -> SyncConflictLog:
    conflict = SyncConflictLog(
        session_id=sess.id,
        item_id=item.id,
        room_ref=room_ref,
        winning_value=server_value,
        losing_value=device_value,
        resolution="PENDING",
        resolved_at=datetime.now(UTC),
    )
    session.add(conflict)
    await session.flush()
    return conflict


async def record_scores(
    session: AsyncSession,
    sess: AuditSession,
    scores: list[ScoreUpsert],
    actor: User,
) -> tuple[int, int, list[uuid.UUID]]:
    """Upsert nilai butir audit online atau offline sync.

    Transisi status otomatis: DRAFT -> IN_PROGRESS.
    Kunci mutasi: Dilarang mengubah jika SUBMITTED atau PUBLISHED.
    """
    if sess.status in ("SUBMITTED", "PUBLISHED"):
        raise HTTPException(
            409, f"Sesi sudah {sess.status} — tidak bisa mengubah nilai"
        )

    items = await _template_items(session, sess.template_id)
    upserted = conflicts = 0
    conflict_ids: list[uuid.UUID] = []

    for s in scores:
        item = items.get(s.item_id)
        if item is None:
            item = await get_by_uuid(session, ChecklistItem, s.item_id)
        if item is None or item.uuid not in items:
            raise HTTPException(422, f"Item {s.item_id} bukan milik template sesi ini")
        if s.value is None and not s.is_na:
            continue  # value kosong non-NA = belum di-score

        base = dict(session_id=sess.id, item_id=item.id, room_ref=s.room_ref)
        existing = await session.scalar(
            select(AuditItemScore).where(
                AuditItemScore.session_id == sess.id,
                AuditItemScore.item_id == item.id,
                AuditItemScore.room_ref.is_not_distinct_from(s.room_ref),
            )
        )
        if existing is not None:
            newer_device = s.updated_at > existing.updated_at
            if not newer_device and (
                (existing.value or None, existing.is_na) != (s.value or None, s.is_na)
            ):
                cfg = await _log_conflict(
                    session, sess, item, s.room_ref, existing.value, s.value
                )
                conflict_ids.append(cfg.uuid)
                conflicts += 1
                continue
            existing.value = s.value
            existing.is_na = s.is_na
            existing.note = s.note
            existing.score = await _compute_stored_score(item, s.value, s.is_na)
            existing.scored_at = s.scored_at
            existing.updated_at = s.updated_at
            existing.scored_by = actor.id
        else:
            score = await _compute_stored_score(item, s.value, s.is_na)
            session.add(
                AuditItemScore(
                    **base,
                    value=s.value,
                    is_na=s.is_na,
                    note=s.note,
                    score=score,
                    scored_by=actor.id,
                    scored_at=s.scored_at,
                    updated_at=s.updated_at,
                )
            )
        upserted += 1

    if sess.status == "DRAFT" and upserted > 0:
        sess.status = "IN_PROGRESS"

    sess.updated_by = actor.id
    if conflicts:
        sess.sync_status = "PENDING_CONFLICT"

    return upserted, conflicts, conflict_ids


async def update_session(
    session: AsyncSession,
    sess: AuditSession,
    payload: SessionUpdateRequest,
    actor: User,
) -> AuditSession:
    """Update metadata sesi audit (hanya diperbolehkan saat status DRAFT)."""
    if sess.status != "DRAFT":
        raise HTTPException(
            409, f"Sesi berstatus {sess.status} — hanya sesi DRAFT yang dapat diubah."
        )

    if payload.department is not None and payload.department != sess.department:
        hotel = sess.hotel or await session.get(Hotel, sess.hotel_id)
        brand = hotel.brand if hotel else None
        template = await find_locked_template(
            session, payload.department, brand.tier if brand else None
        )
        if template is None:
            raise HTTPException(
                422, f"Tidak ada template LOCKED untuk departemen {payload.department}"
            )
        sess.department = payload.department
        sess.template_id = template.id
        sess.template = template

    if payload.audit_type is not None:
        sess.audit_type = payload.audit_type
    if payload.date_start is not None:
        sess.date_start = payload.date_start
    if payload.date_end is not None:
        sess.date_end = payload.date_end

    sess.updated_by = actor.id
    await session.commit()
    await session.refresh(sess)
    return sess


async def delete_session(
    session: AsyncSession,
    sess: AuditSession,
    actor: User,
) -> None:
    """Hapus / batalkan sesi audit (hanya diperbolehkan saat status DRAFT)."""
    if sess.status != "DRAFT":
        raise HTTPException(
            409, f"Sesi berstatus {sess.status} — hanya sesi DRAFT yang dapat dibatalkan/dihapus."
        )
    await session.delete(sess)
    await session.commit()


async def submit_session(
    session: AsyncSession,
    sess: AuditSession,
    actor: User,
) -> AuditSession:
    """Auditor selesai input -> SUBMITTED. Mengunci pengubahan nilai lebih lanjut."""
    if sess.status == "PUBLISHED":
        raise HTTPException(409, "Sesi sudah PUBLISHED")
    sess.status = "SUBMITTED"
    if sess.date_end is None:
        sess.date_end = date.today()
    sess.updated_by = actor.id
    await session.commit()
    await session.refresh(sess)
    return sess


async def reopen_session(
    session: AsyncSession,
    sess: AuditSession,
    actor: User,
) -> AuditSession:
    """Corporate QA mengembalikan sesi SUBMITTED ke IN_PROGRESS untuk revisi."""
    if sess.status != "SUBMITTED":
        raise HTTPException(409, "Hanya sesi SUBMITTED yang dapat di-reopen")
    sess.status = "IN_PROGRESS"
    sess.updated_by = actor.id
    await session.commit()
    await session.refresh(sess)
    return sess


async def publish_session(
    session: AsyncSession,
    sess: AuditSession,
    actor: User,
) -> AuditSession:
    """Finalisasi & publikasi laporan audit (skor agregat, verdict PASS/FAIL, findings, CAPA)."""
    if sess.status == "PUBLISHED":
        raise HTTPException(409, "Sesi sudah PUBLISHED")
    if sess.status != "SUBMITTED":
        raise HTTPException(422, "Sesi harus SUBMITTED sebelum dipublish")

    try:
        agg = await aggregate_session(session, sess.id)
    except AggregationError as exc:
        raise HTTPException(422, str(exc)) from None
    verdict = compute_verdict(agg)
    if verdict.pass_fail is None:
        raise HTTPException(422, "Belum ada nilai yang valid untuk di-score")

    sess.total_score = verdict.total_score
    sess.pass_fail = verdict.pass_fail
    sess.published_at = datetime.now(UTC)
    sess.status = "PUBLISHED"
    sess.updated_by = actor.id

    # Snapshot breakdown per-section (F-02 drill-down) — array kanonik
    sess.department_breakdown = [
        {
            "section_code": sec.code,
            "section_name": sec.name,
            "score": round(sec.ratio * 100, 2) if sec.ratio is not None else None,
            "max": 100.0,
            "pct": sec.ratio,
            "items_count": sec.items_scored + sec.items_na,
        }
        for sec in agg.sections
    ]

    # Temuan dari item yang gagal (life-safety -> CRITICAL utk CAPA P1 di 6e)
    existing_findings = set(
        (
            await session.scalars(
                select(Finding.item_id).where(Finding.session_id == sess.id)
            )
        ).all()
    )
    new_findings = []
    for it in agg.all_items:
        if it.missing or it.is_na or it.ratio != 0.0 or it.item_id in existing_findings:
            continue
        new_findings.append(
            Finding(
                session_id=sess.id,
                item_id=it.item_id,
                hotel_id=sess.hotel_id,
                is_life_safety=it.is_life_safety,
                severity="CRITICAL" if it.is_life_safety else "MAJOR",
                title=it.question_text or it.code,
            )
        )
    session.add_all(new_findings)
    await session.flush()

    # Auto-CAPA trigger (PRD-F-03): tiap finding gagal -> CapaTicket SLA berjenjang
    for finding in new_findings:
        await auto_create_capa_ticket(
            session, finding, actor_id=actor.id, department=sess.department
        )

    await session.commit()
    await session.refresh(sess)
    return sess
