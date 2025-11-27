"""Auth endpoints — openapi.yaml `/auth/*` contract.

JWT access/refresh rotation backed by `user_sessions` (hashed refresh tokens),
login audit trail, argon2 password verification, and A1 active-hotel scope.
"""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, status
from jose import JWTError
from sqlalchemy import select, update

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.core.identity import get_by_uuid
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    is_refresh_token,
    verify_password,
)
from app.models import (
    Hotel,
    LoginAudit,
    Role,
    User,
    UserHotelAssignment,
    UserRole,
    UserSession,
)
from app.schemas.auth import (
    ActiveHotelResponse,
    HotelOut,
    HotelScopeItem,
    HotelsResponse,
    LocaleRequest,
    LoginData,
    LoginRequest,
    LoginResponse,
    MePayload,
    MeResponse,
    RefreshRequest,
    RefreshResponse,
    SwitchHotelRequest,
    TokenPair,
)
from app.schemas.users import RoleOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_out(user: User) -> UserOut:
    return UserOut.model_validate(user)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _fail_login(session: DbSession, user_id: uuid.UUID | None, email: str, reason: str) -> HTTPException:
    session.add(LoginAudit(user_id=user_id, email_attempted=email, success=False, reason=reason))
    session.commit()  # persisted before raising — get_db teardown would otherwise roll it back
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Email atau password salah",
    )


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, session: DbSession) -> LoginResponse:
    email = body.email.lower().strip()
    user = await session.scalar(
        select(User).where(User.email == email, User.deleted_at.is_(None))
    )
    if user is None:
        raise _fail_login(session, None, email, "user_not_found")
    if not user.is_active:
        raise _fail_login(session, user.id, email, "account_disabled")
    if not verify_password(body.password, user.password_hash):
        raise _fail_login(session, user.id, email, "bad_password")

    access_token = create_access_token(str(user.uuid))
    refresh_token = create_refresh_token(str(user.uuid))
    session.add(
        UserSession(
            user_id=user.id,
            refresh_token_hash=_hash_token(refresh_token),
            expires_at=datetime.now(UTC) + timedelta(days=settings.jwt_refresh_expire_days),
        )
    )
    session.add(LoginAudit(user_id=user.id, email_attempted=email, success=True, reason="ok"))
    user.last_login_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(user)

    return LoginResponse(
        data=LoginData(
            access_token=access_token,
            refresh_token=refresh_token,
            user=_user_out(user),
        ),
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_token(body: RefreshRequest, session: DbSession) -> RefreshResponse:
    try:
        payload = decode_token(body.refresh_token)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Refresh token invalid") from exc
    if not is_refresh_token(payload):
        raise HTTPException(status_code=401, detail="Not a refresh token")

    subject = payload.get("sub")
    if subject is None:
        raise HTTPException(status_code=401, detail="Invalid refresh subject")

    user = await session.scalar(select(User).where(User.uuid == subject))
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Account disabled")

    token_hash = _hash_token(body.refresh_token)
    existing = await session.scalar(
        select(UserSession).where(
            UserSession.user_id == user.id,
            UserSession.refresh_token_hash == token_hash,
            UserSession.revoked_at.is_(None),
        )
    )
    if existing is None:
        raise HTTPException(status_code=401, detail="Refresh token revoked or unknown")
    if existing.expires_at and existing.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=401, detail="Refresh token expired")

    await session.execute(
        update(UserSession)
        .where(UserSession.id == existing.id)
        .values(revoked_at=datetime.now(UTC))
    )

    new_access = create_access_token(str(user.uuid))
    new_refresh = create_refresh_token(str(user.uuid))
    session.add(
        UserSession(
            user_id=user.id,
            refresh_token_hash=_hash_token(new_refresh),
            expires_at=datetime.now(UTC) + timedelta(days=settings.jwt_refresh_expire_days),
        )
    )
    await session.commit()
    return RefreshResponse(
        data=TokenPair(access_token=new_access, refresh_token=new_refresh)
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(current: CurrentUser, session: DbSession) -> None:
    await session.execute(
        update(UserSession)
        .where(UserSession.user_id == current.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await session.commit()
    return None


@router.get("/me", response_model=MeResponse)
async def me(current: CurrentUser, session: DbSession) -> MeResponse:
    roles = (
        await session.execute(
            select(Role)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == current.id)
            .order_by(Role.scope_level, Role.code)
        )
    ).scalars().all()

    assignment = await session.scalar(
        select(UserHotelAssignment)
        .join(Hotel, Hotel.id == UserHotelAssignment.hotel_id)
        .where(
            UserHotelAssignment.user_id == current.id,
            UserHotelAssignment.is_primary.is_(True),
            Hotel.deleted_at.is_(None),
        )
    )
    active_hotel = None
    if assignment is not None:
        hotel = await session.get(Hotel, assignment.hotel_id)
        if hotel is not None:
            active_hotel = HotelOut.model_validate(hotel)

    return MeResponse(
        data=MePayload(
            user=_user_out(current),
            roles=[RoleOut.model_validate(r) for r in roles],
            active_hotel=active_hotel,
        )
    )


@router.get("/hotels", response_model=HotelsResponse)
async def list_my_hotels(current: CurrentUser, session: DbSession) -> HotelsResponse:
    rows = (
        await session.execute(
            select(UserHotelAssignment, Hotel, Role)
            .join(Hotel, Hotel.id == UserHotelAssignment.hotel_id)
            .join(Role, Role.id == UserHotelAssignment.role_id)
            .where(
                UserHotelAssignment.user_id == current.id,
                UserHotelAssignment.deleted_at.is_(None),
                Hotel.deleted_at.is_(None),
            )
            .order_by(UserHotelAssignment.is_primary.desc(), Hotel.code)
        )
    ).all()
    items = [
        HotelScopeItem(
            hotel_id=h.uuid,
            hotel_code=h.code,
            hotel_name=h.name,
            role_id=a_role.uuid,
            role_code=a_role.code,
            is_primary=a.is_primary,
        )
        for a, h, a_role in rows
    ]
    return HotelsResponse(data=items)


@router.post("/switch-hotel", response_model=ActiveHotelResponse)
async def switch_hotel(
    body: SwitchHotelRequest, current: CurrentUser, session: DbSession
) -> ActiveHotelResponse:
    hotel = await get_by_uuid(session, Hotel, body.hotel_id)
    if hotel is None:
        raise HTTPException(status_code=403, detail="Hotel di luar scope user")
    assignment = await session.scalar(
        select(UserHotelAssignment).where(
            UserHotelAssignment.user_id == current.id,
            UserHotelAssignment.hotel_id == hotel.id,
            UserHotelAssignment.deleted_at.is_(None),
        )
    )
    if assignment is None:
        raise HTTPException(status_code=403, detail="Hotel di luar scope user")

    await session.execute(
        update(UserHotelAssignment)
        .where(UserHotelAssignment.user_id == current.id)
        .values(is_primary=False)
    )
    assignment.is_primary = True
    await session.commit()
    return ActiveHotelResponse(data={"active_hotel": HotelOut.model_validate(hotel)})


@router.patch("/locale")
async def set_locale(
    body: LocaleRequest, current: CurrentUser, session: DbSession
) -> dict:
    current.preferred_locale = body.locale
    await session.commit()
    return {"success": True, "data": {"preferred_locale": current.preferred_locale}}