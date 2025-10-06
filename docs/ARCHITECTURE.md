# Arsitektur Aplikasi

Ringkasan arsitektur `ehos-backend-api`: struktur repo, lapisan aplikasi, konfigurasi, keamanan inti, runtime, dan catatan RLS.

## 1. Stack

| Aspek | Pilihan |
|---|---|
| Language | Python 3.12 |
| Framework | FastAPI (async-native, OpenAPI auto) |
| ORM | SQLAlchemy 2.0 (async) → Pydantic v2 |
| DB | PostgreSQL 16 + PostGIS 3.4 |
| Pooling | PgBouncer |
| Cache/Queue | Redis 7 + RQ (migration path Celery/SQS bila >100k job/hari) |
| Object storage | MinIO/S3 (presigned PUT/GET) |
| Auth | python-jose (JWT) + argon2-cffi |
| Migrasi | Alembic |
| Test | pytest + httpx AsyncClient |

## 2. Struktur Direktori

```
ehos-backend-api/
├── app/
│   ├── main.py                # Bootstrap FastAPI (lifespan, CORS, /health)
│   ├── api/                   # Lapisan kontrak HTTP
│   │   ├── router.py          # Registrasi 13 router (urutan tetap)
│   │   ├── deps.py            # get_db, CurrentUser, require_permission
│   │   ├── security_helpers.py# perms(), user_scoped_to_hotel(), allowed_hotel_ids()
│   │   └── endpoints/         # 13 file, 80 endpoint (lihat API-REFERENCE.md)
│   ├── core/
│   │   ├── config.py          # Settings pydantic (prefix EHOS_)
│   │   ├── security.py        # argon2 hash + JWT access/refresh
│   │   └── logging.py         # configure_logging()
│   ├── db/
│   │   ├── base.py            # Registri model → Base.metadata (Alembic)
│   │   └── session.py         # Async engine, sessionmaker, NAMING_CONVENTION
│   ├── models/                # 37 model SQLAlchemy (9 file + mixins)
│   ├── services/              # 19 modul business logic + sweeps (lihat SERVICES.md)
│   └── seed/                  # 17 modul seeder + runner (lihat SEEDERS.md)
├── alembic/                   # Single migration b189513cd0ec (initial 37 tabel)
├── tests/                     # 229 pytest
└── docs/                      # Dokumentasi ini
```

## 3. Lapisan & Alur Data

```
HTTP client ──► app/api/endpoints ──► app/services ──► app/models (SQLAlchemy)
                      │  ▲            (State machine,     │
                      │  │             pipelines, sweeps)  ▼
                      │  └──── auth guard ──────────► PostgreSQL (satu-satunya gateway)
                      ▼
               presigned URL ──► MinIO/S3 (media, PDF)
```

- **Endpoints** hanya bertanggung jawab atas kontrak HTTP: validasi request, guard RBAC/tenant, memanggil service, memetakan exception → HTTP.
- **Services** memuat seluruh business logic: state machine (kanban lead, CAPA FSM, billing FSM), perhitungan scoring/agregasi/verdict, pengiriman notifikasi, pipeline quotation/SBM, RFP.
- **Models** = skema DB murni (tidak ada business logic).
- Akses DB **hanya** dari service layer; klien mana pun **tidak pernah** memegang koneksi PostgreSQL (konstrain G3).

## 4. Konfigurasi (app/core/config.py)

`Settings` berbasis pydantic-settings: `SettingsConfigDict(env_prefix="EHOS_", env_file=".env", extra="ignore")` — semua env variabel langsung dipakai instance `settings` (singleton via `get_settings()`/lru_cache).

| Variabel | Default | Keterangan |
|---|---|---|
| `EHOS_PROJECT_NAME` | `"EHOS API"` | Nama aplikasi (dipakai `/health`) |
| `EHOS_ENVIRONMENT` | `"development"` | Nama environment |
| `EHOS_DEBUG` | `True` | Mengaktifkan `echo` SQL + logging DEBUG |
| `EHOS_DATABASE_URL` | — | `postgresql+asyncpg://…` (dev: port 5434) |
| `EHOS_REDIS_URL` | `"redis://localhost:6379/0"` | Redis untuk RQ/queue |
| `EHOS_SEED_DATA_DIR` | — | Folder `crm/Master Data Hotel.xlsx` |
| `EHOS_AUDIT_DATA_DIR` | — | Folder workbook audit tahunan |
| `EHOS_JWT_SECRET_KEY` | — | Rahasia penandatanganan JWT |
| `EHOS_JWT_ACCESS_EXPIRE_MINUTES` | `15` | TTL access token |
| `EHOS_JWT_REFRESH_EXPIRE_DAYS` | `30` | TTL refresh token |
| `EHOS_JWT_ALGORITHM` | `"HS256"` | Algoritma token |
| `EHOS_CORS_ORIGINS` | — | CSV origins (`cors_origins_raw`) → property `cors_origins` mem-parse & strip |
| `EHOS_MINIO_ENDPOINT` | — | Host MinIO (`localhost:9000`) |
| `EHOS_MINIO_ACCESS_KEY` / `EHOS_MINIO_SECRET_KEY` | — | Kredensial MinIO (SigV4) |
| `EHOS_MINIO_BUCKET` | — | Bucket media |
| `EHOS_MINIO_SECURE` | `False` | HTTPS bila true |
| `EHOS_SMTP_HOST` / `_PORT` / `_USERNAME` / `_PASSWORD` / `_FROM` | — | SMTP untuk email (kosong → provider belum dikonfigurasi) |
| `EHOS_WA_API_URL` / `EHOS_WA_TOKEN` | — | HTTP API WhatsApp Business |

## 5. Keamanan Inti (app/core/security.py)

- **Password:** argon2 `PasswordHasher`; `hash_password()` / `verify_password()` (mismatch → `False`, tanpa exception).
- **Token (python-jose):** payload `{sub, type, jti: uuid4(), iat, exp}` — `jti` unik memungkinkan deteksi rotasi/reuse refresh token dalam detik yang sama.
  - Access: `type="access"`, TTL 15 menit.
  - Refresh: `type="refresh"`, TTL 30 hari; disimpan hash-nya di `user_sessions` untuk revoke.
- Helper: `decode_token()`, `is_refresh_token()`, `is_jwt_error()`.

## 6. Database Session (app/db/session.py)

- `NAMING_CONVENTION` konsisten (prefix `ix_`, `uq_`, `ck_`, `fk_`, `pk_`).
- `create_async_engine(..., pool_pre_ping=True, pool_size=10, max_overflow=20, echo=settings.debug)`.
- `SessionLocal = async_sessionmaker(..., expire_on_commit=False)`.
- `Base(DeclarativeBase)` + `MetaData(naming_convention=...)`.

## 7. Bootstrap & Runtime (app/main.py)

- **Lifespan:** startup → `configure_logging()`; shutdown → `await engine.dispose()`. Tanpa eager DB init, tanpa auto-seed, tanpa RQ worker di lifespan.
- `FastAPI(title, version="0.1.0", openapi_url="/openapi.json")`.
- **CORS:** `allow_origins=settings.cors_origins`, credentials=True, methods/headers `["*"]`.
- **Routes:** `app.include_router(api_router)` (no prefix) + `GET /health` → `{status:"ok", service}`.

## 8. Catatan RLS / Isolasi Tenant

- Tidak ada DDL RLS yang dibaca pada jalur kode Python; isolasi keamanan data dilakukan di **lapisan service/endpoint** (pola "golden rule": kombinasi `perms()` + `allowed_hotel_ids()` / `user_scoped_to_hotel()`).
- Detail pola isolasi: [RBAC-AUTH.md](./RBAC-AUTH.md#pola-isolasi-tenant).

## 9. Layout Endpoint Router

Urutan registrasi di `app/api/router.py` (tanpa prefix global):

| # | Router | Prefix | Endpoint |
|---|---|---|---|
| 1 | auth | `/auth` | 7 |
| 2 | users | `/roles`, `/users` | 8 |
| 3 | master | `/hotels`, `/brands`, `/regions`, `/provinces` | 7 |
| 4 | checklist | `/checklist` | 7 |
| 5 | audit | `/audit` | 11 |
| 6 | ingest | `/ingest` (stub) | 0 |
| 7 | analytics | `/analytics` | 1 |
| 8 | capa | `/capa` | 12 |
| 9 | crm | `/crm` | 13 |
| 10 | billing | `/crm/billing` | 3 |
| 11 | frontpage | `/frontpage` | 1 |
| 12 | translations | `/translations` | 4 |
| 13 | notifications | `/notifications` | 5 |
| — | main | `/health` | 1 |

Lihat [API-REFERENCE.md](./API-REFERENCE.md) untuk detail setiap endpoint.