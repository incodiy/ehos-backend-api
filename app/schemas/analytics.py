import uuid

from pydantic import BaseModel


class YoYPointOut(BaseModel):
    year: int
    department: str
    hotel_id: str
    score: float


class HeatmapPointOut(BaseModel):
    hotel_id: uuid.UUID
    code: str
    name: str
    lat: float
    lng: float
    brand_tier: str | None = None
    region: str | None = None
    score: float | None = None
    risk_level: str | None = None


class RiskIndexPointOut(BaseModel):
    hotel_id: uuid.UUID
    code: str
    name: str
    risk_level: str
    score: float | None = None


class HotelDrilldownOut(BaseModel):
    hotel_id: uuid.UUID
    code: str
    name: str
    score_history: list[YoYPointOut] = []
    open_capa_count: int = 0
    risk_level: str | None = None