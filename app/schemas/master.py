"""Master data schemas — openapi.yaml `/hotels`, `/brands`, `/regions`, `/provinces` contract."""

import uuid
from typing import Any

from pydantic import AliasPath, BaseModel, ConfigDict, Field


class HotelGeoOut(BaseModel):
    lat: float
    lng: float


class HotelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    code: str
    name: str
    brand_id: uuid.UUID
    brand: str | None = None
    brand_tier: str | None = None
    region_id: uuid.UUID
    region: str | None = None
    province_id: uuid.UUID | None = None
    city: str | None = None
    geo: HotelGeoOut | None = None
    geofence_radius_meters: int = 200
    mice_facilities: dict[str, Any] | None = None
    status: str = "ACTIVE"
    gm_name: str | None = None
    rom_name: str | None = None


class HotelUpdateRequest(BaseModel):
    name: str | None = None
    geofence_radius_meters: int | None = Field(default=None, ge=50, le=5000)
    mice_facilities: dict[str, Any] | None = None
    status: str | None = Field(default=None, pattern="^(ACTIVE|TEMPORARILY_CLOSED|TERMINATED)$")


class BrandOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    code: str
    name: str
    tier: str


class RegionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    code: str
    name: str
    country: str | None = None
    sales_region: str | None = None


class ProvinceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    code: str
    name: str


class DepartmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    hotel_id: uuid.UUID = Field(validation_alias=AliasPath("hotel", "uuid"))
    code: str
    name: str
    hod_user_id: uuid.UUID | None = Field(
        default=None, validation_alias=AliasPath("hod_user", "uuid")
    )