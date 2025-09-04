"""i18n translation endpoints — openapi.yaml `/translations` (PRD-F-22).

Hanya konten/master data yang di-translate (Constraint F1/F3): checklist
template/section/item, departemen hotel, brand, region, provinsi, role,
permission, dst. Label UI & enum status tetap di bundle frontend (F2).

Rute:
- GET    /translations            bulk per locale/entity (RPAC translations:read)
- PUT    /translations            bulk upsert (translations:write — CORP_AUDITOR)
- GET    /translations/{entity_type}/{entity_id}  semua field satu entity
- DELETE /translations/{id}       hapus satu terjemahan (translations:write)
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession
from app.core.identity import get_by_uuid
from app.models import Translation, User
from app.schemas.common import Envelope, HybridId
from app.schemas.translations import (
    TranslationBundle,
    TranslationOut,
    TranslationPutRequest,
)

router = APIRouter(prefix="/translations", tags=["translations"])


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


async def _get_translation(session: AsyncSession, translation_id: HybridId) -> Translation:
    t = await get_by_uuid(session, Translation, translation_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Terjemahan tidak ditemukan")
    return t


@router.get("", response_model=Envelope[TranslationBundle])
async def get_translations(
    current: CurrentUser,
    session: DbSession,
    locale: str,
    entity_type: str | None = None,
    since: datetime | None = None,
) -> Envelope[TranslationBundle]:
    await _require_permission(session, current, "translations:read")
    stmt = select(Translation).where(Translation.locale == locale)
    if entity_type:
        stmt = stmt.where(Translation.entity_type == entity_type)
    if since:
        stmt = stmt.where(Translation.updated_at >= since)
    stmt = stmt.order_by(Translation.updated_at.desc()).limit(2000)
    rows = list((await session.scalars(stmt)).all())
    return Envelope(
        data=TranslationBundle(
            locale=locale,
            items=[TranslationOut.model_validate(r) for r in rows],
            updated_after=since,
        )
    )


@router.put("", response_model=Envelope[dict])
async def put_translations(
    body: TranslationPutRequest,
    current: CurrentUser,
    session: DbSession,
) -> Envelope[dict]:
    await _require_permission(session, current, "translations:write")
    upsert = pg_insert(Translation).values(
        [
            {
                "entity_type": it.entity_type,
                "entity_id": it.entity_id,
                "field": it.field,
                "locale": body.locale,
                "value": it.value,
                "created_by": current.id,
                "updated_by": current.id,
            }
            for it in body.items
        ]
    )
    upsert = upsert.on_conflict_do_update(
        index_elements=["entity_type", "entity_id", "field", "locale"],
        set_={
            "value": upsert.excluded.value,
            "updated_by": current.id,
        },
    )
    await session.execute(upsert)
    await session.commit()
    return Envelope(data={"upserted": len(body.items)})


@router.get("/{entity_type}/{entity_id}", response_model=Envelope[list[TranslationOut]])
async def get_entity_translations(
    entity_type: str,
    entity_id: HybridId,
    current: CurrentUser,
    session: DbSession,
    locale: str | None = None,
) -> Envelope[list[TranslationOut]]:
    await _require_permission(session, current, "translations:read")
    entity_uuid = entity_id if isinstance(entity_id, uuid.UUID) else None
    stmt = (
        select(Translation)
        .where(Translation.entity_type == entity_type, Translation.entity_id == entity_uuid)
        .order_by(Translation.field)
    )
    if locale:
        stmt = stmt.where(Translation.locale == locale)
    rows = list((await session.scalars(stmt)).all())
    return Envelope(data=[TranslationOut.model_validate(r) for r in rows])


@router.delete("/{translation_id}", status_code=204)
async def delete_translation(
    translation_id: HybridId,
    current: CurrentUser,
    session: DbSession,
) -> None:
    await _require_permission(session, current, "translations:write")
    t = await _get_translation(session, translation_id)
    await session.execute(delete(Translation).where(Translation.uuid == t.uuid))
    await session.commit()
