"""CRM SBM Rates endpoints — list and update government SBM rates."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.api.security_helpers import allowed_hotel_ids
from app.api.security_helpers import perms as _perms
from app.core.identity import get_by_uuid
from app.models import GovernmentSbmRate, Province
from app.schemas.common import Envelope, HybridId
from app.schemas.crm import SbmRateOut, SbmRateUpdateRequest

router = APIRouter()


@router.get("/sbm-rates", response_model=Envelope[list[SbmRateOut]])
async def list_sbm_rates(
    current: CurrentUser,
    session: DbSession,
    province_code: str | None = None,
    package_type: str | None = None,
    fiscal_year: int | None = None,
    is_active: bool | None = None,
) -> Envelope[list[SbmRateOut]]:
    if "crm:read" not in await _perms(session, current):
        raise HTTPException(403, "Missing permission: crm:read")
    stmt = select(GovernmentSbmRate, Province.code, Province.name).join(
        Province, GovernmentSbmRate.province_id == Province.id
    )
    if province_code:
        stmt = stmt.where(Province.code == province_code)
    if package_type:
        stmt = stmt.where(GovernmentSbmRate.package_type == package_type.upper())
    if fiscal_year:
        stmt = stmt.where(GovernmentSbmRate.fiscal_year == fiscal_year)
    if is_active is not None:
        stmt = stmt.where(GovernmentSbmRate.is_active == is_active)
    rows = (await session.execute(stmt.order_by(Province.code, GovernmentSbmRate.fiscal_year))).all()
    return Envelope(data=[
        SbmRateOut.model_validate(r[0]).model_copy(
            update={"province_code": r[1], "province_name": r[2]}
        )
        for r in rows
    ])


@router.patch("/sbm-rates/{id}", response_model=Envelope[SbmRateOut])
async def update_sbm_rate(
    id: HybridId,
    payload: SbmRateUpdateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[SbmRateOut]:
    """Update SBM rate (E2) — Corporate DOSM (corp.exec/root, perubahan PMK)."""
    if "sbm:write" not in await _perms(session, current):
        raise HTTPException(403, "Missing permission: sbm:write")
    scope = await allowed_hotel_ids(session, current)
    if scope:  # hanya corporate/global (tanpa assignment rumah) yang boleh ubah PMK master
        raise HTTPException(403, "SBM master hanya dikelola corporate (zero-assignment role)")
    rate = await get_by_uuid(session, GovernmentSbmRate, id)
    if rate is None:
        raise HTTPException(404, "SBM rate tidak ditemukan")
    changes = payload.model_dump(exclude_unset=True)
    if "fiscal_year" in changes and changes["fiscal_year"] != rate.fiscal_year:
        dup = await session.scalar(
            select(GovernmentSbmRate.id).where(
                GovernmentSbmRate.province_id == rate.province_id,
                GovernmentSbmRate.package_type == rate.package_type,
                GovernmentSbmRate.fiscal_year == changes["fiscal_year"],
            )
        )
        if dup is not None:
            raise HTTPException(409, "SBM rate utk provinsi/package/tahun itu sudah ada")
    for field in ("max_rate_per_pax", "fiscal_year", "is_active"):
        if field in changes and changes[field] is not None:
            setattr(rate, field, changes[field])
    rate.updated_by = current.id
    await session.commit()
    await session.refresh(rate)
    province = await session.get(Province, rate.province_id)
    return Envelope(data=SbmRateOut.model_validate(rate).model_copy(
        update={"province_code": province.code if province else None,
                "province_name": province.name if province else None}
    ))
