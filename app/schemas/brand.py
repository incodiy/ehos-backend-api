import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class BrandTier(str, Enum):
    LUXURY = "Luxury"
    UPSCALE = "Upscale"
    BOUTIQUE = "Boutique"
    MIDSCALE = "Midscale"
    BUDGET = "Budget"
    ECO_RESORT = "Eco-Resort"


class BrandStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    RETIRED = "RETIRED"


class BrandCreateRequest(BaseModel):
    code: str = Field(..., min_length=2, max_length=20, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(..., min_length=2, max_length=255)
    tier: BrandTier
    status: BrandStatus = BrandStatus.ACTIVE

    @property
    def normalized_code(self) -> str:
        return self.code.strip().upper()


class BrandUpdateRequest(BaseModel):
    code: str | None = Field(default=None, min_length=2, max_length=20, pattern=r"^[A-Za-z0-9_-]+$")
    name: str | None = Field(default=None, min_length=2, max_length=255)
    tier: BrandTier | None = None
    status: BrandStatus | None = None


class BrandTierUpdateRequest(BaseModel):
    tier: BrandTier


class BrandHotelSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    code: str
    name: str
    city: str | None = None
    status: str = "ACTIVE"


class BrandOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    code: str
    name: str
    tier: str
    status: str = "ACTIVE"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    hotels_count: int = 0


class BrandDetailOut(BrandOut):
    hotels: list[BrandHotelSummary] = Field(default_factory=list)
