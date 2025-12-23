"""Audit session core endpoints — openapi.yaml `/audit/sessions` (F-02/F-05)."""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Header, HTTPException, Response
from sqlalchemy import func, select, text
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession
from app.api.security_helpers import perms, user_scoped_to_hotel
from app.core.identity import get_by_uuid
from app.models import (
    AuditItemScore,
    AuditSession,
    ChecklistItem,
    ChecklistSection,
    ChecklistTemplate,
    Finding,
    User,
)
from app.models.master import Hotel
from app.schemas.audit import (
    AuditSessionOut,
    BulkScoreUpsert,
    EnsureDepartmentRequest,
    HotelAuditSummaryOut,
    IntegratedCycleCreateRequest,
    IntegratedCycleCreateResult,
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


@router.post("/sessions/ensure-department", response_model=Envelope[AuditSessionOut])
async def ensure_department_session(
    payload: EnsureDepartmentRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[AuditSessionOut]:
    """Pastikan/buat sesi audit untuk departemen dalam siklus audit yang sama (on-demand activation)."""
    source_sess = await _get_session(session, payload.source_session_id)
    await _require_write(session, current, source_sess.hotel_id)

    # Cek apakah sesi untuk departemen ini sudah ada pada siklus tanggal & tipe yang sama
    existing = await session.scalar(
        select(AuditSession).where(
            AuditSession.hotel_id == source_sess.hotel_id,
            AuditSession.department == payload.department,
            AuditSession.date_start == source_sess.date_start,
            AuditSession.audit_type == source_sess.audit_type,
        )
    )
    if existing is not None:
        return Envelope(data=AuditSessionOut.model_validate(existing))

    # Buat sesi baru untuk departemen yang diminta
    single_req = SessionCreateRequest(
        hotel_id=source_sess.hotel.uuid,
        department=payload.department,
        template_id=payload.template_id,
        audit_type=source_sess.audit_type,
        date_start=source_sess.date_start,
        date_end=source_sess.date_end,
        client_id=None,
    )
    new_sess = await audit_lifecycle.create_session(session, single_req, current)
    # Langsung jadikan IN_PROGRESS jika awalnya DRAFT
    if new_sess.status == "DRAFT":
        new_sess.status = "IN_PROGRESS"
        await session.commit()
        await session.refresh(new_sess)

    return Envelope(data=AuditSessionOut.model_validate(new_sess))


@router.post("/sessions/{id}/activate-all-departments", response_model=Envelope[list[AuditSessionOut]])
async def activate_all_departments(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[list[AuditSessionOut]]:
    """Aktifkan seluruh departemen standar (Security, Kitchen FB, Housekeeping) untuk siklus audit ini."""
    source_sess = await _get_session(session, id)
    await _require_write(session, current, source_sess.hotel_id)

    target_depts = ["SECURITY_RISK", "KITCHEN_FB", "HOUSEKEEPING"]
    results: list[AuditSession] = []

    for dept in target_depts:
        existing = await session.scalar(
            select(AuditSession).where(
                AuditSession.hotel_id == source_sess.hotel_id,
                AuditSession.department == dept,
                AuditSession.date_start == source_sess.date_start,
                AuditSession.audit_type == source_sess.audit_type,
            )
        )
        if existing is not None:
            results.append(existing)
        else:
            single_req = SessionCreateRequest(
                hotel_id=source_sess.hotel.uuid,
                department=dept,
                audit_type=source_sess.audit_type,
                date_start=source_sess.date_start,
                date_end=source_sess.date_end,
                client_id=None,
            )
            try:
                new_s = await audit_lifecycle.create_session(session, single_req, current)
                if new_s.status == "DRAFT":
                    new_s.status = "IN_PROGRESS"
                    await session.commit()
                    await session.refresh(new_s)
                results.append(new_s)
            except Exception:
                continue

    return Envelope(data=[AuditSessionOut.model_validate(s) for s in results])


@router.post(
    "/sessions/integrated-cycle",
    response_model=Envelope[IntegratedCycleCreateResult],
    status_code=201,
)
async def create_integrated_cycle(
    payload: IntegratedCycleCreateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[IntegratedCycleCreateResult]:
    """Buat siklus audit terpadu mencakup 3 departemen (Security, Kitchen FB, Housekeeping)."""
    hotel = await get_by_uuid(session, Hotel, payload.hotel_id)
    if hotel is None:
        raise HTTPException(404, "Hotel tidak ditemukan")
    await _require_write(session, current, hotel.id)

    if payload.date_start and payload.date_end and payload.date_end < payload.date_start:
        raise HTTPException(422, "Tanggal selesai tidak boleh lebih awal dari tanggal mulai")

    depts = payload.departments or ["SECURITY_RISK", "KITCHEN_FB", "HOUSEKEEPING"]
    valid_depts = [d for d in depts if d in {"SECURITY_RISK", "KITCHEN_FB", "HOUSEKEEPING", "GM"}]
    if not valid_depts:
        valid_depts = ["SECURITY_RISK", "KITCHEN_FB", "HOUSEKEEPING"]

    created: list[AuditSession] = []
    for dept in valid_depts:
        single_req = SessionCreateRequest(
            hotel_id=payload.hotel_id,
            department=dept,
            audit_type=payload.audit_type,
            date_start=payload.date_start,
            date_end=payload.date_end,
            client_id=None,
        )
        try:
            s_row = await audit_lifecycle.create_session(session, single_req, current)
            created.append(s_row)
        except HTTPException as e:
            if not created and dept == valid_depts[-1]:
                raise e
            continue

    if not created:
        raise HTTPException(422, "Gagal membuat sesi audit terpadu untuk departemen yang dipilih")

    primary_id = created[0].uuid
    return Envelope(
        data=IntegratedCycleCreateResult(
            primary_session_id=primary_id,
            created_sessions=[AuditSessionOut.model_validate(s) for s in created],
            hotel_id=hotel.uuid,
            date_start=payload.date_start,
            date_end=payload.date_end,
            audit_type=payload.audit_type,
        )
    )



MONTH_NAMES_ID = {
    1: "Januari", 2: "Februari", 3: "Maret", 4: "April",
    5: "Mei", 6: "Juni", 7: "Juli", 8: "Agustus",
    9: "September", 10: "Oktober", 11: "November", 12: "Desember"
}


@router.get("/sessions/hotel-summaries", response_model=Envelope[list[HotelAuditSummaryOut]])
async def list_hotel_audit_summaries(
    current: CurrentUser,
    session: DbSession,
    hotel_id: HybridId | None = None,
    department: str | None = None,
    status: str | None = None,
    year: int | None = None,
) -> Envelope[list[HotelAuditSummaryOut]]:
    """Daftar ringkasan audit per properti hotel untuk nested table (A2)."""
    user_perms = await perms(session, current)
    stmt = (
        select(AuditSession)
        .options(
            selectinload(AuditSession.hotel).selectinload(Hotel.brand),
            selectinload(AuditSession.auditor),
        )
    )

    if hotel_id is not None:
        hotel_row = await get_by_uuid(session, Hotel, hotel_id)
        if hotel_row is None:
            raise HTTPException(404, "Hotel tidak ditemukan")
        await _require_read(session, current, hotel_row.id)
        stmt = stmt.where(AuditSession.hotel_id == hotel_row.id)
    else:
        if "audit:read:global" not in user_perms:
            raise HTTPException(403, "Missing permission: audit:read:global (list tanpa filter hotel)")

    if department:
        stmt = stmt.where(AuditSession.department == department)
    if status:
        if status not in ALLOWED_STATUS:
            raise HTTPException(422, f"status tidak dikenal: {status}")
        stmt = stmt.where(AuditSession.status == status)
    if year:
        stmt = stmt.where(func.extract("year", AuditSession.date_start) == year)

    rows = (
        await session.scalars(
            stmt.order_by(AuditSession.date_start.desc(), AuditSession.created_at.desc())
        )
    ).all()

    # Group by hotel
    hotels_map: dict[int, dict] = {}
    for sess in rows:
        h = sess.hotel
        if not h:
            continue
        if h.id not in hotels_map:
            hotels_map[h.id] = {
                "hotel_id": h.uuid,
                "hotel_code": h.code,
                "hotel_name": h.name,
                "hotel_image_url": h.image_url,
                "brand_tier": h.brand.tier if h.brand else "MIDSCALE",
                "city": h.city or "Indonesia",
                "raw_sessions": [],
            }
        hotels_map[h.id]["raw_sessions"].append(sess)

    results: list[HotelAuditSummaryOut] = []
    for hid, hdata in hotels_map.items():
        periods_map: dict[str, dict] = {}
        for sess in hdata["raw_sessions"]:
            ds = sess.date_start or date(2026, 1, 1)
            de = sess.date_end
            pkey = f"{ds.isoformat()}_{de.isoformat() if de else 'none'}"
            if pkey not in periods_map:
                periods_map[pkey] = {
                    "year": ds.year,
                    "month_num": ds.month,
                    "month_name": MONTH_NAMES_ID.get(ds.month, str(ds.month)),
                    "date_start": ds.isoformat(),
                    "date_end": de.isoformat() if de else None,
                    "department_scores": {},
                    "sessions": [],
                    "auditors": set(),
                    "statuses": [],
                }
            p = periods_map[pkey]
            p["sessions"].append(sess)
            p["statuses"].append(sess.status)
            if sess.auditor and sess.auditor.name:
                p["auditors"].add(sess.auditor.name)

            score_val = float(sess.total_score) if sess.total_score is not None else None
            p["department_scores"][sess.department] = {
                "session_id": sess.uuid,
                "score": score_val,
                "status": sess.status,
                "pass_fail": sess.pass_fail,
            }

        periods_list: list[dict] = []
        cumulative_scores: list[float] = []
        for pkey, p in periods_map.items():
            st_set = set(p["statuses"])
            if "IN_PROGRESS" in st_set:
                p_status = "IN_PROGRESS"
            elif "DRAFT" in st_set and len(st_set) == 1:
                p_status = "DRAFT"
            elif "SUBMITTED" in st_set:
                p_status = "SUBMITTED"
            elif "PUBLISHED" in st_set:
                p_status = "PUBLISHED"
            else:
                p_status = p["statuses"][0] if p["statuses"] else "DRAFT"

            valid_scores = [v["score"] for v in p["department_scores"].values() if v["score"] is not None]
            avg_score = round(sum(valid_scores) / len(valid_scores), 2) if valid_scores else None
            if avg_score is not None:
                cumulative_scores.append(avg_score)

            prim_sess = p["sessions"][0]
            for s_cand in p["sessions"]:
                if s_cand.status in ("PUBLISHED", "IN_PROGRESS"):
                    prim_sess = s_cand
                    break

            periods_list.append({
                "period_id": pkey,
                "year": p["year"],
                "month_num": p["month_num"],
                "month_name": p["month_name"],
                "date_start": p["date_start"],
                "date_end": p["date_end"],
                "status": p_status,
                "average_score": avg_score,
                "department_scores": p["department_scores"],
                "primary_session_id": prim_sess.uuid,
                "auditor_name": ", ".join(sorted(p["auditors"])) if p["auditors"] else "—",
            })

        periods_list.sort(key=lambda x: x["date_start"], reverse=True)
        latest_p = periods_list[0] if periods_list else None
        cum_avg = round(sum(cumulative_scores) / len(cumulative_scores), 2) if cumulative_scores else None

        results.append(
            HotelAuditSummaryOut(
                hotel_id=hdata["hotel_id"],
                hotel_code=hdata["hotel_code"],
                hotel_name=hdata["hotel_name"],
                hotel_image_url=hdata["hotel_image_url"],
                brand_tier=hdata["brand_tier"],
                city=hdata["city"],
                total_periods=len(periods_list),
                cumulative_average_score=cum_avg,
                last_audit_period={
                    "year": latest_p["year"],
                    "month_num": latest_p["month_num"],
                    "month_name": latest_p["month_name"],
                    "date_start": latest_p["date_start"],
                    "date_end": latest_p["date_end"],
                } if latest_p else None,
                last_status=latest_p["status"] if latest_p else "DRAFT",
                periods=periods_list,
            )
        )

    results.sort(
        key=lambda h: (h.last_audit_period.date_start if h.last_audit_period else "1970-01-01"),
        reverse=True,
    )

    return Envelope(data=results)


@router.get("/sessions/dashboard-breakdowns", response_model=Envelope[dict])
async def get_dashboard_breakdowns(
    current: CurrentUser,
    session: DbSession,
    hotel_id: HybridId | None = None,
    year: int | None = None,
) -> Envelope[dict]:
    """Breakdown ringkasan 4 departemen/seksi audit untuk Dashboard Overview."""
    user_perms = await perms(session, current)
    hotel_info = None


    if hotel_id is not None:
        hotel_row = await get_by_uuid(session, Hotel, hotel_id)
        if hotel_row is None:
            raise HTTPException(404, "Hotel tidak ditemukan")
        await _require_read(session, current, hotel_row.id)
        hotel_info = {
            "id": str(hotel_row.uuid),
            "code": hotel_row.code,
            "name": hotel_row.name,
            "region": hotel_row.region.name if hotel_row.region else None,
            "city": hotel_row.city,
        }
    else:
        if "audit:read:global" not in user_perms:
            raise HTTPException(403, "Missing permission: audit:read:global (list tanpa filter hotel)")

    stmt_scores = (
        select(AuditItemScore, AuditSession.department)
        .join(AuditSession, AuditItemScore.session_id == AuditSession.id)
        .options(
            selectinload(AuditItemScore.item).selectinload(ChecklistItem.section),
        )
    )

    if hotel_id is not None:
        stmt_scores = stmt_scores.where(AuditSession.hotel_id == hotel_row.id)

    if year:
        stmt_scores = stmt_scores.where(func.extract("year", AuditSession.date_start) == year)

    raw_scores = (await session.execute(stmt_scores)).all()

    # 1. SECURITY BREAKDOWN
    sec_sections_map: dict[str, dict] = {}
    sec_total_achieved = 0.0
    sec_total_max = 0.0

    # 2. KITCHEN FB BREAKDOWN
    kfb_sections_map: dict[str, dict] = {}

    # 3. HOUSEKEEPING GENERAL BREAKDOWN
    hk_sections_map: dict[str, dict] = {}

    # 4. HOUSEKEEPING ROOM CHECK BREAKDOWN
    rc_categories_map: dict[str, dict] = {}

    for sc, dept in raw_scores:
        if sc.is_na or not sc.item:
            continue
        sec_name = sc.item.section.name if sc.item.section else "General"
        sec_code = sc.item.section.code if sc.item.section else "SEC"
        item_score = float(sc.score) if sc.score is not None else (1.0 if sc.value == "YES" else 0.0)
        max_sc = float(sc.item.max_score or 1.0)
        if max_sc <= 0:
            max_sc = 1.0

        if dept == "SECURITY_RISK":
            if sec_name not in sec_sections_map:
                sec_sections_map[sec_name] = {
                    "name": sec_name,
                    "code": sec_code,
                    "achieved": 0.0,
                    "max": 0.0,
                    "items_count": 0,
                }
            sec_sections_map[sec_name]["achieved"] += item_score
            sec_sections_map[sec_name]["max"] += max_sc
            sec_sections_map[sec_name]["items_count"] += 1
            sec_total_achieved += item_score
            sec_total_max += max_sc

        elif dept == "KITCHEN_FB":
            if sec_name not in kfb_sections_map:
                kfb_sections_map[sec_name] = {
                    "name": sec_name,
                    "code": sec_code,
                    "yes_count": 0,
                    "total_count": 0,
                }
            if sc.value == "YES" or item_score > 0:
                kfb_sections_map[sec_name]["yes_count"] += 1
            kfb_sections_map[sec_name]["total_count"] += 1

        elif dept == "HOUSEKEEPING":
            if sc.room_ref:
                # Room Check Physical
                if sec_name not in rc_categories_map:
                    rc_categories_map[sec_name] = {
                        "name": sec_name,
                        "achieved": 0.0,
                        "max": 0.0,
                        "count": 0,
                    }
                rc_categories_map[sec_name]["achieved"] += item_score
                rc_categories_map[sec_name]["max"] += max_sc
                rc_categories_map[sec_name]["count"] += 1
            else:
                # HK General Operations
                if sec_name not in hk_sections_map:
                    hk_sections_map[sec_name] = {
                        "name": sec_name,
                        "code": sec_code,
                        "achieved": 0.0,
                        "max": 0.0,
                        "count": 0,
                    }
                hk_sections_map[sec_name]["achieved"] += item_score
                hk_sections_map[sec_name]["max"] += max_sc
                hk_sections_map[sec_name]["count"] += 1


    # Canonical Section Standards
    CANONICAL_SEC = [
        {"code": "SEC-A", "name": "MANAGEMENT"},
        {"code": "SEC-B", "name": "FIRE SYSTEM MANAGEMENT"},
        {"code": "SEC-C", "name": "SECURITY SYSTEMS AND EQUIPMENT"},
        {"code": "SEC-D", "name": "KEY"},
        {"code": "SEC-E", "name": "LIGHTING AND EMERGENCY POWER SUPPLY"},
        {"code": "SEC-F", "name": "IDENTITY PROTECTION AND GUEST PRIVACY"},
        {"code": "SEC-G", "name": "GUARDING / SECURITY STAFFING"},
        {"code": "SEC-H", "name": "INFOSEC - INTERNET/WIFI/SERVER"},
        {"code": "SEC-I", "name": "HEALTH MEDICAL & LIFE SAFETY"},
        {"code": "SEC-J", "name": "GUEST ROOM SECURITY"},
        {"code": "SEC-K", "name": "LIFT SAFETY"},
        {"code": "SEC-L", "name": "NIGHT PROCEDURE"},
        {"code": "SEC-M", "name": "WATER SAFETY"},
        {"code": "SEC-N", "name": "ELECTIRICTY & GAS SAFETY"},
    ]

    CANONICAL_KFB = [
        {"code": "KFB-A", "name": "BUFFET B'FAST / A'LA CARTE SET UP"},
        {"code": "KFB-B", "name": "RESTAURANT & BAR CLEANLINESS"},
        {"code": "KFB-C", "name": "REST. & BAR MAINTENANCE & CONDITION"},
        {"code": "KFB-D", "name": "KITCHEN STORAGE"},
        {"code": "KFB-E", "name": "KITCHEN CLEANING"},
        {"code": "KFB-F", "name": "KITCHEN MAINTENANCE"},
        {"code": "KFB-G", "name": "KITCHEN PEST CONTROL"},
        {"code": "KFB-H", "name": "KITCHEN WASTE"},
        {"code": "KFB-I", "name": "KITCHEN HYGIENE & FOOD HANDLING"},
    ]

    CANONICAL_HK = [
        {"code": "HK-A", "name": "Organisation & Administration of the department"},
        {"code": "HK-B", "name": "Room cleanliness"},
        {"code": "HK-C", "name": "Public Area and Back of House"},
        {"code": "HK-D", "name": "Staff : grooming, training and communication"},
        {"code": "HK-E", "name": "Control of expenses, trolleys, stores & pantries"},
        {"code": "HK-F", "name": "Control and maintenance of linen"},
        {"code": "HK-G", "name": "Control and maintenance of uniforms"},
        {"code": "HK-H", "name": "Preventive Maintenance"},
    ]

    CANONICAL_RC = [
        {"code": "RC-Z01", "name": "Door/ Entrance", "default_max": 7.0},
        {"code": "RC-Z02", "name": "Wardrobe and content", "default_max": 7.0},
        {"code": "RC-Z03", "name": "Coffe / tea facilities", "default_max": 4.5},
        {"code": "RC-Z04", "name": "Desk top and drawers", "default_max": 8.5},
        {"code": "RC-Z05", "name": "Window", "default_max": 1.5},
        {"code": "RC-Z06", "name": "Bedside tables", "default_max": 3.0},
        {"code": "RC-Z07", "name": "Bed", "default_max": 3.0},
        {"code": "RC-Z08", "name": "General Bedroom", "default_max": 7.0},
        {"code": "RC-Z09", "name": "Bathroom", "default_max": 20.0},
    ]

    # Security output formatting - Merge with 14 canonical sections
    sec_sections_list = []
    used_sec_keys = set()
    for item in CANONICAL_SEC:
        c_code, c_name = item["code"], item["name"]
        match = sec_sections_map.get(c_name) or sec_sections_map.get(c_code)
        if match:
            used_sec_keys.add(c_name)
            pct = round((match["achieved"] / match["max"] * 100), 1) if match["max"] > 0 else 100.0
            sec_sections_list.append({
                "name": c_name,
                "code": c_code,
                "score": round(match["achieved"], 1),
                "max": round(match["max"], 1),
                "pct": pct,
            })
        else:
            sec_sections_list.append({
                "name": c_name,
                "code": c_code,
                "score": 0.0,
                "max": 0.0,
                "pct": 0.0,
            })
    for k, v in sec_sections_map.items():
        if k not in used_sec_keys and not any(s["code"] == v.get("code") for s in sec_sections_list):
            pct = round((v["achieved"] / v["max"] * 100), 1) if v["max"] > 0 else 100.0
            sec_sections_list.append({
                "name": v["name"],
                "code": v["code"],
                "score": round(v["achieved"], 1),
                "max": round(v["max"], 1),
                "pct": pct,
            })
    sec_overall_pct = round((sec_total_achieved / sec_total_max * 100), 1) if sec_total_max > 0 else 0.0

    # Kitchen FB output formatting - Merge with 9 canonical sections
    kfb_sections_list = []
    kfb_total_yes = 0
    kfb_total_all = 0
    used_kfb_keys = set()
    for item in CANONICAL_KFB:
        c_code, c_name = item["code"], item["name"]
        match = kfb_sections_map.get(c_name) or kfb_sections_map.get(c_code)
        if match:
            used_kfb_keys.add(c_name)
            pct = round((match["yes_count"] / match["total_count"] * 100), 1) if match["total_count"] > 0 else 100.0
            kfb_sections_list.append({
                "name": c_name,
                "code": c_code,
                "yes_count": match["yes_count"],
                "total_count": match["total_count"],
                "pct": pct,
            })
            kfb_total_yes += match["yes_count"]
            kfb_total_all += match["total_count"]
        else:
            kfb_sections_list.append({
                "name": c_name,
                "code": c_code,
                "yes_count": 0,
                "total_count": 0,
                "pct": 0.0,
            })
    for k, v in kfb_sections_map.items():
        if k not in used_kfb_keys and not any(s["code"] == v.get("code") for s in kfb_sections_list):
            pct = round((v["yes_count"] / v["total_count"] * 100), 1) if v["total_count"] > 0 else 100.0
            kfb_sections_list.append({
                "name": v["name"],
                "code": v["code"],
                "yes_count": v["yes_count"],
                "total_count": v["total_count"],
                "pct": pct,
            })
            kfb_total_yes += v["yes_count"]
            kfb_total_all += v["total_count"]
    kfb_overall_pct = round((kfb_total_yes / kfb_total_all * 100), 1) if kfb_total_all > 0 else 0.0

    # Housekeeping General output formatting - Merge with 8 canonical sections
    hk_sections_list = []
    hk_total_achieved = 0.0
    hk_total_max = 0.0
    used_hk_keys = set()
    for item in CANONICAL_HK:
        c_code, c_name = item["code"], item["name"]
        match = hk_sections_map.get(c_name) or hk_sections_map.get(c_code)
        if match:
            used_hk_keys.add(c_name)
            pct = round((match["achieved"] / match["max"] * 100), 1) if match["max"] > 0 else 100.0
            hk_sections_list.append({
                "name": c_name,
                "code": c_code,
                "achieved": round(match["achieved"], 1),
                "max": round(match["max"], 1),
                "pct": pct,
            })
            hk_total_achieved += match["achieved"]
            hk_total_max += match["max"]
        else:
            hk_sections_list.append({
                "name": c_name,
                "code": c_code,
                "achieved": 0.0,
                "max": 0.0,
                "pct": 0.0,
            })
    for k, v in hk_sections_map.items():
        if k not in used_hk_keys and not any(s["code"] == v.get("code") for s in hk_sections_list):
            pct = round((v["achieved"] / v["max"] * 100), 1) if v["max"] > 0 else 100.0
            hk_sections_list.append({
                "name": v["name"],
                "code": v["code"],
                "achieved": round(v["achieved"], 1),
                "max": round(v["max"], 1),
                "pct": pct,
            })
            hk_total_achieved += v["achieved"]
            hk_total_max += v["max"]
    hk_overall_pct = round((hk_total_achieved / hk_total_max * 100), 1) if hk_total_max > 0 else 0.0

    # Housekeeping Room Check output formatting - Merge with 9 canonical zones
    rc_categories_list = []
    rc_total_achieved = 0.0
    rc_total_max = 0.0
    used_rc_keys = set()
    for item in CANONICAL_RC:
        c_code, c_name = item["code"], item["name"]
        match = rc_categories_map.get(c_name)
        if match:
            used_rc_keys.add(c_name)
            avg_score = round(match["achieved"] / (match["count"] or 1), 1)
            pct = round((match["achieved"] / match["max"] * 100), 1) if match["max"] > 0 else 100.0
            rc_categories_list.append({
                "name": c_name,
                "code": c_code,
                "score": avg_score,
                "pct": pct,
            })
            rc_total_achieved += match["achieved"]
            rc_total_max += match["max"]
        else:
            rc_categories_list.append({
                "name": c_name,
                "code": c_code,
                "score": 0.0,
                "pct": 0.0,
            })
    for k, v in rc_categories_map.items():
        if k not in used_rc_keys:
            avg_score = round(v["achieved"] / (v["count"] or 1), 1)
            pct = round((v["achieved"] / v["max"] * 100), 1) if v["max"] > 0 else 100.0
            rc_categories_list.append({
                "name": v["name"],
                "score": avg_score,
                "pct": pct,
            })
            rc_total_achieved += v["achieved"]
            rc_total_max += v["max"]
    rc_overall_pct = round((rc_total_achieved / rc_total_max * 100), 1) if rc_total_max > 0 else 0.0

    return Envelope(
        data={
            "hotel_info": hotel_info,
            "security_summary": {
                "overall_score": sec_overall_pct,
                "is_pass": sec_overall_pct >= 80.0,
                "sections": sec_sections_list,
            },
            "kitchen_summary": {
                "overall_score": kfb_overall_pct,
                "is_pass": kfb_overall_pct >= 80.0,
                "sections": kfb_sections_list,
            },
            "housekeeping_section_summary": {
                "overall_score": hk_overall_pct,
                "is_pass": hk_overall_pct >= 80.0,
                "sections": hk_sections_list,
            },
            "housekeeping_room_summary": {
                "total_score": rc_overall_pct,
                "subtotal": rc_overall_pct,
                "categories": rc_categories_list,
            },
        }
    )


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


@router.get("/sessions/{id}/comprehensive", response_model=Envelope[dict])
async def get_session_comprehensive(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[dict]:
    """Detail sesi audit komprehensif 3 departemen (Security, Kitchen FB, Housekeeping + Room Check)."""
    sess = await _get_session(session, id)
    await _require_read(session, current, sess.hotel_id)
    hotel = sess.hotel

    # 1. Temukan sesi saudara (peer sessions) untuk hotel yang sama
    peer_sessions = (
        await session.scalars(
            select(AuditSession).where(
                AuditSession.hotel_id == sess.hotel_id,
                AuditSession.date_start == sess.date_start,
            )
        )
    ).all()

    peer_by_dept = {s.department: s for s in peer_sessions}
    peer_by_dept[sess.department] = sess

    # Helper untuk memuat data template & items & scores
    async def load_dept_worksheet(dept: str, template_name_pattern: str, peer_sess: AuditSession | None):
        tpl = None
        if peer_sess and peer_sess.template and template_name_pattern in peer_sess.template.name:
            tpl = peer_sess.template
        if not tpl:
            tpl = (
                await session.scalars(
                    select(ChecklistTemplate)
                    .where(
                        ChecklistTemplate.department == dept,
                        ChecklistTemplate.name.ilike(f"%{template_name_pattern}%"),
                    )
                    .order_by(ChecklistTemplate.id.desc())
                )
            ).first()
        if not tpl:
            tpl = (
                await session.scalars(
                    select(ChecklistTemplate)
                    .where(ChecklistTemplate.department == dept)
                    .order_by(ChecklistTemplate.id.desc())
                )
            ).first()

        sections_out = []
        items_out = []
        if tpl:
            secs = (
                await session.scalars(
                    select(ChecklistSection)
                    .where(ChecklistSection.template_id == tpl.id)
                    .order_by(ChecklistSection.sort_order.asc(), ChecklistSection.code.asc())
                )
            ).all()

            for sec in secs:
                sections_out.append({
                    "id": str(sec.uuid),
                    "code": sec.code,
                    "name": sec.name,
                    "sort_order": sec.sort_order,
                })
                its = (
                    await session.scalars(
                        select(ChecklistItem)
                        .where(ChecklistItem.section_id == sec.id)
                        .order_by(ChecklistItem.sort_order.asc(), ChecklistItem.code.asc())
                    )
                ).all()
                for it in its:
                    items_out.append({
                        "id": str(it.uuid),
                        "section_id": str(sec.uuid),
                        "section_code": sec.code,
                        "section_name": sec.name,
                        "code": it.code,
                        "question_text": it.question_text,
                        "rubric_type": it.rubric_type,
                        "max_score": float(it.max_score or 0),
                        "weight": float(it.weight or 1),
                        "na_allowed": it.na_allowed,
                        "is_life_safety": it.is_life_safety,
                        "sort_order": it.sort_order,
                    })

        # Load scores jika peer_sess ada
        scores_out = []
        if peer_sess:
            raw_scores = (
                await session.scalars(
                    select(AuditItemScore)
                    .where(AuditItemScore.session_id == peer_sess.id)
                )
            ).all()
            for sc in raw_scores:
                scores_out.append({
                    "id": str(sc.uuid),
                    "item_id": str(sc.item.uuid) if sc.item else None,
                    "item_code": sc.item.code if sc.item else None,
                    "room_ref": sc.room_ref,
                    "value": sc.value,
                    "score": float(sc.score) if sc.score is not None else None,
                    "is_na": sc.is_na,
                    "note": sc.note,
                })

        return {
            "session": AuditSessionOut.model_validate(peer_sess) if peer_sess else None,
            "session_id": str(peer_sess.uuid) if peer_sess else None,
            "template": {
                "id": str(tpl.uuid) if tpl else None,
                "name": tpl.name if tpl else None,
                "version": tpl.version if tpl else None,
                "status": tpl.status if tpl else None,
            } if tpl else None,
            "sections": sections_out,
            "items": items_out,
            "scores": scores_out,
        }

    sec_data = await load_dept_worksheet("SECURITY_RISK", "Security", peer_by_dept.get("SECURITY_RISK"))
    kfb_data = await load_dept_worksheet("KITCHEN_FB", "Kitchen", peer_by_dept.get("KITCHEN_FB"))
    hk_data = await load_dept_worksheet("HOUSEKEEPING", "Housekeeping Operations", peer_by_dept.get("HOUSEKEEPING"))
    rc_data = await load_dept_worksheet("HOUSEKEEPING", "Guest Room", peer_by_dept.get("HOUSEKEEPING"))

    return Envelope(
        data={
            "session": AuditSessionOut.model_validate(sess),
            "hotel": {
                "id": str(hotel.uuid) if hotel else None,
                "code": hotel.code if hotel else None,
                "name": hotel.name if hotel else None,
                "city": hotel.city if hotel else None,
                "region": hotel.region.name if hotel and hotel.region else None,
                "brand": hotel.brand.name if hotel and hotel.brand else None,
            },
            "security": sec_data,
            "kitchen_fb": kfb_data,
            "housekeeping": hk_data,
            "room_check": rc_data,
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
        dept_template_ids = {sess.template_id}
        dept_templates = (
            await session.scalars(
                select(ChecklistTemplate.id).where(
                    ChecklistTemplate.department == sess.department,
                    ChecklistTemplate.status.in_(["LOCKED", "DRAFT"]),
                )
            )
        ).all()
        dept_template_ids.update(dept_templates)
        tr = await load_report_translations(session, dept_template_ids, "en")
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
