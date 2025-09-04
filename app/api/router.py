from fastapi import APIRouter

from app.api.endpoints import (
    analytics,
    audit,
    auth,
    billing,
    capa,
    checklist,
    crm,
    frontpage,
    ingest,
    master,
    notifications,
    translations,
    users,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(master.router)
api_router.include_router(checklist.router)
api_router.include_router(audit.router)
api_router.include_router(ingest.router)
api_router.include_router(analytics.router)
api_router.include_router(capa.router)
api_router.include_router(crm.router)
api_router.include_router(billing.router)
api_router.include_router(frontpage.router)
api_router.include_router(translations.router)
api_router.include_router(notifications.router)