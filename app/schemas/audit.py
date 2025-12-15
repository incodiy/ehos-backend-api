"""Audit scoring session schemas — openapi.yaml `/audit/sessions` (F-02/F-05)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
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


class EnsureDepartmentRequest(BaseModel):
    source_session_id: HybridId
    department: str = Field(
        ...,
        pattern="^(GM|HOUSEKEEPING|KITCHEN_FB|SECURITY_RISK)$",
    )
    template_id: HybridId | None = None
    note: str | None = None


class IntegratedCycleCreateRequest(BaseModel):
    hotel_id: HybridId
    audit_type: str = Field(default="FULL", pattern="^(FULL|MICRO|FOLLOWUP)$")
    date_start: date | None = None
    date_end: date | None = None
    client_id: uuid.UUID | None = None
    departments: list[str] = Field(
        default=["SECURITY_RISK", "KITCHEN_FB", "HOUSEKEEPING"],
        description="Daftar departemen yang akan dibuatkan sesi dalam siklus audit",
    )


class IntegratedCycleCreateResult(BaseModel):
    primary_session_id: uuid.UUID
    created_sessions: list[AuditSessionOut]
    hotel_id: uuid.UUID
    date_start: date | None = None
    date_end: date | None = None
    audit_type: str


class SessionUpdateRequest(BaseModel):
    department: str | None = Field(
        default=None,
        pattern="^(GM|HOUSEKEEPING|KITCHEN_FB|SECURITY_RISK)$",
    )
    audit_type: str | None = Field(default=None, pattern="^(FULL|MICRO|FOLLOWUP)$")
    date_start: date | None = None
    date_end: date | None = None


class AuditSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    hotel_id: uuid.UUID = Field(validation_alias=AliasPath("hotel", "uuid"))
    hotel_code: str = Field(validation_alias=AliasPath("hotel", "code"))
    hotel_name: str = Field(validation_alias=AliasPath("hotel", "name"))
    template_id: uuid.UUID = Field(validation_alias=AliasPath("template", "uuid"))
    template_version: str | None = Field(default=None, validation_alias=AliasPath("template", "version"))
    department: str
    audit_type: str
    status: str
    auditor_id: uuid.UUID = Field(validation_alias=AliasPath("auditor", "uuid"))
    auditor_name: str = Field(validation_alias=AliasPath("auditor", "name"))
    date_start: date | None = None
    date_end: date | None = None
    published_at: datetime | None = None
    total_score: float | None = None
    pass_fail: str | None = None
    origin: str
    sync_status: str
    client_id: uuid.UUID | None = None


class AuditPeriodDepartmentOut(BaseModel):
    session_id: uuid.UUID
    score: float | None = None
    status: str
    pass_fail: str | None = None


class AuditPeriodOut(BaseModel):
    period_id: str
    year: int
    month_num: int
    month_name: str
    date_start: str
    date_end: str | None = None
    status: str
    average_score: float | None = None
    department_scores: dict[str, AuditPeriodDepartmentOut] = Field(default_factory=dict)
    primary_session_id: uuid.UUID
    auditor_name: str | None = None


class LastAuditPeriodOut(BaseModel):
    year: int
    month_num: int
    month_name: str
    date_start: str
    date_end: str | None = None


class HotelAuditSummaryOut(BaseModel):
    hotel_id: uuid.UUID
    hotel_code: str
    hotel_name: str
    hotel_image_url: str | None = None
    brand_tier: str
    city: str
    total_periods: int
    cumulative_average_score: float | None = None
    last_audit_period: LastAuditPeriodOut | None = None
    last_status: str
    periods: list[AuditPeriodOut] = Field(default_factory=list)



class ScoreUpsert(BaseModel):
    item_id: HybridId
    room_ref: str | None = None
    value: str | None = None
    score: float | None = None
    is_na: bool = False
    note: str | None = None
    scored_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    evidence_media_keys: list[str] = Field(default_factory=list)


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
    server_value: str | None = Field(default=None, validation_alias="winning_value")
    device_value: str | None = Field(default=None, validation_alias="losing_value")
    resolution: str | None
    resolved_at: datetime | None


class ResolveConflictRequest(BaseModel):
    winning_value: str
    note: str | None = None


class MediaRegisterRequest(BaseModel):
    phase: str = Field(default="BEFORE", pattern="^(BEFORE|AFTER)$")
    finding_id: HybridId | None = None
    item_id: HybridId | None = None
    source_camera: str = Field(default="LIVE_CAMERA", pattern="^(LIVE_CAMERA)$")
    mime: str = Field(default="image/webp", pattern="^(image/webp)$")
    width: int = Field(..., le=1920)
    height: int = Field(..., le=1080)
    size_bytes: int = Field(..., le=500000)
    checksum_sha256: str = Field(..., min_length=64, max_length=64)
    gps_lat: float | None = None
    gps_lng: float | None = None
    captured_at: datetime


class MediaRegisterResult(BaseModel):
    media_id: uuid.UUID
    presigned_url: str
    object_key: str


class AuditMediaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    session_id: uuid.UUID = Field(validation_alias=AliasPath("session", "uuid"))
    finding_id: uuid.UUID | None = None
    item_id: uuid.UUID | None = None
    phase: str
    source_camera: str
    object_key: str
    file_name: str | None = None
    mime: str | None = None
    upload_status: str
    captured_at: datetime | None = None


class AuditLogOut(BaseModel):
    """Entri audit trail (audit_logs) — read-only, korporat (Phase 12b / G5)."""

    model_config = ConfigDict(from_attributes=True)

    uuid: uuid.UUID
    actor_email: str | None = None
    action: str
    entity_type: str
    entity_id: uuid.UUID
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    ip: str | None = None
    at: datetime | None = None