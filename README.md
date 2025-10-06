# EHOS Backend API

FastAPI (Python 3.12) — Auth/RBAC, Audit, CAPA, CRM, Sync, Worker/RQ. Single gateway ke PostgreSQL (RLS + service layer). Kontrak API: `openapi.yaml` di G1-ARCH (single source of truth).

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env            # sesuaikan secrets
```

## Migrasi & Seeder

```bash
alembic upgrade head            # Task 4b+ — DDL (ERD v1.4)
python scripts/seed.py           # Task 4c-4f — brands/106 hotel/tier + ingest + legacy (idempoten)
```

## Run

```bash
uvicorn app.main:app --reload --port 8080
```

## Test

```bash
pytest                          # coverage >=80% area audit-scoring (G3 exit)
```

## Struktur

```
app/
├── main.py            # FastAPI app + CORS + health
├── api/               # router aggregate + endpoints per domain (openapi.yaml)
├── core/              # config (pydantic-settings), security (jose+argon2), logging
├── db/                # async session + Base
├── models/            # SQLAlchemy 2.0 mapped models (Task 4b)
├── schemas/           # Pydantic v2 (generate dari openapi.yaml)
├── services/          # business logic (thin controllers)
└── seed/              # seeder modul H1-H4 (idempoten, chained FK simulation)
alembic/               # versioned migrations
scripts/seed.py        # CLI entry seeder
```

> Hard rule Constraint G: tidak ada client yang memegang koneksi DB — data hanya lewat endpoint kontrak. Semua modul wajib seeder komprehensif (H1-H4) sebelum layar diuji.