from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, DbSession, require_permission
from app.models.users import User
from app.schemas.brand import (
    BrandCreateRequest,
    BrandDetailOut,
    BrandOut,
    BrandTierUpdateRequest,
    BrandUpdateRequest,
)
from app.schemas.common import Envelope, Paginated
from app.services import brand_service

router = APIRouter(prefix="/brands", tags=["Master Data - Brands"])

_write_guard = Depends(require_permission("master:write"))


@router.get("", response_model=Paginated[BrandOut, BrandOut])
async def list_brands(
    current: CurrentUser,
    session: DbSession,
    search: str | None = Query(default=None, description="Cari nama atau kode brand"),
    tier: str | None = Query(default=None, description="Filter tier brand (Luxury, Upscale, etc.)"),
    status: str | None = Query(default=None, description="Filter status (ACTIVE, INACTIVE, RETIRED)"),
    page: int = Query(default=1, ge=1, description="Nomor halaman"),
    per_page: int = Query(default=50, ge=1, le=100, description="Item per halaman"),
) -> Paginated[BrandOut, BrandOut]:
    """Daftar brand korporat dengan paginasi, filter tier, status, dan jumlah hotel terhubung."""
    items, meta = await brand_service.list_brands(
        session=session,
        search=search,
        tier=tier,
        status=status,
        page=page,
        per_page=per_page,
    )
    return Paginated[BrandOut, BrandOut](data=items, meta=meta)


@router.post("", response_model=Envelope[BrandOut], status_code=status.HTTP_201_CREATED)
async def create_brand(
    body: BrandCreateRequest,
    session: DbSession,
    current: User = _write_guard,
) -> Envelope[BrandOut]:
    """Tambah brand baru (memerlukan hak akses master:write)."""
    brand = await brand_service.create_brand(
        session=session,
        data=body,
        current_user_id=current.id,
    )
    return Envelope(data=brand, message="Brand berhasil ditambahkan")


@router.get("/{identifier}", response_model=Envelope[BrandDetailOut])
async def get_brand(
    identifier: str,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[BrandDetailOut]:
    """Ambil detail brand beserta daftar hotel terafiliasi berdasarkan UUID atau Kode Brand."""
    brand = await brand_service.get_brand_detail(session=session, identifier=identifier)
    return Envelope(data=brand)


@router.put("/{identifier}", response_model=Envelope[BrandOut])
async def update_brand(
    identifier: str,
    body: BrandUpdateRequest,
    session: DbSession,
    current: User = _write_guard,
) -> Envelope[BrandOut]:
    """Update data brand (nama, tier, status, atau kode) (memerlukan hak akses master:write)."""
    brand = await brand_service.update_brand(
        session=session,
        identifier=identifier,
        data=body,
        current_user_id=current.id,
    )
    return Envelope(data=brand, message="Data brand berhasil diperbarui")


@router.patch("/{code}/tier", response_model=Envelope[BrandOut])
async def update_brand_tier_direct(
    code: str,
    body: BrandTierUpdateRequest,
    session: DbSession,
    current: User = _write_guard,
) -> Envelope[BrandOut]:
    """Konfigurasi tier brand khusus untuk PRD-F-01 checklist filter."""
    brand = await brand_service.update_brand_tier(
        session=session,
        code=code,
        data=body,
        current_user_id=current.id,
    )
    return Envelope(data=brand, message="Tier brand berhasil diperbarui")


@router.patch("/{identifier}", response_model=Envelope[BrandOut])
async def patch_brand(
    identifier: str,
    body: BrandUpdateRequest,
    session: DbSession,
    current: User = _write_guard,
) -> Envelope[BrandOut]:
    """Patch data brand atau kompatibilitas patch tier brand lama."""
    brand = await brand_service.update_brand(
        session=session,
        identifier=identifier,
        data=body,
        current_user_id=current.id,
    )
    return Envelope(data=brand, message="Data brand berhasil diperbarui")


@router.delete("/{identifier}", response_model=Envelope[None])
async def delete_brand(
    identifier: str,
    session: DbSession,
    current: User = _write_guard,
) -> Envelope[None]:
    """Hapus brand (soft-delete) dengan proteksi integritas FK terhadap hotel terikat."""
    await brand_service.delete_brand(
        session=session,
        identifier=identifier,
        current_user_id=current.id,
    )
    return Envelope(data=None, message="Brand berhasil dihapus")
