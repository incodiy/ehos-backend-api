import asyncio
from app.db.session import SessionLocal
from app.models.checklist import ChecklistTemplate, ChecklistSection, ChecklistItem
from app.models.audit import AuditSession
from sqlalchemy import select, delete, update

async def cleanup():
    async with SessionLocal() as session:
        # Check all templates
        res = await session.execute(select(ChecklistTemplate).order_by(ChecklistTemplate.id))
        all_templates = res.scalars().all()
        print(f"Total templates before cleanup: {len(all_templates)}")
        
        # Canonical master template names
        canonical_names = {
            "SampleAudit - Security",
            "SampleAudit - Kitchen FB",
            "SampleAudit - Housekeeping",
            "SampleRoomCheck - Housekeeping"
        }
        
        for t in all_templates:
            print(f"ID: {t.id} | Dept: {t.department} | Name: '{t.name}' | Ver: {t.version} | Status: {t.status}")
            
asyncio.run(cleanup())
