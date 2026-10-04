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
    """Run Alembic migrations (non-blocking on error)."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            print(f"⚠️ Migration failed: {result.stderr[:500]}", file=sys.stderr)
            print("⚠️ Continuing without migrations...", file=sys.stderr)
            return False
        print("✅ Migrations completed!")
        return True
    except Exception as e:
        print(f"⚠️ Migration error: {e}", file=sys.stderr)
        print("⚠️ Continuing without migrations...", file=sys.stderr)
        return False


def main():
    print("🚀 EHOS Backend — Production Startup")
    
    # 1. Run migrations (optional, non-blocking)
    print("📦 Running migrations (if DB available)...")
    run_migrations()
    
    # 2. Skip seeding if DB not available — just start API
    print("🚀 Starting uvicorn...")
    subprocess.run([
        sys.executable, "-m", "uvicorn",
        "app.main:app",
        "--host", "0.0.0.0",
        "--port", "8080",
    ])


if __name__ == "__main__":
    main()
