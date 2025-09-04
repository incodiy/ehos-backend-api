"""User & RBAC endpoints — openapi.yaml `/users` + `/roles` contract.

Creator guard honors Constraint A3 (delegated admin): a manager with only
`user:manage:hotel` may only create unit-level accounts inside their assigned
hotels; `user:manage:global` (ROOT_ADMIN, A4) is unrestricted.
"""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, or_, select, text, update

from app.api.deps import CurrentUser, DbSession
from app.core.identity import get_by_uuid, resolve_ids
from app.core.security import hash_password
from app.models import (
    Permission,
    Role,
    User,
    UserHotelAssignment,
    UserRegionAssignment,
    UserRole,
)
from app.models.master import Hotel, Region
from app.schemas.common import Envelope, HybridId, Paginated, PaginationMeta
from app.schemas.users import (
    PermissionOut,
    ResetPasswordRequest,
    RoleWithPermissionsOut,
    UserCreateRequest,
    UserOut,
    UserUpdateRequest,
)

router = APIRouter(tags=["users", "roles"])

UNIT_ROLES = {"HOTEL_GM", "HOTEL_HOD_TECH", "HOTEL_SALES", "HOTEL_FINANCE"}
CORPORATE_ROLES = {"ROOT_ADMIN", "CORP_EXEC", "CORP_AUDITOR", "REGIONAL_ROM"}
FORBIDDEN_FOR_DELEGATE = CORPORATE_ROLES | {"HOTEL_GM"}


async def _resolve_hotel_ids(session: DbSession, hotel_uuids: list[HybridId]) -> set[int]:
    """Resolve UUID publik (atau internal id) hotel → internal id (wajib semua ditemukan)."""
    if not hotel_uuids:
        return set()
    internal = await resolve_ids(session, Hotel, hotel_uuids)
    if len(internal) != len({str(v) for v in hotel_uuids}):
        raise HTTPException(status_code=422, detail="Ada hotel_id tidak dikenal")
    return internal


@router.get("/roles", response_model=Envelope[list[RoleWithPermissionsOut]])
async def list_roles(session: DbSession) -> Envelope[list[RoleWithPermissionsOut]]:
    roles = (await session.scalars(select(Role).order_by(Role.scope_level, Role.code))).all()
    out: list[RoleWithPermissionsOut] = []
    for role in roles:
        rows = (
            await session.execute(
                text(
                    "SELECT p.uuid, p.code, p.module, p.action, p.description "
                    "FROM permissions p "
                    "JOIN roles_permissions rp ON rp.permission_id = p.id "
                    "WHERE rp.role_id = :rid ORDER BY p.module, p.code"
                ).bindparams(rid=role.id)
            )
        ).mappings().all()
        out.append(RoleWithPermissionsOut(
            uuid=role.uuid, code=role.code, name=role.name,
            scope_level=role.scope_level, is_system=role.is_system,
            permissions=[PermissionOut(**dict(r)) for r in rows],
        ))
    return Envelope(data=out)


async def _can_manage_user(
    session: DbSession, current: User, target_role_code: str, hotel_ids: list[HybridId]
) -> dict:
    """Return the effective permission headline of the actor. Raises 403 when out of scope."""
    has_global = await session.scalar(
        text(
            "SELECT 1 FROM user_roles ur JOIN roles_permissions rp ON rp.role_id = ur.role_id "
            "JOIN permissions p ON p.id = rp.permission_id "
            "WHERE ur.user_id = :uid AND p.code = 'user:manage:global' LIMIT 1"
        ).bindparams(uid=current.id)
    )
    if has_global:
        return {"scope": "global"}

    has_hotel = await session.scalar(
        text(
            "SELECT 1 FROM user_roles ur JOIN roles_permissions rp ON rp.role_id = ur.role_id "
            "JOIN permissions p ON p.id = rp.permission_id "
            "WHERE ur.user_id = :uid AND p.code = 'user:manage:hotel' LIMIT 1"
        ).bindparams(uid=current.id)
    )
    if not has_hotel:
        raise HTTPException(status_code=403, detail="Tidak berhak mengelola user")

    if target_role_code in FORBIDDEN_FOR_DELEGATE:
        raise HTTPException(status_code=403, detail="Delegated admin tidak boleh membuat role corporate/GM lain (A3)")
    if not hotel_ids:
        raise HTTPException(status_code=422, detail="hotel_ids wajib untuk role unit")

    actor_hotels = set(
        (await session.execute(
            select(UserHotelAssignment.hotel_id).where(
                UserHotelAssignment.user_id == current.id,
                UserHotelAssignment.deleted_at.is_(None),
            )
        )).scalars().all()
    )
    if not set(hotel_ids).issubset(actor_hotels):
        raise HTTPException(status_code=403, detail="Hotel target di luar scope delegasi")
    return {"scope": "hotel"}


@router.get("/users", response_model=Paginated[UserOut, UserOut])
async def list_users(
    current: CurrentUser,
    session: DbSession,
    role_code: str | None = None,
    hotel_id: HybridId | None = None,
    search: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> Paginated[UserOut, UserOut]:
    require_global = (
        await session.scalar(
            text(
                "SELECT 1 FROM user_roles ur JOIN roles_permissions rp ON rp.role_id = ur.role_id "
                "JOIN permissions p ON p.id = rp.permission_id "
                "WHERE ur.user_id = :uid AND p.code IN "
                "('user:read:global','user:read:region','user:read:hotel') LIMIT 1"
            ).bindparams(uid=current.id)
        )
    )
    if require_global is None:
        raise HTTPException(status_code=403, detail="Missing user read permission")

    stmt = (
        select(User)
        .where(User.deleted_at.is_(None))
    )
    if role_code:
        stmt = stmt.join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id)
        stmt = stmt.where(Role.code == role_code)
    if hotel_id:
        hotel = await get_by_uuid(session, Hotel, hotel_id)
        if hotel is None:
            return Paginated[UserOut, UserOut](
                data=[],
                meta=PaginationMeta(current_page=page, per_page=per_page, total=0, last_page=1),
            )
        stmt = stmt.join(UserHotelAssignment, UserHotelAssignment.user_id == User.id)
        stmt = stmt.where(UserHotelAssignment.hotel_id == hotel.id)
    if search:
        stmt = stmt.where(
            or_(User.name.ilike(f"%{search}%"), User.email.ilike(f"%{search}%"))
        )
    count_stmt = stmt.with_only_columns(func.count(User.id).distinct())
    total = (await session.execute(count_stmt)).scalar() or 0
    rows = (
        await session.execute(
            stmt.distinct()
            .order_by(User.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(min(per_page, 200))
        )
    ).scalars().all()
    return Paginated[UserOut, UserOut](
        data=[UserOut.model_validate(u) for u in rows],
        meta=PaginationMeta(
            current_page=page, per_page=per_page,
            total=total, last_page=max(1, (total + per_page - 1) // per_page),
        ),
    )


@router.get("/users/{user_id}", response_model=Envelope[UserOut])
async def get_user(
    user_id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[UserOut]:
    user = await get_by_uuid(session, User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")
    return Envelope(data=UserOut.model_validate(user))


@router.post("/users", response_model=Envelope[UserOut], status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[UserOut]:
    hotel_ids = await _resolve_hotel_ids(session, body.hotel_ids)
    await _can_manage_user(session, current, body.role_code, hotel_ids)

    email = body.email.lower().strip()
    exists = await session.scalar(
        select(User.id).where(User.email == email, User.deleted_at.is_(None))
    )
    if exists:
        raise HTTPException(status_code=409, detail="Email sudah terdaftar")

    role = await session.scalar(select(Role).where(Role.code == body.role_code))
    if role is None:
        raise HTTPException(status_code=422, detail=f"Role tidak dikenal: {body.role_code}")

    region = None
    if body.region_id is not None:
        region = await get_by_uuid(session, Region, body.region_id)
        if region is None:
            raise HTTPException(status_code=422, detail="Region tidak dikenal")

    user = User(
        email=email,
        name=body.name.strip(),
        password_hash=hash_password(body.password),
        is_active=True,
        must_change_password=True,
        preferred_locale=body.preferred_locale or "id",
        created_by=current.id,
        updated_by=current.id,
    )
    session.add(user)
    await session.flush()

    session.add(UserRole(user_id=user.id, role_id=role.id))
    for position, hotel_id in enumerate(hotel_ids):
        session.add(
            UserHotelAssignment(
                user_id=user.id,
                hotel_id=hotel_id,
                role_id=role.id,
                is_primary=position == 0,
            )
        )
    if region is not None:
        session.add(UserRegionAssignment(user_id=user.id, region_id=region.id))
    await session.commit()
    fresh = await session.scalar(select(User).where(User.id == user.id))
    return Envelope(data=UserOut.model_validate(fresh))


async def _load_target_scope(
    session: DbSession, user_id: HybridId
) -> tuple[User, list[int], str]:
    """Load the target user, its active hotel assignment ids and its role code."""
    target = await get_by_uuid(session, User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")
    hotels = list(
        (
            await session.execute(
                select(UserHotelAssignment.hotel_id).where(
                    UserHotelAssignment.user_id == target.id,
                    UserHotelAssignment.deleted_at.is_(None),
                )
            )
        ).scalars().all()
    )
    role_code = await session.scalar(
        select(Role.code)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == target.id)
        .limit(1)
    )
    return target, hotels, role_code or ""


def _actor_hotels(session_hotels: set[int], requested: set[int]) -> None:
    if not requested.issubset(session_hotels):
        raise HTTPException(status_code=403, detail="Hotel target di luar scope delegasi")


@router.patch("/users/{user_id}", response_model=Envelope[UserOut])
async def update_user(
    user_id: HybridId,
    body: UserUpdateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[UserOut]:
    target, target_hotels, target_role_code = await _load_target_scope(session, user_id)

    is_self = target.id == current.id
    has_global = bool(
        await session.scalar(
            text(
                "SELECT 1 FROM user_roles ur JOIN roles_permissions rp ON rp.role_id = ur.role_id "
                "JOIN permissions p ON p.id = rp.permission_id "
                "WHERE ur.user_id = :uid AND p.code = 'user:manage:global' LIMIT 1"
            ).bindparams(uid=current.id)
        )
    )
    actor_hotels = set(
        (
            await session.execute(
                select(UserHotelAssignment.hotel_id).where(
                    UserHotelAssignment.user_id == current.id,
                    UserHotelAssignment.deleted_at.is_(None),
                )
            )
        ).scalars().all()
    )
    if not has_global and not is_self and not set(target_hotels).intersection(actor_hotels):
        raise HTTPException(status_code=403, detail="Di luar scope delegasi (A3)")
    if not has_global and not is_self and target_role_code in FORBIDDEN_FOR_DELEGATE:
        raise HTTPException(status_code=403, detail="Tidak berhak mengelola manager role (A3)")

    add_hotel_ids = await _resolve_hotel_ids(session, body.add_hotel_ids)
    remove_hotel_ids = await _resolve_hotel_ids(session, body.remove_hotel_ids)
    _actor_hotels(actor_hotels, add_hotel_ids)
    _actor_hotels(actor_hotels, remove_hotel_ids)

    if body.name is not None:
        target.name = body.name.strip()
    if body.phone is not None:
        target.phone = body.phone.strip()
    if body.preferred_locale:
        target.preferred_locale = body.preferred_locale
    if body.is_active is not None:
        if not has_global and target.id == current.id and not body.is_active:
            raise HTTPException(status_code=403, detail="Tidak bisa menonaktifkan akun sendiri")
        target.is_active = body.is_active
    target.updated_by = current.id
    await session.flush()

    if add_hotel_ids:
        role_id = await session.scalar(
            select(UserRole.role_id).where(UserRole.user_id == target.id).limit(1)
        )
        for hotel_id in add_hotel_ids:
            await session.execute(
                text(
                    "INSERT INTO user_hotel_assignments "
                    "(id, user_id, hotel_id, role_id, is_primary, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :uid, :hid, :rid, false, now(), now()) "
                    "ON CONFLICT (user_id, hotel_id, role_id) DO NOTHING"
                ).bindparams(uid=target.id, hid=hotel_id, rid=role_id)
            )
    if remove_hotel_ids:
        await session.execute(
            update(UserHotelAssignment)
            .where(
                UserHotelAssignment.user_id == target.id,
                UserHotelAssignment.hotel_id.in_(remove_hotel_ids),
                UserHotelAssignment.deleted_at.is_(None),
            )
            .values(deleted_at=text("now()"), deleted_by=current.id)
        )

    await session.commit()
    fresh = await session.scalar(select(User).where(User.id == target.id))
    return Envelope(data=UserOut.model_validate(fresh))


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_user(
    user_id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> None:
    target, target_hotels, target_role_code = await _load_target_scope(session, user_id)
    if target.id == current.id:
        raise HTTPException(status_code=403, detail="Tidak bisa menonaktifkan akun sendiri")

    has_global = bool(
        await session.scalar(
            text(
                "SELECT 1 FROM user_roles ur JOIN roles_permissions rp ON rp.role_id = ur.role_id "
                "JOIN permissions p ON p.id = rp.permission_id "
                "WHERE ur.user_id = :uid AND p.code = 'user:manage:global' LIMIT 1"
            ).bindparams(uid=current.id)
        )
    )
    if not has_global:
        actor_hotels = set(
            (
                await session.execute(
                    select(UserHotelAssignment.hotel_id).where(
                        UserHotelAssignment.user_id == current.id,
                        UserHotelAssignment.deleted_at.is_(None),
                    )
                )
            ).scalars().all()
        )
        if not set(target_hotels).intersection(actor_hotels):
            raise HTTPException(status_code=403, detail="Di luar scope delegasi (A3)")
        if target_role_code in FORBIDDEN_FOR_DELEGATE:
            raise HTTPException(status_code=403, detail="Tidak berhak menonaktifkan manager role (A3)")

    if target.deleted_at is None:
        await session.execute(
            update(User)
            .where(User.id == target.id, User.deleted_at.is_(None))
            .values(deleted_at=text("now()"), deleted_by=current.id, is_active=False)
        )
    else:
        await session.execute(
            update(User)
            .where(User.id == target.id)
            .values(deleted_at=None, deleted_by=None, is_active=True)
        )
    await session.commit()


@router.post("/users/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    user_id: HybridId,
    body: ResetPasswordRequest,
    current: CurrentUser,
    session: DbSession,
) -> None:
    target, target_hotels, target_role_code = await _load_target_scope(session, user_id)
    if target.id == current.id:
        raise HTTPException(status_code=403, detail="Ganti password sendiri via profil; reset hanya utk user lain")
    has_global = bool(
        await session.scalar(
            text(
                "SELECT 1 FROM user_roles ur JOIN roles_permissions rp ON rp.role_id = ur.role_id "
                "JOIN permissions p ON p.id = rp.permission_id "
                "WHERE ur.user_id = :uid AND p.code = 'user:manage:global' LIMIT 1"
            ).bindparams(uid=current.id)
        )
    )
    if not has_global:
        actor_hotels = set(
            (
                await session.execute(
                    select(UserHotelAssignment.hotel_id).where(
                        UserHotelAssignment.user_id == current.id,
                        UserHotelAssignment.deleted_at.is_(None),
                    )
                )
            ).scalars().all()
        )
        if not set(target_hotels).intersection(actor_hotels):
            raise HTTPException(status_code=403, detail="Di luar scope delegasi (A3)")
        if target_role_code in FORBIDDEN_FOR_DELEGATE:
            raise HTTPException(status_code=403, detail="Tidak berhak reset password manager role (A3)")

    await session.execute(
        update(User)
        .where(User.id == target.id)
        .values(
            password_hash=hash_password(body.new_password),
            must_change_password=True,
            updated_by=current.id,
        )
    )
    await session.commit()


@router.put("/roles/{role_id}/permissions", response_model=Envelope[RoleWithPermissionsOut])
async def set_role_permissions(
    role_id: HybridId,
    body: dict,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[RoleWithPermissionsOut]:
    has_global = await session.scalar(
        text(
            "SELECT 1 FROM user_roles ur JOIN roles_permissions rp ON rp.role_id = ur.role_id "
            "JOIN permissions p ON p.id = rp.permission_id "
            "WHERE ur.user_id = :uid AND p.code = 'user:manage:global' LIMIT 1"
        ).bindparams(uid=current.id)
    )
    if not has_global:
        raise HTTPException(status_code=403, detail="Khusus ROOT_ADMIN (A4)")
    codes = body.get("permission_codes") or []
    if not isinstance(codes, list) or not all(isinstance(c, str) for c in codes):
        raise HTTPException(status_code=422, detail="permission_codes wajib array of string")

    role = await get_by_uuid(session, Role, role_id)
    if role is None:
        raise HTTPException(status_code=404, detail="Role tidak ditemukan")
    if role.code == "ROOT_ADMIN":
        raise HTTPException(status_code=403, detail="Hak ROOT_ADMIN sovereign, tidak bisa diubah (A4)")

    existing = set(
        (await session.execute(select(Permission.id).where(Permission.code.in_(codes)))).scalars().all()
    )
    if len(existing) != len(set(codes)):
        raise HTTPException(status_code=422, detail="Ada permission code tidak dikenal")

    await session.execute(
        text("DELETE FROM roles_permissions WHERE role_id = :rid").bindparams(rid=role.id)
    )
    for code in set(codes):
        perm_id = await session.scalar(select(Permission.id).where(Permission.code == code))
        await session.execute(
            text(
                "INSERT INTO roles_permissions (role_id, permission_id) VALUES (:rid, :pid)"
            ).bindparams(rid=role.id, pid=perm_id)
        )
    await session.commit()

    rows = (
        await session.execute(
            text(
                "SELECT p.uuid, p.code, p.module, p.action, p.description "
                "FROM permissions p "
                "JOIN roles_permissions rp ON rp.permission_id = p.id "
                "WHERE rp.role_id = :rid ORDER BY p.module, p.code"
            ).bindparams(rid=role.id)
        )
    ).mappings().all()
    return Envelope(
        data=RoleWithPermissionsOut(
            uuid=role.uuid, code=role.code, name=role.name,
            scope_level=role.scope_level, is_system=role.is_system,
            permissions=[PermissionOut(**dict(r)) for r in rows],
        )
    )