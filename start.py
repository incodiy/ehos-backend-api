#!/usr/bin/env python
"""
Production startup script — runs migrations, seeds, then starts uvicorn.
Used by Railway to ensure proper initialization order.
"""

import asyncio
import subprocess
import sys
import os

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
    """Run Alembic migrations in-process (non-blocking on error)."""
    try:
        # Change to script dir to find alembic.ini
        os.chdir(os.path.dirname(os.path.abspath(__file__)))
        
        from alembic.config import Config
        from alembic import command
        
        cfg = Config("alembic.ini")
        command.upgrade(cfg, "head")
        print("✅ Migrations completed!")
        return True
    except Exception as e:
        print(f"⚠️ Migration error (non-blocking): {e}", file=sys.stderr)
        return False


def main():
    print("🚀 EHOS Backend — Production Startup")
    
    # 1. Run migrations (optional, non-blocking)
    print("📦 Running migrations (if DB available)...")
    run_migrations()
    
    # 2. Run minimal seeding (optional, non-blocking)
    print("🌱 Running minimal seeding...")
    try:
        asyncio.run(run_minimal_seed())
    except Exception as e:
        print(f"⚠️ Seeding skipped: {e}", file=sys.stderr)
    
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
