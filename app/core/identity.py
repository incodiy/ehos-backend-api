"""Identity helpers — translate public `uuid` (atau internal `id`) ke internal BIGINT (ARD-007 / Constraint J3).

Hybrid identity: relasi internal (FK, join, index) memakai `id BIGINT identity`
(PK), sedangkan API/klien melihat `uuid` (UUID publik). Translasi terjadi
di service layer sebelum menyentuh SQL. Helper ini single source utk lookup.

Karena test/konsumen lama kadang mengirim nilai internal (`SELECT id`), helper
`get_by_uuid` menerima keduanya: UUID publik ATAU internal id numerik — aman
di-*resolve* ke baris yang sama.

Usage:
    hotel = await get_by_uuid(session, Hotel, hotel_uuid)   # None bila tidak ada
"""

import uuid as uuid_lib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _is_intish(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, str):
        return value.strip().isdigit()
    return False


async def get_by_uuid(
    session: AsyncSession,
    model: type,
    public_uuid: uuid_lib.UUID | str | int | None,
) -> object | None:
    """Return ORM row by public `uuid` — atau internal `id` bila nilai numerik.

    Nilai numerik (internal id) & UUID publik diterima; disarankan konsumen
    hanya memakai UUID, internal id diterima demi kompatibilitas.
    """
    if public_uuid is None:
        return None
    if _is_intish(public_uuid):
        return await session.get(model, int(str(public_uuid).strip()))
    return await session.scalar(select(model).where(model.uuid == public_uuid))


async def resolve_ids(
    session: AsyncSession,
    model: type,
    public_values: list[uuid_lib.UUID | str | int],
) -> set[int]:
    """Resolve daftar UUID publik (atau internal id) → set internal `id`.

    Nilai yang tidak ditemukan diabaikan — pemanggil bertanggung jawab
    memvalidasi kelengkapan bila wajib semua.
    """
    if not public_values:
        return set()
    ints: set[int] = set()
    uuids: list[str] = []
    for value in public_values:
        if value is None:
            continue
        if _is_intish(value):
            ints.add(int(str(value).strip()))
        else:
            uuids.append(str(value))
    resolved: set[int] = ints
    if uuids:
        rows = await session.execute(select(model.id).where(model.uuid.in_(uuids)))
        resolved |= set(rows.scalars().all())
    return resolved


async def get_by_id(session: AsyncSession, model: type, internal_id: int | None):
    """Return ORM row by internal BIGINT `id` — or None. Dipakai untuk alur
    internal yang sudah memegang id BIGINT (mis. dari relationship/aggregation)."""
    if internal_id is None:
        return None
    return await session.get(model, internal_id)


async def require_by_uuid(
    session: AsyncSession,
    model: type,
    public_uuid: uuid_lib.UUID | str | int | None,
) -> object:
    """Same as get_by_uuid but raise LookupError when missing (caller maps to 404)."""
    row = await get_by_uuid(session, model, public_uuid)
    if row is None:
        raise LookupError(f"{model.__name__}(uuid={public_uuid}) not found")
    return row