"""Seeder audit trail (audit_logs) — trail korporat untuk halaman Audit Log admin.

Idempoten (H4): batal bila tabel sudah berisi (audit trail = append-only, tidak
bisa re-run menambah duplikat). Menyimulasikan kejadian nyata lintas entitas
(user/role/hotel/brand/audit_session) dengan kondisi H3: multi-actor, multi-action,
rentang waktu beberapa hari.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, AuditSession, Brand, Role, User
from app.models.master import Hotel

UA = "EHOS/seed 1.0"


def _entry(
    actor: User,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID,
    at: datetime,
    before: dict | None = None,
    after: dict | None = None,
    ip: str = "127.0.0.1",
) -> AuditLog:
    return AuditLog(
        actor_id=actor.id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=after,
        ip=ip,
        user_agent=UA,
        at=at,
    )


async def seed_audit_logs(session: AsyncSession, resolved: dict[str, int]) -> int:
    if (await session.scalar(select(func.count(AuditLog.id))) or 0) > 0:
        return 0

    async def _user_by_id(user_id: int | None) -> User | None:
        if user_id is None:
            return None
        return await session.scalar(select(User).where(User.id == user_id))

    root = await _user_by_id(resolved.get("root.admin@ehos.local"))
    corp_exec = await _user_by_id(resolved.get("corp.exec@ehos.local"))
    gm = await _user_by_id(resolved.get("gm.cluster@ehos.local"))
    if root is None or corp_exec is None or gm is None:
        return 0

    users = (await session.scalars(select(User).order_by(User.id).limit(3))).all()
    roles = (await session.scalars(select(Role).order_by(Role.id).limit(3))).all()
    hotels = (await session.scalars(select(Hotel).order_by(Hotel.id).limit(3))).all()
    brands = (await session.scalars(select(Brand).order_by(Brand.id).limit(2))).all()
    sessions = (await session.scalars(select(AuditSession).order_by(AuditSession.id).limit(2))).all()

    now = datetime.now(UTC)
    day = timedelta(days=1)
    entries = [
        _entry(
            root, "USER_CREATE", "user", users[-1].uuid,
            now - 5 * day,
            after={"email": users[-1].email, "name": users[-1].name, "is_active": True},
        ),
        _entry(
            root, "ROLE_UPDATE", "role", roles[0].uuid if roles else root.uuid,
            now - 4 * day,
            before={"permissions": 4},
            after={"permissions": 6},
        ),
        _entry(
            corp_exec, "HOTEL_UPDATE", "hotel", hotels[0].uuid if hotels else root.uuid,
            now - 3 * day,
            before={"status": "ACTIVE"},
            after={"status": "TEMPORARILY_CLOSED"},
        ),
        _entry(
            corp_exec, "BRAND_TIER_UPDATE", "brand", brands[0].uuid if brands else root.uuid,
            now - 2 * day,
            before={"tier": "Midscale"},
            after={"tier": "Upscale"},
        ),
        _entry(
            gm, "SESSION_PUBLISH", "audit_session", sessions[0].uuid if sessions else root.uuid,
            now - 2 * day,
            after={"status": "PUBLISHED", "pass_fail": "FAIL"},
        ),
        _entry(
            corp_exec, "RESET_PASSWORD", "user", users[1].uuid,
            now - 1 * day,
        ),
        _entry(
            root, "LOGIN", "auth", root.uuid,
            now - timedelta(hours=6),
            ip="10.0.0.23",
        ),
        _entry(
            gm, "SESSION_SUBMIT", "audit_session", sessions[1].uuid if len(sessions) > 1 else root.uuid,
            now - timedelta(hours=2),
            after={"status": "SUBMITTED"},
            ip="10.0.0.41",
        ),
    ]

    session.add_all(entries)
    await session.flush()
    return len(entries)