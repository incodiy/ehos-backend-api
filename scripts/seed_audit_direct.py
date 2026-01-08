import asyncio
from sqlalchemy import select
from app.db.session import SessionLocal
from app.seed.audit_logs import seed_audit_logs
from app.models import User

async def main():
    async with SessionLocal() as session:
        users = (await session.scalars(select(User))).all()
        resolved = {u.email: u.id for u in users}
        count = await seed_audit_logs(session, resolved)
        await session.commit()
        print(f"SUCCESS: Seeded {count} audit logs.")

if __name__ == "__main__":
    asyncio.run(main())
