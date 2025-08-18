"""Checklist bank schemas — openapi.yaml `/checklist/templates` contract (PRD-F-01).

Rubric config drive the dynamic scoring engine (Phase 6a):
- TRAFFIC_LIGHT  → 90/45/0 (max_score 90)
- NUMERIC_SCALE  → measured/reference, percent (max_score 100)
- MULTI_ROOM     → room sampling, sum over N samples (max_score 90 * N)
- BINARY_COUNT   → present/absent (max_score 1.0)
"""

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import AliasPath, BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import HybridId


class DepartEnum(StrEnum):
    GM = "GM"
    HOUSEKEEPING = "HOUSEKEEPING"
    KITCHEN_FB = "KITCHEN_FB"
    SECURITY_RISK = "SECURITY_RISK"


class TemplateStatus(StrEnum):
    DRAFT = "DRAFT"
    LOCKED = "LOCKED"
    ARCHIVED = "ARCHIVED"


class RubricType(StrEnum):
    TRAFFIC_LIGHT = "TRAFFIC_LIGHT"
    NUMERIC_SCALE = "NUMERIC_SCALE"
    MULTI_ROOM = "MULTI_ROOM"
    BINARY_COUNT = "BINARY_COUNT"


class BrandTier(StrEnum):
    LUXURY = "Luxury"
    UPSCALE = "Upscale"
    BOUTIQUE = "Boutique"
    MIDSCALE = "Midscale"
    BUDGET = "Budget"
    ECO_RESORT = "Eco-Resort"


class VersionCreateRequest(BaseModel):
    new_version: str = Field(min_length=1, max_length=20)


class TemplateCreateRequest(BaseModel):
    department: DepartEnum
    name: str = Field(min_length=3, max_length=255)
    version: str = Field(min_length=1, max_length=20)
    brand_tier: BrandTier | None = None


class SectionCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=255)
    parent_id: HybridId | None = None
    sort_order: int = 0


class ItemCreateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    question_text: str = Field(min_length=3)
    rubric_type: RubricType
    max_score: float = Field(gt=0, le=1000)
    weight: float = Field(default=1, gt=0, le=100)
    na_allowed: bool = False
    is_life_safety: bool = False
    sort_order: int = 0

    @field_validator("max_score", mode="after")
    @classmethod
    def _clamp_to_rubric(cls, v: float, info) -> float:
        rubric = info.data.get("rubric_type")
        if rubric == RubricType.TRAFFIC_LIGHT or rubric == RubricType.MULTI_ROOM:
            if v > 0 and v % 90 != 0:
                raise ValueError("TRAFFIC_LIGHT / MULTI_ROOM max_score kelipatan 90")
        return v


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    department: str
    name: str
    version: str
    brand_tier: str | None
    status: str
    locked_at: datetime | None = None
    published_by: uuid.UUID = Field(validation_alias=AliasPath("publisher", "uuid"))


class SectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    code: str
    name: str
    parent_id: uuid.UUID | None = Field(
        default=None, validation_alias=AliasPath("parent", "uuid")
    )
    sort_order: int = 0


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    code: str
    question_text: str
    question_localized: str | None = None
    rubric_type: str
    max_score: float
    weight: float
    na_allowed: bool
    is_life_safety: bool
    sort_order: int = 0


class SectionWithItems(SectionOut):
    items: list[ItemOut] = Field(default_factory=list)


class TemplateDetail(BaseModel):
    template: TemplateOut
    sections: list[SectionWithItems] = Field(default_factory=list)