from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health", tags=["system"])
async def health() -> dict:
    return {"status": "ok", "service": settings.project_name}