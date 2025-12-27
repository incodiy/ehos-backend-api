import asyncio
from app.db.session import SessionLocal
from app.models.audit import AuditSession
from app.models.checklist import ChecklistTemplate, ChecklistSection, ChecklistItem
from sqlalchemy import select

async def check():
    async with SessionLocal() as session:
        # Check session
        res = await session.execute(select(AuditSession).where(AuditSession.uuid == "1d756fe4-5531-45ab-afb0-68f15c0c3eda"))
        sess = res.scalar_one_or_none()
        if not sess:
            print("Session not found!")
            return
        print(f"Session: ID={sess.id}, UUID={sess.uuid}, Dept={sess.department}, TemplateID={sess.template_id}")
        
        # Check template of this session
        res = await session.execute(select(ChecklistTemplate).where(ChecklistTemplate.id == sess.template_id))
        tpl = res.scalar_one_or_none()
        if tpl:
            print(f"Session Template: ID={tpl.id}, Name='{tpl.name}', Ver={tpl.version}, Dept={tpl.department}, UUID={tpl.uuid}")
        else:
            print(f"Template ID {sess.template_id} NOT FOUND!")

        # Check item
        res = await session.execute(select(ChecklistItem).where(ChecklistItem.uuid == "dfbfd4c0-2530-4af8-be56-2b2a06010392"))
        item = res.scalar_one_or_none()
        if item:
            res_sec = await session.execute(select(ChecklistSection).where(ChecklistSection.id == item.section_id))
            sec = res_sec.scalar_one_or_none()
            print(f"Item found: ID={item.id}, SectionID={item.section_id}, Code={item.code}, SectionTemplateID={sec.template_id if sec else None}")
        else:
            print("Item NOT FOUND in DB!")

asyncio.run(check())
