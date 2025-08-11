import uuid

UUIDT = uuid.UUID
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import BigInteger, Identity
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.mixins import AppendOnlyMixin, SoftDeleteMixin, TimestampMixin

UUID_PK = PG_UUID(as_uuid=True)
BIGINT = BigInteger()


class User(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"
    __table_args__ = (
        Index(
            "ix_users_email_active",
            "email",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    preferred_locale: Mapped[str] = mapped_column(String(10), nullable=False, default="id")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))
    deleted_by: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))


class Role(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    scope_level: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description: Mapped[str | None] = mapped_column(Text)


class Permission(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    code: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    module: Mapped[str] = mapped_column(String(50), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class RolesPermission(Base, TimestampMixin):
    __tablename__ = "roles_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    role_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)
    permission_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False
    )


class UserRole(Base, TimestampMixin):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    user_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)


class UserHotelAssignment(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "user_hotel_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "hotel_id", "role_id"),
        Index("ix_uh_assignment_user", "user_id"),
        Index("ix_uh_assignment_hotel", "hotel_id"),
        Index("ix_uh_assignment_role", "role_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    user_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    hotel_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False)
    role_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deleted_by: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))


class UserRegionAssignment(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "user_region_assignments"
    __table_args__ = (UniqueConstraint("user_id", "region_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    user_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    region_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("regions.id", ondelete="CASCADE"), nullable=False)


class UserSession(Base, TimestampMixin):
    __tablename__ = "user_sessions"
    __table_args__ = (Index("ix_user_sessions_user", "user_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    user_id: Mapped[int] = mapped_column(BIGINT, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    refresh_token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginAudit(Base, AppendOnlyMixin):
    __tablename__ = "login_audits"
    __table_args__ = (Index("ix_login_audits_email_at", "email_attempted", "at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    uuid: Mapped[UUIDT] = mapped_column(UUID_PK, unique=True, nullable=False, server_default=func.gen_random_uuid())
    user_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey("users.id"))
    email_attempted: Mapped[str] = mapped_column(String(255), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(100))
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())