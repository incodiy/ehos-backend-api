"""RFP intake pipeline (PRD-F-11, task 8c) — form publik → lead CRM otomatis.

POST /frontpage/rfp (publik, tanpa auth) membuat `rfp_requests` (status NEW)
dan — bila `target_hotel_code` valid & hotel punya owner aktif — **langsung
transform menjadi lead `source=RFP_PORTAL`** (openapi "auto-create lead",
wireframe "lead CRM otomatis + notif sales"). Tanpa target / kode hotel tidak
valid → RFP tetap NEW menunggu assign corporate (ERD: "RFP publik target semua
→ di-assign corporate admin → leads ber-hotel_id").

Idempoten & deterministik (H4): seeder melewati `ref_no`/`lead_no` tetap → upsert
berdasarkan `ref_no`, lead dibuat sekali, notifikasi `RFP_INTAKE` dedup per rfp.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Lead, RfpRequest, User
from app.models.master import Hotel
from app.services.crm_pipeline import (
    _log_activity,
    create_lead,
    generate_lead_no,
    resolve_crm_owner,
)
from app.services.notifications import create_notification

RFP_STATUSES = ("NEW", "ASSIGNED", "ACCEPTED", "DECLINED")
PACKAGE_TYPES = ("FULLDAY", "HALFDAY", "FULLBOARD")
RFP_DEFAULT_FOLLOWUP_DAYS = 1


def generate_ref_no() -> str:
    """Nomor referensi RFP unik — `RFP-YYMMDD-XXXXX` (uuid suffix)."""
    stamp = datetime.now(UTC).strftime("%y%m%d")
    return f"RFP-{stamp}-{uuid.uuid4().hex[:5].upper()}"


def _placeholder_email(ref_no: str, email: str | None) -> str:
    """Email kosong → placeholder `.invalid` RFC 2606 (jujur, bukan data bisnis palsu).

    Model `rfp_requests.pic_email` NOT NULL sedangkan kontrak RfpPublicCreate
    email opsional. Placeholder TLD `.invalid` menandakan "tidak ada email"
    tanpa memalsukan alamat yang terlihat nyata (Constraint G1/G4).
    """
    return email or f"rfp-{ref_no.lower()}@noemail.invalid"


async def _rfp_notif_exists(session: AsyncSession, rfp_id: uuid.UUID) -> bool:
    row = await session.scalar(
        text(
            "SELECT 1 FROM notifications WHERE entity_type='rfp' "
            "AND entity_id=:id AND type='RFP_INTAKE' LIMIT 1"
        ),
        {"id": rfp_id},
    )
    return bool(row)


async def _ensure_rfp_lead(
    session: AsyncSession,
    *,
    lead_no: str,
    rfp: RfpRequest,
    target: Hotel,
    owner: User,
    company_name: str,
    institution_type: str,
    pic_name: str | None,
    phone: str | None,
    email: str,
    actor_id: uuid.UUID,
) -> Lead:
    lead = await session.scalar(select(Lead).where(Lead.lead_no == lead_no))
    if lead is not None:
        return lead
    lead = await create_lead(
        session,
        lead_no=lead_no,
        hotel_id=target.id,
        source="RFP_PORTAL",
        institution_type=institution_type,
        company_name=company_name,
        owner_id=owner.id,
        pic_name=pic_name,
        pic_phone=phone[:20] if phone else None,
        pic_email=email,
        province_id=target.province_id,
        next_followup_at=datetime.now(UTC) + timedelta(days=RFP_DEFAULT_FOLLOWUP_DAYS),
        created_by=actor_id,
    )
    details = rfp.details or {}
    await _log_activity(
        session,
        lead,
        activity_type="NOTE",
        note=(
            f"RFP {rfp.ref_no} — {details.get('package_type')} "
            f"{details.get('pax')} pax {details.get('event_date')} ({details.get('city')})"
        ),
        actor_id=actor_id,
    )
    return lead


async def submit_rfp(
    session: AsyncSession,
    *,
    company_name: str,
    institution_type: str,
    pic_name: str | None,
    phone: str | None,
    email: str | None,
    event_date: date,
    pax: int,
    package_type: str,
    city: str,
    target_hotel_code: str | None,
    notes: str | None,
    actor_id: uuid.UUID,
    ref_no: str | None = None,
    lead_no: str | None = None,
) -> tuple[RfpRequest, Lead | None, User | None]:
    """Submit RFP → RfpRequest (NEW) + transform lead otomatis bila ada target.

    Returns (rfp, lead|None, owner|None). `ref_no`/`lead_no` opsional utk seeder
    (deterministik/idempoten); endpoint memakainya default generate.
    """
    ref_no = ref_no or generate_ref_no()
    target = None
    if target_hotel_code and target_hotel_code.strip():
        target = await session.scalar(
            select(Hotel).where(Hotel.code == target_hotel_code.strip().upper())
        )

    details: dict = {
        "event_date": event_date.isoformat(),
        "pax": pax,
        "package_type": package_type,
        "city": city,
        "notes": notes,
        "target_hotel_code": (target_hotel_code or None) if target_hotel_code else None,
        "institution_type": institution_type,
    }
    stmt = (
        pg_insert(RfpRequest)
        .values(
            ref_no=ref_no,
            company_name=company_name,
            pic_name=pic_name,
            pic_phone=phone,
            pic_email=_placeholder_email(ref_no, email),
            details=details,
            target_hotel_id=target.id if target else None,
            status="NEW",
        )
        .on_conflict_do_update(
            index_elements=[RfpRequest.ref_no],
            set_={
                "company_name": company_name,
                "pic_name": pic_name,
                "pic_phone": phone,
                "pic_email": _placeholder_email(ref_no, email),
                "details": details,
                "target_hotel_id": target.id if target else None,
            },
        )
    )
    await session.execute(stmt)
    rfp = await session.scalar(select(RfpRequest).where(RfpRequest.ref_no == ref_no))

    lead = None
    owner = None
    if target is not None:
        owner = await resolve_crm_owner(session, target.id)
        if owner is not None:
            lead = await _ensure_rfp_lead(
                session,
                lead_no=lead_no or generate_lead_no(target.code, "RFP_PORTAL"),
                rfp=rfp,
                target=target,
                owner=owner,
                company_name=company_name,
                institution_type=institution_type,
                pic_name=pic_name,
                phone=phone,
                email=_placeholder_email(ref_no, email),
                actor_id=actor_id,
            )
            rfp.assigned_hotel_id = target.id
            rfp.status = "ASSIGNED"
            if not await _rfp_notif_exists(session, rfp.uuid):
                await create_notification(
                    session,
                    key="rfp_new",
                    user=owner,
                    context={
                        "ref_no": ref_no,
                        "company_name": company_name,
                        "event_date": details["event_date"],
                        "pax": pax,
                        "package_type": package_type,
                        "url": f"/crm/leads/{lead.uuid}",
                    },
                    entity_type="rfp",
                    entity_id=rfp.uuid,
                )
    return rfp, lead, owner