"""Translation entry endpoint request/response models.

Contract per openapi.yaml `/translations` (GET bulk + PUT upsert) diperluas dengan
GET single-entity + DELETE (task 5f, F-22). Hanya konten/master data yang
di-translate — label UI/enum status tetap bundle frontend (Constraint F2).
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import AliasPath, BaseModel, ConfigDict, Field

from app.schemas.common import HybridId


class Locale(StrEnum):
    ID = "id"
    EN = "en"


class TranslationItem(BaseModel):
    entity_type: str = Field(min_length=1, max_length=50)
    entity_id: HybridId
    field: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1)


class TranslationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    entity_type: str
    entity_id: uuid.UUID
    field: str
    locale: str
    value: str
    updated_at: datetime | None = None


class TranslationPutRequest(BaseModel):
    locale: Literal["id", "en"]
    items: list[TranslationItem] = Field(min_length=1)


class TranslationBundle(BaseModel):
    locale: str
    items: list[TranslationOut]
    updated_after: datetime | None = None
