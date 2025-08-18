"""RFP intake schemas — openapi.yaml `RfpPublicCreate` + response (F-11, task 8c)."""

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class RfpPublicCreate(BaseModel):
    company_name: str = Field(min_length=2, max_length=255)
    institution_type: Literal["GOV", "PRIVATE"] = "GOV"
    pic_name: str = Field(min_length=2, max_length=255)
    phone: str = Field(min_length=6, max_length=20)
    email: EmailStr | None = None
    event_date: date
    pax: int = Field(ge=1, le=10000)
    package_type: Literal["FULLDAY", "HALFDAY", "FULLBOARD"] = "FULLDAY"
    city: str = Field(min_length=2, max_length=100)
    target_hotel_code: str | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None, max_length=2000)


class RfpPublicOut(BaseModel):
    ref_no: str
    status: str


class RfpLeadLink(BaseModel):
    """Kait RFP → lead (dipakai seeder/handoff; bukan bagian kontrak publik)."""

    rfp_id: uuid.UUID
    lead_no: str | None = None
    lead_id: uuid.UUID | None = None