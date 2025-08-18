"""User & RBAC schemas — openapi.yaml `/users` + `/roles` contract."""

import uuid
from datetime import datetime

from pydantic import AliasPath, BaseModel, ConfigDict, Field

from app.schemas.common import HybridId


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    email: str
    name: str
    phone: str | None = None
    preferred_locale: str = "id"
    is_active: bool = True
    last_login_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    code: str
    name: str
    scope_level: int
    is_system: bool


class PermissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    code: str
    module: str
    action: str
    description: str | None = None


class RoleWithPermissionsOut(RoleOut):
    permissions: list[PermissionOut]


class UserCreateRequest(BaseModel):
    email: str
    name: str
    role_code: str
    hotel_ids: list[HybridId] = Field(default_factory=list)
    region_id: HybridId | None = None
    preferred_locale: str = "id"
    password: str


class UserUpdateRequest(BaseModel):
    name: str | None = None
    phone: str | None = None
    preferred_locale: str = Field(default="id", pattern="^(id|en)$")
    is_active: bool | None = None
    add_hotel_ids: list[HybridId] = Field(default_factory=list)
    remove_hotel_ids: list[HybridId] = Field(default_factory=list)


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8)