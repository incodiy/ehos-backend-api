# EHOS Backend API — Dokumentasi Teknis

Dokumentasi teknis komprehensif untuk **EHOS Backend API** (`ehos-backend-api`), gateway data tunggal platform EHOS (Enterprise Hospitality Operations & Suite). Dokumen ini mencakup arsitektur aplikasi, skema database (37 tabel), 80 endpoint API level-3, RBAC, pipeline fitur end-to-end, service layer, dan seeder.

> **Kontrak tunggal:** `openapi.yaml` (di `.opencode/artifacts/ehos/G1-ARCH/openapi.yaml`) adalah single source of truth kontrak API. Seluruh klien (Admin Panel, Frontpage, Mobile) **men-generate client sendiri** dari spec tersebut; tidak ada shared package (ARD-002/003).

## Status Implementasi

- **Gate:** G3 — Development (Phase 1-8 tuntas: Auth/Master/Checklist, Dynamic Scoring, CAPA Engine, CRM & MICE).
- **Test:** 229 pytest lulus · **Lint:** ruff clean.

## Struktur Dokumen

| Dokumen | Isi |
|---|---|
| [ARCHITECTURE.md](./ARCHITECTURE.md) | Layout repo, layer aplikasi, konfigurasi env (`EHOS_*`), JWT/argon2, engine DB, boot sequence |
| [DATABASE.md](./DATABASE.md) | Katalog 37 tabel per domain, mixin, index, constraint unik, nilai enum legal |
| [API-REFERENCE.md](./API-REFERENCE.md) | Seluruh 80 endpoint: method/path, guard RBAC, parameter, response, kode error |
| [RBAC-AUTH.md](./RBAC-AUTH.md) | Alur auth (JWT access/refresh, session, login audit), 9 role + scope level, 36 permission, constraint A3/A4, pola isolasi tenant |
| [FEATURES.md](./FEATURES.md) | Pipeline fitur: checklist bank, scoring/aggregation/verdict, siklus hidup audit + sync offline + PDF, engine CAPA, CRM (kanban/referral/RFP/quotation/SBM/billing), notifikasi, i18n, legacy, analytics |
| [SERVICES.md](./SERVICES.md) | Referensi 19 modul service: fungsi publik, state machine eksak, exception + mapping HTTP, 5 background sweep |
| [SEEDERS.md](./SEEDERS.md) | 17 modul seeder + runner, urutan eksekusi, kredensial dev, idempotensi, cakupan simulasi H1-H4 |

## Quickstart

```bash
# 1. Infra (Docker Compose 9 service): db/pgbouncer/redis/minio
# 2. Environment
cp .env.example .env          # isi EHOS_DATABASE_URL, JWT, MinIO, SMTP/WA
export EHOS_DATABASE_URL="postgresql+asyncpg://ehos:ehos@localhost:5434/ehos"

# 3. Migrasi + seeder (WAJIB sebelum uji — Aturan 2 H1-H4)
alembic upgrade head
python -m app.seed.runner          # atau command seed sesuai repo

# 4. Jalankan
uvicorn app.main:app --reload     # OpenAPI: http://localhost:8080/docs

# 5. Test
pytest
```

> **Catatan dev:** DB dev berjalan di port **5434** (bukan 5432). Semua pytest/script harus memakai `EHOS_DATABASE_URL=...:5434/ehos`. Reset schema aman untuk dev: `DROP SCHEMA public CASCADE` → `alembic upgrade head` → seed (data hanya berasal dari seeder).

## Konvensi Keras (Ringkasan)

Sumber resmi: `ARCHITECTURAL-CONSTRAINTS.md` (konstrain A-I) & AGENTS.md project.

- **Aturan 1 — Real Data Only (G1-G4):** Tidak ada hardcode data bisnis & mock/fallback di kode. Setiap layar fetch data nyata dari API. Error API → tampilkan state error jujur, bukan data palsu.
- **Aturan 2 — Seeder & Real Simulation (H1-H4):** Tiap modul wajib punya seeder komprehensif yang mensimulasikan alur bisnis ujung-ke-ujung (chained FK), variasi multi-status/SLA/locale, deterministik & idempoten.
- **G3 — API-only:** Tidak ada klien yang memegang koneksi PostgreSQL; satu-satunya gateway DB adalah backend ini.
- **RBAC (A3-A6):** 9 role, delegated admin tidak boleh membuat role corporate/GM (A3), ROOT_ADMIN sovereign (A4), isolasi tenant global/region/hotel (A5).

## Referensi Sumber

| Artefak | Path |
|---|---|
| Kontrak API | `.opencode/artifacts/ehos/G1-ARCH/openapi.yaml` |
| ERD | `.opencode/artifacts/ehos/G1-ARCH/ERD.md` |
| Sistem | `.opencode/artifacts/ehos/G1-ARCH/SYSTEM.md` |
| Konstrain | `.opencode/artifacts/ehos/G1-ARCH/ARCHITECTURAL-CONSTRAINTS.md` |
| Stack | `.opencode/artifacts/ehos/G1-ARCH/STACK.md` |