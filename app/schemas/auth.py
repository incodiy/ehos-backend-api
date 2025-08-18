"""Auth schemas — mirror openapi.yaml `/auth/*` contract (Login/Refresh/Me/Scope)."""

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.users import RoleOut, UserOut


class LoginRequest(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str


class LoginResponse(BaseModel):
    success: bool = True
    message: str | None = "Login berhasil"
    data: TokenPair
    user: UserOut


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    success: bool = True
    data: TokenPair


class SwitchHotelRequest(BaseModel):
    hotel_id: uuid.UUID


class LocaleRequest(BaseModel):
    locale: str = Field(pattern="^(id|en)$")


class HotelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(validation_alias="uuid")
    code: str
    name: str
    city: str | None = None
    status: str | None = None


class HotelScopeItem(BaseModel):
    hotel_id: uuid.UUID
    hotel_code: str
    hotel_name: str
    role_id: uuid.UUID
    role_code: str
    is_primary: bool


class MePayload(BaseModel):
    user: UserOut
    roles: list[RoleOut]
    active_hotel: HotelOut | None = None


class MeResponse(BaseModel):
    success: bool = True
    data: MePayload


class HotelsResponse(BaseModel):
    success: bool = True
    data: list[HotelScopeItem]


class ActiveHotelResponse(BaseModel):
    success: bool = True
    data: dict[str, HotelOut | None]