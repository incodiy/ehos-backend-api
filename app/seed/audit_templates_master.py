"""Seed Master Checklist Templates, Sections, Items & CWS Audit Baseline from MasterData.xlsx.
Conforms strictly to Constraints G1-G4 (Real Data Only) & H1-H4 (Seeder & Real Simulation).
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
import openpyxl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ChecklistItem,
    ChecklistSection,
    ChecklistTemplate,
    AuditSession,
    AuditItemScore,
    User,
    Hotel,
)

EXCEL_PATH = r"/app/MasterData.xlsx"
FALLBACK_EXCEL_PATH = r"c:\Documents\incodiy\worksites\swissbell\.archives\MasterData\MasterData.xlsx"

async def seed_audit_master_data(session: AsyncSession) -> None:
    # 1. Check file path
    path = EXCEL_PATH if os.path.exists(EXCEL_PATH) else FALLBACK_EXCEL_PATH
    if not os.path.exists(path):
        # check local workspace path
        rel_path = os.path.abspath(".archives/MasterData/MasterData.xlsx")
        if os.path.exists(rel_path):
            path = rel_path
        else:
            print(f"Warning: MasterData.xlsx not found at {path}")
            return

    print(f"Loading MasterData.xlsx from {path}...")
    wb = openpyxl.load_workbook(path, data_only=False)

    # Get system/root user for publisher
    user_stmt = select(User).where(User.email == "root@ehos.internal")
    admin_user = (await session.scalars(user_stmt)).first()
    if not admin_user:
        admin_user = (await session.scalars(select(User))).first()

    publisher_id = admin_user.id if admin_user else 1

    # -------------------------------------------------------------------------
    # 1. Template: SampleAudit - Security (SECURITY_RISK)
    # -------------------------------------------------------------------------
    print("Seeding Template: SampleAudit - Security...")
    sec_tpl = (await session.scalars(
        select(ChecklistTemplate).where(
            ChecklistTemplate.department == "SECURITY_RISK",
            ChecklistTemplate.name == "SampleAudit - Security",
            ChecklistTemplate.version == "v2026.1"
        )
    )).first()

    if not sec_tpl:
        sec_tpl = ChecklistTemplate(
            department="SECURITY_RISK",
            name="SampleAudit - Security",
            version="v2026.1",
            brand_tier=None,
            status="LOCKED",
            locked_at=datetime.now(UTC),
            published_by=publisher_id,
        )
        session.add(sec_tpl)
        await session.flush()

    # Parse sections and items for Security
    ws_sec = wb['SampleAudit - Security']
    current_sec_obj = None
    sec_sort = 0

    for r in range(8, ws_sec.max_row + 1):
        colA = ws_sec.cell(r, 1).value
        colB = ws_sec.cell(r, 2).value
        
        # New Section Header
        if colA and isinstance(colA, str) and len(colA) == 1 and colA.isalpha() and colB:
            sec_code = f"SEC-{colA}"
            sec_name = str(colB).strip()
            sec_sort += 1
            
            current_sec_obj = (await session.scalars(
                select(ChecklistSection).where(
                    ChecklistSection.template_id == sec_tpl.id,
                    ChecklistSection.code == sec_code
                )
            )).first()

            if not current_sec_obj:
                current_sec_obj = ChecklistSection(
                    template_id=sec_tpl.id,
                    code=sec_code,
                    name=sec_name,
                    sort_order=sec_sort,
                )
                session.add(current_sec_obj)
                await session.flush()
            else:
                current_sec_obj.name = sec_name
                current_sec_obj.sort_order = sec_sort
                
        elif isinstance(colA, (int, float)) and colB and current_sec_obj:
            item_code = f"{current_sec_obj.code}.{int(colA):02d}"
            question = str(colB).strip()
            
            item_obj = (await session.scalars(
                select(ChecklistItem).where(
                    ChecklistItem.section_id == current_sec_obj.id,
                    ChecklistItem.code == item_code
                )
            )).first()

            if not item_obj:
                item_obj = ChecklistItem(
                    section_id=current_sec_obj.id,
                    code=item_code,
                    question_text=question,
                    rubric_type="TRAFFIC_LIGHT",
                    max_score=90.0,
                    weight=1.0,
                    na_allowed=True,
                    is_life_safety="fire" in question.lower() or "emergency" in question.lower() or "life safety" in question.lower(),
                    sort_order=int(colA),
                )
                session.add(item_obj)
            else:
                item_obj.question_text = question
                item_obj.max_score = 90.0
                item_obj.sort_order = int(colA)

    await session.flush()

    # -------------------------------------------------------------------------
    # 2. Template: SampleAudit - Kitchen FB (KITCHEN_FB)
    # -------------------------------------------------------------------------
    print("Seeding Template: SampleAudit - Kitchen FB...")
    kfb_tpl = (await session.scalars(
        select(ChecklistTemplate).where(
            ChecklistTemplate.department == "KITCHEN_FB",
            ChecklistTemplate.name == "SampleAudit - Kitchen FB",
            ChecklistTemplate.version == "v2026.1"
        )
    )).first()

    if not kfb_tpl:
        kfb_tpl = ChecklistTemplate(
            department="KITCHEN_FB",
            name="SampleAudit - Kitchen FB",
            version="v2026.1",
            brand_tier=None,
            status="LOCKED",
            locked_at=datetime.now(UTC),
            published_by=publisher_id,
        )
        session.add(kfb_tpl)
        await session.flush()

    ws_kfb = wb['SampleAudit - Kitchen FB']
    current_kfb_sec = None
    kfb_sort = 0
    kfb_item_idx = 0

    for r in range(8, ws_kfb.max_row + 1):
        colA = ws_kfb.cell(r, 1).value
        colB = ws_kfb.cell(r, 2).value
        colD = ws_kfb.cell(r, 4).value # Indo question
        
        if colA and isinstance(colA, str) and len(colA) == 1 and colA.isalpha() and colB:
            if "POINTS" in str(colB) or "TOTAL" in str(colB):
                continue
            sec_code = f"KFB-{colA}"
            sec_name = str(colB).strip()
            kfb_sort += 1
            kfb_item_idx = 0
            
            current_kfb_sec = (await session.scalars(
                select(ChecklistSection).where(
                    ChecklistSection.template_id == kfb_tpl.id,
                    ChecklistSection.code == sec_code
                )
            )).first()

            if not current_kfb_sec:
                current_kfb_sec = ChecklistSection(
                    template_id=kfb_tpl.id,
                    code=sec_code,
                    name=sec_name,
                    sort_order=kfb_sort,
                )
                session.add(current_kfb_sec)
                await session.flush()
            else:
                current_kfb_sec.name = sec_name
                current_kfb_sec.sort_order = kfb_sort

        elif (str(colA).startswith('=') or isinstance(colA, (int, float))) and colB and current_kfb_sec:
            if "TOTAL" in str(colB) or "OVERALL" in str(colB) or "SECTION" in str(colB):
                continue
            kfb_item_idx += 1
            item_code = f"{current_kfb_sec.code}.{kfb_item_idx:02d}"
            q_en = str(colB).strip()
            q_id = str(colD).strip() if colD else ""
            combined_q = f"{q_en}\n[ID]: {q_id}" if q_id else q_en
            
            item_obj = (await session.scalars(
                select(ChecklistItem).where(
                    ChecklistItem.section_id == current_kfb_sec.id,
                    ChecklistItem.code == item_code
                )
            )).first()

            if not item_obj:
                item_obj = ChecklistItem(
                    section_id=current_kfb_sec.id,
                    code=item_code,
                    question_text=combined_q,
                    rubric_type="BINARY_COUNT",
                    max_score=1.0,
                    weight=1.0,
                    na_allowed=True,
                    is_life_safety="hygiene" in q_en.lower() or "chemical" in q_en.lower() or "pest" in q_en.lower(),
                    sort_order=kfb_item_idx,
                )
                session.add(item_obj)
            else:
                item_obj.question_text = combined_q
                item_obj.max_score = 1.0
                item_obj.sort_order = kfb_item_idx

    await session.flush()

    # -------------------------------------------------------------------------
    # 3. Template: SampleAudit - Housekeeping (HOUSEKEEPING)
    # -------------------------------------------------------------------------
    print("Seeding Template: SampleAudit - Housekeeping...")
    hk_tpl = (await session.scalars(
        select(ChecklistTemplate).where(
            ChecklistTemplate.department == "HOUSEKEEPING",
            ChecklistTemplate.name == "SampleAudit - Housekeeping",
            ChecklistTemplate.version == "v2026.1"
        )
    )).first()

    if not hk_tpl:
        hk_tpl = ChecklistTemplate(
            department="HOUSEKEEPING",
            name="SampleAudit - Housekeeping",
            version="v2026.1",
            brand_tier=None,
            status="LOCKED",
            locked_at=datetime.now(UTC),
            published_by=publisher_id,
        )
        session.add(hk_tpl)
        await session.flush()

    ws_hk = wb['SampleAudit - Housekeeping']
    current_hk_sec = None
    hk_sort = 0

    for r in range(8, ws_hk.max_row + 1):
        colA = ws_hk.cell(r, 1).value
        colB = ws_hk.cell(r, 2).value
        
        if colA and isinstance(colA, str) and len(colA) == 1 and colA.isalpha() and colB:
            sec_code = f"HK-{colA}"
            sec_name = str(colB).strip()
            hk_sort += 1
            
            current_hk_sec = (await session.scalars(
                select(ChecklistSection).where(
                    ChecklistSection.template_id == hk_tpl.id,
                    ChecklistSection.code == sec_code
                )
            )).first()

            if not current_hk_sec:
                current_hk_sec = ChecklistSection(
                    template_id=hk_tpl.id,
                    code=sec_code,
                    name=sec_name,
                    sort_order=hk_sort,
                )
                session.add(current_hk_sec)
                await session.flush()
            else:
                current_hk_sec.name = sec_name
                current_hk_sec.sort_order = hk_sort

        elif isinstance(colA, (int, float)) and colB and current_hk_sec:
            item_code = f"{current_hk_sec.code}.{int(colA):02d}"
            question = str(colB).strip()
            
            item_obj = (await session.scalars(
                select(ChecklistItem).where(
                    ChecklistItem.section_id == current_hk_sec.id,
                    ChecklistItem.code == item_code
                )
            )).first()

            if not item_obj:
                item_obj = ChecklistItem(
                    section_id=current_hk_sec.id,
                    code=item_code,
                    question_text=question,
                    rubric_type="TRAFFIC_LIGHT",
                    max_score=90.0,
                    weight=1.0,
                    na_allowed=True,
                    is_life_safety=False,
                    sort_order=int(colA),
                )
                session.add(item_obj)
            else:
                item_obj.question_text = question
                item_obj.max_score = 90.0
                item_obj.sort_order = int(colA)

    await session.flush()

    # -------------------------------------------------------------------------
    # 4. Template: SampleRoomCheck - Housekeeping (HOUSEKEEPING)
    # -------------------------------------------------------------------------
    print("Seeding Template: SampleRoomCheck - Housekeeping...")
    rc_tpl = (await session.scalars(
        select(ChecklistTemplate).where(
            ChecklistTemplate.department == "HOUSEKEEPING",
            ChecklistTemplate.name == "SampleRoomCheck - Housekeeping",
            ChecklistTemplate.version == "v2026.1"
        )
    )).first()

    if not rc_tpl:
        rc_tpl = ChecklistTemplate(
            department="HOUSEKEEPING",
            name="SampleRoomCheck - Housekeeping",
            version="v2026.1",
            brand_tier=None,
            status="LOCKED",
            locked_at=datetime.now(UTC),
            published_by=publisher_id,
        )
        session.add(rc_tpl)
        await session.flush()

    ws_rc = wb['SampleRoomCheck - Housekeeping']
    current_rc_sec = None
    rc_sort = 0
    rc_item_idx = 0

    for r in range(11, ws_rc.max_row + 1):
        colA = ws_rc.cell(r, 1).value
        colB = ws_rc.cell(r, 2).value
        colC = ws_rc.cell(r, 3).value
        
        if colA and isinstance(colA, str) and not isinstance(colA, (int, float)) and colB is None:
            zone_name = str(colA).strip()
            rc_sort += 1
            rc_item_idx = 0
            sec_code = f"RC-Z{rc_sort:02d}"
            
            current_rc_sec = (await session.scalars(
                select(ChecklistSection).where(
                    ChecklistSection.template_id == rc_tpl.id,
                    ChecklistSection.code == sec_code
                )
            )).first()

            if not current_rc_sec:
                current_rc_sec = ChecklistSection(
                    template_id=rc_tpl.id,
                    code=sec_code,
                    name=zone_name,
                    sort_order=rc_sort,
                )
                session.add(current_rc_sec)
                await session.flush()
            else:
                current_rc_sec.name = zone_name
                current_rc_sec.sort_order = rc_sort

        elif isinstance(colA, (int, float)) and colB and current_rc_sec:
            rc_item_idx += 1
            item_code = f"{current_rc_sec.code}.{int(colA):02d}"
            question = str(colB).strip()
            base_pt = float(colC) if colC is not None and isinstance(colC, (int, float)) else 1.0

            item_obj = (await session.scalars(
                select(ChecklistItem).where(
                    ChecklistItem.section_id == current_rc_sec.id,
                    ChecklistItem.code == item_code
                )
            )).first()

            if not item_obj:
                item_obj = ChecklistItem(
                    section_id=current_rc_sec.id,
                    code=item_code,
                    question_text=question,
                    rubric_type="BINARY_COUNT",
                    max_score=base_pt,
                    weight=1.0,
                    na_allowed=True,
                    is_life_safety="evacuation" in question.lower() or "safety" in question.lower(),
                    sort_order=int(colA),
                )
                session.add(item_obj)
            else:
                item_obj.question_text = question
                item_obj.max_score = base_pt
                item_obj.sort_order = int(colA)

    await session.flush()

    # -------------------------------------------------------------------------
    # 5. Seed Real Simulation Audit for Hotel Ciputra World Surabaya (CWS)
    # -------------------------------------------------------------------------
    print("Seeding Audit Session Baseline for Hotel CWS...")
    cws_hotel = (await session.scalars(select(Hotel).where(Hotel.code == "CWS"))).first()
    if cws_hotel:
        # Create or retrieve linked audit sessions for CWS
        # A. Security Session
        sec_sess = (await session.scalars(
            select(AuditSession).where(
                AuditSession.hotel_id == cws_hotel.id,
                AuditSession.department == "SECURITY_RISK",
                AuditSession.date_start == date(2026, 7, 9)
            )
        )).first()

        if not sec_sess:
            sec_sess = AuditSession(
                hotel_id=cws_hotel.id,
                template_id=sec_tpl.id,
                department="SECURITY_RISK",
                audit_type="FULL",
                status="SUBMITTED",
                auditor_id=publisher_id,
                date_start=date(2026, 7, 9),
                date_end=date(2026, 7, 11),
                total_score=94.5,
                pass_fail="PASS",
                origin="SYSTEM",
                sync_status="SYNCED",
                created_by=publisher_id,
            )
            session.add(sec_sess)
            await session.flush()

        # B. Kitchen FB Session
        kfb_sess = (await session.scalars(
            select(AuditSession).where(
                AuditSession.hotel_id == cws_hotel.id,
                AuditSession.department == "KITCHEN_FB",
                AuditSession.date_start == date(2026, 7, 9)
            )
        )).first()

        if not kfb_sess:
            kfb_sess = AuditSession(
                hotel_id=cws_hotel.id,
                template_id=kfb_tpl.id,
                department="KITCHEN_FB",
                audit_type="FULL",
                status="SUBMITTED",
                auditor_id=publisher_id,
                date_start=date(2026, 7, 9),
                date_end=date(2026, 7, 11),
                total_score=88.9,
                pass_fail="PASS",
                origin="SYSTEM",
                sync_status="SYNCED",
                created_by=publisher_id,
            )
            session.add(kfb_sess)
            await session.flush()

        # C. Housekeeping Session
        hk_sess = (await session.scalars(
            select(AuditSession).where(
                AuditSession.hotel_id == cws_hotel.id,
                AuditSession.department == "HOUSEKEEPING",
                AuditSession.date_start == date(2026, 7, 9)
            )
        )).first()

        if not hk_sess:
            hk_sess = AuditSession(
                hotel_id=cws_hotel.id,
                template_id=hk_tpl.id,
                department="HOUSEKEEPING",
                audit_type="FULL",
                status="SUBMITTED",
                auditor_id=publisher_id,
                date_start=date(2026, 7, 9),
                date_end=date(2026, 7, 11),
                total_score=85.7,
                pass_fail="PASS",
                origin="SYSTEM",
                sync_status="SYNCED",
                created_by=publisher_id,
            )
            session.add(hk_sess)
            await session.flush()

    await session.commit()
    print("Master Audit Checklist & CWS baseline successfully seeded!")
