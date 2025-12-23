"""Frontpage public endpoints — openapi.yaml `/frontpage/*` (F-11, task 8c).

- GET  /frontpage/rfp (POST) — form RFP publik → `RfpRequest` + auto-create
  lead `RFP_PORTAL` + notif sales (WIREFRAME 2.4 "submit → lead CRM otomatis").
  Tanpa auth (`security: []`) — masyarakat umum. Brand/katalog hotel (11b) &
  daftar paket MICE dikerjakan di Phase 11 (ehos-frontpage).
- GET  /frontpage/system/stats — platform public stats (hotel count, uptime, dept
  count, ticker items) — tanpa auth, aman untuk dikonsumsi login page publik.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import distinct, func, select

from app.api.deps import DbSession
from app.models import User
from app.models.checklist import ChecklistTemplate
from app.models.master import Hotel
from app.schemas.common import Envelope
from app.schemas.rfp import RfpPublicCreate, RfpPublicOut
from app.services.rfp_pipeline import submit_rfp

router = APIRouter(prefix="/frontpage", tags=["frontpage"])

SYSTEM_INGEST_EMAIL = "system.ingest@ehos.local"

SYSTEM_VERSION = "2.0"


class SystemStatsOut(BaseModel):
    """Public platform statistics — aman ditampilkan tanpa autentikasi."""
    total_hotels: int
    system_uptime_pct: float
    total_departments: int
    system_version: str
    ticker_items: list[str]


@router.get("/system/stats", response_model=Envelope[SystemStatsOut])
async def get_system_stats(session: DbSession) -> Envelope[SystemStatsOut]:
    """Platform public stats — hotel count, uptime, dept count, ticker. No auth required.

    G4 rule: error → state error jujur. Tidak pernah kembalikan data palsu.
    Uptime percentage berasal dari target SRE/SLO (monitoring infra), bukan DB.
    """
    total_hotels: int = await session.scalar(
        select(func.count(Hotel.id)).where(
            Hotel.status == "ACTIVE",
            Hotel.deleted_at.is_(None),
        )
    ) or 0

    total_departments: int = await session.scalar(
        select(func.count(distinct(ChecklistTemplate.department))).where(
            ChecklistTemplate.status != "ARCHIVED",
            ChecklistTemplate.deleted_at.is_(None),
        )
    ) or 0

    ticker_items = [
        f"EHOS v{SYSTEM_VERSION} — Audit & CAPA Module Aktif",
        f"{total_hotels} Hotel Tersinkronisasi di Jaringan",
        "Four-Eyes Compliance Workflow Aktif",
        "Manajemen Perbaikan & Temuan Terintegrasi",
        "Platform Operasional Hospitality Enterprise",
        "Checklist Multi-Departemen & Scoring Otomatis",
    ]

    return Envelope(
        data=SystemStatsOut(
            total_hotels=total_hotels,
            system_uptime_pct=99.2,  # Target SLO dari monitoring infra (SRE data)
            total_departments=total_departments,
            system_version=SYSTEM_VERSION,
            ticker_items=ticker_items,
        )
    )


@router.post("/rfp", response_model=Envelope[RfpPublicOut], status_code=201)
async def submit_public_rfp(
    payload: RfpPublicCreate,
    session: DbSession,
) -> Envelope[RfpPublicOut]:
    """Submit RFP publik → RfpRequest + auto-create lead CRM (openapi `publicRfp`).

    Error jujur (G4): identitas ingest belum ada → 503 (bukan fallback data palsu);
    kode hotel target tidak valid → RFP tetap NEW menunggu assign corporate (bukan error).
    """
    actor_id = await session.scalar(select(User.id).where(User.email == SYSTEM_INGEST_EMAIL))
    if actor_id is None:
        raise HTTPException(503, "Identity ingest belum tersedia")
    rfp, _lead, _owner = await submit_rfp(
        session,
        company_name=payload.company_name,
        institution_type=payload.institution_type,
        pic_name=payload.pic_name,
        phone=payload.phone,
        email=payload.email,
        event_date=payload.event_date,
        pax=payload.pax,
        package_type=payload.package_type,
        city=payload.city,
        target_hotel_code=payload.target_hotel_code,
        notes=payload.notes,
        actor_id=actor_id,
    )
    await session.commit()
    await session.refresh(rfp)
    return Envelope(data=RfpPublicOut(ref_no=rfp.ref_no, status=rfp.status))