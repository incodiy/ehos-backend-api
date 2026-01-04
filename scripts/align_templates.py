import asyncio
from app.db.session import SessionLocal
from sqlalchemy import text

async def align():
    async with SessionLocal() as session:
        # 1. Delete capa_media and capa_tickets referencing any non-canonical findings
        await session.execute(text("""
            DELETE FROM capa_media WHERE ticket_id IN (
                SELECT ct.id FROM capa_tickets ct
                JOIN findings f ON ct.finding_id = f.id
                JOIN checklist_items ci ON f.item_id = ci.id
                JOIN checklist_sections cs ON ci.section_id = cs.id
                WHERE cs.template_id NOT IN (226, 227, 228, 229)
            );
        """))
        
        await session.execute(text("""
            DELETE FROM capa_tickets WHERE finding_id IN (
                SELECT f.id FROM findings f
                JOIN checklist_items ci ON f.item_id = ci.id
                JOIN checklist_sections cs ON ci.section_id = cs.id
                WHERE cs.template_id NOT IN (226, 227, 228, 229)
            );
        """))

        # 2. Delete findings referencing non-canonical items
        await session.execute(text("""
            DELETE FROM findings WHERE item_id IN (
                SELECT ci.id FROM checklist_items ci
                JOIN checklist_sections cs ON ci.section_id = cs.id
                WHERE cs.template_id NOT IN (226, 227, 228, 229)
            );
        """))

        # 3. Delete sync_conflict_logs referencing non-canonical items
        await session.execute(text("""
            DELETE FROM sync_conflict_logs WHERE item_id IN (
                SELECT ci.id FROM checklist_items ci
                JOIN checklist_sections cs ON ci.section_id = cs.id
                WHERE cs.template_id NOT IN (226, 227, 228, 229)
            );
        """))

        # 4. Delete audit_item_scores referencing non-canonical items
        await session.execute(text("""
            DELETE FROM audit_item_scores WHERE item_id IN (
                SELECT ci.id FROM checklist_items ci
                JOIN checklist_sections cs ON ci.section_id = cs.id
                WHERE cs.template_id NOT IN (226, 227, 228, 229)
            );
        """))

        # 5. Delete audit sessions referencing non-canonical templates
        await session.execute(text("""
            DELETE FROM capa_media WHERE ticket_id IN (
                SELECT ct.id FROM capa_tickets ct
                JOIN findings f ON ct.finding_id = f.id
                JOIN audit_sessions s ON f.session_id = s.id
                WHERE s.template_id NOT IN (226, 227, 228, 229)
            );
        """))

        await session.execute(text("""
            DELETE FROM capa_tickets WHERE finding_id IN (
                SELECT f.id FROM findings f
                JOIN audit_sessions s ON f.session_id = s.id
                WHERE s.template_id NOT IN (226, 227, 228, 229)
            );
        """))

        await session.execute(text("""
            DELETE FROM findings WHERE session_id IN (
                SELECT id FROM audit_sessions WHERE template_id NOT IN (226, 227, 228, 229)
            );
        """))

        await session.execute(text("""
            DELETE FROM audit_item_scores WHERE session_id IN (
                SELECT id FROM audit_sessions WHERE template_id NOT IN (226, 227, 228, 229)
            );
        """))

        await session.execute(text("""
            DELETE FROM audit_sessions WHERE template_id NOT IN (226, 227, 228, 229);
        """))

        # 6. Delete non-canonical items, sections, and templates
        await session.execute(text("""
            DELETE FROM checklist_items WHERE section_id IN (
                SELECT id FROM checklist_sections WHERE template_id NOT IN (226, 227, 228, 229)
            );
        """))

        await session.execute(text("""
            DELETE FROM checklist_sections WHERE template_id NOT IN (226, 227, 228, 229);
        """))

        await session.execute(text("""
            DELETE FROM checklist_templates WHERE id NOT IN (226, 227, 228, 229);
        """))

        await session.commit()
        print("Obsolete records and non-canonical templates successfully purged!")

        res = await session.execute(text("SELECT id, department, name, version, status, uuid FROM checklist_templates ORDER BY department, id;"))
        rows = res.fetchall()
        print(f"Remaining active templates in DB: {len(rows)}")
        for r in rows:
            print(f"- [{r.department}] {r.name} ({r.version}) - {r.status} - ID: {r.id} - UUID: {r.uuid}")

asyncio.run(align())
