import uuid

UUIDT = uuid.UUID
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import BigInteger, Identity
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.mixins import AppendOnlyMixin, TimestampMixin

UUID_PK = PG_UUID(as_uuid=True)
BIGINT = BigInteger()


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_status", "user_id", "status"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    user_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(50))
    entity_id: Mapped[UUIDT | None] = mapped_column(UUID_PK)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="QUEUED")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Translation(Base, TimestampMixin):
    __tablename__ = "translations"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", "field", "locale"),
        Index("ix_translations_entity", "entity_type", "entity_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[UUIDT] = mapped_column(UUID_PK, nullable=False)
    field: Mapped[str] = mapped_column(String(100), nullable=False)
    locale: Mapped[str] = mapped_column(String(10), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))


class AuditLog(Base, AppendOnlyMixin):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_entity", "entity_type", "entity_id", "at"),
        Index("ix_audit_logs_actor", "actor_id", "at"),
        Index("ix_audit_logs_at", "at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    actor_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[UUIDT] = mapped_column(UUID_PK, nullable=False)
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())