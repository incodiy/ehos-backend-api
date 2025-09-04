"""Analytics & dashboard endpoints (F-12/F-21)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CurrentUser, DbSession
from app.api.endpoints.audit import _has_hotel_scope, _is_corporate
from app.schemas.analytics import YoYPointOut
from app.schemas.common import Envelope, HybridId
from app.services.analytics import compute_yoy

router = APIRouter(prefix="/analytics", tags=["analytics", "dashboard"])


@router.get("/yoy", response_model=Envelope[list[YoYPointOut]])
async def get_yoy(
    current: CurrentUser,
    session: DbSession,
    hotel_id: HybridId | None = None,
    department: str | None = None,
    years: Annotated[list[int] | None, Query()] = None,
) -> Envelope[list[YoYPointOut]]:
    """YoY trend score per hotel/departemen (F-21). Legacy 2024-26 + live."""
    if hotel_id is not None:
        if not await _has_hotel_scope(session, current, hotel_id):
            raise HTTPException(403, "Missing permission: audit scope hotel/region/global")
    elif not await _is_corporate(session, current):
        raise HTTPException(403, "Missing permission: audit:read:global (korporat/eksekutif)")
    data = await compute_yoy(session, hotel_id=hotel_id, department=department, years=years)
    return Envelope(data=data)