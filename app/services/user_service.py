"""User & RBAC business service layer — encapsulates user management & scoping.

Enforces:
- Constraint A1 (Many-to-Many User ↔ Hotel via user_hotel_assignments, Cluster GM).
- Constraint A3 (Delegated Admin: scoped unit manager creation & updates).
- Constraint A4 (Root Sovereignty: global management, immutable root role).
- Constraint J1-J3 (Hybrid Identity: BIGINT internal FKs & UUID public interfaces).
"""

from fastapi import HTTPException, status
from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identity import get_by_uuid, resolve_ids
from app.core.security import hash_password
from app.models import (
    Hotel,
    Permission,
    Region,
    Role,
    User,
    UserHotelAssignment,
    UserRegionAssignment,
    UserRole,
)
from app.schemas.common import HybridId, PaginationMeta
from app.schemas.users import (
    PermissionOut,
    RoleOut,
    RoleWithPermissionsOut,
    UserCreateRequest,
    UserHotelInfoOut,
    UserOut,
    UserRegionInfoOut,
    UserUpdateRequest,
)

UNIT_ROLES = {"HOTEL_GM", "HOTEL_HOD_TECH", "HOTEL_SALES", "HOTEL_FINANCE"}
CORPORATE_ROLES = {"ROOT_ADMIN", "CORP_EXEC", "CORP_AUDITOR", "REGIONAL_ROM"}
FORBIDDEN_FOR_DELEGATE = CORPORATE_ROLES | {"HOTEL_GM"}


async def resolve_hotel_ids(session: AsyncSession, hotel_uuids: list[HybridId]) -> set[int]:
    """Resolve UUID publik (atau internal id) hotel → internal id (wajib semua ditemukan)."""
    if not hotel_uuids:
        return set()
    internal = await resolve_ids(session, Hotel, hotel_uuids)
    if len(internal) != len({str(v) for v in hotel_uuids}):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Ada hotel_id tidak dikenal")
    return internal


async def can_manage_user(
    session: AsyncSession, current: User, target_role_code: str, hotel_ids: list[HybridId] | set[int]
) -> dict[str, str]:
    """Return effective permission headline of the actor. Raises 403 when out of scope."""
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
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tidak berhak mengelola user")

    if target_role_code in FORBIDDEN_FOR_DELEGATE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Delegated admin tidak boleh membuat role corporate/GM lain (A3)",
        )
    if not hotel_ids:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="hotel_ids wajib untuk role unit")

    actor_hotels = set(
        (
            await session.execute(
                select(UserHotelAssignment.hotel_id).where(
                    UserHotelAssignment.user_id == current.id,
                    UserHotelAssignment.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not set(hotel_ids).issubset(actor_hotels):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Hotel target di luar scope delegasi")
    return {"scope": "hotel"}


async def load_target_scope(
    session: AsyncSession, user_id: HybridId
) -> tuple[User, list[int], str]:
    """Load the target user, active assigned hotel ids, and primary role code."""
    target = await get_by_uuid(session, User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User tidak ditemukan")
    hotels = list(
        (
            await session.execute(
                select(UserHotelAssignment.hotel_id).where(
                    UserHotelAssignment.user_id == target.id,
                    UserHotelAssignment.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    role_code = await session.scalar(
        select(Role.code)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == target.id)
        .limit(1)
    )
    return target, hotels, role_code or ""


async def populate_user_out(session: AsyncSession, user: User) -> UserOut:
    """Enrich a User model instance into UserOut with role and hotel assignments."""
    # 1. Fetch roles
    roles_rows = (
        await session.execute(
            select(Role)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user.id)
            .order_by(Role.scope_level, Role.code)
        )
    ).scalars().all()
    roles_out = [RoleOut.model_validate(r) for r in roles_rows]
    primary_role = roles_out[0] if roles_out else None

    # 2. Fetch assigned hotels
    assignments = (
        await session.execute(
            select(Hotel, UserHotelAssignment.is_primary)
            .join(UserHotelAssignment, UserHotelAssignment.hotel_id == Hotel.id)
            .where(
                UserHotelAssignment.user_id == user.id,
                UserHotelAssignment.deleted_at.is_(None),
            )
            .order_by(UserHotelAssignment.is_primary.desc(), Hotel.name)
        )
    ).all()

    hotels_out = [
        UserHotelInfoOut(
            id=h.uuid,
            hotel_id=h.uuid,
            code=h.code,
            name=h.name,
            is_primary=bool(is_prim),
        )
        for h, is_prim in assignments
    ]

    # 3. Fetch assigned regions
    regions_rows = (
        await session.execute(
            select(Region)
            .join(UserRegionAssignment, UserRegionAssignment.region_id == Region.id)
            .where(
                UserRegionAssignment.user_id == user.id,
                UserRegionAssignment.deleted_at.is_(None),
            )
            .order_by(Region.name)
        )
    ).scalars().all()

    regions_out = [
        UserRegionInfoOut(
            id=r.uuid,
            region_id=r.uuid,
            code=r.code,
            name=r.name,
        )
        for r in regions_rows
    ]

    out = UserOut.model_validate(user)
    out.role = primary_role
    out.roles = roles_out
    out.role_code = primary_role.code if primary_role else None
    out.role_name = primary_role.name if primary_role else None
    out.hotels = hotels_out
    out.hotel_assignments = hotels_out
    out.region_assignments = regions_out
    return out


from app.services.role_service import (
    get_role_service,
    list_permissions_catalog_service,
    list_roles_service,
    set_role_permissions_service,
)


async def list_users_service(
    session: AsyncSession,
    current: User,
    role_code: str | None = None,
    hotel_id: HybridId | None = None,
    search: str | None = None,
    status_filter: str | None = "ACTIVE",
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[UserOut], PaginationMeta]:
    """List scoped users with dynamic role/hotel filters, tenant isolation, and status filtering."""
    has_global = bool(
        await session.scalar(
            text(
                "SELECT 1 FROM user_roles ur JOIN roles_permissions rp ON rp.role_id = ur.role_id "
                "JOIN permissions p ON p.id = rp.permission_id "
                "WHERE ur.user_id = :uid AND p.code = 'user:read:global' LIMIT 1"
            ).bindparams(uid=current.id)
        )
    )
    has_region = bool(
        await session.scalar(
            text(
                "SELECT 1 FROM user_roles ur JOIN roles_permissions rp ON rp.role_id = ur.role_id "
                "JOIN permissions p ON p.id = rp.permission_id "
                "WHERE ur.user_id = :uid AND p.code = 'user:read:region' LIMIT 1"
            ).bindparams(uid=current.id)
        )
    )
    has_hotel = bool(
        await session.scalar(
            text(
                "SELECT 1 FROM user_roles ur JOIN roles_permissions rp ON rp.role_id = ur.role_id "
                "JOIN permissions p ON p.id = rp.permission_id "
                "WHERE ur.user_id = :uid AND p.code = 'user:read:hotel' LIMIT 1"
            ).bindparams(uid=current.id)
        )
    )

    if not (has_global or has_region or has_hotel):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing user read permission")

    stmt = select(User)

    # 1. Status Filter (ACTIVE / INACTIVE / ALL)
    norm_status = (status_filter or "ACTIVE").upper()
    if norm_status == "INACTIVE":
        stmt = stmt.where(User.deleted_at.is_not(None))
    elif norm_status == "ALL":
        pass
    else:
        stmt = stmt.where(User.deleted_at.is_(None))

    # 2. Enforce Scoped Tenant Isolation
    if not has_global:
        if has_hotel:
            actor_hotels = select(UserHotelAssignment.hotel_id).where(
                UserHotelAssignment.user_id == current.id,
                UserHotelAssignment.deleted_at.is_(None),
            )
            stmt = stmt.join(UserHotelAssignment, UserHotelAssignment.user_id == User.id)
            stmt = stmt.where(
                UserHotelAssignment.hotel_id.in_(actor_hotels),
                UserHotelAssignment.deleted_at.is_(None),
            )
        elif has_region:
            actor_regions = select(UserRegionAssignment.region_id).where(
                UserRegionAssignment.user_id == current.id
            )
            stmt = stmt.join(UserHotelAssignment, UserHotelAssignment.user_id == User.id)
            stmt = stmt.join(Hotel, Hotel.id == UserHotelAssignment.hotel_id)
            stmt = stmt.where(
                Hotel.region_id.in_(actor_regions),
                UserHotelAssignment.deleted_at.is_(None),
            )

    # 3. Explicit Parameter Filters
    if role_code:
        stmt = stmt.join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id)
        stmt = stmt.where(Role.code == role_code)
    if hotel_id:
        hotel = await get_by_uuid(session, Hotel, hotel_id)
        if hotel is None:
            meta = PaginationMeta(current_page=page, per_page=per_page, total=0, last_page=1)
            return [], meta
        # Avoid duplicate join if already joined above
        if has_global:
            stmt = stmt.join(UserHotelAssignment, UserHotelAssignment.user_id == User.id)
        stmt = stmt.where(
            UserHotelAssignment.hotel_id == hotel.id,
            UserHotelAssignment.deleted_at.is_(None),
        )
    if search:
        term = search.strip()
        stmt = stmt.where(or_(User.name.ilike(f"%{term}%"), User.email.ilike(f"%{term}%")))

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

    users_out = [await populate_user_out(session, u) for u in rows]
    meta = PaginationMeta(
        current_page=page,
        per_page=per_page,
        total=total,
        last_page=max(1, (total + per_page - 1) // per_page),
    )
    return users_out, meta


async def get_user_service(
    session: AsyncSession,
    current: User,
    user_id: HybridId,
) -> UserOut:
    """Retrieve single user details with eager loaded roles and hotel assignments."""
    user = await get_by_uuid(session, User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User tidak ditemukan")
    return await populate_user_out(session, user)


async def create_user_service(
    session: AsyncSession,
    current: User,
    body: UserCreateRequest,
) -> UserOut:
    """Create a new user with role and hotel assignments enforcing A3/A4 rules."""
    hotel_ids = await resolve_hotel_ids(session, body.hotel_ids)
    await can_manage_user(session, current, body.role_code, hotel_ids)

    email = body.email.lower().strip()
    exists = await session.scalar(
        select(User.id).where(User.email == email, User.deleted_at.is_(None))
    )
    if exists:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email sudah terdaftar")

    role = await session.scalar(select(Role).where(Role.code == body.role_code))
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Role tidak dikenal: {body.role_code}",
        )

    user = User(
        email=email,
        name=body.name.strip(),
        phone=body.phone.strip() if body.phone else None,
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

    if body.hotel_assignments:
        for item in body.hotel_assignments:
            h = await get_by_uuid(session, Hotel, item.hotel_id)
            if h:
                session.add(
                    UserHotelAssignment(
                        user_id=user.id,
                        hotel_id=h.id,
                        role_id=role.id,
                        is_primary=item.is_primary,
                    )
                )
    else:
        for position, hid in enumerate(hotel_ids):
            session.add(
                UserHotelAssignment(
                    user_id=user.id,
                    hotel_id=hid,
                    role_id=role.id,
                    is_primary=(position == 0),
                )
            )

    if body.region_assignments:
        for r_item in body.region_assignments:
            reg = await get_by_uuid(session, Region, r_item.region_id)
            if reg:
                session.add(UserRegionAssignment(user_id=user.id, region_id=reg.id))
    elif body.region_ids:
        for r_uuid in body.region_ids:
            reg = await get_by_uuid(session, Region, r_uuid)
            if reg:
                session.add(UserRegionAssignment(user_id=user.id, region_id=reg.id))
    elif body.region_id is not None:
        region = await get_by_uuid(session, Region, body.region_id)
        if region is not None:
            session.add(UserRegionAssignment(user_id=user.id, region_id=region.id))

    await session.commit()

    fresh = await session.scalar(select(User).where(User.id == user.id))
    return await populate_user_out(session, fresh)


async def update_user_service(
    session: AsyncSession,
    current: User,
    user_id: HybridId,
    body: UserUpdateRequest,
) -> UserOut:
    """Update user attributes, role, and hotel/region assignments safely without SQL ID identity collisions."""
    target, target_hotels, target_role_code = await load_target_scope(session, user_id)

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
        )
        .scalars()
        .all()
    )
    if not has_global and not is_self and not set(target_hotels).intersection(actor_hotels):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Di luar scope delegasi (A3)")
    if not has_global and not is_self and target_role_code in FORBIDDEN_FOR_DELEGATE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tidak berhak mengelola manager role (A3)")

    if body.name is not None:
        target.name = body.name.strip()
    if body.phone is not None:
        target.phone = body.phone.strip() if body.phone else None
    if body.preferred_locale is not None:
        target.preferred_locale = body.preferred_locale
    if body.is_active is not None:
        if not has_global and target.id == current.id and not body.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tidak bisa menonaktifkan akun sendiri")
        target.is_active = body.is_active
    target.updated_by = current.id
    await session.flush()

    # 1. Update role if role_code is provided
    target_role_id = None
    if body.role_code is not None:
        if not has_global and body.role_code in FORBIDDEN_FOR_DELEGATE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tidak berhak memberikan corporate/manager role (A3)",
            )
        new_role = await session.scalar(select(Role).where(Role.code == body.role_code))
        if new_role is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Role tidak dikenal: {body.role_code}",
            )
        target_role_id = new_role.id
        await session.execute(delete(UserRole).where(UserRole.user_id == target.id))
        session.add(UserRole(user_id=target.id, role_id=new_role.id))
        await session.flush()
    else:
        target_role_id = await session.scalar(
            select(UserRole.role_id).where(UserRole.user_id == target.id).limit(1)
        )

    # 2. Update hotel assignments if provided
    if body.hotel_assignments is not None:
        await session.execute(delete(UserHotelAssignment).where(UserHotelAssignment.user_id == target.id))
        if target_role_id:
            for item in body.hotel_assignments:
                h = await get_by_uuid(session, Hotel, item.hotel_id)
                if h:
                    session.add(
                        UserHotelAssignment(
                            user_id=target.id,
                            hotel_id=h.id,
                            role_id=target_role_id,
                            is_primary=item.is_primary,
                        )
                    )
    elif body.hotel_ids is not None:
        await session.execute(delete(UserHotelAssignment).where(UserHotelAssignment.user_id == target.id))
        resolved_hids = await resolve_hotel_ids(session, body.hotel_ids)
        if target_role_id:
            for position, hid in enumerate(resolved_hids):
                session.add(
                    UserHotelAssignment(
                        user_id=target.id,
                        hotel_id=hid,
                        role_id=target_role_id,
                        is_primary=(position == 0),
                    )
                )
    else:
        add_hotel_ids = await resolve_hotel_ids(session, body.add_hotel_ids)
        remove_hotel_ids = await resolve_hotel_ids(session, body.remove_hotel_ids)

        if not has_global:
            if not add_hotel_ids.issubset(actor_hotels):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Hotel target di luar scope delegasi")
            if not remove_hotel_ids.issubset(actor_hotels):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Hotel target di luar scope delegasi")

        if add_hotel_ids and target_role_id:
            for hid in add_hotel_ids:
                existing = await session.scalar(
                    select(UserHotelAssignment).where(
                        UserHotelAssignment.user_id == target.id,
                        UserHotelAssignment.hotel_id == hid,
                        UserHotelAssignment.role_id == target_role_id,
                    )
                )
                if existing:
                    if existing.deleted_at is not None:
                        existing.deleted_at = None
                        existing.deleted_by = None
                else:
                    session.add(
                        UserHotelAssignment(
                            user_id=target.id,
                            hotel_id=hid,
                            role_id=target_role_id,
                            is_primary=False,
                        )
                    )

        if remove_hotel_ids:
            await session.execute(
                update(UserHotelAssignment)
                .where(
                    UserHotelAssignment.user_id == target.id,
                    UserHotelAssignment.hotel_id.in_(remove_hotel_ids),
                    UserHotelAssignment.deleted_at.is_(None),
                )
                .values(
                    {
                        UserHotelAssignment.deleted_at: func.now(),
                        UserHotelAssignment.deleted_by: current.id,
                    }
                )
            )

    # 3. Update region assignments if provided
    if body.region_assignments is not None:
        await session.execute(delete(UserRegionAssignment).where(UserRegionAssignment.user_id == target.id))
        for r_item in body.region_assignments:
            reg = await get_by_uuid(session, Region, r_item.region_id)
            if reg:
                session.add(UserRegionAssignment(user_id=target.id, region_id=reg.id))
    elif body.region_ids is not None:
        await session.execute(delete(UserRegionAssignment).where(UserRegionAssignment.user_id == target.id))
        for r_uuid in body.region_ids:
            reg = await get_by_uuid(session, Region, r_uuid)
            if reg:
                session.add(UserRegionAssignment(user_id=target.id, region_id=reg.id))
    elif body.region_id is not None:
        await session.execute(delete(UserRegionAssignment).where(UserRegionAssignment.user_id == target.id))
        reg = await get_by_uuid(session, Region, body.region_id)
        if reg:
            session.add(UserRegionAssignment(user_id=target.id, region_id=reg.id))

    await session.commit()
    fresh = await session.scalar(select(User).where(User.id == target.id))
    return await populate_user_out(session, fresh)


async def toggle_user_active_service(
    session: AsyncSession,
    current: User,
    user_id: HybridId,
) -> None:
    """Toggle soft-delete active/inactive status of a user (Constraint A3/A4)."""
    target, target_hotels, target_role_code = await load_target_scope(session, user_id)
    if target.id == current.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tidak bisa menonaktifkan akun sendiri")

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
            )
            .scalars()
            .all()
        )
        if not set(target_hotels).intersection(actor_hotels):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Di luar scope delegasi (A3)")
        if target_role_code in FORBIDDEN_FOR_DELEGATE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tidak berhak menonaktifkan manager role (A3)",
            )

    if target.deleted_at is None:
        target.deleted_at = func.now()
        target.deleted_by = current.id
        target.is_active = False
    else:
        target.deleted_at = None
        target.deleted_by = None
        target.is_active = True
    await session.commit()


async def reset_user_password_service(
    session: AsyncSession,
    current: User,
    user_id: HybridId,
    new_password: str,
) -> None:
    """Reset user password by a delegated manager or ROOT_ADMIN."""
    target, target_hotels, target_role_code = await load_target_scope(session, user_id)
    if target.id == current.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ganti password sendiri via profil; reset hanya utk user lain",
        )
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
            )
            .scalars()
            .all()
        )
        if not set(target_hotels).intersection(actor_hotels):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Di luar scope delegasi (A3)")
        if target_role_code in FORBIDDEN_FOR_DELEGATE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tidak berhak reset password manager role (A3)",
            )

    target.password_hash = hash_password(new_password)
    target.must_change_password = True
    target.updated_by = current.id
    await session.commit()
