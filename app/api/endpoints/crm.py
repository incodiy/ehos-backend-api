"""CRM endpoints aggregator router — openapi.yaml `/crm/*` (PRD-F-07/08/09/10, task 8).

Modular SRP Architecture:
- crm_common: Shared helpers, RBAC scope guards, entity resolution
- crm_leads: Leads pipeline CRUD, activities, referrals (F-07, F-08)
- crm_quotations: Quotations, SBM pagu verification, PDF export (F-09)
- crm_sbm: SBM government rates management (E1, E2)
- crm_analytics: Lost reason analytics per quarter (F-07)
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.endpoints import (
    crm_analytics,
    crm_leads,
    crm_quotations,
    crm_sbm,
)
from app.api.endpoints.crm_common import (
    _get_lead,
    _get_quotation,
    _lead_activities,
    _lead_out,
    _lead_quotations,
    _lead_referrals,
    _quarter_start,
    _quotation_detail,
    _quotation_pdf_url,
    _require_manage,
    _require_read,
    _resolve_hotel_uuid,
    _scope_hotel_ids,
)
from app.services.media_storage import presigned_get_url, upload_bytes

router = APIRouter(prefix="/crm", tags=["crm"])

# Mount sub-routers
router.include_router(crm_leads.router)
router.include_router(crm_quotations.router)
router.include_router(crm_sbm.router)
router.include_router(crm_analytics.router)

__all__ = [
    "router",
    "upload_bytes",
    "presigned_get_url",
    "_scope_hotel_ids",
    "_require_read",
    "_require_manage",
    "_resolve_hotel_uuid",
    "_get_lead",
    "_lead_out",
    "_lead_quotations",
    "_lead_activities",
    "_lead_referrals",
    "_get_quotation",
    "_quotation_pdf_url",
    "_quotation_detail",
    "_quarter_start",
]