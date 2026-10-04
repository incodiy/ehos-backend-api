from contextlib import asynccontextmanager
import subprocess
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import engine
from app.seed.runner import run_seeders


def run_migrations():
    """Run Alembic migrations on startup."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            print(f"⚠️ Migration warning: {result.stderr}", file=sys.stderr)
        else:
            print("✓ Migrations completed successfully")
    except Exception as e:
        print(f"⚠️ Migration error (non-blocking): {e}", file=sys.stderr)


async def run_seed():
    """Run data seeders on startup (idempotent — safe to run multiple times)."""
    try:
        print("🌱 Seeding database...")
        await run_seeders()
        print("✓ Seeding completed successfully")
    except Exception as e:
        print(f"⚠️ Seed error (non-blocking): {e}", file=sys.stderr)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    print("🚀 Running database migrations...")
    run_migrations()
    print("🌱 Running data seeders...")
    await run_seed()
    yield
    await engine.dispose()


app = FastAPI(
    title=settings.project_name,
    version="0.1.0",
    debug=settings.debug,
    lifespan=lifespan,
    openapi_url="/openapi.json",
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health", tags=["system"])
async def health() -> dict:
    return {"status": "ok", "service": settings.project_name}