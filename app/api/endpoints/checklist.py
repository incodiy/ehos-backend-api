"""Checklist bank endpoints — openapi.yaml `/checklist/templates` (PRD-F-01, B1-B3).

Clean router layer delegating business logic to `ChecklistService`.
Protected by centralized RBAC guards: `checklist:read` and `checklist:write`.
"""

from fastapi import APIRouter, Depends, status

from app.api.deps import CurrentUser, DbSession, require_permission
from app.schemas.checklist import (
    DepartEnum,
    ItemCreateRequest,
    ItemOut,
    ItemUpdateRequest,
    SectionCreateRequest,
    SectionOut,
    TemplateCreateRequest,
    TemplateDetail,
    TemplateOut,
    TemplateStatus,
    VersionCreateRequest,
)
from app.schemas.common import Envelope, HybridId
from app.services.checklist_service import ChecklistService

router = APIRouter(prefix="/checklist", tags=["checklist"])

_read_guard = Depends(require_permission("checklist:read"))
_write_guard = Depends(require_permission("checklist:write"))


@router.get(
    "/templates",
    response_model=Envelope[list[TemplateOut]],
    dependencies=[_read_guard],
)
async def list_templates(
    session: DbSession,
    department: DepartEnum | None = None,
    brand_tier: str | None = None,
    status: TemplateStatus | None = None,
) -> Envelope[list[TemplateOut]]:
    data = await ChecklistService.list_templates(
        session=session,
        department=department,
        brand_tier=brand_tier,
        status_filter=status,
    )
    return Envelope(data=data)


@router.post(
    "/templates",
    response_model=Envelope[TemplateOut],
    status_code=status.HTTP_201_CREATED,
    dependencies=[_write_guard],
)
async def create_template(
    body: TemplateCreateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[TemplateOut]:
    data = await ChecklistService.create_template(
        session=session,
        body=body,
        current_user=current,
    )
    return Envelope(data=data)


@router.get(
    "/templates/{id}",
    response_model=Envelope[TemplateDetail],
    dependencies=[_read_guard],
)
async def get_template(
    id: HybridId,
    session: DbSession,
) -> Envelope[TemplateDetail]:
    data = await ChecklistService.get_template_detail(
        session=session,
        template_id=id,
    )
    return Envelope(data=data)


@router.post(
    "/templates/{id}/versions",
    response_model=Envelope[TemplateOut],
    status_code=status.HTTP_201_CREATED,
    dependencies=[_write_guard],
)
async def create_template_version(
    id: HybridId,
    body: VersionCreateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[TemplateOut]:
    data = await ChecklistService.fork_template_version(
        session=session,
        template_id=id,
        new_version=body.new_version,
        current_user=current,
    )
    return Envelope(data=data)


@router.post(
    "/templates/{id}/lock",
    response_model=Envelope[TemplateOut],
    dependencies=[_write_guard],
)
async def lock_template(
    id: HybridId,
    session: DbSession,
) -> Envelope[TemplateOut]:
    data = await ChecklistService.lock_template(
        session=session,
        template_id=id,
    )
    return Envelope(data=data)


@router.post(
    "/templates/{id}/archive",
    response_model=Envelope[TemplateOut],
    dependencies=[_write_guard],
)
async def archive_template(
    id: HybridId,
    session: DbSession,
) -> Envelope[TemplateOut]:
    data = await ChecklistService.archive_template(
        session=session,
        template_id=id,
    )
    return Envelope(data=data)


@router.delete(
    "/templates/{id}",
    response_model=Envelope[dict[str, str]],
    dependencies=[_write_guard],
)
async def delete_template(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[dict[str, str]]:
    data = await ChecklistService.delete_template(
        session=session,
        template_id=id,
        current_user=current,
    )
    return Envelope(data=data)


@router.post(
    "/templates/{id}/sections",
    response_model=Envelope[SectionOut],
    status_code=status.HTTP_201_CREATED,
    dependencies=[_write_guard],
)
async def create_section(
    id: HybridId,
    body: SectionCreateRequest,
    session: DbSession,
) -> Envelope[SectionOut]:
    data = await ChecklistService.create_section(
        session=session,
        template_id=id,
        body=body,
    )
    return Envelope(data=data)


@router.delete(
    "/templates/{id}/sections/{section_id}",
    response_model=Envelope[dict[str, str]],
    dependencies=[_write_guard],
)
async def delete_section(
    id: HybridId,
    section_id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[dict[str, str]]:
    data = await ChecklistService.delete_section(
        session=session,
        template_id=id,
        section_id=section_id,
        current_user=current,
    )
    return Envelope(data=data)


@router.post(
    "/templates/{id}/sections/{section_id}/items",
    response_model=Envelope[ItemOut],
    status_code=status.HTTP_201_CREATED,
    dependencies=[_write_guard],
)
async def create_item(
    id: HybridId,
    section_id: HybridId,
    body: ItemCreateRequest,
    session: DbSession,
) -> Envelope[ItemOut]:
    data = await ChecklistService.create_item(
        session=session,
        template_id=id,
        section_id=section_id,
        body=body,
    )
    return Envelope(data=data)


@router.patch(
    "/templates/{id}/sections/{section_id}/items/{item_id}",
    response_model=Envelope[ItemOut],
    dependencies=[_write_guard],
)
async def update_item(
    id: HybridId,
    section_id: HybridId,
    item_id: HybridId,
    body: ItemUpdateRequest,
    session: DbSession,
) -> Envelope[ItemOut]:
    data = await ChecklistService.update_item(
        session=session,
        template_id=id,
        section_id=section_id,
        item_id=item_id,
        body=body,
    )
    return Envelope(data=data)


@router.delete(
    "/templates/{id}/sections/{section_id}/items/{item_id}",
    response_model=Envelope[dict[str, str]],
    dependencies=[_write_guard],
)
async def delete_item(
    id: HybridId,
    section_id: HybridId,
    item_id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[dict[str, str]]:
    data = await ChecklistService.delete_item(
        session=session,
        template_id=id,
        section_id=section_id,
        item_id=item_id,
        current_user=current,
    )
    return Envelope(data=data)