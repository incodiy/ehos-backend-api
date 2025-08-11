from __future__ import annotations

import uuid

UUIDT = uuid.UUID
from datetime import date, datetime

from sqlalchemy import (
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
from app.models.mixins import AppendOnlyMixin, SoftDeleteMixin, TimestampMixin

UUID_PK = PG_UUID(as_uuid=True)
BIGINT = BigInteger()


class Lead(Base, TimestampMixin):
    __tablename__ = "leads"
    __table_args__ = (
        Index("ix_leads_hotel_status", "hotel_id", "status"),
        Index("ix_leads_owner_followup", "owner_id", "next_followup_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    lead_no: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    hotel_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("hotels.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    institution_type: Mapped[str] = mapped_column(String(20), nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    pic_name: Mapped[str | None] = mapped_column(String(255))
    pic_phone: Mapped[str | None] = mapped_column(String(20))
    pic_email: Mapped[str | None] = mapped_column(String(255))
    province_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("provinces.id"))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="LEAD")
    lost_reason: Mapped[str | None] = mapped_column(Text)
    next_followup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    amount_est: Mapped[float | None] = mapped_column(Numeric(15, 2))
    owner_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("users.id"), nullable=False)
    referred_from_hotel_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("hotels.id"))
    created_by: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))
    hotel: Mapped["Hotel"] = relationship(foreign_keys=[hotel_id], lazy="selectin")
    province: Mapped["Province | None"] = relationship(foreign_keys=[province_id], lazy="selectin")
    owner: Mapped["User"] = relationship(foreign_keys=[owner_id], lazy="selectin")
    referred_from_hotel: Mapped["Hotel | None"] = relationship(
        foreign_keys=[referred_from_hotel_id], lazy="selectin"
    )


class LeadActivity(Base, AppendOnlyMixin):
    __tablename__ = "lead_activities"
    __table_args__ = (Index("ix_lead_activities_lead", "lead_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    lead_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    actor_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("users.id"), nullable=False)
    lead: Mapped["Lead"] = relationship(foreign_keys=[lead_id], lazy="selectin")
    actor: Mapped["User"] = relationship(foreign_keys=[actor_id], lazy="selectin")


class LeadReferral(Base, TimestampMixin):
    __tablename__ = "lead_referrals"
    __table_args__ = (Index("ix_lead_referrals_to_hotel", "to_hotel_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    lead_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("leads.id"), nullable=False)
    from_hotel_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("hotels.id"), nullable=False)
    to_hotel_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("hotels.id"), nullable=False)
    commission_amount: Mapped[float | None] = mapped_column(Numeric(15, 2))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    lead: Mapped["Lead"] = relationship(foreign_keys=[lead_id], lazy="selectin")
    from_hotel: Mapped["Hotel"] = relationship(foreign_keys=[from_hotel_id], lazy="selectin")
    to_hotel: Mapped["Hotel"] = relationship(foreign_keys=[to_hotel_id], lazy="selectin")


class Quotation(Base, TimestampMixin):
    __tablename__ = "quotations"
    __table_args__ = (
        Index("ix_quotation_lead", "lead_id"),
        Index("ix_quotation_hotel_event", "hotel_id", "event_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    quotation_no: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    lead_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("leads.id"), nullable=False)
    hotel_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("hotels.id"), nullable=False)
    event_date: Mapped[date | None] = mapped_column(Date)
    event_name: Mapped[str | None] = mapped_column(String(255))
    package_type: Mapped[str] = mapped_column(String(20), nullable=False)
    pax_count: Mapped[int | None] = mapped_column(Integer)
    sbm_rate_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("government_sbm_rates.id"))
    sbm_rate_value: Mapped[float | None] = mapped_column(Numeric(15, 2))
    sbm_fiscal_year: Mapped[int | None] = mapped_column(Integer)
    gross_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    discount_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    final_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    discount_approval_status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    pdf_key: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))
    lead: Mapped["Lead"] = relationship(foreign_keys=[lead_id], lazy="selectin")
    hotel: Mapped["Hotel"] = relationship(foreign_keys=[hotel_id], lazy="selectin")
    sbm_rate: Mapped["GovernmentSbmRate | None"] = relationship(
        foreign_keys=[sbm_rate_id], lazy="selectin"
    )
    created_by_user: Mapped["User | None"] = relationship(foreign_keys=[created_by], lazy="selectin")


class GovernmentSbmRate(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "government_sbm_rates"
    __table_args__ = (
        UniqueConstraint("province_id", "package_type", "fiscal_year"),
        Index("ix_sbm_province_year", "province_id", "fiscal_year"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    province_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("provinces.id"), nullable=False)
    package_type: Mapped[str] = mapped_column(String(20), nullable=False)
    max_rate_per_pax: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))
    province: Mapped["Province"] = relationship(foreign_keys=[province_id], lazy="selectin")
    updated_by_user: Mapped["User | None"] = relationship(foreign_keys=[updated_by], lazy="selectin")


class BillingMilestone(Base, TimestampMixin):
    __tablename__ = "billing_milestones"
    __table_args__ = (
        Index("ix_billing_quotation", "quotation_id"),
        Index("ix_billing_due_status", "due_date", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    quotation_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("quotations.id"), nullable=False
    )
    milestone_type: Mapped[str] = mapped_column(String(20), nullable=False)
    doc_no: Mapped[str | None] = mapped_column(String(100))
    doc_key: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="EXPECTED")
    amount: Mapped[float | None] = mapped_column(Numeric(15, 2))
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))
    quotation: Mapped["Quotation"] = relationship(foreign_keys=[quotation_id], lazy="selectin")
    updated_by_user: Mapped["User | None"] = relationship(foreign_keys=[updated_by], lazy="selectin")


class RfpRequest(Base, TimestampMixin):
    __tablename__ = "rfp_requests"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    ref_no: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    pic_name: Mapped[str | None] = mapped_column(String(255))
    pic_phone: Mapped[str | None] = mapped_column(String(20))
    pic_email: Mapped[str] = mapped_column(String(255), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False)
    target_hotel_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("hotels.id"))
    assigned_hotel_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("hotels.id"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="NEW")