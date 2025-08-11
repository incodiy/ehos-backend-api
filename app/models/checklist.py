from __future__ import annotations

import uuid

UUIDT = uuid.UUID
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import BigInteger, Identity
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.mixins import SoftDeleteMixin, TimestampMixin

UUID_PK = PG_UUID(as_uuid=True)
BIGINT = BigInteger()


class ChecklistTemplate(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "checklist_templates"
    __table_args__ = (UniqueConstraint("department", "name", "version"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    department: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    brand_tier: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[int] = mapped_column(BIGINT, ForeignKey("users.id"), nullable=False)
    publisher: Mapped["User"] = relationship(foreign_keys=[published_by], lazy="selectin")


class ChecklistSection(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "checklist_sections"
    __table_args__ = (UniqueConstraint("template_id", "code"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    template_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("checklist_templates.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("checklist_sections.id"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    template: Mapped["ChecklistTemplate"] = relationship(foreign_keys=[template_id], lazy="selectin")
    parent: Mapped["ChecklistSection | None"] = relationship(
        foreign_keys=[parent_id], remote_side=[id], lazy="selectin"
    )


class ChecklistItem(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "checklist_items"
    __table_args__ = (UniqueConstraint("section_id", "code"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    section_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("checklist_sections.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    rubric_type: Mapped[str] = mapped_column(String(20), nullable=False)
    max_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    weight: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    na_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_life_safety: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    section: Mapped["ChecklistSection"] = relationship(foreign_keys=[section_id], lazy="selectin")