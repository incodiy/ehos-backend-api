"""Audit offline sync & conflict resolution service (Constraint C1-C4, PRD-F-05)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identity import get_by_uuid
from app.models import (
    AuditItemScore,
    AuditMedia,
    AuditSession,
    ChecklistItem,
    SyncConflictLog,
    User,
)
from app.schemas.audit import SyncPushRequest, SyncPushResult
from app.schemas.common import HybridId
from app.services.audit_lifecycle import _compute_stored_score, record_scores


async def sync_push(
    session: AsyncSession,
    payload: SyncPushRequest,
    current: User,
) -> SyncPushResult:
    """Push batch nilai offline dengan deduplikasi client_id dan deteksi benturan timestamp."""
    sess = await session.scalar(
        select(AuditSession).where(AuditSession.client_id == payload.session_client_id)
    )
    if sess is None:
        raise HTTPException(
            404,
            "Sesi dengan client_id tsb tidak ada di server (buat dulu via POST /audit/sessions)",
        )

    upserted, conflicts, conflict_ids = await record_scores(
        session, sess, payload.scores, current
    )
    await session.commit()

    return SyncPushResult(
        session_id=sess.uuid,
        upserted=upserted,
        conflicts=conflicts,
        conflict_ids=conflict_ids,
        server_now=datetime.now(UTC),
    )


async def sync_pull(
    session: AsyncSession,
    sess: AuditSession,
    since: datetime,
) -> dict:
    """Pull perubahan inkremental sejak timestamp tertentu."""
    scores = (
        await session.scalars(
            select(AuditItemScore)
            .where(AuditItemScore.session_id == sess.id, AuditItemScore.updated_at > since)
            .order_by(AuditItemScore.updated_at.asc())
        )
    ).all()
    media = (
        await session.scalars(
            select(AuditMedia).where(
                AuditMedia.session_id == sess.id, AuditMedia.captured_at > since
            )
        )
    ).all()

    return {
        "scores": [
            {
                "id": r.uuid,
                "item_id": r.item.uuid if r.item else None,
                "room_ref": r.room_ref,
                "value": r.value,
                "score": r.score,
                "is_na": r.is_na,
                "note": r.note,
                "scored_by": r.scored_by_user.uuid if r.scored_by_user else None,
                "updated_at": r.updated_at,
            }
            for r in scores
        ],
        "media_status": [
            {
                "id": m.uuid,
                "phase": m.phase,
                "object_key": m.object_key,
                "upload_status": m.upload_status,
            }
            for m in media
        ],
        "server_now": datetime.now(UTC),
    }


async def list_conflicts(
    session: AsyncSession,
    sess: AuditSession,
) -> list[SyncConflictLog]:
    """Daftar konflik sinkronisasi yang berstatus PENDING."""
    return list(
        (
            await session.scalars(
                select(SyncConflictLog)
                .where(
                    SyncConflictLog.session_id == sess.id,
                    SyncConflictLog.resolution == "PENDING",
                )
                .order_by(SyncConflictLog.resolved_at.desc())
            )
        ).all()
    )


async def resolve_conflict(
    session: AsyncSession,
    sess: AuditSession,
    conflict_id: HybridId,
    winning_value: str,
    note: str | None,
    current: User,
) -> SyncConflictLog:
    """Resolusi konflik manual: perbarui skor dengan winning_value dan update status sesi."""
    conflict = await get_by_uuid(session, SyncConflictLog, conflict_id)
    if conflict is None or conflict.session_id != sess.id:
        raise HTTPException(404, "Konflik tidak ditemukan")
    if conflict.resolution != "PENDING":
        raise HTTPException(409, "Konflik sudah di-resolve")

    score_row = await session.scalar(
        select(AuditItemScore).where(
            AuditItemScore.session_id == sess.id,
            AuditItemScore.item_id == conflict.item_id,
            AuditItemScore.room_ref.is_not_distinct_from(conflict.room_ref),
        )
    )
    if score_row is None:
        raise HTTPException(409, "Baris nilai yang dikonflik sudah tidak ada")

    item = await session.get(ChecklistItem, conflict.item_id)
    if item is not None:
        try:
            score_row.score = await _compute_stored_score(item, winning_value, False)
        except HTTPException:
            raise
    score_row.value = winning_value
    score_row.is_na = False
    score_row.updated_at = datetime.now(UTC)
    score_row.scored_by = current.id

    conflict.winning_value = winning_value
    conflict.resolution = "MANUAL"
    conflict.resolved_at = datetime.now(UTC)
    conflict.resolved_by = current.id

    pending_left = await session.scalar(
        select(func.count(SyncConflictLog.id)).where(
            SyncConflictLog.session_id == sess.id,
            SyncConflictLog.resolution == "PENDING",
        )
    )
    if (pending_left or 0) == 0:
        sess.sync_status = "SYNCED"
        sess.updated_by = current.id

    await session.commit()
    await session.refresh(conflict)
    return conflict
