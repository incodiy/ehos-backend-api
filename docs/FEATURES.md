# Fitur & Pipeline

Alur bisnis ujung-ke-ujung setiap fitur. Detail implementasi fungsi ada di [SERVICES.md](./SERVICES.md) dan [API-REFERENCE.md](./API-REFERENCE.md).

---

## 1. Checklist Bank (F-01)

**Tujuan:** Sumber standar penilaian kualitas (bukan hardcode) yang terkunci sebagai snapshot auditable.

- **Template** dikelompokkan per `department` (GM / HOUSEKEEPING / KITCHEN_FB / SECURITY_RISK) + `brand_tier` (atau universal bila `brand_tier IS NULL`).
- **Status lifecycle:** `DRAFT` → (lock) → `LOCKED` → (archived) `ARCHIVED`. Hanya template `LOCKED` yang boleh dipakai sesi audit (B3).
- **Versioning:** `POST /checklist/templates/{id}/versions` deep-clone section+item dari template LOCKED (snapshot immutable).
- **Hierarki:** template → sections (bisa nested via `parent_id`) → items.

**Validator item saat create:** `MULTI_ROOM max_score harus = 90 × jumlah sample ruangan`.

---

## 2. Scoring → Agregasi → Verdict (F-01/F-02/F-03)

Pipeline tiga tahap, murni & deterministik (tanpa DB di jalur evaluasi):

### 2a. Rubrik per item (`services/scoring.py`)
| Rubric | Cara nilai | Nilai ratio |
|---|---|---|
| `TRAFFIC_LIGHT` | `YES/PASS`=1.0, `REVIEW/REVISIT/PARTIAL/NEED REVIEW/NEEDREVIEW`=0.5, `NO/FAIL`=0.0 | score/max |
| `BINARY_COUNT` | `YES/PASS/1/TRUE`=1.0, `NO/FAIL/0/FALSE`=0.0 | score/max |
| `NUMERIC_SCALE` | float ≥ 0, dibatasi `min(measured, max_score)` | score/max |
| `MULTI_ROOM` | per-ruangan TRAFFIC_LIGHT; N/A (bila diizinkan) dikeluarkan dari pembilang & penyebut | sum/count |

- **N/A guard:** `is_na=True` hanya bila `na_allowed=True` (lainnya `InvalidNA`).
- **Tier resolution:** cari template LOCKED `(department, brand_tier)` eksak → fallback `(department, NULL)`.

### 2b. Agregasi bottom-up (`services/aggregation.py`)
- Bobot per item (`weight`); **node ratio = Σ(weight×ratio) / Σ(weight)** untuk semua item turunan (nesting section). Total weight 0 → rata-rata takterboboti.
- `total_score = round(total_ratio × 100, 2)`.
- Item yang tidak dinilai **dilaporkan**, tidak menghukum total.

### 2c. Verdict (`services/verdict.py`)
- Ambang **PASS ≥ 80%** (`PASS_THRESHOLD = 0.80`).
- Blokir (additive): `has_unscored_items`, `life_safety_hazard` (item life-safety nilai 0), `below_threshold`.
- Output: `pass_fail`, `total_score`, `hazards[]` (item life-safety gagal — bahan auto-CAPA).

---

## 3. Sesi Audit & Offline Sync (F-04/F-05)

**Siklus hidup sesi:** `DRAFT → IN_PROGRESS → SUBMITTED → PUBLISHED`.

- **Create (offline-safe):** `client_id` unik → dedup di server (create kedua mengembalikan sesi yang sudah ada).
- **Bulk score:** `POST /audit/sessions/{id}/items` — merge berbasis timestamp: `updated_at` lebih baru menang; nilai lama yang bertentangan → `sync_conflict_logs` (`SERVER_WINS`/`CLIENT_WINS`/`MANUAL_MERGE`).
- **Push/pull:** `POST /audit/sessions/sync` + `GET /audit/sessions/{id}/sync?since=…`.
- **Publish (corporate QA):** menghasilkan `findings` + **auto-CAPA** untuk tiap item gagal (lihat §4). Sesi PUBLISHED tak bisa diubah.
- **Laporan PDF:** `GET /audit/sessions/{id}/report.pdf` — lokal `Accept-Language` (F-22), memakai data nyata (G).

---

## 4. Engine CAPA (F-03/F-04)

### 4a. Lifecycle — Hierarki approval "Four-Eyes"
Status: `OPEN → AWAITING_GM → AWAITING_QA → CLOSED`. Bukti foto `AFTER` terverifikasi **wajib** sebelum approval (C1); approver ≠ assignee (Four-Eyes); untuk origin `WHISTLEBLOWER`, approver ≠ reporter.

### 4b. Auto-trigger & SLA
- Publish audit → item gagal (`ratio == 0`) → `capa_tickets` (origin `AUDIT`), idempoten per finding.
- **SLA:** priority 1 (CRITICAL) 24 jam, 2 (MAJOR) 48 jam, 3 (MINOR) 168 jam.

### 4c. Media split-path (C2 / ARD-005)
```
PENDING →(presign 7 menit SigV4)→ PRESIGNED →(client PUT ke MinIO)→ confirm(verify size+mime+reachability w/ Range GET) → VERIFIED
mismatch → FAILED →(retry presign)→ PRESIGNED
```
- Kegagalan storage → `503 StorageUnavailableError` (state jujur — G4), bukan status palsu.
- `sha256` penuh didefer ke worker media.

### 4d. SLA class & eskalasi
- `sla_status`: `ON_TRACK` / `AT_RISK` (remaining < 25% window) / `OVERDUE`.
- Hierarki eskalasi **GM → ROM → CORP_EXEC** (max level 3); tiap level membuat notif `CAPA_ESCALATE`.

---

## 5. CRM (F-07/F-08)

### 5a. Kanban lead
Status: `LEAD → CONTACTED → PROSPECT → CONFIRMED; LOST` (terminal, **wajib** `lost_reason` non-blank → 422). Skip-forward diizinkan. Lead `LOST` = terminal (tanpa aktivitas lanjutan).

Semua transisi dicek terhadap `ALLOWED_TRANSITIONS` → transisi ilegal = 409 `InvalidTransitionError`.

### 5b. Follow-up reminder
Sweep lead non-terminal dengan `next_followup_at ≤ now` → notif `FOLLOWUP_CRM` ke owner. Idempoten via `reminder_key`: `{lead_no}:{next_followup_at:YYYYMMDDHH}` — jadwal berubah → reminder baru.

### 5c. Cross-property referral (F-08)
- `POST /crm/leads/{id}/refer`: pindah kepemilikan antar-hotel + komisi immutable di `lead_referrals`.
- Guardian **single-hop**: lead hasil referral tak bisa direfer lagi; referensi antar hotel yang sama ditolak.
- Owner tujuan = `resolve_crm_owner` (prefer HOTEL_SALES aktif, fallback GM dgn `crm:manage`). Notif `CRM_REFERRAL`.

---

## 6. RFP Intake → Lead (F-11)

- **Publik** `POST /frontpage/rfp` (tanpa JWT) → `rfp_requests` (status `NEW`).
- Target hotel valid + owner aktif → auto-transform ke lead CRM `source=RFP_PORTAL` + status RFP `ASSIGNED` + notif `RFP_INTAKE`.
- Tanpa target / kode invalid → RFP tetap `NEW` (jalur assign korporat) — validasi jujur. Email kosong → placeholder `rfp-…@noemail.invalid` (RFC 2606).
- Status RFP: `NEW → ASSIGNED → ACCEPTED/DECLINED`.
- Alur status lead dari portal tercatat sebagai `LEAD` sejak awal (follow-up default 1 hari).

---

## 7. Quotation, Pagu SBM & PDF (F-09/ADR-009/E1-E3)

- **Sumber rate:** query dinamis `government_sbm_rates` berkunci `(province, package_type, fiscal_year, is_active)` (E2) + **snapshot stabil** `sbm_rate_value`/`sbm_fiscal_year` ke quotation (E3) — historis tetap walau PMK berubah.
- **Validasi pagu GOV:** `gross/pax > max_rate_per_pax` → `409 SbmPaguExceededError`; missing rate → `SbmRateMissingError`. PRIVATE: snapshot opsional, tanpa blok.
- **Diskon:** GOV + `discount>0` → `discount_approval_status=PENDING` (approval GM); lainnya `APPROVED`.
- **Quotation immutable:** tidak ada delete; pembatalan via status `DECLINED`. Status: `DRAFT → SENT → ACCEPTED/DECLINED`.
- **PDF:** pure-Python — kop letterhead, rincian biaya (gross/discount/final), blok status pagu, **barcode Code39** quotation_no; disimpan ke object store; akses via presigned GET.

---

## 8. Document & Billing Milestone (F-10)

- **Syarat:** quotation harus `ACCEPTED` (alur menang).
- **Milestone:** `SPK → NPWP → BAST → LPJ`; status `EXPECTED → UPLOADED → PAID` (PAID **terminal** — imutabilitas finansial, tanpa delete/rollback; `OVERDUE` dari EXPECTED bila lewat due).
- Transisi `UPLOADED` wajib `doc_key` (bukti dokumen nyata); `PAID` otomatis kunci `paid_at`.
- **Auto-reminder:** sweep non-PAID `due ≤ now + 14 hari` → notif `BILLING_REMINDER` ke finance + GM hotel. Idempoten via `reminder_key`: `bm:{milestone_id}:{due:YYYYMMDD}`.

---

## 9. Notifikasi & Delivery (F-03/F-22)

- **Tipe:** `CAPA_SLA`, `CAPA_ESCALATE`, `FOLLOWUP_CRM`, `CRM_REFERRAL`, `RFP_INTAKE`, `BILLING_REMINDER`, `REPORT`.
- **Channel per user:** `PUSH` (inbox selalu) + `EMAIL` (bila email) + `WA` (bila phone).
- **Template kanonik + i18n:** registry template (id kanonik di kode, en di tabel translations); prioritas render: override DB (locale) → override DB (`id`) → konstanta kode (fallback en→id). Variabel `{var}` hilang → ditampilkan literal (tidak menghapus data — F3).
- **Delivery honest (G4):** `QUEUED → SENT/FAILED` via sweep (`run_delivery_sweep`, max 3 attempts, `last_error` 500 char). PUSH tidak di-deliver via provider.
- **Worker:** `POST /notifications/sweep`, `/remind-sla`, `/remind-billing` (guard `notifications:deliver`) — juga bisa dijalankan RQ/scheduler.

---

## 10. Terjemahan (F-22/F3)

- Konten kanonikal id di DB, terjemahan en di tabel `translations` (unique `entity_type, entity_id, field, locale`).
- Covered: nama template/section, question item (override kurasi), nama brand/region/province, nama role/deskripsi permission, dan template notifikasi (id+en).
- **Fallback F3:** bila terjemahan hilang → konten kanonikal **nyata** dari DB (bukan mock).
- Endpoint: `GET/PUT /translations`, `GET /translations/{entity_type}/{entity_id}`, `DELETE`.

---

## 11. Legacy (ARD-008, Dual-Layer)

- **`legacy_score_rows`** (2024-26, sumber `dm_audit_ops`, via Excel/CSV) — tidak merusak scoring baru.
- **Batch:** `legacy_ingestion_batches` (status UPLOADED → PROCESSING → COMPLETED/FAILED, fail_log).
- Digabung di **YoY analytics** (F-21): rerata dua sumber per `(year, hotel, department)`; pemetaan nama dept legacy → enum kanonik (`SECURITY RISK MANAGEMENT → SECURITY_RISK`, dll).

---

## 12. Analytics

| Fitur | Endpoint | Isi |
|---|---|---|
| YoY trend (F-21) | `GET /analytics/yoy` | Legacy + live PUBLISHED, filter hotel/department/years |
| Lost Reason (F-07) | `GET /crm/analytics/lost-reasons` | Status LOST per `lost_reason`: total, lost_rate (penyebut `created_at`), total amount, breakdown, trend kuartal (`2026-Q3`) |

Isolasi tenant analytics disuntik di layer endpoint (service menerima `hotel_ids` yang sudah di-scope).