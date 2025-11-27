"""CAPA endpoints aggregator — `/capa` (F-03/F-04, task 7a-7d).

Menggabungkan modul-modul terfokus (Anti-God-File):
- capa_tickets: Lifecycle tiket (list, create, detail, patch)
- capa_workflow: Alur Four-Eyes (assign, resolve, verify GM/QA, escalate, history)
- capa_media: Bukti media split-path presigned MinIO (list, presign, confirm)
"""

from fastapi import APIRouter

from app.api.endpoints import capa_media, capa_tickets, capa_workflow

router = APIRouter(prefix="/capa", tags=["capa"])

router.include_router(capa_tickets.router)
router.include_router(capa_workflow.router)
router.include_router(capa_media.router)

__all__ = ["router"]