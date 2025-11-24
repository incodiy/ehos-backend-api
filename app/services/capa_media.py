"""CAPA media split-path (task 7c, Constraint C2 / ARD-005) — presign → confirm.

Keadaan daftar CapaMedia (SYSTEM.md §5.2 state machine):
    PENDING ─(presign)─▶ PRESIGNED ─(PUT object)─▶ confirm(verify) ─▶ VERIFIED
                                     │                                 (sha/size/mime mismatch)
                                     └────────────▶ FAILED ─(retry presign)─▶ PRESIGNED

Resolve (7a) mendaftarkan bukti AFTER sebagai PENDING. Klien memanggil presign
per media → dapat presigned PUT URL → PUT 1-per-1 → confirm. Confirm memverifikasi
reachability/ukuran/mime via `media_storage.verify_object`; storage tak
terjangkau → StorageUnavailableError (503 jujur, G4). `before_after_summary`
menyajikan komparasi bukti BEFORE vs AFTER utk Four-Eyes verification hub.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CapaMedia
from app.services.capa_lifecycle import InvalidTransitionError
from app.services.media_storage import (
    StorageUnavailableError,
    presigned_get_url,
    presigned_put_url,
    verify_object,
)

VERIFIED = "VERIFIED"
FAILED = "FAILED"
PRESIGNED = "PRESIGNED"


def ensure_owned(media: CapaMedia, ticket_id: int) -> None:
    if media.ticket_id != ticket_id:
        raise InvalidTransitionError("Media bukan milik ticket ini")


async def presign_ticket_media(
    session: AsyncSession, media: CapaMedia, ticket_id: int, actor_id: int
) -> tuple[CapaMedia, str]:
    """Keluarkan presigned PUT URL (renewable) untuk media milik ticket."""
    ensure_owned(media, ticket_id)
    if media.upload_status == VERIFIED:
        raise InvalidTransitionError("Media sudah diverifikasi (VERIFIED)")
    media.upload_status = PRESIGNED
    url = presigned_put_url(media.object_key, media.mime)
    return media, url


async def confirm_ticket_media(
    session: AsyncSession, media: CapaMedia, ticket_id: int, actor_id: int
) -> CapaMedia:
    """Verifikasi object telah ter-upload: reachable + size + mime cocok."""
    ensure_owned(media, ticket_id)
    if media.upload_status == VERIFIED:
        return media  # idempotent
    try:
        ok = verify_object(media.object_key, media.size_bytes, media.mime)
    except StorageUnavailableError:
        raise
    media.upload_status = VERIFIED if ok else FAILED
    return media


def _count_verified(group: list[CapaMedia]) -> int:
    return sum(1 for m in group if m.upload_status == VERIFIED)


def before_after_summary(medias: list[CapaMedia]) -> dict:
    """Komparasi bukti BEFORE vs AFTER utk Four-Eyes (verification hub)."""
    before = [m for m in medias if m.phase == "BEFORE"]
    after = [m for m in medias if m.phase == "AFTER"]
    verified = _count_verified(after)
    return {
        "before": {"count": len(before), "verified": _count_verified(before)},
        "after": {"count": len(after), "verified": verified},
        "has_verified_after": verified > 0,
        "ready": verified > 0,
    }


def presign_ticket_media_get(media: CapaMedia, ticket_id: int) -> str:
    """Presigned GET URL utk menampilkan bukti media (verification hub).

    Hanya media VERIFIED yang benar-benar ada di object store; media PENDING/
    PRESIGNED/FAILED tidak akan ditemukan saat diakses → G4 (honest data).
    """
    ensure_owned(media, ticket_id)
    if media.upload_status != VERIFIED:
        raise InvalidTransitionError("Media belum VERIFIED — bukti belum tervalidasi")
    return presigned_get_url(media.object_key)