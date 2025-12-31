"""User & RBAC endpoints — openapi.yaml `/users` + `/roles` contract.

Delegates all business logic and delegated administration constraints (A3/A4)
to `app.services.user_service`.
"""

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.common import Envelope, HybridId, Paginated
from app.schemas.users import (
    ResetPasswordRequest,
    RoleWithPermissionsOut,
    UserCreateRequest,
    UserOut,
    UserUpdateRequest,
)
from app.services.user_service import (
    create_user_service,
    get_user_service,
    list_roles_service,
    list_users_service,
    reset_user_password_service,
    set_role_permissions_service,
    toggle_user_active_service,
    update_user_service,
)

router = APIRouter(tags=["users"])


@router.get("/users", response_model=Paginated[UserOut, UserOut])
async def list_users(
    current: CurrentUser,
    session: DbSession,
    role_code: str | None = None,
    hotel_id: HybridId | None = None,
    search: str | None = None,
    status: str | None = "ACTIVE",
    page: int = 1,
    per_page: int = 50,
) -> Paginated[UserOut, UserOut]:
    users, meta = await list_users_service(
        session=session,
        current=current,
        role_code=role_code,
        hotel_id=hotel_id,
        search=search,
        status_filter=status,
        page=page,
        per_page=per_page,
    )
    return Paginated[UserOut, UserOut](data=users, meta=meta)


@router.get("/users/{user_id}", response_model=Envelope[UserOut])
async def get_user(
    user_id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[UserOut]:
    user = await get_user_service(session=session, current=current, user_id=user_id)
    return Envelope(data=user)


@router.post("/users", response_model=Envelope[UserOut], status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[UserOut]:
    created = await create_user_service(session=session, current=current, body=body)
    return Envelope(data=created)


@router.patch("/users/{user_id}", response_model=Envelope[UserOut])
async def update_user(
    user_id: HybridId,
    body: UserUpdateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[UserOut]:
    updated = await update_user_service(session=session, current=current, user_id=user_id, body=body)
    return Envelope(data=updated)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_user(
    user_id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> None:
    await toggle_user_active_service(session=session, current=current, user_id=user_id)


@router.post("/users/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    user_id: HybridId,
    body: ResetPasswordRequest,
    current: CurrentUser,
    session: DbSession,
) -> None:
    await reset_user_password_service(
        session=session,
        current=current,
        user_id=user_id,
        new_password=body.new_password,
    )