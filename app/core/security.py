"""JWT signing & password hashing. Tokens carry a unique `jti` claim so that
refresh rotation is always distinguishable (two tokens minted in the same
second would otherwise collide and break reuse detection)."""

import uuid
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from jose import JWTError, jwt

from app.core.config import settings

_password_hasher = PasswordHasher()

TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _password_hasher.verify(hashed, password)
    except VerifyMismatchError:
        return False


def _create_token(subject: str, token_type: str, expires_delta: timedelta) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "type": token_type,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(subject: str) -> str:
    return _create_token(subject, TOKEN_TYPE_ACCESS, timedelta(minutes=settings.jwt_access_expire_minutes))


def create_refresh_token(subject: str) -> str:
    return _create_token(subject, TOKEN_TYPE_REFRESH, timedelta(days=settings.jwt_refresh_expire_days))


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])


def is_refresh_token(payload: dict) -> bool:
    return payload.get("type") == TOKEN_TYPE_REFRESH


def is_jwt_error(exc: Exception) -> bool:
    return isinstance(exc, JWTError)