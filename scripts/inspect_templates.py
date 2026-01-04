import asyncio
from app.db.session import SessionLocal
from app.models.checklist import ChecklistTemplate
from sqlalchemy import select

async def check():
    async with SessionLocal() as session:
        res = await session.execute(select(ChecklistTemplate).order_by(ChecklistTemplate.id))
        all_templates = res.scalars().all()
        print(f"Total templates: {len(all_templates)}")
        for t in all_templates:
            print(f"ID: {t.id} | Dept: {t.department} | Name: '{t.name}' | Ver: {t.version} | Status: {t.status} | UUID: {t.uuid}")

asyncio.run(check())
