from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db.session import get_db
from app.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

DbSession = Annotated[AsyncSession, Depends(get_db)]


def credentials_exc(detail: str = "Invalid or expired token") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user_id(token: Annotated[str, Depends(oauth2_scheme)]) -> str:
    try:
        payload = decode_token(token)
    except JWTError as exc:
        raise credentials_exc() from exc
    subject = payload.get("sub")
    if subject is None:
        raise credentials_exc("Invalid token subject")
    return subject


async def get_current_user(
    user_id: Annotated[str, Depends(get_current_user_id)],
    session: DbSession,
) -> User:
    user = await session.scalar(select(User).where(User.uuid == user_id))
    if user is None or not user.is_active:
        raise credentials_exc("Account disabled or not found")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_permission(permission_code: str):
    """Authorization guard (Constraint A5) — backed by seeded RBAC tables.

    Resolves the caller's effective permissions through
    users → user_roles → roles_permissions → permissions and rejects with
    403 when `permission_code` is absent.
    """

    async def _guard(current: CurrentUser, session: DbSession) -> CurrentUser:
        row = await session.execute(
            text(
                "SELECT 1 FROM users u "
                "JOIN user_roles ur ON ur.user_id = u.id "
                "JOIN roles_permissions rp ON rp.role_id = ur.role_id "
                "JOIN permissions p ON p.id = rp.permission_id "
                "WHERE u.uuid = :user_id AND p.code = :perm LIMIT 1"
            ),
            {"user_id": str(current.uuid), "perm": permission_code},
        )
        if row.first() is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {permission_code}",
            )
        return current

    return _guard