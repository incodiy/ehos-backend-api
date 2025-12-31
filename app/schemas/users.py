"""User & RBAC schemas — openapi.yaml `/users` + `/roles` contract."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import HybridId


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    code: str
    name: str
    scope_level: int
    is_system: bool


class PermissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    code: str
    module: str
    action: str
    description: str | None = None


class RoleWithPermissionsOut(RoleOut):
    permissions: list[PermissionOut]


class UserHotelInfoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    hotel_id: uuid.UUID | None = None
    code: str
    name: str
    is_primary: bool = False


class UserRegionInfoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    region_id: uuid.UUID | None = None
    code: str
    name: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    email: str
    name: str
    phone: str | None = None
    preferred_locale: str = "id"
    is_active: bool = True
    last_login_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    role: RoleOut | None = None
    roles: list[RoleOut] = Field(default_factory=list)
    role_code: str | None = None
    role_name: str | None = None
    hotels: list[UserHotelInfoOut] = Field(default_factory=list)
    hotel_assignments: list[UserHotelInfoOut] = Field(default_factory=list)
    region_assignments: list[UserRegionInfoOut] = Field(default_factory=list)


class UserHotelAssignmentItem(BaseModel):
    hotel_id: HybridId
    is_primary: bool = False


class UserRegionAssignmentItem(BaseModel):
    region_id: HybridId


class UserCreateRequest(BaseModel):
    email: str
    name: str
    phone: str | None = None
    role_code: str
    hotel_ids: list[HybridId] = Field(default_factory=list)
    hotel_assignments: list[UserHotelAssignmentItem] | None = None
    region_ids: list[HybridId] = Field(default_factory=list)
    region_id: HybridId | None = None
    region_assignments: list[UserRegionAssignmentItem] | None = None
    preferred_locale: str = "id"
    password: str


class UserUpdateRequest(BaseModel):
    name: str | None = None
    phone: str | None = None
    preferred_locale: str | None = Field(default=None, pattern="^(id|en)$")
    is_active: bool | None = None
    role_code: str | None = None
    hotel_ids: list[HybridId] | None = None
    hotel_assignments: list[UserHotelAssignmentItem] | None = None
    region_ids: list[HybridId] | None = None
    region_id: HybridId | None = None
    region_assignments: list[UserRegionAssignmentItem] | None = None
    add_hotel_ids: list[HybridId] = Field(default_factory=list)
    remove_hotel_ids: list[HybridId] = Field(default_factory=list)


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8)