"""Region schemas — contract for /regions CRUD, FSM status, and detail statistics."""

from datetime import datetime
from enum import Enum
import uuid

from pydantic import BaseModel, ConfigDict, Field


class RegionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    RETIRED = "RETIRED"


class RegionCreateRequest(BaseModel):
    code: str = Field(..., min_length=2, max_length=20, pattern="^[A-Z0-9_-]+$")
    name: str = Field(..., min_length=2, max_length=100)
    country: str = Field(default="Indonesia", max_length=50)
    sales_region: str | None = Field(default=None, max_length=100)
    status: RegionStatus = Field(default=RegionStatus.ACTIVE)


class RegionUpdateRequest(BaseModel):
    code: str | None = Field(default=None, min_length=2, max_length=20, pattern="^[A-Z0-9_-]+$")
    name: str | None = Field(default=None, min_length=2, max_length=100)
    country: str | None = Field(default=None, max_length=50)
    sales_region: str | None = Field(default=None, max_length=100)
    status: RegionStatus | None = None


class RegionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    code: str
    name: str
    country: str | None = None
    sales_region: str | None = None
    status: str = "ACTIVE"
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RegionDetailOut(RegionOut):
    hotels_count: int = 0
    rom_names: list[str] = []
