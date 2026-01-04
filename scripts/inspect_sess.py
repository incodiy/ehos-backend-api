import asyncio
from app.db.session import SessionLocal
from app.models.audit import AuditSession
from app.models.checklist import ChecklistTemplate
from sqlalchemy import select

async def inspect_session():
    async with SessionLocal() as session:
        res = await session.execute(
            select(AuditSession).where(AuditSession.uuid == "1d756fe4-5531-45ab-afb0-68f15c0c3eda")
        )
        s = res.scalar_one_or_none()
        print(f"Session {s.id}: Dept={s.department}, TemplateID={s.template_id}, Status={s.status}")
        
        res_t = await session.execute(select(ChecklistTemplate).where(ChecklistTemplate.id == s.template_id))
        t = res_t.scalar_one_or_none()
        print(f"Template {t.id}: Name='{t.name}', Dept={t.department}")

asyncio.run(inspect_session())
