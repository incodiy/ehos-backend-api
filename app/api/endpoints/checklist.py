"""Checklist bank endpoints — openapi.yaml `/checklist/templates` (PRD-F-01, B2/B3).

Template lifecycle: DRAFT (editable) → LOCKED (immutable snapshot for audit sessions)
→ version fork (LOCKED → new DRAFT). Only `checklist:write` holders (CORP_AUDITOR)
create/version/lock; reads require `checklist:read`.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession
from app.core.identity import get_by_uuid, require_by_uuid
from app.models import ChecklistItem, ChecklistSection, ChecklistTemplate, User
from app.schemas.checklist import (
    DepartEnum,
    ItemCreateRequest,
    ItemOut,
    RubricType,
    SectionCreateRequest,
    SectionOut,
    SectionWithItems,
    TemplateCreateRequest,
    TemplateDetail,
    TemplateOut,
    TemplateStatus,
    VersionCreateRequest,
)
from app.schemas.common import Envelope, HybridId

router = APIRouter(prefix="/checklist", tags=["checklist"])


async def _has_permission(session: AsyncSession, user: User, code: str) -> bool:
    row = await session.execute(
        text(
            "SELECT 1 FROM users u "
            "JOIN user_roles ur ON ur.user_id = u.id "
            "JOIN roles_permissions rp ON rp.role_id = ur.role_id "
            "JOIN permissions p ON p.id = rp.permission_id "
            "WHERE u.id = :uid AND p.code = :code LIMIT 1"
        ).bindparams(uid=user.id, code=code)
    )
    return row.first() is not None


async def _require_permission(session: AsyncSession, user: User, code: str) -> None:
    if not await _has_permission(session, user, code):
        raise HTTPException(status_code=403, detail=f"Missing permission: {code}")


async def _get_template(session: AsyncSession, template_id: HybridId) -> ChecklistTemplate:
    template = await get_by_uuid(session, ChecklistTemplate, template_id)
    if template is None or template.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Template tidak ditemukan")
    return template


def _ensure_draft(template: ChecklistTemplate) -> None:
    if template.status != TemplateStatus.DRAFT.value:
        raise HTTPException(status_code=409, detail="Hanya template DRAFT yang bisa dimodifikasi")


@router.get("/templates", response_model=Envelope[list[TemplateOut]])
async def list_templates(
    current: CurrentUser,
    session: DbSession,
    department: DepartEnum | None = None,
    brand_tier: str | None = None,
    status: TemplateStatus | None = None,
) -> Envelope[list[TemplateOut]]:
    await _require_permission(session, current, "checklist:read")
    stmt = select(ChecklistTemplate).where(ChecklistTemplate.deleted_at.is_(None))
    if department:
        stmt = stmt.where(ChecklistTemplate.department == department.value)
    if brand_tier:
        stmt = stmt.where(ChecklistTemplate.brand_tier == brand_tier)
    if status:
        stmt = stmt.where(ChecklistTemplate.status == status.value)
    rows = (
        await session.scalars(
            stmt.order_by(
                ChecklistTemplate.department,
                ChecklistTemplate.name,
                ChecklistTemplate.version,
            )
        )
    ).all()
    return Envelope(data=[TemplateOut.model_validate(t) for t in rows])


@router.post("/templates", response_model=Envelope[TemplateOut], status_code=201)
async def create_template(
    body: TemplateCreateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[TemplateOut]:
    await _require_permission(session, current, "checklist:write")
    stmt = pg_insert(ChecklistTemplate).values(
        department=body.department.value,
        name=body.name.strip(),
        version=body.version.strip(),
        brand_tier=body.brand_tier.value if body.brand_tier else None,
        status=TemplateStatus.DRAFT.value,
        published_by=current.id,
    )
    stmt = stmt.on_conflict_do_nothing(
        index_elements=[
            ChecklistTemplate.department,
            ChecklistTemplate.name,
            ChecklistTemplate.version,
        ]
    )
    result = await session.execute(stmt)
    if result.rowcount == 0:
        raise HTTPException(status_code=409, detail="Template (department, name, version) sudah ada")
    template_id = await session.scalar(
        select(ChecklistTemplate.id).where(
            ChecklistTemplate.department == body.department.value,
            ChecklistTemplate.name == body.name.strip(),
            ChecklistTemplate.version == body.version.strip(),
        )
    )
    await session.commit()
    template = await _get_template(session, template_id)
    return Envelope(data=TemplateOut.model_validate(template))


@router.get("/templates/{id}", response_model=Envelope[TemplateDetail])
async def get_template(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[TemplateDetail]:
    await _require_permission(session, current, "checklist:read")
    template = await _get_template(session, id)
    sections = (
        await session.scalars(
            select(ChecklistSection)
            .where(ChecklistSection.template_id == template.id, ChecklistSection.deleted_at.is_(None))
            .order_by(ChecklistSection.sort_order, ChecklistSection.code)
        )
    ).all()
    detail = TemplateDetail(template=TemplateOut.model_validate(template))
    for sec in sections:
        items = (
            await session.scalars(
                select(ChecklistItem)
                .where(ChecklistItem.section_id == sec.id, ChecklistItem.deleted_at.is_(None))
                .order_by(ChecklistItem.sort_order, ChecklistItem.code)
            )
        ).all()
        sec_model = SectionWithItems.model_validate(sec)
        sec_model.items = [ItemOut.model_validate(i) for i in items]
        detail.sections.append(sec_model)
    return Envelope(data=detail)


@router.post("/templates/{id}/versions", response_model=Envelope[TemplateOut], status_code=201)
async def create_template_version(
    id: HybridId,
    body: VersionCreateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[TemplateOut]:
    await _require_permission(session, current, "checklist:write")
    source = await _get_template(session, id)
    if source.status != TemplateStatus.LOCKED.value:
        raise HTTPException(status_code=409, detail="Versi baru hanya bisa dibuat dari template LOCKED")

    conflict = await session.scalar(
        select(ChecklistTemplate.id).where(
            ChecklistTemplate.department == source.department,
            ChecklistTemplate.name == source.name,
            ChecklistTemplate.version == body.new_version.strip(),
        )
    )
    if conflict:
        raise HTTPException(status_code=409, detail="Versi baru sudah ada")

    new_template = ChecklistTemplate(
        department=source.department,
        name=source.name,
        version=body.new_version.strip(),
        brand_tier=source.brand_tier,
        status=TemplateStatus.DRAFT.value,
        published_by=current.id,
    )
    session.add(new_template)
    await session.flush()

    sections = (
        await session.scalars(
            select(ChecklistSection)
            .where(ChecklistSection.template_id == source.id, ChecklistSection.deleted_at.is_(None))
            .order_by(ChecklistSection.sort_order)
        )
    ).all()
    for sec in sections:
        parent_id: int | None = None
        if sec.parent_id is not None:
            parent = await session.scalar(
                select(ChecklistSection.id).where(
                    ChecklistSection.template_id == new_template.id,
                    ChecklistSection.code == sec.code.rsplit(".", 1)[0],
                )
            )
            parent_id = parent
        new_sec = ChecklistSection(
            template_id=new_template.id,
            parent_id=parent_id,
            name=sec.name,
            code=sec.code,
            sort_order=sec.sort_order,
        )
        session.add(new_sec)
        await session.flush()
        items = (
            await session.scalars(
                select(ChecklistItem)
                .where(ChecklistItem.section_id == sec.id, ChecklistItem.deleted_at.is_(None))
                .order_by(ChecklistItem.sort_order)
            )
        ).all()
        for it in items:
            session.add(
                ChecklistItem(
                    section_id=new_sec.id,
                    code=it.code,
                    question_text=it.question_text,
                    rubric_type=it.rubric_type,
                    max_score=it.max_score,
                    weight=it.weight,
                    na_allowed=it.na_allowed,
                    is_life_safety=it.is_life_safety,
                    sort_order=it.sort_order,
                )
            )
    await session.commit()
    return Envelope(data=TemplateOut.model_validate(new_template))


@router.post("/templates/{id}/lock", response_model=Envelope[TemplateOut])
async def lock_template(
    id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[TemplateOut]:
    await _require_permission(session, current, "checklist:write")
    template = await _get_template(session, id)
    if template.status != TemplateStatus.DRAFT.value:
        raise HTTPException(status_code=409, detail="Hanya template DRAFT yang bisa di-lock")
    template.status = TemplateStatus.LOCKED.value
    template.locked_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(template)
    return Envelope(data=TemplateOut.model_validate(template))


@router.post("/templates/{id}/sections", response_model=Envelope[SectionOut], status_code=201)
async def create_section(
    id: HybridId,
    body: SectionCreateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[SectionOut]:
    await _require_permission(session, current, "checklist:write")
    template = await _get_template(session, id)
    _ensure_draft(template)
    parent_internal: int | None = None
    if body.parent_id is not None:
        parent = await require_by_uuid(session, ChecklistSection, body.parent_id)
        if parent.template_id != template.id:
            raise HTTPException(status_code=422, detail="parent_id bukan bagian dari template ini")
        parent_internal = parent.id

    conflict = await session.scalar(
        select(ChecklistSection.id).where(
            ChecklistSection.template_id == template.id,
            ChecklistSection.code == body.code.strip(),
        )
    )
    if conflict:
        raise HTTPException(status_code=409, detail="Section code sudah ada di template ini")
    section = ChecklistSection(
        template_id=template.id,
        parent_id=parent_internal,
        code=body.code.strip(),
        name=body.name.strip(),
        sort_order=body.sort_order,
    )
    session.add(section)
    await session.commit()
    await session.refresh(section)
    return Envelope(data=SectionOut.model_validate(section))


@router.post("/templates/{id}/sections/{section_id}/items", response_model=Envelope[ItemOut], status_code=201)
async def create_item(
    id: HybridId,
    section_id: HybridId,
    body: ItemCreateRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[ItemOut]:
    await _require_permission(session, current, "checklist:write")
    template = await _get_template(session, id)
    _ensure_draft(template)
    section = await require_by_uuid(session, ChecklistSection, section_id)
    if section.template_id != template.id:
        raise HTTPException(status_code=422, detail="Section bukan bagian dari template ini")

    if body.rubric_type == RubricType.MULTI_ROOM and body.max_score <= 90:
        raise HTTPException(
            status_code=422, detail="MULTI_ROOM max_score harus = 90 x jumlah sample ruangan"
        )

    conflict = await session.scalar(
        select(ChecklistItem.id).where(
            ChecklistItem.section_id == section.id,
            ChecklistItem.code == body.code.strip(),
        )
    )
    if conflict:
        raise HTTPException(status_code=409, detail="Item code sudah ada di section ini")
    item = ChecklistItem(
        section_id=section.id,
        code=body.code.strip(),
        question_text=body.question_text.strip(),
        rubric_type=body.rubric_type.value,
        max_score=body.max_score,
        weight=body.weight,
        na_allowed=body.na_allowed,
        is_life_safety=body.is_life_safety,
        sort_order=body.sort_order,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return Envelope(data=ItemOut.model_validate(item))