"""Audit scenario seeder — PRD-F-01/02/03/05 (Constraint H1-H4).

Mensimulasikan 6 variasi kondisi produksi dinamis untuk pengujian FSM & UI:
1. DRAFT: Sesi baru dijadwalkan di Hotel MANP, skor kosong.
2. IN_PROGRESS: Sesi di Hotel SBAI, 40% butir telah terisi, mutasi aktif.
3. SUBMITTED: Sesi di Hotel SQYO selesai diinput auditor, menunggu publish QA.
4. PUBLISHED_BORDERLINE_PASS: Sesi di Hotel CWS dengan skor 80.5% (PASS tipis).
5. PUBLISHED_CRITICAL_FAIL: Sesi di Hotel ZHAI dengan skor 74.2% (FAIL) + temuan Life-Safety terhubung ke tiket CAPA P1 SLA 24h.
6. SYNC_CONFLICT: Sesi dengan rekaman sync_conflict_logs PENDING (F-05).

Deterministik & idempoten (H4).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuditItemScore,
    AuditSession,
    CapaTicket,
    ChecklistItem,
    ChecklistSection,
    ChecklistTemplate,
    Finding,
    Hotel,
    SyncConflictLog,
)
from app.services.capa_trigger import auto_create_capa_ticket

# Fixed client_ids for deterministic & idempotent runs
SCENARIO_CLIENT_IDS = {
    "DRAFT": uuid.UUID("11111111-0000-4000-8000-000000000001"),
    "IN_PROGRESS": uuid.UUID("11111111-0000-4000-8000-000000000002"),
    "SUBMITTED": uuid.UUID("11111111-0000-4000-8000-000000000003"),
    "BORDERLINE_PASS": uuid.UUID("11111111-0000-4000-8000-000000000004"),
    "CRITICAL_FAIL": uuid.UUID("11111111-0000-4000-8000-000000000005"),
    "SYNC_CONFLICT": uuid.UUID("11111111-0000-4000-8000-000000000006"),
}


async def seed_audit_scenarios(
    session: AsyncSession,
    resolved_users: dict[str, int],
) -> None:
    """Jalankan seeder 6 skenario sesi audit."""
    auditor_id = resolved_users.get("corp.auditor@ehos.local") or resolved_users.get("root.admin@ehos.local", 1)

    # Bersihkan skenario sebelumnya jika ada
    existing_sessions = (
        await session.scalars(
            select(AuditSession).where(AuditSession.client_id.in_(list(SCENARIO_CLIENT_IDS.values())))
        )
    ).all()
    for s in existing_sessions:
        # Hapus conflict logs
        await session.execute(delete(SyncConflictLog).where(SyncConflictLog.session_id == s.id))
        # Hapus CAPA tickets via findings
        finding_ids = (await session.scalars(select(Finding.id).where(Finding.session_id == s.id))).all()
        if finding_ids:
            await session.execute(delete(CapaTicket).where(CapaTicket.finding_id.in_(finding_ids)))
            await session.execute(delete(Finding).where(Finding.session_id == s.id))
        # Hapus item scores
        await session.execute(delete(AuditItemScore).where(AuditItemScore.session_id == s.id))
        await session.delete(s)
    await session.flush()

    # Load master references
    hotels = {h.code: h.id for h in (await session.scalars(select(Hotel))).all()}
    cws_id = hotels.get("CWS")
    manp_id = hotels.get("MANP") or cws_id
    sbai_id = hotels.get("SBAI") or cws_id
    sqyo_id = hotels.get("SQYO") or cws_id
    zhai_id = hotels.get("ZHAI") or hotels.get("ZHBA") or cws_id

    # Load templates per department
    templates = {}
    for dept in ("GM", "HOUSEKEEPING", "KITCHEN_FB", "SECURITY_RISK"):
        t = await session.scalar(
            select(ChecklistTemplate)
            .where(ChecklistTemplate.department == dept, ChecklistTemplate.status == "LOCKED")
            .order_by(ChecklistTemplate.locked_at.desc())
        )
        if t:
            templates[dept] = t

    if not templates:
        return

    now = datetime.now(UTC)
    today = date.today()

    # ──────────────────────────────────────────────────────────────────────────
    # 1. SCENARIO DRAFT: Sesi baru di MANP
    # ──────────────────────────────────────────────────────────────────────────
    hk_tpl = templates.get("HOUSEKEEPING") or list(templates.values())[0]
    s_draft = AuditSession(
        client_id=SCENARIO_CLIENT_IDS["DRAFT"],
        hotel_id=manp_id,
        template_id=hk_tpl.id,
        department=hk_tpl.department,
        audit_type="FULL",
        status="DRAFT",
        auditor_id=auditor_id,
        date_start=today + timedelta(days=7),
        date_end=today + timedelta(days=9),
        origin="SYSTEM",
        sync_status="SYNCED",
        created_by=auditor_id,
        updated_by=auditor_id,
    )
    session.add(s_draft)

    # ──────────────────────────────────────────────────────────────────────────
    # 2. SCENARIO IN_PROGRESS: Sesi sedang berjalan di SBAI (40% terisi)
    # ──────────────────────────────────────────────────────────────────────────
    srm_tpl = templates.get("SECURITY_RISK") or list(templates.values())[0]
    s_prog = AuditSession(
        client_id=SCENARIO_CLIENT_IDS["IN_PROGRESS"],
        hotel_id=sbai_id,
        template_id=srm_tpl.id,
        department=srm_tpl.department,
        audit_type="FULL",
        status="IN_PROGRESS",
        auditor_id=auditor_id,
        date_start=today - timedelta(days=2),
        date_end=today + timedelta(days=1),
        origin="SYSTEM",
        sync_status="SYNCED",
        created_by=auditor_id,
        updated_by=auditor_id,
    )
    session.add(s_prog)
    await session.flush()

    srm_items = (
        await session.scalars(
            select(ChecklistItem)
            .join(ChecklistSection)
            .where(ChecklistSection.template_id == srm_tpl.id)
            .order_by(ChecklistItem.sort_order)
        )
    ).all()
    count_to_score = max(1, len(srm_items) * 4 // 10)
    for idx, it in enumerate(srm_items[:count_to_score]):
        session.add(
            AuditItemScore(
                session_id=s_prog.id,
                item_id=it.id,
                value="YES" if idx % 4 != 0 else "NO",
                score=90.0 if idx % 4 != 0 else 0.0,
                is_na=False,
                note="Pemeriksaan parsial lapangan" if idx % 4 == 0 else None,
                scored_by=auditor_id,
                scored_at=now - timedelta(hours=idx + 1),
                updated_at=now - timedelta(hours=idx + 1),
            )
        )

    # ──────────────────────────────────────────────────────────────────────────
    # 3. SCENARIO SUBMITTED: Sesi selesai input di SQYO, menunggu review QA
    # ──────────────────────────────────────────────────────────────────────────
    gm_tpl = templates.get("GM") or list(templates.values())[0]
    s_subm = AuditSession(
        client_id=SCENARIO_CLIENT_IDS["SUBMITTED"],
        hotel_id=sqyo_id,
        template_id=gm_tpl.id,
        department=gm_tpl.department,
        audit_type="FULL",
        status="SUBMITTED",
        auditor_id=auditor_id,
        date_start=today - timedelta(days=5),
        date_end=today - timedelta(days=1),
        origin="SYSTEM",
        sync_status="SYNCED",
        created_by=auditor_id,
        updated_by=auditor_id,
    )
    session.add(s_subm)
    await session.flush()

    gm_items = (
        await session.scalars(
            select(ChecklistItem)
            .join(ChecklistSection)
            .where(ChecklistSection.template_id == gm_tpl.id)
        )
    ).all()
    for it in gm_items:
        session.add(
            AuditItemScore(
                session_id=s_subm.id,
                item_id=it.id,
                value="YES",
                score=90.0,
                is_na=False,
                scored_by=auditor_id,
                scored_at=now - timedelta(days=2),
                updated_at=now - timedelta(days=2),
            )
        )

    # ──────────────────────────────────────────────────────────────────────────
    # 4. SCENARIO PUBLISHED BORDERLINE PASS (80.5%) di CWS
    # ──────────────────────────────────────────────────────────────────────────
    kfb_tpl = templates.get("KITCHEN_FB") or list(templates.values())[0]
    s_pass = AuditSession(
        client_id=SCENARIO_CLIENT_IDS["BORDERLINE_PASS"],
        hotel_id=cws_id,
        template_id=kfb_tpl.id,
        department=kfb_tpl.department,
        audit_type="FULL",
        status="PUBLISHED",
        auditor_id=auditor_id,
        date_start=today - timedelta(days=20),
        date_end=today - timedelta(days=18),
        published_at=now - timedelta(days=17),
        pass_fail="PASS",
        total_score=80.50,
        origin="SYSTEM",
        sync_status="SYNCED",
        created_by=auditor_id,
        updated_by=auditor_id,
    )
    session.add(s_pass)

    # ──────────────────────────────────────────────────────────────────────────
    # 5. SCENARIO PUBLISHED CRITICAL FAIL (74.2% & Life-Safety CAPA P1) di ZHAI
    # ──────────────────────────────────────────────────────────────────────────
    s_fail = AuditSession(
        client_id=SCENARIO_CLIENT_IDS["CRITICAL_FAIL"],
        hotel_id=zhai_id,
        template_id=srm_tpl.id,
        department=srm_tpl.department,
        audit_type="FULL",
        status="PUBLISHED",
        auditor_id=auditor_id,
        date_start=today - timedelta(days=12),
        date_end=today - timedelta(days=10),
        published_at=now - timedelta(days=9),
        pass_fail="FAIL",
        total_score=74.20,
        origin="SYSTEM",
        sync_status="SYNCED",
        created_by=auditor_id,
        updated_by=auditor_id,
    )
    session.add(s_fail)
    await session.flush()

    # Buat temuan life-safety & trigger CAPA P1
    life_safety_item = next((it for it in srm_items if it.is_life_safety), srm_items[0])
    f_crit = Finding(
        session_id=s_fail.id,
        item_id=life_safety_item.id,
        hotel_id=zhai_id,
        is_life_safety=True,
        severity="CRITICAL",
        title="Jalur Evakuasi Darurat & Pintu Darurat Terkunci",
        description="Pintu darurat lantai 3 digembok dari luar. Menghalangi evakuasi jika terjadi kebakaran.",
        location="Lantai 3 Sayap Barat",
    )
    session.add(f_crit)
    await session.flush()

    # Chained CAPA Ticket Priority 1 SLA 24h
    await auto_create_capa_ticket(
        session, f_crit, actor_id=auditor_id, department=s_fail.department
    )

    # ──────────────────────────────────────────────────────────────────────────
    # 6. SCENARIO SYNC CONFLICT: Sesi dengan log konflik pending di CWS
    # ──────────────────────────────────────────────────────────────────────────
    s_conf = AuditSession(
        client_id=SCENARIO_CLIENT_IDS["SYNC_CONFLICT"],
        hotel_id=cws_id,
        template_id=hk_tpl.id,
        department=hk_tpl.department,
        audit_type="MICRO",
        status="IN_PROGRESS",
        auditor_id=auditor_id,
        date_start=today - timedelta(days=1),
        date_end=today,
        origin="SYSTEM",
        sync_status="PENDING_CONFLICT",
        created_by=auditor_id,
        updated_by=auditor_id,
    )
    session.add(s_conf)
    await session.flush()

    hk_items = (
        await session.scalars(
            select(ChecklistItem)
            .join(ChecklistSection)
            .where(ChecklistSection.template_id == hk_tpl.id)
        )
    ).all()
    if hk_items:
        it_conf = hk_items[0]
        session.add(
            SyncConflictLog(
                session_id=s_conf.id,
                item_id=it_conf.id,
                room_ref="ROOM-302",
                winning_value="YES",
                losing_value="NO",
                resolution="TIMESTAMP_MERGE",
                resolved_at=now,
            )
        )

    await session.commit()
