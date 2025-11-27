"""Regions master data endpoints — CRUD, FSM status, and chained FK guard."""

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, DbSession, require_permission
from app.models.users import User
from app.schemas.common import Envelope, Paginated
from app.schemas.region import (
    RegionCreateRequest,
    RegionDetailOut,
    RegionOut,
    RegionUpdateRequest,
)
from app.services import region_service

router = APIRouter(prefix="/regions", tags=["Regions"])

_write_guard = Depends(require_permission("master:write"))


@router.get("", response_model=Paginated[RegionOut, RegionOut])
async def list_regions(
    current: CurrentUser,
    session: DbSession,
    search: str | None = Query(None, description="Pencarian kode, nama, negara, atau sales region"),
    status: str | None = Query(None, description="Filter status FSM: ACTIVE, INACTIVE, RETIRED"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
) -> Paginated[RegionOut, RegionOut]:
    """Daftar wilayah operasional & sales dengan paginasi dan filter status."""
    items, meta = await region_service.list_regions(
        session=session,
        search=search,
        status=status,
        page=page,
        per_page=per_page,
    )
    return Paginated[RegionOut, RegionOut](data=items, meta=meta)


@router.post("", response_model=Envelope[RegionOut], status_code=status.HTTP_201_CREATED)
async def create_region(
    body: RegionCreateRequest,
    session: DbSession,
    current: User = _write_guard,
) -> Envelope[RegionOut]:
    """Tambah wilayah baru (memerlukan hak akses master:write)."""
    region = await region_service.create_region(
        session=session,
        data=body,
        current_user_id=current.id,
    )
    return Envelope(data=region, message="Wilayah berhasil ditambahkan")


@router.get("/{identifier}", response_model=Envelope[RegionDetailOut])
async def get_region(
    identifier: str,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[RegionDetailOut]:
    """Detail satu wilayah berdasarkan UUID atau kode (termasuk counter hotel & ROM)."""
    detail = await region_service.get_region_by_id_or_code(
        session=session,
        identifier=identifier,
    )
    return Envelope(data=detail)


@router.patch("/{identifier}", response_model=Envelope[RegionOut])
async def update_region(
    identifier: str,
    body: RegionUpdateRequest,
    session: DbSession,
    current: User = _write_guard,
) -> Envelope[RegionOut]:
    """Perbarui wilayah atau transisi status FSM (memerlukan hak akses master:write)."""
    updated = await region_service.update_region(
        session=session,
        identifier=identifier,
        data=body,
        current_user_id=current.id,
    )
    return Envelope(data=updated, message="Wilayah berhasil diperbarui")


@router.delete("/{identifier}", response_model=Envelope[dict])
async def delete_region(
    identifier: str,
    session: DbSession,
    current: User = _write_guard,
) -> Envelope[dict]:
    """Soft-delete wilayah dengan validasi dependensi relasional (hotel aktif)."""
    res = await region_service.delete_region(
        session=session,
        identifier=identifier,
        current_user_id=current.id,
    )
    return Envelope(data=res, message="Wilayah berhasil dihapus")
