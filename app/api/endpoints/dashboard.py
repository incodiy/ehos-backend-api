"""Dashboard korporat endpoints (PRD-F-12/F-21) — geo-heatmap, Risk Index,
drill-down hotel, dan overview operasional.

Akses (A1/A5): korporat (audit:read:global) → semua hotel; REGIONAL_ROM / GM →
hotel dalam scope asignment (region / hotel). Tanpa scope legal → 403.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from app.api.deps import CurrentUser, DbSession
from app.api.endpoints.audit import _has_hotel_scope
from app.api.security_helpers import allowed_hotel_ids, perms
from app.core.identity import get_by_uuid
from app.models.master import Hotel
from app.schemas.analytics import (
    DashboardOverviewOut,
    HeatmapPointOut,
    HotelDrilldownOut,
)
from app.schemas.common import Envelope
from app.services import dashboard as dashboard_svc

router = APIRouter(prefix="/dashboard", tags=["analytics", "dashboard"])


async def _scope_hotel_ids(session, current) -> set[int] | None:
    """None = semua hotel (korporat); selain itu set hotel internal milik user."""
    codes = await perms(session, current)
    if "audit:read:global" in codes:
        return None
    ids = await allowed_hotel_ids(session, current)
    if not ids:
        raise HTTPException(403, "Missing permission: audit scope hotel/region/global")
    return ids


@router.get("/heatmap", response_model=Envelope[list[HeatmapPointOut]])
async def get_heatmap(current: CurrentUser, session: DbSession) -> Envelope[list[HeatmapPointOut]]:
    """Geo-heatmap semua hotel scope + Risk Index (F-12) — point utk cavaloc."""
    scope = await _scope_hotel_ids(session, current)
    data = await dashboard_svc.compute_heatmap(session, scope)
    return Envelope(data=[HeatmapPointOut.model_validate(p) for p in data])


@router.get("/risk-index", response_model=Envelope[list[HeatmapPointOut]])
async def get_risk_index(current: CurrentUser, session: DbSession) -> Envelope[list[HeatmapPointOut]]:
    """Ranking risiko per hotel (life-safety blink) — CRITICAL→LOW."""
    scope = await _scope_hotel_ids(session, current)
    data = await dashboard_svc.compute_risk_index(session, scope)
    return Envelope(data=[HeatmapPointOut.model_validate(p) for p in data])


@router.get("/hotels/{hotel_id}", response_model=Envelope[HotelDrilldownOut])
async def get_hotel_drilldown(
    current: CurrentUser,
    session: DbSession,
    hotel_id: uuid.UUID,
) -> Envelope[HotelDrilldownOut]:
    """Drill-down performa satu hotel: score_history (YoY) + CAPA + risiko."""
    if not await _has_hotel_scope(session, current, hotel_id):
        raise HTTPException(403, "Missing permission: audit scope hotel/region/global")
    hotel = await get_by_uuid(session, Hotel, hotel_id)
    if hotel is None:
        raise HTTPException(404, "Hotel not found")
    data = await dashboard_svc.compute_hotel_drilldown(session, hotel.id, hotel_id)
    return Envelope(data=HotelDrilldownOut.model_validate(data))


@router.get("/overview", response_model=Envelope[DashboardOverviewOut])
async def get_overview(current: CurrentUser, session: DbSession) -> Envelope[DashboardOverviewOut]:
    """Agregat hero stats + CAPA pipeline + SLA + quick insights."""
    scope = await _scope_hotel_ids(session, current)
    data = await dashboard_svc.compute_overview(session, scope)
    return Envelope(data=DashboardOverviewOut.model_validate(data))