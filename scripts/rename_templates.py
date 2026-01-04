import asyncio
from app.db.session import SessionLocal
from app.models.checklist import ChecklistTemplate
from sqlalchemy import select, text

async def rename_and_align():
    async with SessionLocal() as session:
        # 1. Update names to professional standards
        name_map = {
            "SampleAudit - Security": "Security & Risk Management Standard Checklist",
            "SampleAudit - Kitchen FB": "Kitchen & F&B Hygiene Standard Checklist",
            "SampleAudit - Housekeeping": "Housekeeping Operations Standard Checklist",
            "SampleRoomCheck - Housekeeping": "Guest Room Inspection Checklist",
        }
        
        for old_name, new_name in name_map.items():
            await session.execute(
                text(f"UPDATE checklist_templates SET name = '{new_name}' WHERE name = '{old_name}';")
            )
            
        # 2. For any template with multiple LOCKED versions of the same name/department, archive older ones
        res = await session.execute(
            select(ChecklistTemplate).where(ChecklistTemplate.status == "LOCKED").order_by(ChecklistTemplate.department, ChecklistTemplate.name, ChecklistTemplate.version.desc())
        )
        locked_templates = res.scalars().all()
        seen = set()
        for tpl in locked_templates:
            key = (tpl.department, tpl.name)
            if key in seen:
                print(f"Archiving older locked template: {tpl.name} ({tpl.version}) [ID: {tpl.id}]")
                tpl.status = "ARCHIVED"
            else:
                seen.add(key)
                
        await session.commit()
        print("Database checklist template names and version statuses updated!")

        # Verify
        res = await session.execute(
            select(ChecklistTemplate).order_by(ChecklistTemplate.department, ChecklistTemplate.name, ChecklistTemplate.version)
        )
        all_tpls = res.scalars().all()
        print(f"Total templates in DB: {len(all_tpls)}")
        for t in all_tpls:
            print(f"- [{t.department}] {t.name} ({t.version}) - Status: {t.status} - ID: {t.id} - UUID: {t.uuid}")

asyncio.run(rename_and_align())
