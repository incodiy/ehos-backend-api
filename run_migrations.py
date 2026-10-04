#!/usr/bin/env python
"""Run migrations via Railway."""
import asyncio
import sys
import os
from alembic.config import Config
from alembic import command

# Set working directory
os.chdir(os.path.dirname(__file__))

# Load environment
from app.core.config import settings
print(f"Database: {settings.database_url}")

# Run migrations
cfg = Config("alembic.ini")
try:
    command.upgrade(cfg, "head")
    print("✅ Migrations completed!")
except Exception as e:
    print(f"❌ Migration failed: {e}", file=sys.stderr)
    sys.exit(1)
