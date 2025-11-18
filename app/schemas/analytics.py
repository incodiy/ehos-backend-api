import uuid

from pydantic import BaseModel


class YoYPointOut(BaseModel):
    year: int
    department: str
    hotel_id: str
    score: float


class HeatmapPointOut(BaseModel):
    """Titik geo-heatmap dashboard (F-12) — 1 per hotel."""
    hotel_id: uuid.UUID
    code: str
    name: str
    lat: float
    lng: float
    brand_tier: str | None = None
    region: str | None = None
    score: float | None = None
    risk_level: str | None = None
    has_life_safety: bool = False
    open_capa: int = 0


class RiskIndexPointOut(BaseModel):
    hotel_id: uuid.UUID
    code: str
    name: str
    risk_level: str
    score: float | None = None


class HotelDrilldownOut(BaseModel):
    hotel_id: uuid.UUID
    code: str | None = None
    name: str | None = None
    score_history: list[YoYPointOut] = []
    open_capa_count: int = 0
    risk_level: str | None = None


class OverviewSlaOut(BaseModel):
    on_time: int = 0
    near_overdue: int = 0
    overdue: int = 0
    unknown: int = 0


class OverviewInsightOut(BaseModel):
    """Insight utk Quick Insight cards — frontend pakai `type` utk kunci i18n."""
    type: str
    count: int = 0
    open_capa: int | None = None
    near_overdue: int | None = None
    findings_ls: int | None = None


class DashboardOverviewOut(BaseModel):
    as_of: str
    hotels_total: int = 0
    hotels_with_risk: int = 0
    audits_ytd: int = 0
    audits_today: int = 0
    findings_total: int = 0
    life_safety_open: int = 0
    capa_active: int = 0
    capa_closed: int = 0
    pipeline: dict[str, int] = {}
    sla: OverviewSlaOut = OverviewSlaOut()
    insights: list[OverviewInsightOut] = []