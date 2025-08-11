from __future__ import annotations

import uuid

UUIDT = uuid.UUID
from datetime import datetime

from sqlalchemy import (
    CHAR,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
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


class CapaTicket(Base, TimestampMixin):
    __tablename__ = "capa_tickets"
    __table_args__ = (
        Index("ix_capa_status_due", "status", "due_at"),
        Index("ix_capa_hotel_priority", "hotel_id", "priority"),
        Index("ix_capa_assigned", "assigned_to"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    finding_id: Mapped[int | None] = mapped_column(
        BIGINT, ForeignKey("findings.id", ondelete="RESTRICT")
    )
    hotel_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("hotels.id"), nullable=False)
    department_id: Mapped[int | None] = mapped_column(
        BIGINT, ForeignKey("hotel_departments.id")
    )
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=3)
    sla_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=168)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="OPEN")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    assigned_to: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))
    escalation_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))
    reporter_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))
    receipt_id: Mapped[str | None] = mapped_column(String(12), unique=True)
    origin: Mapped[str] = mapped_column(String(20), nullable=False, default="AUDIT")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))
    finding: Mapped["Finding | None"] = relationship(foreign_keys=[finding_id], lazy="selectin")
    hotel: Mapped["Hotel"] = relationship(foreign_keys=[hotel_id], lazy="selectin")
    department: Mapped["HotelDepartment | None"] = relationship(
        foreign_keys=[department_id], lazy="selectin"
    )
    assigned_to_user: Mapped["User | None"] = relationship(foreign_keys=[assigned_to], lazy="selectin")
    created_by_user: Mapped["User | None"] = relationship(foreign_keys=[created_by], lazy="selectin")
    reporter: Mapped["User | None"] = relationship(foreign_keys=[reporter_id], lazy="selectin")
    closed_by_user: Mapped["User | None"] = relationship(foreign_keys=[closed_by], lazy="selectin")


class CapaStatusHistory(Base, AppendOnlyMixin):
    __tablename__ = "capa_status_histories"
    __table_args__ = (Index("ix_capa_status_history_ticket", "ticket_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    ticket_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("capa_tickets.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(30))
    to_status: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("users.id"), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ticket: Mapped["CapaTicket"] = relationship(foreign_keys=[ticket_id], lazy="selectin")
    actor: Mapped["User"] = relationship(foreign_keys=[actor_id], lazy="selectin")


class CapaMedia(Base, AppendOnlyMixin):
    __tablename__ = "capa_media"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    ticket_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("capa_tickets.id"), nullable=False
    )
    phase: Mapped[str] = mapped_column(String(10), nullable=False, default="AFTER")
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
    ticket: Mapped["CapaTicket"] = relationship(foreign_keys=[ticket_id], lazy="selectin")