"""Master data schemas — openapi.yaml `/hotels`, `/brands`, `/regions`, `/provinces` contract."""

import uuid
from typing import Any

from pydantic import AliasPath, BaseModel, ConfigDict, Field

from app.schemas.checklist import BrandTier


class HotelGeoOut(BaseModel):
    lat: float
    lng: float


class HotelContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    contact_type: str
    name: str
    email: str | None = None
    phone: str | None = None
    user_id: uuid.UUID | None = None
    hotel_id: uuid.UUID | None = None
    hotel_code: str | None = None
    hotel_name: str | None = None
    is_primary: bool = True


class HotelContactCreateRequest(BaseModel):
    contact_type: str = Field(..., pattern="^(GM|SALES|FINANCE|ROM)$")
    name: str = Field(..., min_length=1, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    user_id: uuid.UUID | None = None
    is_primary: bool = True


class HotelContactGlobalCreateRequest(BaseModel):
    hotel_id: uuid.UUID
    contact_type: str = Field(..., pattern="^(GM|SALES|FINANCE|ROM)$")
    name: str = Field(..., min_length=1, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    user_id: uuid.UUID | None = None
    is_primary: bool = True


class HotelContactUpdateRequest(BaseModel):
    hotel_id: uuid.UUID | None = None
    contact_type: str | None = Field(default=None, pattern="^(GM|SALES|FINANCE|ROM)$")
    name: str | None = Field(default=None, min_length=1, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    user_id: uuid.UUID | None = None
    is_primary: bool | None = None


class CityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    name: str
    province_id: uuid.UUID
    province: str | None = None
    region_id: uuid.UUID
    region: str | None = None
    ecommerce_city: str | None = None


class CityCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    province_id: uuid.UUID
    region_id: uuid.UUID
    ecommerce_city: str | None = Field(default=None, max_length=100)


class CityUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    province_id: uuid.UUID | None = None
    region_id: uuid.UUID | None = None
    ecommerce_city: str | None = Field(default=None, max_length=100)


class HotelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    code: str
    name: str
    brand_id: uuid.UUID
    brand: str | None = None
    brand_tier: str | None = None
    region_id: uuid.UUID
    region: str | None = None
    province_id: uuid.UUID | None = None
    city_id: uuid.UUID | None = None
    city: str | None = None
    ecommerce_city: str | None = None
    sales_region: str | None = None
    ecommerce_region: str | None = None
    geo: HotelGeoOut | None = None
    geofence_radius_meters: int = 200
    mice_facilities: dict[str, Any] | None = None
    status: str = "ACTIVE"
    image_url: str | None = None
    has_fb: bool = True
    opening_date: str | None = None
    terminate_date: str | None = None
    period_update: str | None = None
    gm_name: str | None = None
    rom_name: str | None = None
    contacts: list[HotelContactOut] = []


class HotelGeoIn(BaseModel):
    lat: float = Field(..., ge=-90.0, le=90.0)
    lng: float = Field(..., ge=-180.0, le=180.0)


class HotelCreateRequest(BaseModel):
    code: str = Field(..., min_length=2, max_length=10, pattern="^[A-Z0-9_-]+$")
    name: str = Field(..., min_length=2, max_length=255)
    brand_id: uuid.UUID
    region_id: uuid.UUID
    province_id: uuid.UUID
    city_id: uuid.UUID | None = None
    city: str = Field(..., min_length=2, max_length=100)
    geo: HotelGeoIn
    geofence_radius_meters: int = Field(default=200, ge=50, le=5000)
    mice_facilities: dict[str, Any] | None = None
    gm_id: uuid.UUID | None = None
    rom_id: uuid.UUID | None = None
    opening_date: str | None = None
    terminate_date: str | None = None
    status: str = Field(default="ACTIVE", pattern="^(ACTIVE|TEMPORARILY_CLOSED|TERMINATED)$")
    image_url: str | None = None
    has_fb: bool = True
    contacts: list[HotelContactCreateRequest] | None = None


class HotelUpdateRequest(BaseModel):
    code: str | None = Field(default=None, min_length=2, max_length=10)
    name: str | None = Field(default=None, min_length=2, max_length=255)
    brand_id: uuid.UUID | None = None
    region_id: uuid.UUID | None = None
    province_id: uuid.UUID | None = None
    city_id: uuid.UUID | None = None
    city: str | None = None
    geo: HotelGeoIn | None = None
    geofence_radius_meters: int | None = Field(default=None, ge=50, le=5000)
    mice_facilities: dict[str, Any] | None = None
    gm_id: uuid.UUID | None = None
    rom_id: uuid.UUID | None = None
    opening_date: str | None = None
    terminate_date: str | None = None
    status: str | None = Field(default=None, pattern="^(ACTIVE|TEMPORARILY_CLOSED|TERMINATED)$")
    image_url: str | None = None
    has_fb: bool | None = None
    contacts: list[HotelContactCreateRequest] | None = None


from app.schemas.brand import BrandOut, BrandTier, BrandTierUpdateRequest


from app.schemas.region import RegionOut


class ProvinceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    code: str
    name: str


class DepartmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    hotel_id: uuid.UUID = Field(validation_alias=AliasPath("hotel", "uuid"))
    code: str
    name: str
    hod_user_id: uuid.UUID | None = Field(
        default=None, validation_alias=AliasPath("hod_user", "uuid")
    )