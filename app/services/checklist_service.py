"""Checklist service layer (PRD-F-01, B1-B3) — Anti-God-File & Service Decoupling.

Orchestrates template lifecycle:
DRAFT -> LOCKED -> ARCHIVED
and version forking: LOCKED -> new DRAFT version.
Enforces relational integrity, rubric clamping, and soft-delete safeguards.
"""

from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.identity import get_by_uuid, require_by_uuid
from app.models import AuditSession, ChecklistItem, ChecklistSection, ChecklistTemplate, User
from app.schemas.checklist import (
    DepartEnum,
    ItemCreateRequest,
    ItemOut,
    ItemUpdateRequest,
    RubricType,
    SectionCreateRequest,
    SectionOut,
    SectionWithItems,
    TemplateCreateRequest,
    TemplateDetail,
    TemplateOut,
    TemplateStatus,
)
from app.schemas.common import HybridId


class ChecklistService:
    @staticmethod
    async def get_template(session: AsyncSession, template_id: HybridId) -> ChecklistTemplate:
        template = await get_by_uuid(session, ChecklistTemplate, template_id)
        if template is None or template.deleted_at is not None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template tidak ditemukan")
        return template

    @staticmethod
    def ensure_draft(template: ChecklistTemplate) -> None:
        if template.status != TemplateStatus.DRAFT.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Hanya template berstatus DRAFT yang dapat dimodifikasi",
            )

    @classmethod
    async def list_templates(
        cls,
        session: AsyncSession,
        department: DepartEnum | None = None,
        brand_tier: str | None = None,
        status_filter: TemplateStatus | None = None,
    ) -> list[TemplateOut]:
        stmt = select(ChecklistTemplate).where(ChecklistTemplate.deleted_at.is_(None))
        if department:
            stmt = stmt.where(ChecklistTemplate.department == department.value)
        if brand_tier:
            stmt = stmt.where(ChecklistTemplate.brand_tier == brand_tier)
        if status_filter:
            stmt = stmt.where(ChecklistTemplate.status == status_filter.value)

        rows = (
            await session.scalars(
                stmt.order_by(
                    ChecklistTemplate.department,
                    ChecklistTemplate.name,
                    ChecklistTemplate.version,
                )
            )
        ).all()
        return [TemplateOut.model_validate(t) for t in rows]

    @classmethod
    async def get_template_detail(
        cls,
        session: AsyncSession,
        template_id: HybridId,
    ) -> TemplateDetail:
        template = await cls.get_template(session, template_id)
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
        return detail

    @classmethod
    async def create_template(
        cls,
        session: AsyncSession,
        body: TemplateCreateRequest,
        current_user: User,
    ) -> TemplateOut:
        stmt = pg_insert(ChecklistTemplate).values(
            department=body.department.value,
            name=body.name.strip(),
            version=body.version.strip(),
            brand_tier=body.brand_tier.value if body.brand_tier else None,
            status=TemplateStatus.DRAFT.value,
            published_by=current_user.id,
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
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Template (department, name, version) sudah ada",
            )
        template_id = await session.scalar(
            select(ChecklistTemplate.id).where(
                ChecklistTemplate.department == body.department.value,
                ChecklistTemplate.name == body.name.strip(),
                ChecklistTemplate.version == body.version.strip(),
            )
        )
        await session.commit()
        template = await cls.get_template(session, template_id)
        return TemplateOut.model_validate(template)

    @classmethod
    async def fork_template_version(
        cls,
        session: AsyncSession,
        template_id: HybridId,
        new_version: str,
        current_user: User,
    ) -> TemplateOut:
        source = await cls.get_template(session, template_id)
        if source.status != TemplateStatus.LOCKED.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Versi baru hanya bisa dibuat dari template berstatus LOCKED",
            )

        conflict = await session.scalar(
            select(ChecklistTemplate.id).where(
                ChecklistTemplate.department == source.department,
                ChecklistTemplate.name == source.name,
                ChecklistTemplate.version == new_version.strip(),
                ChecklistTemplate.deleted_at.is_(None),
            )
        )
        if conflict:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Template versi '{new_version.strip()}' sudah terdaftar",
            )

        new_template = ChecklistTemplate(
            department=source.department,
            name=source.name,
            version=new_version.strip(),
            brand_tier=source.brand_tier,
            status=TemplateStatus.DRAFT.value,
            published_by=current_user.id,
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
        await session.refresh(new_template)
        return TemplateOut.model_validate(new_template)

    @classmethod
    async def lock_template(
        cls,
        session: AsyncSession,
        template_id: HybridId,
    ) -> TemplateOut:
        template = await cls.get_template(session, template_id)
        if template.status != TemplateStatus.DRAFT.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Hanya template berstatus DRAFT yang bisa di-lock",
            )
        template.status = TemplateStatus.LOCKED.value
        template.locked_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(template)
        return TemplateOut.model_validate(template)

    @classmethod
    async def archive_template(
        cls,
        session: AsyncSession,
        template_id: HybridId,
    ) -> TemplateOut:
        template = await cls.get_template(session, template_id)
        if template.status != TemplateStatus.LOCKED.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Hanya template berstatus LOCKED yang dapat diarsipkan (ARCHIVED)",
            )
        template.status = TemplateStatus.ARCHIVED.value
        await session.commit()
        await session.refresh(template)
        return TemplateOut.model_validate(template)

    @classmethod
    async def delete_template(
        cls,
        session: AsyncSession,
        template_id: HybridId,
        current_user: User,
    ) -> dict[str, str]:
        template = await cls.get_template(session, template_id)
        if template.status != TemplateStatus.DRAFT.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Template berstatus LOCKED atau ARCHIVED tidak boleh dihapus. Gunakan versi baru atau arsip.",
            )

        linked_audits = await session.scalar(
            select(AuditSession.id).where(AuditSession.template_id == template.id).limit(1)
        )
        if linked_audits:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Template tidak dapat dihapus karena telah terhubung dengan sesi audit",
            )

        now = datetime.now(UTC)
        template.deleted_at = now
        template.deleted_by = current_user.id
        await session.commit()
        return {"status": "success", "message": "Template berhasil dihapus"}

    @classmethod
    async def create_section(
        cls,
        session: AsyncSession,
        template_id: HybridId,
        body: SectionCreateRequest,
    ) -> SectionOut:
        template = await cls.get_template(session, template_id)
        cls.ensure_draft(template)

        parent_internal: int | None = None
        if body.parent_id is not None:
            parent = await require_by_uuid(session, ChecklistSection, body.parent_id)
            if parent.template_id != template.id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="parent_id bukan bagian dari template ini",
                )
            parent_internal = parent.id

        conflict = await session.scalar(
            select(ChecklistSection.id).where(
                ChecklistSection.template_id == template.id,
                ChecklistSection.code == body.code.strip(),
                ChecklistSection.deleted_at.is_(None),
            )
        )
        if conflict:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Kode section sudah terdaftar di template ini",
            )

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
        return SectionOut.model_validate(section)

    @classmethod
    async def delete_section(
        cls,
        session: AsyncSession,
        template_id: HybridId,
        section_id: HybridId,
        current_user: User,
    ) -> dict[str, str]:
        template = await cls.get_template(session, template_id)
        cls.ensure_draft(template)
        section = await require_by_uuid(session, ChecklistSection, section_id)
        if section.template_id != template.id:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Section bukan milik template ini")

        now = datetime.now(UTC)
        section.deleted_at = now
        section.deleted_by = current_user.id
        await session.commit()
        return {"status": "success", "message": "Section berhasil dihapus"}

    @classmethod
    async def create_item(
        cls,
        session: AsyncSession,
        template_id: HybridId,
        section_id: HybridId,
        body: ItemCreateRequest,
    ) -> ItemOut:
        template = await cls.get_template(session, template_id)
        cls.ensure_draft(template)
        section = await require_by_uuid(session, ChecklistSection, section_id)
        if section.template_id != template.id:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Section bukan bagian dari template ini")

        if body.rubric_type == RubricType.MULTI_ROOM and body.max_score <= 90:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="MULTI_ROOM max_score harus > 90 (kelipatan 90 x jumlah sample ruangan)",
            )

        conflict = await session.scalar(
            select(ChecklistItem.id).where(
                ChecklistItem.section_id == section.id,
                ChecklistItem.code == body.code.strip(),
                ChecklistItem.deleted_at.is_(None),
            )
        )
        if conflict:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Kode item sudah terdaftar di section ini")

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
        return ItemOut.model_validate(item)

    @classmethod
    async def update_item(
        cls,
        session: AsyncSession,
        template_id: HybridId,
        section_id: HybridId,
        item_id: HybridId,
        body: ItemUpdateRequest,
    ) -> ItemOut:
        template = await cls.get_template(session, template_id)
        cls.ensure_draft(template)
        section = await require_by_uuid(session, ChecklistSection, section_id)
        if section.template_id != template.id:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Section bukan bagian dari template ini")
        item = await require_by_uuid(session, ChecklistItem, item_id)
        if item.section_id != section.id:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Item bukan bagian dari section ini")

        effective_rubric = body.rubric_type.value if body.rubric_type is not None else item.rubric_type
        effective_max = body.max_score if body.max_score is not None else float(item.max_score)

        if effective_rubric == RubricType.MULTI_ROOM.value and effective_max <= 90:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="MULTI_ROOM max_score harus lebih besar dari 90 (kelipatan 90 x jumlah sample)",
            )

        if body.question_text is not None:
            item.question_text = body.question_text.strip()
        if body.rubric_type is not None:
            item.rubric_type = body.rubric_type.value
        if body.max_score is not None:
            item.max_score = body.max_score
        if body.weight is not None:
            item.weight = body.weight
        if body.na_allowed is not None:
            item.na_allowed = body.na_allowed
        if body.is_life_safety is not None:
            item.is_life_safety = body.is_life_safety
        if body.sort_order is not None:
            item.sort_order = body.sort_order

        await session.commit()
        await session.refresh(item)
        return ItemOut.model_validate(item)

    @classmethod
    async def delete_item(
        cls,
        session: AsyncSession,
        template_id: HybridId,
        section_id: HybridId,
        item_id: HybridId,
        current_user: User,
    ) -> dict[str, str]:
        template = await cls.get_template(session, template_id)
        cls.ensure_draft(template)
        section = await require_by_uuid(session, ChecklistSection, section_id)
        if section.template_id != template.id:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Section bukan bagian dari template ini")
        item = await require_by_uuid(session, ChecklistItem, item_id)
        if item.section_id != section.id:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Item bukan bagian dari section ini")

        now = datetime.now(UTC)
        item.deleted_at = now
        item.deleted_by = current_user.id
        await session.commit()
        return {"status": "success", "message": "Item berhasil dihapus"}
