# Skema Database

Katalog lengkap **37 tabel** EHOS Backend API. Sumber: model SQLAlchemy 2.0 (`app/models/`) + single Alembic migration `b189513cd0ec` (initial schema, 2026-09-08) + validasi enum di service layer.

**Karakteristik global:**

- Semua PK = `UUID` v4 via `gen_random_uuid()` (pgcrypto).
- Extension DB: `postgis` + `pgcrypto` (dibuat di migration awal).
- Nama kolom snake_case; konvensi indeks `ix_`/`uq_` dari `NAMED_CONVENTION`.
- Soft-delete = kolom `deleted_at` (NULL = aktif) + partial unique `WHERE deleted_at IS NULL` untuk kolom unik.

## Mixin

| Mixin | File | Kolom |
|---|---|---|
| `TimestampMixin` | `models/mixins.py` | `created_at` (NOT NULL, `now()`), `updated_at` (nullable, `now()`, onupdate `now()`) |
| `SoftDeleteMixin` | `models/mixins.py` | `deleted_at` (nullable) |
| `AppendOnlyMixin` | `models/mixins.py` | `created_at` saja (row immutable, tanpa `updated_at`) |

> Semua kolom `DateTime` adalah timezone-aware (`DateTime(tz)`).

---

## Domain 1 — Users & RBAC (9 tabel)

### `users` — `User` (`models/users.py`)

| Kolom | Tipe | Null | Default | Catatan |
|---|---|---|---|---|
| id | UUID | NO | `gen_random_uuid()` | PK |
| email | String(255) | NO | — | partial unique `(deleted_at IS NULL)` |
| name | String(255) | NO | — | |
| password_hash | String(255) | NO | — | argon2 |
| phone | String(20) | YES | — | |
| is_active | Boolean | NO | True | |
| must_change_password | Boolean | NO | True | |
| preferred_locale | String(10) | NO | `"id"` | |
| last_login_at | DateTime(tz) | YES | — | |
| created_by / updated_by / deleted_by | UUID | YES | — | FK → `users.id` |
| created_at / updated_at / deleted_at | — | — | — | Timestamp + SoftDelete |

**Unique/Index:** `ix_users_email_active` UNIQUE partial `email WHERE deleted_at IS NULL`.

### `roles` — `Role` (`models/users.py`)

| Kolom | Tipe | Null | Catatan |
|---|---|---|---|
| id | UUID | NO | PK |
| code | String(50) | NO | UNIQUE (`uq_roles_code`) |
| name | String(255) | NO | |
| scope_level | SmallInteger | NO | 0..5 |
| is_system | Boolean | NO | `False` |
| description | Text | YES | |
| Timestamp + SoftDelete | | | |

### `permissions` — `Permission`

| Kolom | Tipe | Null | Catatan |
|---|---|---|---|
| id | UUID | NO | PK |
| code | String(100) | NO | UNIQUE (`uq_permissions_code`) |
| module | String(50) | NO | `audit`, `capa`, `crm`, `checklist`, `hotel`, `user`, `rbac`, `translation`, `rfp`, `legacy` |
| action | String(100) | NO | `view`, `create`, `update`, `delete`, `approve`, `manage` |
| description | Text | YES | |
| Timestamp + SoftDelete | | | |

### Tabel relasi RBAC

| Tabel | Model | Kolom kunci | Unique |
|---|---|---|---|
| `roles_permissions` | `RolesPermission` | `role_id` (FK roles, CASCADE), `permission_id` (FK permissions, CASCADE) | `(role_id, permission_id)` |
| `user_roles` | `UserRole` | `user_id` (FK users, CASCADE), `role_id` (FK roles, CASCADE) | `(user_id, role_id)` |
| `user_hotel_assignments` | `UserHotelAssignment` | `user_id`, `hotel_id`, `role_id` (semua FK + CASCADE), `is_primary` (Boolean, NO, False), `deleted_by` | `(user_id, hotel_id, role_id)`; index `user/hotel/role` |
| `user_region_assignments` | `UserRegionAssignment` | `user_id`, `region_id` (FK CASCADE) | `(user_id, region_id)` |

### `user_sessions` — `UserSession`

`id`, `user_id` (FK users, CASCADE), `refresh_token_hash` (String 255, NO), `ip` (String 45), `user_agent`, `expires_at` (NO), `revoked_at`, `created_at/updated_at`. Index `ix_user_sessions_user`. **Tanpa soft-delete** — sesi di-revoke.

### `login_audits` — `LoginAudit` (AppendOnly)

`id`, `user_id` (FK users, nullable — login email tak dikenal), `email_attempted`, `success` (Boolean NO), `reason` (String 100: `WRONG_PASSWORD`, `ACCOUNT_LOCKED`, `INACTIVE`), `ip`, `user_agent`, `at` (NO, `now()`), `created_at`. Index `ix_login_audits_email_at`.

---

## Domain 2 — Master Data (5 tabel)

| Tabel | Model | Kolom penting | Unik / Index |
|---|---|---|---|
| `brands` | `Brand` | `code` String(20) NO, `name`, `tier` String(30) NO (`BrandTier`: `Luxury`, `Upscale`, `Boutique`, `Midscale`, `Budget`, `Eco-Resort`) | `code` UNIQUE |
| `provinces` | `Province` | `code` String(10) NO, `name` | `code` UNIQUE |
| `regions` | `Region` | `code` String(20) NO, `name`, `country`, `sales_region` | `code` UNIQUE |
| `hotels` | `Hotel` | lihat bawah | lihat bawah |
| `hotel_departments` | `HotelDepartment` | `hotel_id` (FK, CASCADE), `code` String(30) NO, `name`, `hod_user_id` (FK users) | `(hotel_id, code)` |

### `hotels` — `Hotel` (kunci)

| Kolom | Tipe | Null | Catatan |
|---|---|---|---|
| id | UUID | NO | PK |
| code | String(10) | NO | UNIQUE (`uq_hotels_code`) |
| name | String(255) | NO | |
| brand_id / region_id / province_id | UUID | NO | FK → brands/regions/provinces |
| city | String(100) | YES | |
| **geo** | **Geography(POINT, 4326)** | NO | **PostGIS**, GiST index `idx_hotels_geo` |
| geofence_radius_meters | Integer | NO | 200 |
| mice_facilities | JSONB | YES | GIN index `ix_hotels_mice_facilities` (mis. `ballroom_capacity`) |
| gm_id / rom_id | UUID | YES | FK → users |
| opening_date / terminate_date | Date | YES | |
| status | String(20) | NO | `"ACTIVE"`; legal: `ACTIVE`, `TERMINATED` |

Index lain: `ix_hotels_region`, `ix_hotels_brand`, `ix_hotels_status`.

---

## Domain 3 — Checklist (3 tabel)

| Tabel | Model | Kolom penting | Unik |
|---|---|---|---|
| `checklist_templates` | `ChecklistTemplate` | `department` String(30) NO (`DepartEnum`: `GM`, `HOUSEKEEPING`, `KITCHEN_FB`, `SECURITY_RISK`), `name`, `version` String(20) NO, `brand_tier` (`BrandTier`, nullable=universal), `status` (`DRAFT`/`LOCKED`/`ARCHIVED`, default DRAFT), `locked_at`, `published_by` (FK users, NO) | `(department, name, version)` |
| `checklist_sections` | `ChecklistSection` | `template_id` (FK, CASCADE), `parent_id` (self FK — nesting), `name`, `code`, `sort_order` | `(template_id, code)` |
| `checklist_items` | `ChecklistItem` | `section_id` (FK, CASCADE), `code`, `question_text` Text NO, `rubric_type` (`RubricType`: `TRAFFIC_LIGHT`, `BINARY_COUNT`, `NUMERIC_SCALE`, `MULTI_ROOM`), `max_score` Numeric(5,2) NO, `weight` Numeric(5,2) NO, `na_allowed` Boolean NO False, `is_life_safety` Boolean NO False, `sort_order` | `(section_id, code)` |

---

## Domain 4 — Audit / Scoring (5 tabel)

### `audit_sessions` — `AuditSession`

| Kolom | Tipe | Null | Catatan |
|---|---|---|---|
| id | UUID | NO | PK |
| hotel_id / template_id | UUID | NO | FK → hotels / checklist_templates |
| department | String(30) | NO | `DepartEnum` |
| audit_type | String(20) | NO | `FULL` (juga `SPOT_CHECK`, `FOLLOW_UP`) |
| status | String(20) | NO | `DRAFT`; legal: `DRAFT`, `IN_PROGRESS`, `SUBMITTED`, `PUBLISHED` |
| auditor_id | UUID | NO | FK → users |
| date_start / date_end | Date | YES | |
| published_at | DateTime(tz) | YES | |
| pass_fail | String(10) | YES | `PASS`/`FAIL` |
| total_score | Numeric(5,2) | YES | |
| department_breakdown / subcategory_scores | JSONB | YES | |
| origin | String(10) | NO | `SYSTEM`/`LEGACY` |
| legacy_source_year | Integer | YES | |
| client_id | UUID | YES | loose ref (offline F-05), tanpa FK |
| sync_status | String(20) | NO | `SYNCED`/`PENDING`/`CONFLICT` |
| created_by / updated_by | UUID | YES | FK → users |
| Timestamp (tanpa soft-delete) | | | |

**Unique:** `uq_audit_hotel_dept_period_type` `(hotel_id, department, date_start, audit_type)` **NULLS NOT DISTINCT**.
**Index:** `ix_audit_hotel_status`, `ix_audit_department_date`, `ix_audit_client_id`.

### `audit_item_scores` — `AuditItemScore` (AppendOnly + Timestamp campuran)

`id`, `session_id` (FK, CASCADE), `item_id` (FK checklist_items), `room_ref` String(50) (MULTI_ROOM), `value` String(50) (nilai mentah), `score` Numeric(5,2), `is_na` Boolean NO False, `note` Text, `scored_by` (FK users, NO), `scored_at` (NO), `updated_at` (NO — eksplisit, untuk merge F-05), `created_at` (AppendOnly).

**Unique:** `(session_id, item_id, room_ref)` NULLS NOT DISTINCT. Index `session`, `item`.

**Legal `value` (scoring.py):**
- TRAFFIC_LIGHT: `YES`/`PASS`→1.0; `REVIEW`/`REVISIT`/`PARTIAL`/`NEED REVIEW`/`NEEDREVIEW`→0.5; `NO`/`FAIL`→0.0.
- BINARY_COUNT: `YES`/`PASS`/`1`/`TRUE`→1.0; `NO`/`FAIL`/`0`/`FALSE`→0.0.

### `findings` — `Finding` (AppendOnly)

`id`, `session_id` (FK, CASCADE), `item_id` (FK checklist_items, nullable), `hotel_id` (FK, NO), `is_life_safety` Boolean NO False, `severity` String(20) NO (`CRITICAL`/`MAJOR`/`MINOR`), `title` Text NO, `description` Text, `location` String(255), `created_at`. Index `ix_findings_hotel_safety`.

### `sync_conflict_logs` — `SyncConflictLog` (AppendOnly)

`id`, `session_id`, `item_id`, `room_ref`, `winning_value`, `losing_value`, `resolution` String(20) NO (`SERVER_WINS`/`CLIENT_WINS`/`MANUAL_MERGE`), `resolved_at` NO, `resolved_by` (FK users), `created_at`.

### `audit_media` — `AuditMedia` (AppendOnly)

`id`, `finding_id` (FK), `session_id` (FK NO), `item_id` (FK), `phase` String(10) NO `BEFORE` (`MediaPhase`: `BEFORE`/`AFTER`), `source_camera` String(20) NO `LIVE_CAMERA` (gallery disabled — F-04), `object_key` String(500) NO (MinIO), `file_name`, `mime`, `width`, `height`, `size_bytes`, `checksum_sha256` CHAR(64) NO, `gps_lat`/`gps_lng` Numeric(9,6), `gps_valid` Boolean NO False, `captured_at`/`server_captured_at` NO, `watermark_meta` JSONB, `upload_status` String(20) NO `PENDING` (`PENDING`/`VERIFIED`/`FAILED`), `created_at`. Index `session`, `finding`, `uploadstatus`.

---

## Domain 5 — CAPA (3 tabel)

### `capa_tickets` — `CapaTicket`

| Kolom | Tipe | Null | Catatan |
|---|---|---|---|
| id | UUID | NO | PK |
| finding_id | UUID | YES | FK findings **RESTRICT** |
| hotel_id | UUID | NO | FK hotels |
| department_id | UUID | YES | FK hotel_departments |
| priority | SmallInteger | NO | 3 (1/2/3) |
| sla_hours | Integer | NO | 168 |
| due_at | DateTime(tz) | NO | |
| status | String(30) | NO | `OPEN`; legal: `OPEN`, `AWAITING_GM`, `AWAITING_QA`, `CLOSED` |
| title / description | Text | NO/YES | |
| assigned_to | UUID | YES | FK users |
| escalation_level | Integer | NO | 0, maks 3 |
| created_by / reporter_id / closed_by | UUID | YES | FK users |
| receipt_id | String(12) | YES | UNIQUE (`uq_capa_tickets_receipt_id`) |
| origin | String(20) | NO | `AUDIT`/`WHISTLEBLOWER` |
| submitted_at / closed_at | DateTime(tz) | YES | |

**Index:** `ix_capa_status_due`, `ix_capa_hotel_priority`, `ix_capa_assigned`.

**SLA (capa_trigger / sla_escalation):** priority 1 (CRITICAL) = 24 jam; priority 2 (MAJOR) = 48 jam; priority 3 (MINOR) = 168 jam.

### `capa_status_histories` — `CapaStatusHistory` (AppendOnly)

`id`, `ticket_id` (FK, CASCADE), `from_status` (NULL = state awal), `to_status`, `actor_id` (FK users NO), `note`, `at` (NO), `created_at`. Index `ticket`.

### `capa_media` — `CapaMedia` (AppendOnly)

Sama persis struktur `audit_media` (tanpa `finding_id`): `ticket_id` (FK), `phase` (default `AFTER`), `source_camera`, `object_key`, `file_name`, `mime`, `width/height`, `size_bytes`, `checksum_sha256`, `gps_lat/lng`, `gps_valid`, `captured_at`/`server_captured_at`, `watermark_meta`, `upload_status`.

---

## Domain 6 — CRM / Quotation / Billing / Referral / RFP (7 tabel)

### `leads` — `Lead`

| Kolom | Tipe | Null | Catatan |
|---|---|---|---|
| id | UUID | NO | PK |
| lead_no | String(30) | NO | UNIQUE (`uq_leads_lead_no`) |
| hotel_id | UUID | NO | FK hotels |
| source | String(30) | NO | `LEAD_SOURCE`: `RFP_PORTAL`, `CROSS_SELLING`, `REFERRAL`, `MANUAL` |
| institution_type | String(20) | NO | `GOV`/`PRIVATE` |
| company_name | String(255) | NO | |
| pic_name / pic_phone / pic_email | — | YES | |
| province_id | UUID | YES | FK provinces |
| status | String(30) | NO | `LEAD`/`CONTACTED`/`PROSPECT`/`CONFIRMED`/`LOST` (LOST terminal) |
| lost_reason | Text | YES | wajib saat LOST (F-07) |
| next_followup_at | DateTime(tz) | YES | |
| amount_est | Numeric(15,2) | YES | |
| owner_id | UUID | NO | FK users |
| referred_from_hotel_id | UUID | YES | FK hotels |
| created_by / updated_by | UUID | YES | |

**Index:** `ix_leads_hotel_status`, `ix_leads_owner_followup`.

### `lead_activities` — `LeadActivity` (AppendOnly)

`id`, `lead_id` (FK, CASCADE), `type` String(20) NO (`ACTIVITY_TYPE`: `CALL`, `EMAIL`, `MEETING`, `NOTE`), `note` Text, `at` NO, `actor_id` FK users NO. Index `lead`.

### `lead_referrals` — `LeadReferral`

`id`, `lead_id`, `from_hotel_id`/`to_hotel_id` (FK hotels), `commission_amount` Numeric(15,2), `status` (`REFERRED`), `note`, `created_at/updated_at`. Index `to_hotel`.

### `quotations` — `Quotation`

| Kolom | Tipe | Null | Catatan |
|---|---|---|---|
| id | UUID | NO | PK |
| quotation_no | String(30) | NO | UNIQUE (`uq_quotations_quotation_no`) |
| lead_id / hotel_id | UUID | NO | FK |
| event_date | Date | YES | |
| event_name | String(255) | YES | |
| package_type | String(20) | NO | `FULLDAY`/`HALFDAY`/`FULLBOARD` |
| pax_count | Integer | YES | |
| sbm_rate_id | UUID | YES | FK government_sbm_rates |
| **sbm_rate_value** | Numeric(15,2) | YES | **Snapshot E3** (stabil historis) |
| **sbm_fiscal_year** | Integer | YES | **Snapshot E3** |
| gross_amount | Numeric(15,2) | NO | |
| discount_amount | Numeric(15,2) | NO | 0 |
| final_amount | Numeric(15,2) | NO | gross − discount |
| discount_approval_status | String(20) | NO | `PENDING`/`APPROVED` |
| status | String(20) | NO | `DRAFT`/`SENT`/`ACCEPTED`/`DECLINED` (immutable — no delete) |
| pdf_key | String(500) | YES | MinIO |
| created_by | UUID | YES | |

**Index:** `ix_quotation_lead`, `ix_quotation_hotel_event`.

### `government_sbm_rates` — `GovernmentSbmRate`

`id`, `province_id` (FK NO), `package_type` String(20) NO, `max_rate_per_pax` Numeric(15,2) NO, `fiscal_year` Integer NO, `is_active` Boolean NO True, `updated_by` FK users. **Unique:** `(province_id, package_type, fiscal_year)`. Index `province_year`.

### `billing_milestones` — `BillingMilestone`

| Kolom | Tipe | Null | Catatan |
|---|---|---|---|
| id | UUID | NO | PK |
| quotation_id | UUID | NO | FK quotations |
| milestone_type | String(20) | NO | `SPK`/`NPWP`/`BAST`/`LPJ` |
| doc_no | String(100) | YES | |
| doc_key | String(500) | YES | MinIO |
| status | String(20) | NO | `EXPECTED`/`UPLOADED`/`PAID`/`OVERDUE` (PAID terminal) |
| amount | Numeric(15,2) | YES | |
| due_date | Date | NO | |
| paid_at | DateTime(tz) | YES | |
| updated_by | UUID | YES | |

**Index:** `ix_billing_quotation`, `ix_billing_due_status`.

### `rfp_requests` — `RfpRequest`

`id`, `ref_no` String(30) NO UNIQUE (`uq_rfp_requests_ref_no`), `company_name`, `pic_name`, `pic_phone`, `pic_email` NO, `details` JSONB NO, `target_hotel_id`/`assigned_hotel_id` (FK hotels), `status` (`NEW`/`ASSIGNED`/`ACCEPTED`/`DECLINED`), `created_at/updated_at`.

---

## Domain 7 — Cross-Cutting (3 tabel)

### `notifications` — `Notification`

| Kolom | Tipe | Null | Catatan |
|---|---|---|---|
| id | UUID | NO | PK |
| user_id | UUID | YES | FK users |
| type | String(40) | NO | `KNOWN_TYPES`: `CAPA_SLA`, `CAPA_ESCALATE`, `FOLLOWUP_CRM`, `CRM_REFERRAL`, `RFP_INTAKE`, `BILLING_REMINDER`, `REPORT` |
| channel | String(10) | NO | `PUSH`/`EMAIL`/`WA` |
| entity_type | String(50) | YES | `capa_ticket`, `lead`, `billing_milestone`, `rfp`, `notification_template` |
| entity_id | UUID | YES | polymorphic (tanpa FK) |
| payload | JSONB | YES | berisi `template_key`, `locale`, `title`, `body`, `reminder_key` (dedup) |
| status | String(20) | NO | `QUEUED`/`SENT`/`FAILED` |
| sent_at / read_at | DateTime(tz) | YES | |

**Index:** `ix_notifications_user_status`.

### `translations` — `Translation`

`id`, `entity_type`, `entity_id` (UUID, polymorphic), `field`, `locale` (`id`/`en`), `value` Text NO, `created_by`/`updated_by` FK users, `created_at/updated_at`. **Unique:** `(entity_type, entity_id, field, locale)`. Index `ix_translations_entity`.

### `audit_logs` — `AuditLog` (AppendOnly)

`id`, `actor_id` FK users, `action` String(20) NO (`CREATE`/`UPDATE`/`DELETE`/`LOGIN`/`LOGOUT`/`APPROVE`/`REJECT`/`PUBLISH`/`ESCALATE`…), `entity_type`, `entity_id` (polymorphic), `before`/`after` JSONB, `ip`, `user_agent`, `at`, `created_at`. Index: `entity+at`, `actor+at`, `at`.

---

## Domain 8 — Legacy (2 tabel)

### `legacy_ingestion_batches` — `LegacyIngestionBatch`

`id`, `source` String(20) NO, `file_key` String(500) NO (MinIO CSV), `hotel_code`, `year`, `row_count` NO 0, `status` (`UPLOADED`/`PROCESSING`/`COMPLETED`/`FAILED`), `fail_count` NO 0, `fail_log` JSONB, `imported_by` FK users, `imported_at`, `created_at/updated_at`.

### `legacy_score_rows` — `LegacyScoreRow` (AppendOnly)

`id`, `batch_id` FK NO, `hotel_id` FK NO, `years` Integer, `period_date` Date, `main_category`/`category`/`sub_category`/`sub_category_child` String(100), `score` Numeric(5,2), `totals` JSONB, `raw_csv` JSONB, `created_at`. Index: `ix_legacy_batch`, `ix_legacy_hotel_period`.

---

## Rekap

| Metrik | Jumlah |
|---|---|
| Total tabel | **37** |
| Model SQLAlchemy | 37/37 (tidak ada tabel Alembic-only) |
| SoftDeleteMixin | 14 tabel |
| TimestampMixin | 28 tabel |
| AppendOnlyMixin | 10 tabel (login_audits, audit_item_scores, findings, sync_conflict_logs, audit_media, capa_status_histories, capa_media, lead_activities, audit_logs, legacy_score_rows) |
| PostGIS | 1 kolom (`hotels.geo`) + GiST |
| JSONB | 13 tabel |
| Index GiST / GIN | 1 / 1 |
| Index parsial | 1 (`ix_users_email_active`) |