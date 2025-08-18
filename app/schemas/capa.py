"""CAPA ticket lifecycle schemas — openapi.yaml `/capa/*` (PRD-F-03, task 7a).

Lifecycle status OPEN → AWAITING_GM → AWAITING_QA → CLOSED (Four-Eyes):
- resolve (HOD/Teknisi)   : OPEN | AWAITING_GM → AWAITING_GM (submit bukti AFTER)
- verify/gm (First Appr.) : AWAITING_GM → AWAITING_QA (APPROVE) / OPEN (REJECT)
- verify/qa (Final Appr.) : AWAITING_QA → CLOSED (CLOSE)   / OPEN (REOPEN)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import AliasPath, BaseModel, ConfigDict, Field

from app.schemas.common import HybridId

MediaPhase = Literal["BEFORE", "AFTER"]
CameraSource = Literal["LIVE_CAMERA"]

CAPA_STATUSES = {"OPEN", "AWAITING_GM", "AWAITING_QA", "CLOSED"}


class CapaTicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    finding_id: uuid.UUID | None = Field(
        default=None, validation_alias=AliasPath("finding", "uuid")
    )
    hotel_id: uuid.UUID = Field(validation_alias=AliasPath("hotel", "uuid"))
    department_id: uuid.UUID | None = Field(
        default=None, validation_alias=AliasPath("department", "uuid")
    )
    priority: int
    sla_hours: int
    due_at: datetime
    status: str
    title: str
    description: str | None
    assigned_to: uuid.UUID | None = Field(
        default=None, validation_alias=AliasPath("assigned_to_user", "uuid")
    )
    escalation_level: int
    created_by: uuid.UUID | None = Field(
        default=None, validation_alias=AliasPath("created_by_user", "uuid")
    )
    reporter_id: uuid.UUID | None = Field(
        default=None, validation_alias=AliasPath("reporter", "uuid")
    )
    receipt_id: str | None
    origin: str
    submitted_at: datetime | None
    closed_at: datetime | None
    closed_by: uuid.UUID | None = Field(
        default=None, validation_alias=AliasPath("closed_by_user", "uuid")
    )


class CapaHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    ticket_id: uuid.UUID = Field(validation_alias=AliasPath("ticket", "uuid"))
    from_status: str | None
    to_status: str
    actor_id: uuid.UUID = Field(validation_alias=AliasPath("actor", "uuid"))
    note: str | None
    at: datetime


class CapaMediaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    ticket_id: uuid.UUID = Field(validation_alias=AliasPath("ticket", "uuid"))
    phase: MediaPhase
    object_key: str
    mime: str
    size_bytes: int | None
    gps_valid: bool
    upload_status: str
    captured_at: datetime


class CapaMediaPresignOut(BaseModel):
    media_id: uuid.UUID
    object_key: str
    presigned_url: str
    upload_status: str
    expires_in: int = 420


class CapaMediaConfirmRequest(BaseModel):
    object_key: str | None = Field(
        default=None, description="Guard path: wajib sama dengan object_key tersimpan"
    )


class CapaMediaListOut(BaseModel):
    items: list[CapaMediaOut]
    summary: dict


class CapaTicketCreateRequest(BaseModel):
    finding_id: HybridId
    assigned_to: HybridId | None = None


class AssignTicketRequest(BaseModel):
    assigned_to: HybridId
    note: str | None = None


class MediaRegisterRequest(BaseModel):
    phase: MediaPhase = Field(default="AFTER")
    source_camera: CameraSource = Field(default="LIVE_CAMERA")
    mime: str = Field(default="image/webp")
    width: int = Field(..., ge=1, le=1920)
    height: int = Field(..., ge=1, le=1080)
    size_bytes: int = Field(..., ge=1, le=400_000)
    checksum_sha256: str = Field(
        ..., pattern=r"^[0-9a-fA-F]{64}$", description="sha256 hex media (ARD-005 verify)"
    )
    gps_lat: float | None = Field(default=None, ge=-90, le=90)
    gps_lng: float | None = Field(default=None, ge=-180, le=180)
    gps_valid: bool = True
    captured_at: datetime


class ResolveTicketRequest(BaseModel):
    note: str = Field(..., min_length=3)
    media: list[MediaRegisterRequest] = Field(default_factory=list)


class ReviewDecisionRequest(BaseModel):
    decision: str
    note: str | None = None


class GmReviewRequest(ReviewDecisionRequest):
    decision: Literal["APPROVE", "REJECT"]


class QaReviewRequest(ReviewDecisionRequest):
    decision: Literal["CLOSE", "REOPEN"]