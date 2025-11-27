"""CRM Lost Reason Analytics endpoints — quarterly analytics and breakdown."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.api.endpoints.crm_common import (
    _quarter_start,
    _require_read,
    _resolve_hotel_uuid,
)
from app.api.security_helpers import allowed_hotel_ids
from app.api.security_helpers import perms as _perms
from app.core.identity import get_by_uuid
from app.models import Hotel, Region
from app.schemas.common import Envelope, HybridId
from app.schemas.crm import LostReasonAnalyticsOut
from app.services.crm_analytics import compute_lost_reason_analytics, date_to_range_utc

router = APIRouter()


@router.get("/analytics/lost-reasons", response_model=Envelope[LostReasonAnalyticsOut])
async def lost_reason_analytics(
    current: CurrentUser,
    session: DbSession,
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
    hotel_id: HybridId | None = None,
    region_id: HybridId | None = None,
) -> Envelope[LostReasonAnalyticsOut]:
    """Lost Reason analytics per kuartal (F-07) — breakdown alasan + trend.

    RBAC: `crm:read` wajib. Scope tenant isolation: hotel/region user (scope)
    atau korporat (zero-assignment, global). Tanpa filter hotel/region → scope
    user dipakai (`hotel_ids=None` hanya untuk user global).
    """
    codes = await _perms(session, current)
    if "crm:read" not in codes:
        raise HTTPException(403, "Missing permission: crm:read")
    scope = await allowed_hotel_ids(session, current)
    scope_or_global = scope or None  # None = korporat/global

    if from_date and to_date:
        from_dt, to_dt = date_to_range_utc(from_date, to_date)
    else:
        from_dt, to_dt = date_to_range_utc(_quarter_start(date.today()), date.today())

    # resolusi scope: hotel = pin, region = subset scope, else scope user.
    if hotel_id is not None:
        hotel = await _resolve_hotel_uuid(session, hotel_id)
        await _require_read(session, current, hotel.id)
        hotel_ids: set[int] | None = {hotel.id}
    elif region_id is not None:
        region = await get_by_uuid(session, Region, region_id)
        if region is None:
            raise HTTPException(404, "Region tidak ditemukan")
        region_hotel_ids = set(
            (await session.scalars(select(Hotel.id).where(Hotel.region_id == region.id))).all()
        )
        if scope_or_global is not None:
            region_hotel_ids &= scope_or_global
            if not region_hotel_ids:
                raise HTTPException(403, "Missing permission: crm scope hotel/region/global")
        # set (mungkin kosong) = filter eksplisit region; None = tanpa filter.
        hotel_ids = region_hotel_ids
    else:
        hotel_ids = scope_or_global

    data = await compute_lost_reason_analytics(
        session,
        from_dt=from_dt,
        to_dt=to_dt,
        hotel_ids=hotel_ids,
    )
    data["period"] = f"{from_dt.date()}..{to_dt.date()}"
    return Envelope(data=LostReasonAnalyticsOut.model_validate(data))
