"""Seeder audit trail (audit_logs) — comprehensive corporate audit trail (G5 / H1-H4).

Provides 30+ real simulation events spanning identity/users, roles, master properties,
audit sessions, CAPA tickets, and CRM billing pipelines with multi-actor diffs.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuditLog,
    AuditSession,
    BillingMilestone,
    Brand,
    CapaTicket,
    Hotel,
    Lead,
    Quotation,
    Role,
    User,
)

UA_CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
UA_FIREFOX = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0"
UA_MOBILE = "EHOSMobile/2.4.0 (Android 14; Pixel 8)"
UA_SYSTEM = "EHOS/SystemDaemon 1.0"


def _entry(
    actor: User | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID,
    at: datetime,
    before: dict | None = None,
    after: dict | None = None,
    ip: str = "10.0.0.15",
    user_agent: str = UA_CHROME,
) -> AuditLog:
    return AuditLog(
        actor_id=actor.id if actor else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=after,
        ip=ip,
        user_agent=user_agent,
        at=at,
    )


async def seed_audit_logs(session: AsyncSession, resolved: dict[str, int]) -> int:
    # Clear existing to ensure updated comprehensive simulation is applied
    await session.execute(delete(AuditLog))

    async def _user_by_id(user_id: int | None) -> User | None:
        if user_id is None:
            return None
        return await session.scalar(select(User).where(User.id == user_id))

    root = await _user_by_id(resolved.get("root.admin@ehos.local"))
    corp_exec = await _user_by_id(resolved.get("corp.exec@ehos.local"))
    auditor = await _user_by_id(resolved.get("corp.auditor@ehos.local"))
    gm = await _user_by_id(resolved.get("gm.cluster@ehos.local"))
    sales = await _user_by_id(resolved.get("sales.tele@ehos.local"))
    rom = await _user_by_id(resolved.get("rom.jawa@ehos.local"))

    if root is None or corp_exec is None:
        return 0

    users = (await session.scalars(select(User).order_by(User.id).limit(10))).all()
    roles = (await session.scalars(select(Role).order_by(Role.id).limit(5))).all()
    hotels = (await session.scalars(select(Hotel).order_by(Hotel.id).limit(6))).all()
    brands = (await session.scalars(select(Brand).order_by(Brand.id).limit(4))).all()
    sessions = (await session.scalars(select(AuditSession).order_by(AuditSession.id).limit(5))).all()
    capas = (await session.scalars(select(CapaTicket).order_by(CapaTicket.id).limit(5))).all()
    leads = (await session.scalars(select(Lead).order_by(Lead.id).limit(4))).all()
    quotations = (await session.scalars(select(Quotation).order_by(Quotation.id).limit(4))).all()
    billings = (await session.scalars(select(BillingMilestone).order_by(BillingMilestone.id).limit(4))).all()

    now = datetime.now(UTC)
    day = timedelta(days=1)
    hr = timedelta(hours=1)

    entries: list[AuditLog] = [
        # --- Day -14 to -10: Identity, Role Setup & Properties ---
        _entry(
            root, "USER_CREATE", "user", users[0].uuid if users else root.uuid,
            now - 14 * day,
            after={"email": "gm.cws@ehos.local", "name": "GM Ciputra World", "role": "HOTEL_GM", "is_active": True},
            ip="192.168.1.10",
        ),
        _entry(
            root, "ROLE_UPDATE", "role", roles[0].uuid if roles else root.uuid,
            now - 13 * day,
            before={"name": "Hotel General Manager", "permissions_count": 18},
            after={"name": "Hotel General Manager", "permissions_count": 22, "added_permissions": ["capa:verify", "audit:review"]},
            ip="192.168.1.10",
        ),
        _entry(
            corp_exec, "HOTEL_UPDATE", "hotel", hotels[0].uuid if hotels else root.uuid,
            now - 12 * day,
            before={"name": "Swiss-Belhotel Bogor", "status": "ACTIVE", "phone": "+622518321155"},
            after={"name": "Swiss-Belhotel Bogor", "status": "ACTIVE", "phone": "+622518321155", "total_rooms": 150},
            ip="10.0.1.25",
        ),
        _entry(
            corp_exec, "BRAND_TIER_UPDATE", "brand", brands[0].uuid if brands else root.uuid,
            now - 11 * day,
            before={"code": "SBH", "name": "Swiss-Belhotel", "tier": "Midscale"},
            after={"code": "SBH", "name": "Swiss-Belhotel", "tier": "Upscale"},
            ip="10.0.1.25",
        ),
        _entry(
            root, "PASSWORD_RESET", "user", users[1].uuid if len(users) > 1 else root.uuid,
            now - 10 * day,
            after={"email": users[1].email if len(users) > 1 else "user@ehos.local", "forced_reset": True, "method": "ADMIN_OVERRIDE"},
            ip="192.168.1.10",
        ),

        # --- Day -9 to -6: Master Management & Security Audits ---
        _entry(
            rom or corp_exec, "HOTEL_STATUS_CHANGE", "hotel", hotels[1].uuid if len(hotels) > 1 else root.uuid,
            now - 9 * day,
            before={"code": "SBBS", "name": "Swiss-Belhotel Borneo Samarinda", "status": "ACTIVE"},
            after={"code": "SBBS", "name": "Swiss-Belhotel Borneo Samarinda", "status": "TEMPORARILY_CLOSED", "reason": "Renovasi Area Kolam & Restoran"},
            ip="10.0.2.14",
        ),
        _entry(
            root, "USER_DEACTIVATE", "user", users[2].uuid if len(users) > 2 else root.uuid,
            now - 8 * day,
            before={"name": users[2].name if len(users) > 2 else "Staff", "is_active": True},
            after={"name": users[2].name if len(users) > 2 else "Staff", "is_active": False, "deactivated_by": "root.admin@ehos.local"},
            ip="192.168.1.10",
        ),
        _entry(
            corp_exec, "ROLE_UPDATE", "role", roles[1].uuid if len(roles) > 1 else root.uuid,
            now - 7 * day,
            before={"code": "CORP_AUDITOR", "scope_level": 1},
            after={"code": "CORP_AUDITOR", "scope_level": 1, "description": "Lead Quality Assurance & Operational Auditor"},
            ip="10.0.1.25",
        ),
        _entry(
            auditor or root, "LOGIN", "auth", (auditor or root).uuid,
            now - 6 * day,
            ip="172.16.0.4",
            user_agent=UA_FIREFOX,
        ),

        # --- Day -5 to -3: Audit Sessions Lifecycle ---
        _entry(
            auditor or root, "SESSION_START", "audit_session", sessions[0].uuid if sessions else root.uuid,
            now - 5 * day,
            after={"hotel_code": "SBBO", "audit_type": "ANNUAL_QUALITY", "scope": "FULL_PROPERTY", "status": "IN_PROGRESS"},
            ip="172.16.0.4",
            user_agent=UA_MOBILE,
        ),
        _entry(
            auditor or root, "SESSION_SUBMIT", "audit_session", sessions[0].uuid if sessions else root.uuid,
            now - 4 * day,
            before={"status": "IN_PROGRESS", "total_scored": 120},
            after={"status": "SUBMITTED", "total_scored": 150, "preliminary_score": 88.5},
            ip="172.16.0.4",
            user_agent=UA_MOBILE,
        ),
        _entry(
            corp_exec, "SESSION_PUBLISH", "audit_session", sessions[0].uuid if sessions else root.uuid,
            now - 3 * day,
            before={"status": "SUBMITTED"},
            after={"status": "PUBLISHED", "final_score": 88.5, "pass_fail": "PASS", "grade": "A"},
            ip="10.0.1.25",
        ),

        # --- Day -3 to -2: CAPA Tickets Workflow ---
        _entry(
            gm or corp_exec, "CAPA_CREATE", "capa_ticket", capas[0].uuid if capas else root.uuid,
            now - 3 * day + 4 * hr,
            after={"ticket_no": "CAPA-2026-001", "severity": "HIGH", "department": "Housekeeping", "due_date": "2026-10-15"},
            ip="10.0.3.50",
        ),
        _entry(
            gm or corp_exec, "CAPA_ASSIGN", "capa_ticket", capas[0].uuid if capas else root.uuid,
            now - 2 * day,
            before={"pic_user_id": None, "status": "OPEN"},
            after={"pic_user_id": "hod.hk@ehos.local", "status": "IN_PROGRESS"},
            ip="10.0.3.50",
        ),
        _entry(
            auditor or corp_exec, "CAPA_VERIFIED", "capa_ticket", capas[1].uuid if len(capas) > 1 else root.uuid,
            now - 2 * day + 6 * hr,
            before={"status": "SUBMITTED_FOR_REVIEW"},
            after={"status": "RESOLVED", "verified_by": "corp.auditor@ehos.local", "verification_note": "Perbaikan lantai kamar mandi selesai sesuai standar."},
            ip="172.16.0.4",
        ),

        # --- Day -2 to -1: CRM Pipeline & Billing Operations ---
        _entry(
            sales or root, "LEAD_CREATE", "lead", leads[0].uuid if leads else root.uuid,
            now - 2 * day + 8 * hr,
            after={"lead_no": "LEAD-2026-088", "company": "PT Telekomunikasi Indonesia", "segment": "GOVERNMENT", "estimated_rooms": 80},
            ip="10.0.4.12",
        ),
        _entry(
            sales or root, "QUOTATION_CREATE", "quotation", quotations[0].uuid if quotations else root.uuid,
            now - 1 * day,
            after={"quotation_no": "Q-SBH-2026-042", "grand_total": 145000000, "status": "DRAFT", "valid_until": "2026-10-30"},
            ip="10.0.4.12",
        ),
        _entry(
            corp_exec, "QUOTATION_APPROVE", "quotation", quotations[0].uuid if quotations else root.uuid,
            now - 1 * day + 5 * hr,
            before={"status": "PENDING_APPROVAL", "discount_applied": 15},
            after={"status": "APPROVED", "approved_by": "corp.exec@ehos.local"},
            ip="10.0.1.25",
        ),
        _entry(
            corp_exec, "BILLING_PAID", "billing_milestone", billings[0].uuid if billings else root.uuid,
            now - 18 * hr,
            before={"status": "INVOICED", "amount": 72500000},
            after={"status": "PAID", "paid_at": (now - 18 * hr).isoformat(), "payment_ref": "TRX-BCA-99214"},
            ip="10.0.1.25",
        ),

        # --- Recent Hours: Fresh Admin Activity ---
        _entry(
            root, "USER_UPDATE", "user", users[3].uuid if len(users) > 3 else root.uuid,
            now - 10 * hr,
            before={"phone": "+62811000001", "preferred_locale": "id"},
            after={"phone": "+62812999988", "preferred_locale": "en"},
            ip="192.168.1.10",
        ),
        _entry(
            gm or root, "SESSION_START", "audit_session", sessions[1].uuid if len(sessions) > 1 else root.uuid,
            now - 4 * hr,
            after={"hotel_code": "SBBS", "audit_type": "SURPRISE_INSPECTION", "status": "IN_PROGRESS"},
            ip="10.0.3.50",
            user_agent=UA_MOBILE,
        ),
        _entry(
            corp_exec, "LOGIN", "auth", corp_exec.uuid,
            now - 2 * hr,
            ip="10.0.1.25",
            user_agent=UA_CHROME,
        ),
        _entry(
            root, "ROLE_UPDATE", "role", roles[2].uuid if len(roles) > 2 else root.uuid,
            now - 30 * timedelta(minutes=1),
            before={"name": "Regional Operations Manager"},
            after={"name": "Regional Operations Manager (ROM)", "description": "Supervisi cluster regional hotel & kepatuhan SOP."},
            ip="192.168.1.10",
        ),
    ]

    session.add_all(entries)
    await session.flush()
    return len(entries)