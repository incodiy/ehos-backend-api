"""Audit scoring session schemas — openapi.yaml `/audit/sessions` (F-02/F-05)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import AliasPath, BaseModel, ConfigDict, Field

from app.schemas.common import HybridId


class SessionCreateRequest(BaseModel):
    hotel_id: HybridId
    template_id: HybridId | None = Field(
        default=None, description="Kosong = template LOCKED terbaru utk (department, brand_tier)"
    )
    department: str = Field(
        ...,
        pattern="^(GM|HOUSEKEEPING|KITCHEN_FB|SECURITY_RISK)$",
    )
    audit_type: str = Field(default="FULL", pattern="^(FULL|MICRO|FOLLOWUP)$")
    date_start: date | None = None
    date_end: date | None = None
    client_id: uuid.UUID | None = None


class AuditSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    hotel_id: uuid.UUID = Field(validation_alias=AliasPath("hotel", "uuid"))
    template_id: uuid.UUID = Field(validation_alias=AliasPath("template", "uuid"))
    department: str
    audit_type: str
    status: str
    auditor_id: uuid.UUID = Field(validation_alias=AliasPath("auditor", "uuid"))
    date_start: date | None = None
    date_end: date | None = None
    published_at: datetime | None = None
    total_score: float | None = None
    pass_fail: str | None = None
    origin: str
    sync_status: str
    client_id: uuid.UUID | None = None


class ScoreUpsert(BaseModel):
    item_id: HybridId
    room_ref: str | None = None
    value: str | None = None
    is_na: bool = False
    note: str | None = None
    scored_at: datetime
    updated_at: datetime


class BulkScoreUpsert(BaseModel):
    scores: list[ScoreUpsert]


class SyncPushRequest(BaseModel):
    session_client_id: uuid.UUID
    scores: list[ScoreUpsert]
    media_events: list[dict[str, Any]] = Field(default_factory=list)
    device_now: datetime


class SyncPushResult(BaseModel):
    session_id: uuid.UUID
    upserted: int
    conflicts: int
    conflict_ids: list[uuid.UUID] = Field(default_factory=list)
    server_now: datetime


class SyncConflictOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    session_id: uuid.UUID = Field(validation_alias=AliasPath("session", "uuid"))
    item_id: uuid.UUID | None = Field(
        default=None, validation_alias=AliasPath("item", "uuid")
    )
    room_ref: str | None
    winning_value: str | None
    losing_value: str | None
    resolution: str | None
    resolved_at: datetime | None


class ResolveConflictRequest(BaseModel):
    winning_value: str
    note: str | None = None