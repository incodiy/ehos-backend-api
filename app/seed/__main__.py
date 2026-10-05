"""CLI entry point for seeding."""

import asyncio
import sys

from app.db.session import SessionLocal
from app.seed.full_demo import seed_full_demo


async def main():
    """Run full demo seeder."""
    try:
        async with SessionLocal() as session:
            await seed_full_demo(session)
            print("✅ Full seeding successful!")
            return 0
    except Exception as e:
        print(f"❌ Seeding failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
