from __future__ import annotations

import uuid

UUIDT = uuid.UUID
from datetime import date, datetime

from sqlalchemy import (
    CHAR,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import BigInteger, Identity
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.mixins import AppendOnlyMixin, TimestampMixin

UUID_PK = PG_UUID(as_uuid=True)
BIGINT = BigInteger()


class AuditSession(Base, TimestampMixin):
    __tablename__ = "audit_sessions"
    __table_args__ = (
        Index("ix_audit_hotel_status", "hotel_id", "status"),
        Index("ix_audit_department_date", "department", "date_start"),
        Index("ix_audit_client_id", "client_id"),
        UniqueConstraint(
            "hotel_id",
            "department",
            "date_start",
            "audit_type",
            postgresql_nulls_not_distinct=True,
            name="uq_audit_hotel_dept_period_type",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    hotel_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("hotels.id"), nullable=False)
    template_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("checklist_templates.id"), nullable=False
    )
    department: Mapped[str] = mapped_column(String(30), nullable=False)
    audit_type: Mapped[str] = mapped_column(String(20), nullable=False, default="FULL")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    auditor_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("users.id"), nullable=False)
    date_start: Mapped[date | None] = mapped_column(Date)
    date_end: Mapped[date | None] = mapped_column(Date)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pass_fail: Mapped[str | None] = mapped_column(String(10))
    total_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    department_breakdown: Mapped[dict | None] = mapped_column(JSONB)
    subcategory_scores: Mapped[dict | None] = mapped_column(JSONB)
    origin: Mapped[str] = mapped_column(String(10), nullable=False, default="SYSTEM")
    legacy_source_year: Mapped[int | None] = mapped_column(Integer)
    client_id: Mapped[UUIDT | None] = mapped_column(UUID_PK)
    sync_status: Mapped[str] = mapped_column(String(20), nullable=False, default="SYNCED")
    created_by: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))
    hotel: Mapped["Hotel"] = relationship(foreign_keys=[hotel_id], lazy="selectin")
    template: Mapped["ChecklistTemplate"] = relationship(foreign_keys=[template_id], lazy="selectin")
    auditor: Mapped["User"] = relationship(foreign_keys=[auditor_id], lazy="selectin")


class AuditItemScore(Base, AppendOnlyMixin):
    __tablename__ = "audit_item_scores"
    __table_args__ = (
        Index("ix_audit_item_session", "session_id"),
        Index("ix_audit_item_item", "item_id"),
        UniqueConstraint(
            "session_id",
            "item_id",
            "room_ref",
            postgresql_nulls_not_distinct=True,
            name="uq_audit_item_session_item_room",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    session_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("audit_sessions.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("checklist_items.id"), nullable=False
    )
    room_ref: Mapped[str | None] = mapped_column(String(50))
    value: Mapped[str | None] = mapped_column(String(50))
    score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    is_na: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[str | None] = mapped_column(Text)
    scored_by: Mapped[int] = mapped_column(BIGINT, ForeignKey("users.id"), nullable=False)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    item: Mapped["ChecklistItem"] = relationship(foreign_keys=[item_id], lazy="selectin")
    scored_by_user: Mapped["User"] = relationship(foreign_keys=[scored_by], lazy="selectin")


class SyncConflictLog(Base, AppendOnlyMixin):
    __tablename__ = "sync_conflict_logs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    session_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("audit_sessions.id"), nullable=False
    )
    item_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("checklist_items.id"))
    room_ref: Mapped[str | None] = mapped_column(String(50))
    winning_value: Mapped[str | None] = mapped_column(String(50))
    losing_value: Mapped[str | None] = mapped_column(String(50))
    resolution: Mapped[str] = mapped_column(String(20), nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_by: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))
    session: Mapped["AuditSession"] = relationship(foreign_keys=[session_id], lazy="selectin")
    item: Mapped["ChecklistItem | None"] = relationship(foreign_keys=[item_id], lazy="selectin")
    resolved_by_user: Mapped["User | None"] = relationship(foreign_keys=[resolved_by], lazy="selectin")


class Finding(Base, AppendOnlyMixin):
    __tablename__ = "findings"
    __table_args__ = (Index("ix_findings_hotel_safety", "hotel_id", "is_life_safety"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    session_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("audit_sessions.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("checklist_items.id"))
    hotel_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("hotels.id"), nullable=False)
    is_life_safety: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(255))
    item: Mapped["ChecklistItem"] = relationship(foreign_keys=[item_id], lazy="selectin")
    session: Mapped["AuditSession"] = relationship(foreign_keys=[session_id], lazy="selectin")
    hotel: Mapped["Hotel"] = relationship(foreign_keys=[hotel_id], lazy="selectin")


class AuditMedia(Base, AppendOnlyMixin):
    __tablename__ = "audit_media"
    __table_args__ = (
        Index("ix_audit_media_session", "session_id"),
        Index("ix_audit_media_finding", "finding_id"),
        Index("ix_audit_media_uploadstatus", "upload_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    finding_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("findings.id"))
    session_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("audit_sessions.id"), nullable=False
    )
    item_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("checklist_items.id"))
    phase: Mapped[str] = mapped_column(String(10), nullable=False, default="BEFORE")
    source_camera: Mapped[str] = mapped_column(String(20), nullable=False, default="LIVE_CAMERA")
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(255))
    mime: Mapped[str | None] = mapped_column(String(50))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    checksum_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    gps_lat: Mapped[float | None] = mapped_column(Numeric(9, 6))
    gps_lng: Mapped[float | None] = mapped_column(Numeric(9, 6))
    gps_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    server_captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    watermark_meta: Mapped[dict | None] = mapped_column(JSONB)
    upload_status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    session: Mapped["AuditSession"] = relationship(foreign_keys=[session_id], lazy="selectin")
    finding: Mapped["Finding"] = relationship(foreign_keys=[finding_id], lazy="selectin")