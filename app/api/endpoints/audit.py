"""Audit endpoints aggregator — `/audit` (F-02/F-04/F-05).

Menggabungkan modul-modul terfokus (Anti-God-File):
- audit_sessions: Lifecycle sesi (list, create, detail, update, delete, submit, reopen, publish, report.pdf)
- audit_media: Registrasi media presigned MinIO, list, confirm (C1-C4)
- audit_sync: Offline sync push/pull & conflict resolution (F-05)
"""

from fastapi import APIRouter

from app.api.endpoints import audit_media, audit_sessions, audit_sync
from app.api.endpoints.audit_sessions import _has_hotel_scope, _is_corporate

router = APIRouter(prefix="/audit", tags=["audit"])

router.include_router(audit_sessions.router)
router.include_router(audit_media.router)
router.include_router(audit_sync.router)

__all__ = ["router", "_has_hotel_scope", "_is_corporate"]