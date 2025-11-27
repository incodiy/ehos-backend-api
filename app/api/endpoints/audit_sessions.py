"""Audit session core endpoints — openapi.yaml `/audit/sessions` (F-02/F-05)."""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Header, HTTPException, Response
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession
from app.api.security_helpers import perms, user_scoped_to_hotel
from app.core.identity import get_by_uuid
from app.models import (
    AuditItemScore,
    AuditSession,
    ChecklistItem,
    ChecklistSection,
    Finding,
    User,
)
from app.models.master import Hotel
from app.schemas.audit import (
    AuditSessionOut,
    BulkScoreUpsert,
    SessionCreateRequest,
    SessionUpdateRequest,
)
from app.schemas.common import Envelope, HybridId, Paginated, PaginationMeta
from app.services import audit_lifecycle
from app.services.aggregation import AggregationError, aggregate_session
from app.services.pdf_report import render_audit_report
from app.services.report_i18n import load_report_translations
from app.services.verdict import compute_verdict

router = APIRouter()

ALLOWED_STATUS = {"DRAFT", "IN_PROGRESS", "SUBMITTED", "PUBLISHED"}


# ─── RBAC Helpers ─────────────────────────────────────────────────────────────

async def _permission_codes(session: AsyncSession, user: User) -> set[str]:
    return await perms(session, user)


async def _is_corporate(session: AsyncSession, user: User) -> bool:
    return "audit:read:global" in await perms(session, user)


async def _has_hotel_scope(
    session: AsyncSession, user: User, hotel_id: int | HybridId | None
) -> bool:
    user_perms = await perms(session, user)
    if "audit:read:global" in user_perms:
        return True
    hotel = await get_by_uuid(session, Hotel, hotel_id)
    if hotel is None:
        return False
    return await user_scoped_to_hotel(session, user, hotel.uuid)


async def _require_read(session: AsyncSession, user: User, hotel_internal_id: int | None = None) -> None:
    user_perms = await perms(session, user)
    if hotel_internal_id is None:
        if "audit:read:global" not in user_perms:
            raise HTTPException(403, "Missing permission: audit:read:global (list tanpa filter hotel)")
        return
    if "audit:read:global" in user_perms:
        return
    hotel = await session.get(Hotel, hotel_internal_id)
    if hotel and await user_scoped_to_hotel(session, user, hotel.uuid):
        return
    raise HTTPException(403, "Missing permission: audit scope hotel/region/global")


async def _require_write(session: AsyncSession, user: User, hotel_internal_id: int) -> None:
    user_perms = await perms(session, user)
    if "audit:read:global" in user_perms or "audit:create" in user_perms or "audit:score" in user_perms:
        return
    hotel = await session.get(Hotel, hotel_internal_id)
    if hotel and await user_scoped_to_hotel(session, user, hotel.uuid):
        if "audit:run:hotel" in user_perms:
            return
    raise HTTPException(403, "Missing permission: audit:run:hotel")


async def _require_publish(session: AsyncSession, user: User) -> None:
    user_perms = await perms(session, user)
    if "audit:publish" in user_perms or "audit:read:global" in user_perms or user.is_superuser:
        return
    raise HTTPException(403, "Missing permission: audit:read:global (publish = corporate QA)")


async def _get_session(session: AsyncSession, session_id: HybridId) -> AuditSession:
    s = await get_by_uuid(session, AuditSession, session_id)
    if s is None:
        raise HTTPException(404, "Sesi audit tidak ditemukan")
    return s


def _normalize_breakdown(
    raw: dict | list | None, section_names: dict[str, str]
) -> list[dict]:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    out: list[dict] = []
    for code, info in raw.items():
        data = info if isinstance(info, dict) else {}
        out.append({
            "section_code": code,
            "section_name": section_names.get(code, code),
            "score": data.get("score"),
            "max": data.get("max"),
            "pct": data.get("pct"),
            "items_count": data.get("items", data.get("count")),
        })
    return out


# ─── Endpoints ────────────────────────────────────────────────────────────────

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
    """Daftar sesi audit dengan isolasi tenant otomatis (Constraint A2)."""
    user_perms = await perms(session, current)
    stmt = select(AuditSession)
    count_stmt = select(func.count(AuditSession.id))

    if hotel_id is not None:
        hotel_row = await get_by_uuid(session, Hotel, hotel_id)
        if hotel_row is None:
            raise HTTPException(404, "Hotel tidak ditemukan")
        await _require_read(session, current, hotel_row.id)
        stmt = stmt.where(AuditSession.hotel_id == hotel_row.id)
        count_stmt = count_stmt.where(AuditSession.hotel_id == hotel_row.id)
    else:
        if "audit:read:global" not in user_perms:
            raise HTTPException(403, "Missing permission: audit:read:global (list tanpa filter hotel)")

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
    rows = (
        await session.scalars(
            stmt.order_by(AuditSession.date_start.desc(), AuditSession.created_at.desc())
            .offset((max(1, page) - 1) * per_page)
            .limit(per_page)
        )
    ).all()

    return Paginated(
        data=[AuditSessionOut.model_validate(r) for r in rows],
        meta=PaginationMeta(
            current_page=max(1, page),
            per_page=per_page,
            total=total or 0,
            last_page=last_page,
        ),
    )


@router.post("/sessions", response_model=Envelope[AuditSessionOut], status_code=201)
async def create_session(
    payload: SessionCreateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[AuditSessionOut]:
    """Buat sesi audit baru — bind snapshot template terkunci (B3)."""
    hotel = await get_by_uuid(session, Hotel, payload.hotel_id)
    if hotel is None:
        raise HTTPException(404, "Hotel tidak ditemukan")
    await _require_write(session, current, hotel.id)

    new_session = await audit_lifecycle.create_session(session, payload, current)
    return Envelope(data=AuditSessionOut.model_validate(new_session))


@router.get("/sessions/{id}", response_model=Envelope[dict])
async def get_session_detail(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[dict]:
    """Detail sesi audit + breakdown departemen + skor butir + temuan."""
    sess = await _get_session(session, id)
    await _require_read(session, current, sess.hotel_id)

    scores = (
        await session.scalars(
            select(AuditItemScore)
            .where(AuditItemScore.session_id == sess.id)
            .order_by(AuditItemScore.updated_at.asc())
        )
    ).all()
    findings = (
        await session.scalars(select(Finding).where(Finding.session_id == sess.id))
    ).all()

    section_names = {
        code: name
        for code, name in (
            await session.execute(select(ChecklistSection.code, ChecklistSection.name))
        ).all()
    }
    breakdown = _normalize_breakdown(sess.department_breakdown, section_names)

    if not breakdown and scores:
        sec_map: dict[str, dict] = {}
        for r in scores:
            if not r.item or not r.item.section:
                continue
            code = r.item.section.code
            name = r.item.section.name or section_names.get(code, code)
            if code not in sec_map:
                sec_map[code] = {
                    "section_code": code,
                    "section_name": name,
                    "score": 0.0,
                    "max": 0.0,
                    "items_count": 0,
                }
            sec_map[code]["items_count"] += 1
            if not r.is_na:
                item_max = float(r.item.max_score or 90)
                sec_map[code]["max"] += item_max
                if r.score is not None:
                    sec_map[code]["score"] += float(r.score)

        breakdown = []
        for code, data in sec_map.items():
            max_val = data["max"]
            pct = round((data["score"] / max_val) * 100, 1) if max_val > 0 else 100.0
            breakdown.append({
                "section_code": data["section_code"],
                "section_name": data["section_name"],
                "score": round(data["score"], 1),
                "max": round(data["max"], 1),
                "pct": pct,
                "items_count": data["items_count"],
            })

    return Envelope(
        data={
            "session": AuditSessionOut.model_validate(sess),
            "department_breakdown": breakdown,
            "items": [
                {
                    "id": r.uuid,
                    "session_id": sess.uuid,
                    "item_id": r.item.uuid if r.item else None,
                    "room_ref": r.room_ref,
                    "value": r.value,
                    "score": r.score,
                    "is_na": r.is_na,
                    "note": r.note,
                    "scored_by": r.scored_by_user.uuid if r.scored_by_user else None,
                    "updated_at": r.updated_at,
                    "code": r.item.code if r.item else None,
                    "question_text": r.item.question_text if r.item else None,
                    "rubric_type": r.item.rubric_type if r.item else None,
                    "max_score": r.item.max_score if r.item else None,
                    "is_life_safety": r.item.is_life_safety if r.item else None,
                    "sort_order": r.item.sort_order if r.item else None,
                    "section_code": r.item.section.code if r.item and r.item.section else None,
                    "section_name": r.item.section.name if r.item and r.item.section else None,
                }
                for r in scores
            ],
            "findings": [
                {
                    "id": f.uuid,
                    "session_id": sess.uuid,
                    "item_id": f.item.uuid if f.item else None,
                    "item_code": f.item.code if f.item else None,
                    "is_life_safety": f.is_life_safety,
                    "severity": f.severity,
                    "title": f.title,
                    "description": f.description,
                    "location": f.location,
                }
                for f in findings
            ],
        }
    )


@router.patch("/sessions/{id}", response_model=Envelope[AuditSessionOut])
async def update_session(
    id: HybridId,
    payload: SessionUpdateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[AuditSessionOut]:
    """Update sesi audit (hanya status DRAFT)."""
    sess = await _get_session(session, id)
    await _require_write(session, current, sess.hotel_id)

    updated = await audit_lifecycle.update_session(session, sess, payload, current)
    return Envelope(data=AuditSessionOut.model_validate(updated))


@router.delete("/sessions/{id}", response_model=Envelope[dict])
async def delete_session(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[dict]:
    """Batalkan / hapus sesi audit (hanya status DRAFT)."""
    sess = await _get_session(session, id)
    await _require_write(session, current, sess.hotel_id)

    await audit_lifecycle.delete_session(session, sess, current)
    return Envelope(data={"success": True, "message": "Sesi audit berhasil dihapus"})


@router.get("/sessions/{id}/report.pdf")
async def get_session_report(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
    response: Response,
    accept_language: str | None = Header(default=None),
) -> Response:
    """Ekspor laporan audit PDF (F-02), localized via Accept-Language (F-22)."""
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

    findings_rows = (
        await session.execute(
            select(Finding, ChecklistItem.code)
            .join(ChecklistItem, ChecklistItem.id == Finding.item_id)
            .where(Finding.session_id == sess.id)
            .order_by(Finding.is_life_safety.desc(), Finding.severity)
        )
    ).all()
    capa_count, p1_count = (
        await session.execute(
            text(
                "SELECT count(*), count(*) FILTER (WHERE ct.priority = 1) "
                "FROM capa_tickets ct JOIN findings f ON f.id = ct.finding_id "
                "WHERE f.session_id = :s"
            ),
            {"s": sess.id},
        )
    ).one()

    item_uuids = {i.id: i.uuid for i in (await session.scalars(select(ChecklistItem))).all()}
    section_uuids = {
        s.id: s.uuid for s in (await session.scalars(select(ChecklistSection))).all()
    }

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
            "published_at": (
                sess.published_at.strftime("%d-%m-%Y %H:%M")
                if sess.published_at
                else "-"
            ),
            "status": sess.status,
        },
        "score": {
            "total_score": (
                f"{agg.total_score:.1f}" if agg.total_score is not None else None
            ),
            "pass_fail": verdict.pass_fail,
            "items_scored": agg.items_scored,
            "items_missed": agg.items_missed,
            "items_na": agg.items_na,
        },
        "hazards": [
            {
                "item_id": str(item_uuids.get(h.item_id, h.item_id)),
                "code": h.code,
                "question_text": h.question_text,
            }
            for h in verdict.hazards
        ],
        "sections": [
            {
                "section_id": str(section_uuids.get(s.section_id, s.section_id)),
                "code": s.code,
                "name": s.name,
                "ratio": s.ratio,
                "achieved_points": s.achieved_points,
                "max_points": s.max_points,
                "items_scored": s.items_scored,
            }
            for s in agg.sections
        ],
        "findings": [
            {
                "item_id": str(item_uuids.get(finding.item_id, finding.item_id)),
                "code": f_code,
                "question_text": finding.title,
                "severity": finding.severity,
                "is_life_safety": finding.is_life_safety,
            }
            for finding, f_code in findings_rows
        ],
        "capa": {"count": capa_count, "p1_count": p1_count},
        "items": [
            {
                "item_id": str(item_uuids.get(it.item_id, it.item_id)),
                "code": it.code,
                "question_text": it.question_text,
                "rubric_type": it.rubric_type,
                "ratio": it.ratio,
                "achieved": it.achieved,
                "max_score": it.max_score,
                "is_na": it.is_na,
                "missing": it.missing,
                "is_life_safety": it.is_life_safety,
            }
            for it in agg.all_items
        ],
    }

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
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/sessions/{id}/items", response_model=Envelope[dict])
async def bulk_upsert_scores(
    id: HybridId,
    payload: BulkScoreUpsert,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[dict]:
    """Bulk upsert nilai butir audit (online) -> transisi otomatis DRAFT ke IN_PROGRESS."""
    sess = await _get_session(session, id)
    await _require_write(session, current, sess.hotel_id)

    upserted, conflicts, _ = await audit_lifecycle.record_scores(
        session, sess, payload.scores, current
    )
    await session.commit()
    return Envelope(data={"upserted": upserted, "conflicts": conflicts})


@router.post("/sessions/{id}/submit", response_model=Envelope[AuditSessionOut])
async def submit_session(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[AuditSessionOut]:
    """Auditor selesai input -> SUBMITTED (mengunci mutasi skor)."""
    sess = await _get_session(session, id)
    await _require_write(session, current, sess.hotel_id)

    sess = await audit_lifecycle.submit_session(session, sess, current)
    return Envelope(data=AuditSessionOut.model_validate(sess))


@router.post("/sessions/{id}/reopen", response_model=Envelope[AuditSessionOut])
async def reopen_session(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[AuditSessionOut]:
    """Corporate QA mengembalikan sesi SUBMITTED ke IN_PROGRESS bila butuh perbaikan."""
    sess = await _get_session(session, id)
    await _require_publish(session, current)

    sess = await audit_lifecycle.reopen_session(session, sess, current)
    return Envelope(data=AuditSessionOut.model_validate(sess))


@router.post("/sessions/{id}/publish", response_model=Envelope[AuditSessionOut])
async def publish_session(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[AuditSessionOut]:
    """Finalisasi scoring, komputasi PASS/FAIL, create findings, trigger CAPA."""
    sess = await _get_session(session, id)
    await _require_publish(session, current)

    sess = await audit_lifecycle.publish_session(session, sess, current)
    return Envelope(data=AuditSessionOut.model_validate(sess))
