"""Frontpage public endpoints — openapi.yaml `/frontpage/*` (F-11, task 8c).

- GET  /frontpage/rfp (POST) — form RFP publik → `RfpRequest` + auto-create
  lead `RFP_PORTAL` + notif sales (WIREFRAME 2.4 "submit → lead CRM otomatis").
  Tanpa auth (`security: []`) — masyarakat umum. Brand/katalog hotel (11b) &
  daftar paket MICE dikerjakan di Phase 11 (ehos-frontpage).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import DbSession
from app.models import User
from app.schemas.common import Envelope
from app.schemas.rfp import RfpPublicCreate, RfpPublicOut
from app.services.rfp_pipeline import submit_rfp

router = APIRouter(prefix="/frontpage", tags=["frontpage"])

SYSTEM_INGEST_EMAIL = "system.ingest@ehos.local"


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