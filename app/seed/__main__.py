"""CLI entry point for seeding."""

import asyncio
import sys

from app.db.session import SessionLocal
from app.seed.minimal import seed_minimal


async def main():
    """Run minimal seeder."""
    try:
        async with SessionLocal() as session:
            await seed_minimal(session)
            print("✅ Seeding successful!")
            return 0
    except Exception as e:
        print(f"❌ Seeding failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
