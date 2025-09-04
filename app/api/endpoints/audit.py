"""Audit session endpoints — openapi.yaml `/audit/sessions` (F-02/F-05, task 6d).

CRUD sesi + scoring live/offline + conflict resolution (timestamp merge):
- GET    /audit/sessions                     list + filter (hotel/dept/status/origin/period)
- POST   /audit/sessions                     create → bind template LOCKED (B3, brand_tier aware)
- GET    /audit/sessions/{id}                detail + item scores + findings
- POST   /audit/sessions/sessions/{id}/items          bulk upsert nil (live/online, timestamp merge F-05)
- POST   /audit/sessions/sessions/{id}/submit         auditor selesai input → SUBMITTED
- POST   /audit/sessions/sessions/{id}/publish        finalize → aggregate(6b) + verdict(6c) → findings
- POST   /audit/sessions/sync                push offline (dedupe client_id, conflict detection)
- GET    /audit/sessions/sessions/{id}/sync           pull incremental since timestamp
- GET    /audit/sessions/sessions/{id}/conflicts      daftar konflik PENDING
- POST   /audit/sessions/sessions/sessions/{id}/conflicts/{conflictId}/resolve   pilih nilai menang (manual)

Komponen scoring dari service layer: scoring.evaluate (6a), aggregation.aggregate_session
(6b), verdict.compute_verdict (6c).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Header, HTTPException, Response
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession
from app.core.identity import get_by_uuid
from app.models import (
    AuditItemScore,
    AuditMedia,
    AuditSession,
    ChecklistItem,
    ChecklistSection,
    ChecklistTemplate,
    Finding,
    SyncConflictLog,
    User,
)
from app.models.master import Hotel
from app.schemas.audit import (
    AuditSessionOut,
    BulkScoreUpsert,
    ResolveConflictRequest,
    SessionCreateRequest,
    SyncConflictOut,
    SyncPushRequest,
    SyncPushResult,
)
from app.schemas.common import Envelope, HybridId, Paginated, PaginationMeta
from app.services.aggregation import AggregationError, aggregate_session
from app.services.pdf_report import render_audit_report
from app.services.report_i18n import load_report_translations
from app.services.scoring import (
    InvalidNA,
    InvalidScoreValue,
    RubricType,
    evaluate_item,
)
from app.services.verdict import compute_verdict

router = APIRouter(prefix="/audit", tags=["audit"])

ALLOWED_STATUS = {"DRAFT", "IN_PROGRESS", "SUBMITTED", "PUBLISHED"}


# ─── RBAC / scope helpers ────────────────────────────────────────────────

async def _permission_codes(session: AsyncSession, user: User) -> set[str]:
    rows = await session.execute(
        text(
            "SELECT p.code FROM users u "
            "JOIN user_roles ur ON ur.user_id = u.id "
            "JOIN roles_permissions rp ON rp.role_id = ur.role_id "
            "JOIN permissions p ON p.id = rp.permission_id "
            "WHERE u.id = :uid"
        ),
        {"uid": user.id},
    )
    return {r.code for r in rows}


async def _is_corporate(session: AsyncSession, user: User) -> bool:
    return "audit:read:global" in await _permission_codes(session, user)


async def _has_hotel_scope(session: AsyncSession, user: User, hotel_id: int | uuid.UUID | None) -> bool:
    from app.models.users import UserHotelAssignment, UserRegionAssignment

    codes = await _permission_codes(session, user)
    if "audit:read:global" in codes:
        return True
    hotel = await get_by_uuid(session, Hotel, hotel_id)
    if hotel is None:
        return False
    if "audit:read:region" in codes:
        assigned = await session.scalar(
            select(UserRegionAssignment).where(
                UserRegionAssignment.user_id == user.id,
                UserRegionAssignment.region_id == hotel.region_id,
                UserRegionAssignment.deleted_at.is_(None),
            )
        )
        if assigned:
            return True
    if "audit:read:hotel" in codes or "audit:run:hotel" in codes:
        assigned = await session.scalar(
            select(UserHotelAssignment).where(
                UserHotelAssignment.user_id == user.id,
                UserHotelAssignment.hotel_id == hotel.id,
                UserHotelAssignment.deleted_at.is_(None),
            )
        )
        if assigned:
            return True
    return False


async def _require_read(session: AsyncSession, user: User, hotel_id: int | None = None) -> None:
    if hotel_id is None:
        if not await _is_corporate(session, user):
            raise HTTPException(403, "Missing permission: audit:read:global (list tanpa filter hotel)")
        return
    if not await _has_hotel_scope(session, user, hotel_id):
        raise HTTPException(403, "Missing permission: audit scope hotel/region/global")


async def _require_run(session: AsyncSession, user: User, hotel_id: int) -> None:
    codes = await _permission_codes(session, user)
    if "audit:read:global" in codes:
        return  # corporate auditor boleh eksekusi/scoring
    if "audit:run:hotel" in codes and await _has_hotel_scope(session, user, hotel_id):
        return
    raise HTTPException(403, "Missing permission: audit:run:hotel")


# ─── helpers ─────────────────────────────────────────────────────────────

async def _get_session(session: AsyncSession, session_id: HybridId) -> AuditSession:
    s = await get_by_uuid(session, AuditSession, session_id)
    if s is None:
        raise HTTPException(404, "Sesi audit tidak ditemukan")
    return s


async def _template_items(session: AsyncSession, template_id: int) -> dict[uuid.UUID, ChecklistItem]:
    rows = (await session.scalars(
        select(ChecklistItem).join(ChecklistSection).where(ChecklistSection.template_id == template_id)
    )).all()
    return {i.uuid: i for i in rows}


async def _compute_stored_score(item: ChecklistItem, value: str | None, is_na: bool) -> float | None:
    """Skor tersimpan per-butir. N/A → None (agregasi mengecualikan, ratio None).
    MULTI_ROOM → None (diagregasi ulang di publish 6b). value kosong non-NA → None."""
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


async def _log_conflict(session: AsyncSession, sess: AuditSession, item: ChecklistItem,
                        room_ref: str | None, server_value: str | None,
                        device_value: str | None) -> SyncConflictLog:
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
    await session.flush()  # ambil id untuk conflict_ids
    return conflict


async def _upsert_scores(
    session: AsyncSession,
    sess: AuditSession,
    items: dict[uuid.UUID, ChecklistItem],
    scores: list,
    actor: User,
) -> tuple[int, int, list[uuid.UUID]]:
    upserted = conflicts = 0
    conflict_ids: list[uuid.UUID] = []
    for s in scores:
        item = items.get(s.item_id)
        if item is None:
            item = await get_by_uuid(session, ChecklistItem, s.item_id)
        if item is None or item.uuid not in items:
            raise HTTPException(422, f"Item {s.item_id} bukan milik template sesi ini")
        if s.value is None and not s.is_na:
            continue  # value kosong non-NA = belum di-score, jangan disimpan
        base = dict(
            session_id=sess.id, item_id=item.id, room_ref=s.room_ref,
        )
        existing = await session.scalar(
            select(AuditItemScore).where(
                AuditItemScore.session_id == sess.id,
                AuditItemScore.item_id == item.id,
                AuditItemScore.room_ref.is_not_distinct_from(s.room_ref),
            )
        )
        if existing is not None:
            newer_device = s.updated_at > existing.updated_at
            if not newer_device and ((existing.value or None, existing.is_na) !=
                                     (s.value or None, s.is_na)):
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
            session.add(AuditItemScore(**base, value=s.value, is_na=s.is_na, note=s.note,
                                       score=score, scored_by=actor.id, scored_at=s.scored_at,
                                       updated_at=s.updated_at))
        upserted += 1
    return upserted, conflicts, conflict_ids


# ─── endpoints ───────────────────────────────────────────────────────────

@router.get("/sessions", response_model=Paginated[Envelope[list[AuditSessionOut]], AuditSessionOut])
async def list_sessions(
    current: CurrentUser,
    session: DbSession,
    hotel_id: HybridId | None = None,
    department: str | None = None,
    status: str | None = None,
    origin: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
) -> Paginated[Envelope[list[AuditSessionOut]], AuditSessionOut]:
    if hotel_id is None:
        await _require_read(session, current, None)
    else:
        hotel_row = await get_by_uuid(session, Hotel, hotel_id)
        hotel_internal = hotel_row.id if hotel_row else None
        await _require_read(session, current, hotel_internal)
    stmt = select(AuditSession)
    count_stmt = select(func.count(AuditSession.id))
    if hotel_id:
        stmt = stmt.where(AuditSession.hotel_id == hotel_internal)
        count_stmt = count_stmt.where(AuditSession.hotel_id == hotel_internal)
    if department:
        stmt = stmt.where(AuditSession.department == department)
        count_stmt = count_stmt.where(AuditSession.department == department)
    if status:
        if status not in ALLOWED_STATUS:
            raise HTTPException(422, f"status tidak dikenal: {status}")
        stmt = stmt.where(AuditSession.status == status)
        count_stmt = count_stmt.where(AuditSession.status == status)
    if origin:
        stmt = stmt.where(AuditSession.origin == origin)
        count_stmt = count_stmt.where(AuditSession.origin == origin)
    if date_from:
        stmt = stmt.where(AuditSession.date_start >= date_from)
        count_stmt = count_stmt.where(AuditSession.date_start >= date_from)
    if date_to:
        stmt = stmt.where(AuditSession.date_start <= date_to)
        count_stmt = count_stmt.where(AuditSession.date_start <= date_to)

    per_page = 50
    total = await session.scalar(count_stmt) or 0
    last_page = max(1, (total + per_page - 1) // per_page)
    rows = (await session.scalars(
        stmt.order_by(AuditSession.date_start.desc(), AuditSession.created_at.desc())
        .offset((max(1, page) - 1) * per_page).limit(per_page)
    )).all()
    return Paginated(
        data=[AuditSessionOut.model_validate(r) for r in rows],
        meta=PaginationMeta(current_page=max(1, page), per_page=per_page,
                            total=total or 0, last_page=last_page),
    )


@router.post("/sessions", response_model=Envelope[AuditSessionOut], status_code=201)
async def create_session(
    payload: SessionCreateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[AuditSessionOut]:
    hotel = await get_by_uuid(session, Hotel, payload.hotel_id)
    if hotel is None:
        raise HTTPException(404, "Hotel tidak ditemukan")
    await _require_run(session, current, hotel.id)

    if payload.template_id is not None:
        template = await get_by_uuid(session, ChecklistTemplate, payload.template_id)
        if template is None:
            raise HTTPException(404, "Template tidak ditemukan")
        if template.department != payload.department:
            raise HTTPException(422, "Template department ≠ sesi department")
        if template.status != "LOCKED":
            raise HTTPException(422, "Template belum LOCKED (B3 — harus snapshot terkunci)")
    else:
        from app.services.scoring import find_locked_template

        brand = hotel.brand
        template = await find_locked_template(session, payload.department, brand.tier if brand else None)
        if template is None:
            raise HTTPException(422,
                                "Tidak ada template LOCKED utk (department, brand_tier) hotel ini")

    # dedupe offline (F-05): client_id sama → kembalikan sesi yang ada
    if payload.client_id is not None:
        existing = await session.scalar(
            select(AuditSession).where(AuditSession.client_id == payload.client_id)
        )
        if existing is not None:
            return Envelope(data=AuditSessionOut.model_validate(existing))

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
    return Envelope(data=AuditSessionOut.model_validate(new_session))


@router.get("/sessions/{id}", response_model=Envelope[dict])
async def get_session_detail(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[dict]:
    sess = await _get_session(session, id)
    await _require_read(session, current, sess.hotel_id)
    scores = (await session.scalars(
        select(AuditItemScore).where(AuditItemScore.session_id == sess.id)
        .order_by(AuditItemScore.updated_at.asc())
    )).all()
    findings = (await session.scalars(
        select(Finding).where(Finding.session_id == sess.id)
    )).all()
    return Envelope(data={
        "session": AuditSessionOut.model_validate(sess),
        "items": [{"id": r.uuid, "session_id": sess.uuid,
                   "item_id": r.item.uuid if r.item else None, "room_ref": r.room_ref,
                   "value": r.value, "score": r.score, "is_na": r.is_na,
                   "note": r.note, "scored_by": r.scored_by_user.uuid if r.scored_by_user else None,
                   "updated_at": r.updated_at} for r in scores],
        "findings": [{"id": f.uuid, "session_id": sess.uuid,
                      "item_id": f.item.uuid if f.item else None,
                      "is_life_safety": f.is_life_safety, "severity": f.severity,
                      "title": f.title, "description": f.description,
                      "location": f.location} for f in findings],
    })


@router.get("/sessions/{id}/report.pdf")
async def get_session_report(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
    response: Response,
    accept_language: str | None = Header(default=None),
) -> Response:
    """Ekspor laporan audit PDF (F-02), localized via Accept-Language (F-22).

    Konten: identitas sesi + skor total + verdict (6c) + section breakdown (6b)
    + temuan (6d) + tiket CAPA otomatis (6e) + rincian per item. Sedang digabung
    dari DB real (Aturan 1/2 — tanpa hardcode/mock).
    """
    sess = await _get_session(session, id)
    await _require_read(session, current, sess.hotel_id)

    locale = "id"
    first = (accept_language or "").split(",")[0].strip().lower()
    if first.startswith("en"):
        locale = "en"

    try:
        agg = await aggregate_session(session, sess.id)
    except AggregationError as exc:
        raise HTTPException(422, str(exc)) from None
    verdict = compute_verdict(agg)

    hotel = sess.hotel
    auditor = sess.auditor

    findings_rows = (await session.execute(
        select(Finding, ChecklistItem.code)
        .join(ChecklistItem, ChecklistItem.id == Finding.item_id)
        .where(Finding.session_id == sess.id)
        .order_by(Finding.is_life_safety.desc(), Finding.severity)
    )).all()
    capa_count, p1_count = (await session.execute(text(
        "SELECT count(*), count(*) FILTER (WHERE ct.priority = 1) "
        "FROM capa_tickets ct JOIN findings f ON f.id = ct.finding_id "
        "WHERE f.session_id = :s"
    ), {"s": sess.id})).one()

    item_uuids = {i.id: i.uuid for i in (await session.scalars(select(ChecklistItem))).all()}
    section_uuids = {s.id: s.uuid for s in (await session.scalars(select(ChecklistSection))).all()}

    data = {
        "session": {
            "session_id": str(sess.uuid),
            "hotel_code": hotel.code if hotel else "-",
            "hotel_name": hotel.name if hotel else "-",
            "department": sess.department,
            "audit_type": sess.audit_type,
            "date_start": sess.date_start.isoformat() if sess.date_start else "-",
            "date_end": sess.date_end.isoformat() if sess.date_end else "-",
            "auditor_name": auditor.name if auditor else "-",
            "published_at": sess.published_at.strftime("%d-%m-%Y %H:%M") if sess.published_at else "-",
            "status": sess.status,
        },
        "score": {
            "total_score": f"{agg.total_score:.1f}" if agg.total_score is not None else None,
            "pass_fail": verdict.pass_fail,
            "items_scored": agg.items_scored,
            "items_missed": agg.items_missed,
            "items_na": agg.items_na,
        },
        "hazards": [{"item_id": str(item_uuids.get(h.item_id, h.item_id)), "code": h.code,
                     "question_text": h.question_text} for h in verdict.hazards],
        "sections": [{
            "section_id": str(section_uuids.get(s.section_id, s.section_id)), "code": s.code,
            "name": s.name, "ratio": s.ratio,
            "achieved_points": s.achieved_points, "max_points": s.max_points,
            "items_scored": s.items_scored,
        } for s in agg.sections],
        "findings": [{
            "item_id": str(item_uuids.get(finding.item_id, finding.item_id)),
            "code": f_code, "question_text": finding.title,
            "severity": finding.severity, "is_life_safety": finding.is_life_safety,
        } for finding, f_code in findings_rows],
        "capa": {"count": capa_count, "p1_count": p1_count},
        "items": [{
            "item_id": str(item_uuids.get(it.item_id, it.item_id)), "code": it.code,
            "question_text": it.question_text,
            "rubric_type": it.rubric_type, "ratio": it.ratio,
            "achieved": it.achieved, "max_score": it.max_score,
            "is_na": it.is_na, "missing": it.missing, "is_life_safety": it.is_life_safety,
        } for it in agg.all_items],
    }

    # 6h: konten lokal (question_text / nama section) di-terjemahkan dari tabel
    # `translations` (real data, Constraint G); fallback nilai kanonikal id (F3).
    if locale == "en":
        tr = await load_report_translations(session, sess.template_id, "en")
        t_items, t_sections = tr["items"], tr["sections"]
        for s in data["sections"]:
            s["name"] = t_sections.get(s["section_id"]) or s["name"]
        for it in data["items"]:
            it["question_text"] = t_items.get(it["item_id"]) or it["question_text"]
        for h in data["hazards"]:
            h["question_text"] = t_items.get(h["item_id"]) or h["question_text"]
        for f in data["findings"]:
            f["question_text"] = t_items.get(f["item_id"]) or f["question_text"]

    pdf_bytes = render_audit_report(data, locale)
    fname = f"audit-report-{sess.uuid}.pdf"
    response.headers["Content-Disposition"] = f'attachment; filename="{fname}"'
    response.media_type = "application/pdf"
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.post("/sessions/{id}/items", response_model=Envelope[dict])
async def bulk_upsert_scores(
    id: HybridId,
    payload: BulkScoreUpsert,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[dict]:
    sess = await _get_session(session, id)
    await _require_run(session, current, sess.hotel_id)
    if sess.status == "PUBLISHED":
        raise HTTPException(409, "Sesi sudah PUBLISHED — tidak bisa mengubah nilai")
    items = await _template_items(session, sess.template_id)
    upserted, conflicts, _ = await _upsert_scores(session, sess, items, payload.scores, current)
    await session.commit()
    return Envelope(data={"upserted": upserted, "conflicts": conflicts})


@router.post("/sessions/{id}/submit", response_model=Envelope[AuditSessionOut])
async def submit_session(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[AuditSessionOut]:
    sess = await _get_session(session, id)
    await _require_run(session, current, sess.hotel_id)
    if sess.status == "PUBLISHED":
        raise HTTPException(409, "Sesi sudah PUBLISHED")
    sess.status = "SUBMITTED"
    if sess.date_end is None:
        sess.date_end = date.today()
    sess.updated_by = current.id
    await session.commit()
    await session.refresh(sess)
    return Envelope(data=AuditSessionOut.model_validate(sess))


@router.post("/sessions/{id}/publish", response_model=Envelope[AuditSessionOut])
async def publish_session(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[AuditSessionOut]:
    sess = await _get_session(session, id)
    if not await _is_corporate(session, current):
        raise HTTPException(403, "Missing permission: audit:read:global (publish = corporate QA)")
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
    sess.updated_by = current.id

    # temuan dari item yang gagal (life-safety → CRITICAL utk CAPA P1 di 6e)
    existing_findings = set((await session.scalars(
        select(Finding.item_id).where(Finding.session_id == sess.id)
    )).all())
    new_findings = []
    for it in agg.all_items:
        if it.missing or it.is_na or it.ratio != 0.0 or it.item_id in existing_findings:
            continue
        new_findings.append(Finding(
            session_id=sess.id,
            item_id=it.item_id,
            hotel_id=sess.hotel_id,
            is_life_safety=it.is_life_safety,
            severity="CRITICAL" if it.is_life_safety else "MAJOR",
            title=it.question_text or it.code,
        ))
    session.add_all(new_findings)
    await session.flush()  # ambil finding.id untuk auto-CAPA (6e)

    # auto-CAPA trigger (PRD-F-03): tiap finding gagal → CapaTicket SLA berjenjang
    from app.services.capa_trigger import auto_create_capa_ticket

    for finding in new_findings:
        await auto_create_capa_ticket(
            session, finding, actor_id=current.id, department=sess.department
        )
    await session.commit()
    await session.refresh(sess)
    return Envelope(data=AuditSessionOut.model_validate(sess))


@router.post("/sessions/sync", response_model=Envelope[SyncPushResult])
async def sync_push(
    payload: SyncPushRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[SyncPushResult]:
    sess = await session.scalar(
        select(AuditSession).where(AuditSession.client_id == payload.session_client_id)
    )
    if sess is None:
        raise HTTPException(404, "Sesi dengan client_id tsb tidak ada di server (buat dulu via POST /audit/sessions)")
    await _require_run(session, current, sess.hotel_id)
    items = await _template_items(session, sess.template_id)
    upserted, conflicts, conflict_ids = await _upsert_scores(
        session, sess, items, payload.scores, current
    )
    if conflicts:
        sess.sync_status = "PENDING_CONFLICT"
        sess.updated_by = current.id
    await session.commit()
    return Envelope(data=SyncPushResult(
        session_id=sess.uuid, upserted=upserted, conflicts=conflicts,
        conflict_ids=conflict_ids, server_now=datetime.now(UTC),
    ))


@router.get("/sessions/{id}/sync", response_model=Envelope[dict])
async def sync_pull(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
    since: datetime,
) -> Envelope[dict]:
    sess = await _get_session(session, id)
    await _require_read(session, current, sess.hotel_id)
    scores = (await session.scalars(
        select(AuditItemScore).where(
            AuditItemScore.session_id == sess.id, AuditItemScore.updated_at > since
        ).order_by(AuditItemScore.updated_at.asc())
    )).all()
    media = (await session.scalars(
        select(AuditMedia).where(
            AuditMedia.session_id == sess.id, AuditMedia.captured_at > since
        )
    )).all()
    return Envelope(data={
        "scores": [{"id": r.uuid, "item_id": r.item.uuid if r.item else None,
                    "room_ref": r.room_ref,
                    "value": r.value, "score": r.score, "is_na": r.is_na,
                    "note": r.note, "scored_by": r.scored_by_user.uuid if r.scored_by_user else None,
                    "updated_at": r.updated_at}
                   for r in scores],
        "media_status": [{"id": m.uuid, "phase": m.phase, "object_key": m.object_key,
                          "upload_status": m.upload_status} for m in media],
        "server_now": datetime.now(UTC),
    })


@router.get("/sessions/{id}/conflicts", response_model=Envelope[list[SyncConflictOut]])
async def list_conflicts(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[list[SyncConflictOut]]:
    sess = await _get_session(session, id)
    await _require_read(session, current, sess.hotel_id)
    rows = (await session.scalars(
        select(SyncConflictLog).where(
            SyncConflictLog.session_id == sess.id, SyncConflictLog.resolution == "PENDING"
        ).order_by(SyncConflictLog.resolved_at.desc())
    )).all()
    return Envelope(data=[SyncConflictOut.model_validate(r) for r in rows])


@router.post("/sessions/{id}/conflicts/{conflictId}/resolve", response_model=Envelope[SyncConflictOut])
async def resolve_conflict(
    id: HybridId,
    conflictId: HybridId,
    payload: ResolveConflictRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[SyncConflictOut]:
    sess = await _get_session(session, id)
    if not await _has_hotel_scope(session, current, sess.hotel_id):
        raise HTTPException(403, "Missing scope hotel/region/global")
    conflict = await get_by_uuid(session, SyncConflictLog, conflictId)
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
            score_row.score = await _compute_stored_score(item, payload.winning_value, False)
        except HTTPException:
            raise
    score_row.value = payload.winning_value
    score_row.is_na = False
    score_row.updated_at = datetime.now(UTC)
    score_row.scored_by = current.id

    conflict.winning_value = payload.winning_value
    conflict.resolution = "MANUAL"
    conflict.resolved_at = datetime.now(UTC)
    conflict.resolved_by = current.id

    pending_left = await session.scalar(
        select(func.count(SyncConflictLog.id)).where(
            SyncConflictLog.session_id == sess.id, SyncConflictLog.resolution == "PENDING"
        )
    )
    if (pending_left or 0) == 0:
        sess.sync_status = "SYNCED"
        sess.updated_by = current.id

    await session.commit()
    return Envelope(data=SyncConflictOut.model_validate(conflict))