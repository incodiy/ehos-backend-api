"""Roles & Permissions endpoints — OpenAPI `/roles` + `/permissions` contract.

Delegates all RBAC business logic and sovereign constraints (Constraint A4)
to `app.services.role_service`.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DbSession
from app.schemas.common import Envelope, HybridId
from app.schemas.users import PermissionOut, RoleWithPermissionsOut
from app.services.role_service import (
    get_role_service,
    list_permissions_catalog_service,
    list_roles_service,
    set_role_permissions_service,
)

router = APIRouter(tags=["roles"])


class SetRolePermissionsRequest(BaseModel):
    permission_codes: list[str] = Field(
        ...,
        examples=[["audit:read:hotel", "capa:read:hotel", "capa:resolve:hotel"]],
    )


@router.get("/roles", response_model=Envelope[list[RoleWithPermissionsOut]])
async def list_roles(session: DbSession) -> Envelope[list[RoleWithPermissionsOut]]:
    """List all system roles and their assigned permissions."""
    roles = await list_roles_service(session)
    return Envelope(data=roles)


@router.get("/roles/{role_id}", response_model=Envelope[RoleWithPermissionsOut])
async def get_role(
    role_id: HybridId,
    session: DbSession,
) -> Envelope[RoleWithPermissionsOut]:
    """Get detailed information and assigned permissions for a specific role."""
    role = await get_role_service(session, role_id)
    return Envelope(data=role)


@router.put("/roles/{role_id}/permissions", response_model=Envelope[RoleWithPermissionsOut])
async def set_role_permissions(
    role_id: HybridId,
    body: SetRolePermissionsRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[RoleWithPermissionsOut]:
    """Update assigned permissions for a role (ROOT_ADMIN sovereign only)."""
    updated = await set_role_permissions_service(
        session=session,
        current=current,
        role_id=role_id,
        permission_codes=body.permission_codes,
    )
    return Envelope(data=updated)


@router.get("/permissions", response_model=Envelope[dict[str, list[PermissionOut]]])
async def list_permissions_catalog(
    session: DbSession,
) -> Envelope[dict[str, list[PermissionOut]]]:
    """List master catalogue of 38 granular permissions grouped by business module."""
    catalog = await list_permissions_catalog_service(session)
    return Envelope(data=catalog)
