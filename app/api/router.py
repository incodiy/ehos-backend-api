from fastapi import APIRouter

from app.api.endpoints import (
    analytics,
    audit,
    audit_logs,
    auth,
    billing,
    brands,
    capa,
    checklist,
    crm,
    dashboard,
    frontpage,
    ingest,
    master,
    notifications,
    regions,
    translations,
    users,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(regions.router)
api_router.include_router(brands.router)
api_router.include_router(master.router)
api_router.include_router(checklist.router)
api_router.include_router(audit.router)
api_router.include_router(audit_logs.router)
api_router.include_router(ingest.router)
api_router.include_router(analytics.router)
api_router.include_router(dashboard.router)
api_router.include_router(capa.router)
api_router.include_router(crm.router)
api_router.include_router(billing.router)
api_router.include_router(frontpage.router)
api_router.include_router(translations.router)
api_router.include_router(notifications.router)