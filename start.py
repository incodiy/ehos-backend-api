#!/usr/bin/env python
"""
Production startup script — runs migrations, seeds, then starts uvicorn.
Used by Railway to ensure proper initialization order.
"""

import asyncio
import subprocess
import sys

from app.db.session import SessionLocal
from app.seed.minimal import seed_minimal


async def run_minimal_seed():
    """Run minimal seeding."""
    try:
        async with SessionLocal() as session:
            await seed_minimal(session)
            print("✅ Seeding successful!")
    except Exception as e:
        print(f"⚠️ Seeding error (non-blocking): {e}", file=sys.stderr)


def run_migrations():
    """Run Alembic migrations."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            print(f"⚠️ Migration error: {result.stderr}", file=sys.stderr)
            return False
        print("✅ Migrations completed!")
        return True
    except Exception as e:
        print(f"⚠️ Migration error: {e}", file=sys.stderr)
        return False


def main():
    print("🚀 EHOS Backend — Production Startup")
    
    # 1. Run migrations
    print("📦 Running migrations...")
    if not run_migrations():
        print("⚠️ Migration failed, continuing anyway...")
    
    # 2. Run minimal seeding
    print("🌱 Running minimal seeding...")
    asyncio.run(run_minimal_seed())
    
    # 3. Start uvicorn
    print("🚀 Starting uvicorn...")
    subprocess.run([
        sys.executable, "-m", "uvicorn",
        "app.main:app",
        "--host", "0.0.0.0",
        "--port", "8080",
    ])


if __name__ == "__main__":
    main()
