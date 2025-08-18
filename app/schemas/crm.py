"""CRM schemas — openapi.yaml `/crm/leads*` (PRD-F-07, task 8a).

Kanban status LEAD/CONTACTED/PROSPECT/CONFIRMED/LOST (F-07); `lost_reason`
wajib saat LOST; `next_followup_at` untuk reminder otomatis — tercermin juga
di respons aktivitas (jadwal terbaru lead setelah aktivitas).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import AliasPath, BaseModel, ConfigDict, EmailStr, Field

from app.schemas.common import HybridId

LEAD_STATUS = Literal["LEAD", "CONTACTED", "PROSPECT", "CONFIRMED", "LOST"]
LEAD_STATUSES = {"LEAD", "CONTACTED", "PROSPECT", "CONFIRMED", "LOST"}
LEAD_SOURCE = Literal["RFP_PORTAL", "CROSS_SELLING", "REFERRAL", "MANUAL"]
INSTITUTION_TYPE = Literal["GOV", "PRIVATE"]
ACTIVITY_TYPE = Literal["CALL", "EMAIL", "MEETING", "NOTE"]
ACTIVITY_TYPES = {"CALL", "EMAIL", "MEETING", "NOTE"}
QUOTATION_STATUS = Literal["DRAFT", "SENT", "ACCEPTED", "DECLINED"]
QUOTATION_STATUSES = {"DRAFT", "SENT", "ACCEPTED", "DECLINED"}
MILESTONE_TYPE = Literal["SPK", "NPWP", "BAST", "LPJ"]
MILESTONE_STATUS = Literal["EXPECTED", "UPLOADED", "PAID", "OVERDUE"]


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    lead_no: str
    hotel_id: uuid.UUID = Field(validation_alias=AliasPath("hotel", "uuid"))
    source: str
    institution_type: str
    company_name: str
    pic_name: str | None
    pic_phone: str | None
    pic_email: str | None
    province_id: uuid.UUID | None = Field(
        default=None, validation_alias=AliasPath("province", "uuid")
    )
    status: str
    lost_reason: str | None
    next_followup_at: datetime | None
    amount_est: float | None
    owner_id: uuid.UUID = Field(validation_alias=AliasPath("owner", "uuid"))
    referred_from_hotel_id: uuid.UUID | None = Field(
        default=None, validation_alias=AliasPath("referred_from_hotel", "uuid")
    )
    created_at: datetime
    updated_at: datetime | None


class LeadActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    lead_id: uuid.UUID = Field(validation_alias=AliasPath("lead", "uuid"))
    type: str
    note: str | None
    next_followup_at: datetime | None = None
    actor_id: uuid.UUID = Field(validation_alias=AliasPath("actor", "uuid"))
    at: datetime


class LeadCreateRequest(BaseModel):
    hotel_id: HybridId
    source: LEAD_SOURCE = Field(default="MANUAL")
    institution_type: INSTITUTION_TYPE = Field(default="PRIVATE")
    company_name: str = Field(..., min_length=2, max_length=255)
    pic_name: str | None = Field(default=None, max_length=255)
    pic_phone: str | None = Field(default=None, max_length=20)
    pic_email: EmailStr | None = None
    province_id: HybridId | None = None
    amount_est: float | None = Field(default=None, ge=0)
    next_followup_at: datetime | None = None


class LeadUpdateRequest(BaseModel):
    status: LEAD_STATUS | None = None
    lost_reason: str | None = None
    next_followup_at: datetime | None = None
    amount_est: float | None = Field(default=None, ge=0)
    owner_id: HybridId | None = None
    pic_name: str | None = Field(default=None, max_length=255)
    pic_phone: str | None = Field(default=None, max_length=20)
    pic_email: EmailStr | None = None


class AddActivityRequest(BaseModel):
    type: ACTIVITY_TYPE = Field(default="NOTE")
    note: str | None = Field(default=None, max_length=2000)
    next_followup_at: datetime | None = None


class ReferLeadRequest(BaseModel):
    to_hotel_id: HybridId
    commission_amount: float | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=500)


class LeadReferralOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    lead_id: uuid.UUID = Field(validation_alias=AliasPath("lead", "uuid"))
    from_hotel_id: uuid.UUID = Field(validation_alias=AliasPath("from_hotel", "uuid"))
    to_hotel_id: uuid.UUID = Field(validation_alias=AliasPath("to_hotel", "uuid"))
    commission_amount: float | None
    status: str
    note: str | None
    created_at: datetime


class QuotationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    quotation_no: str
    lead_id: uuid.UUID = Field(validation_alias=AliasPath("lead", "uuid"))
    hotel_id: uuid.UUID = Field(validation_alias=AliasPath("hotel", "uuid"))
    event_date: date | None
    event_name: str | None
    package_type: str
    pax_count: int | None
    sbm_rate_id: uuid.UUID | None = Field(
        default=None, validation_alias=AliasPath("sbm_rate", "uuid")
    )
    sbm_rate_value: float | None
    sbm_fiscal_year: int | None
    gross_amount: float
    discount_amount: float
    final_amount: float
    discount_approval_status: str
    status: str
    created_by: uuid.UUID | None = Field(
        default=None, validation_alias=AliasPath("created_by_user", "uuid")
    )
    created_at: datetime
    updated_at: datetime | None


class QuotationCreateRequest(BaseModel):
    """Generate quotation — server menghitung SBM max per (provinsi × package ×
    tahun anggaran), snapshot ke quotation (E3), validasi pagu (F-09).

    `gross_amount` optional — bila kosong, dipakai `lead.amount_est` (quotation
    1-klik dari estimasi lead). `discount_amount` default 0.
    """

    lead_id: HybridId
    event_date: date
    event_name: str | None = Field(default=None, max_length=255)
    package_type: Literal["FULLDAY", "HALFDAY", "FULLBOARD"] = "FULLDAY"
    pax_count: int = Field(ge=1, le=10000)
    gross_amount: float | None = Field(default=None, gt=0)
    discount_amount: float = Field(default=0, ge=0)


class BillingMilestoneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    quotation_id: uuid.UUID = Field(validation_alias=AliasPath("quotation", "uuid"))
    milestone_type: str
    doc_no: str | None
    doc_key: str | None
    status: str
    amount: float | None
    due_date: date
    paid_at: datetime | None
    updated_by: uuid.UUID | None = Field(
        default=None, validation_alias=AliasPath("updated_by_user", "uuid")
    )
    created_at: datetime
    updated_at: datetime | None


class BillingMilestoneCreateRequest(BaseModel):
    """Buat milestone dokumen dinas (F-10) utk quotation ACCEPTED.

    Satu `milestone_type` (SPK/NPWP/BAST/LPJ) per quotation — finansial
    immutable, tidak ada delete; status EXPECTED di awal.
    """

    quotation_id: HybridId
    milestone_type: MILESTONE_TYPE
    due_date: date
    amount: float | None = Field(default=None, ge=0)
    doc_no: str | None = Field(default=None, max_length=100)


class BillingMilestoneUpdateRequest(BaseModel):
    """Update milestone: lampirkan dokumen / tandai PAID (F-10).

    Transisi `UPLOADED` wajib `doc_key`; `PAID` terminal & mengisi `paid_at`.
    """

    status: MILESTONE_STATUS | None = None
    doc_key: str | None = Field(default=None, max_length=500)
    doc_no: str | None = Field(default=None, max_length=100)
    paid_at: datetime | None = None


class QuotationDetailOut(QuotationOut):
    milestones: list[BillingMilestoneOut] = []
    pdf_url: str | None = None


class SbmRateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    province_id: uuid.UUID = Field(validation_alias=AliasPath("province", "uuid"))
    province_code: str | None = None
    province_name: str | None = None
    package_type: str
    max_rate_per_pax: float
    fiscal_year: int
    is_active: bool
    updated_by: uuid.UUID | None = Field(
        default=None, validation_alias=AliasPath("updated_by_user", "uuid")
    )


class SbmRateUpdateRequest(BaseModel):
    max_rate_per_pax: float | None = Field(default=None, gt=0)
    fiscal_year: int | None = Field(default=None, ge=2000, le=2100)
    is_active: bool | None = None


class LostReasonBreakdownRow(BaseModel):
    """Satu baris agregasi Lost Reason (PRD-F-07): alasan → jumlah lead + nilai.

    `pct` = share jumlah lead LOST terhadap total dalam periode (0-100, 1 desimal).
    """

    reason: str
    count: int
    lost_amount: float
    pct: float


class LostReasonTrendRow(BaseModel):
    """Agregasi per kuartal dalam periode — mendukung metrik keberhasilan
    'Lost Reason analytics tersedia per kuartal'."""

    quarter: str  # "2027-Q1"
    count: int
    lost_amount: float


class LostReasonAnalyticsOut(BaseModel):
    """Aggregat Lost Reason Analytics (F-07, task 8e).

    `total_lost` = jumlah lead LOST; `total_leads` = seluruh lead dalam periode
    (dipakai menghitung `lost_rate`); `breakdown` per alasan; `trend` per kuartal.
    """

    period: str | None = None
    total_leads: int
    total_lost: int
    lost_rate: float | None = None
    total_lost_amount: float
    breakdown: list[LostReasonBreakdownRow]
    trend: list[LostReasonTrendRow]