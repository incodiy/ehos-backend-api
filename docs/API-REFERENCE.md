# Referensi API — 80 Endpoint

Inventori lengkap seluruh endpoint HTTP. Sumber kebenaran kontrak: `openapi.yaml` di `.opencode/artifacts/ehos/G1-ARCH/` (klien generate client dari spec ini — ARD-003).

## Konvensi

- **Auth:** semua endpoint wajib `Authorization: Bearer <access_token>` kecuali ditandai **PUBLIC**. Guard diperoleh dari `app/api/deps.py` (`CurrentUser`, `require_permission`) + `app/api/security_helpers.py`.
- **Guard modul** (CAPA/CRM/Billing): `_require_read` / `_require_manage` / `_require_resolve` / `_require_approve` / `_require_upload` — menggabungkan `perms()` + `allowed_hotel_ids()` / `user_scoped_to_hotel()` untuk isolasi tenant.
- **Envelope:** `Envelope[T]` = `{ data: T }`; `Paginated[T, U]` = `{ data: [U], meta: PaginationMeta }`; beberapa modul memakai envelope manual (`{ success, data, meta }`).
- **Kode status:** 201 untuk create, 204 No Content untuk delete/reset, 401 (auth), 403 (RBAC/scope), 404 (not found), 409 (kontainer/state conflict), 422 (validasi), 503 (object store ora terjangkau).
- **Kode error lain:** `403 "Missing permission: ..."`, `403 "Missing scope hotel/region/global"`.

## Ringkasan

| Metode | Jumlah |
|---|---|
| GET | 34 |
| POST | 36 |
| PATCH | 6 |
| PUT | 2 |
| DELETE | 2 |
| **Total** | **80** |

Public (tanpa auth): `POST /auth/login`, `POST /auth/refresh`, `POST /frontpage/rfp`, `GET /health`.

---

## Auth — `/auth` (7)

### `POST /auth/login` — PUBLIC
Body: `{ email, password }`. → `{ data: TokenPair, user: UserOut }`.
- `401 "Email atau password salah"` — user tak ditemukan / akun nonaktif / password salah (alasan dicatat ke `login_audits`).

### `POST /auth/refresh` — PUBLIC
Body: `{ refresh_token }`. → `{ data: TokenPair }`.
- `401`: `"Refresh token invalid"` · `"Not a refresh token"` · `"Invalid refresh subject"` · `"Account disabled"` · `"Refresh token revoked or unknown"` · `"Refresh token expired"`.

### `POST /auth/logout`
CurrentUser. 204. Merevoke seluruh sesi refresh token user.

### `GET /auth/me`
CurrentUser. → `{ data: { user, roles, active_hotel } }`. Mengembalikan hotel primer dari `user_hotel_assignments`.

### `GET /auth/hotels`
CurrentUser. → `{ data: [HotelScopeItem] }` — `{ hotel_id, hotel_code, hotel_name, role_id, role_code, is_primary }`. Hanya hotel yang ditugaskan ke user.

### `POST /auth/switch-hotel`
Body: `{ hotel_id }`. → `{ data: { active_hotel } }`.
- `403 "Hotel di luar scope user"` — hotel di luar assignment. Menandai target `is_primary=True`, sisanya `False`.

### `PATCH /auth/locale`
Body: `{ locale }`. → `{ success, data: { preferred_locale } }`.

---

## Users & Roles — `/roles`, `/users` (8)

### `GET /roles`
CurrentUser. → `Envelope[list[RoleWithPermissionsOut]]`. Global.

### `GET /users`
Query: `role_code`, `hotel_id`, `search` (ILIKE name/email), `page=1`, `per_page=50` (max 200).
- `403 "Missing user read permission"` — tanpa salah satu `user:read:global|region|hotel`.

### `GET /users/{user_id}`
- `404 "User tidak ditemukan"`. Siapa pun login boleh baca.

### `POST /users` — 201
Body: `{ email, name, password, role_code, hotel_ids: [UUID], region_id, preferred_locale }`.
- `403 "Tidak berhak mengelola user"` · `403 "Delegated admin tidak boleh membuat role corporate/GM lain (A3)"` · `403 "Hotel target di luar scope delegasi"` · `422 "hotel_ids wajib untuk role unit"` · `422 "Role tidak dikenal: {role_code}"` · `409 "Email sudah terdaftar"`.

### `PATCH /users/{user_id}`
Body: `{ name, phone, preferred_locale, is_active, add_hotel_ids, remove_hotel_ids }`.
- `404` · `403 "Di luar scope delegasi (A3)"` · `403 "Tidak berhak mengelola manager role (A3)"` · `403 "Tidak bisa menonaktifkan akun sendiri"` · `403 "Hotel target di luar scope delegasi"`.

### `DELETE /users/{user_id}` — 204
Toggle soft-delete/restore (bukan delete permanen).
- `404` · `403 "Tidak bisa menonaktifkan akun sendiri"` · `403 "Di luar scope delegasi (A3)"` · `403 "Tidak berhak menonaktifkan manager role (A3)"`.

### `POST /users/{user_id}/reset-password` — 204
Body: `{ new_password }`.
- `404` · `403 "Ganti password sendiri via profil; reset hanya utk user lain"` · `403 "Di luar scope delegasi (A3)"` · `403 "Tidak berhak reset password manager role (A3)"`.

### `PUT /roles/{role_id}/permissions`
Body: `{ permission_codes: [str] }`. → `Envelope[RoleWithPermissionsOut]`.
- `403 "Khusus ROOT_ADMIN (A4)"` · `422 "permission_codes wajib array of string"` · `404 "Role tidak ditemukan"` · `403 "Hak ROOT_ADMIN sovereign, tidak bisa diubah (A4)"` · `422 "Ada permission code tidak dikenal"`.

---

## Master Data — `/hotels`, `/brands`, `/regions`, `/provinces` (7)

### `GET /hotels`
Query: `brand_tier`, `region_id`, `city` (ILIKE), `status`, `has_ballroom` (JSONB `mice_facilities.ballroom_capacity`), `page`, `per_page` (max 200).
→ `Paginated[HotelOut]` — join brand/brand_tier/region + `gm_name`, `rom_name`, `lat`, `lng`. Semua hotel aktif terlihat.

### `GET /hotels/{code}`
`code` di-uppercase. → `Envelope[HotelOut]`. `404 "Hotel tidak ditemukan"`.

### `PATCH /hotels/{code}`
Guard: `master:write` (inline). Body: `{ name, geofence_radius_meters, mice_facilities, status }`.
- `403 "Missing permission: master:write"` · `404`.

### `GET /hotels/{code}/departments`
→ `Envelope[list[DepartmentOut]]` — `{ id, hotel_id, code, name, hod_user_id }`.

### `GET /brands` · `GET /regions` · `GET /provinces`
CurrentUser. → `Envelope[list[BrandOut/RegionOut/ProvinceOut]]`. Brands terurut tier+name.

---

## Checklist — `/checklist` (7)

Guard modul: write=inline `checklist:write`, read=`checklist:read`.

### `GET /checklist/templates`
Query: `department` (DepartEnum), `brand_tier`, `status` (DRAFT/LOCKED). → `Envelope[list[TemplateOut]]`.

### `POST /checklist/templates` — 201
Body: `{ department, name, version, brand_tier }`.
- `403 "Missing permission: checklist:write"` · `409 "Template (department, name, version) sudah ada"`.

### `GET /checklist/templates/{id}`
→ `Envelope[TemplateDetail]` — nested `sections[].items[]`. `404`.

### `POST /checklist/templates/{id}/versions` — 201
Body: `{ new_version }`. Deep-clone seluruh section+item dari sumber.
- `404` · `409 "Versi baru hanya bisa dibuat dari template LOCKED"` · `409 "Versi baru sudah ada"`.

### `POST /checklist/templates/{id}/lock`
- `404` · `409 "Hanya template DRAFT yang bisa di-lock"`. Menetapkan `locked_at` (snapshot tak terubah — B3).

### `POST /checklist/templates/{id}/sections` — 201
Body: `{ parent_id, code, name, sort_order }`.
- `404` · `409 "Hanya template DRAFT yang bisa dimodifikasi"` · `422 "parent_id bukan bagian dari template ini"` · `409 "Section code sudah ada di template ini"`.

### `POST /checklist/templates/{id}/sections/{section_id}/items` — 201
Body: `{ code, question_text, rubric_type, max_score, weight, na_allowed, is_life_safety, sort_order }`.
- `404` · `409 "Hanya template DRAFT yang bisa dimodifikasi"` · `422 "Section bukan bagian dari template ini"` · `422 "MULTI_ROOM max_score harus = 90 x jumlah sample ruangan"` · `409 "Item code sudah ada di section ini"`.

---

## Audit — `/audit` (11)

Guard spesifik audit: `_require_read` (periksa `audit:read:global|region|hotel`) dan `_require_run` (periksa `audit:read:global` atau `audit:run:hotel` + scope).

### `GET /audit/sessions`
Query: `hotel_id`, `department`, `status` (∈ `DRAFT,IN_PROGRESS,SUBMITTED,PUBLISHED`), `origin`, `date_from`, `date_to`, `page=1` (per_page tetap 50).
- `403 "Missing permission: audit:read:global (list tanpa filter hotel)"` · `403 "Missing permission: audit scope hotel/region/global"` · `422 "status tidak dikenal: {status}"`.

### `POST /audit/sessions` — 201
Body: `{ hotel_id, template_id, department, audit_type, date_start, date_end, client_id }`.
- `404 "Hotel tidak ditemukan"` / `"Template tidak ditemukan"` · `422 "Template department ≠ sesi department"` · `422 "Template belum LOCKED (B3 — harus snapshot terkunci)"` · `422 "Tidak ada template LOCKED utk (department, brand_tier) hotel ini"`.
- Dedup offline F-05: `client_id` sama → mengembalikan sesi yang sudah ada.

### `GET /audit/sessions/{id}`
→ `Envelope[dict]` — `{ session, items, findings }`. `404` · `403`.

### `GET /audit/sessions/{id}/report.pdf`
Header: `Accept-Language` (en → English, lainnya Indonesian). → `application/pdf`, `Content-Disposition: attachment`.
- `404` · `403` · `422` AggregationError (tidak ada nilai valid). Konten lokal F-22, real data only (G).

### `POST /audit/sessions/{id}/items`
Body: `{ scores: [{ item_id, value, is_na, note, room_ref, scored_at, updated_at }] }`. → `{ upserted, conflicts }`.
- `404` · `409 "Sesi sudah PUBLISHED — tidak bisa mengubah nilai"` · `422 "Item {id} bukan milik template sesi ini"` · `422` scoring (`InvalidNA`/`InvalidScoreValue`).
- Merge F-05: `updated_at` lebih baru menang; nilai konflik lama → `sync_conflict_logs`.

### `POST /audit/sessions/{id}/submit`
- `404` · `409 "Sesi sudah PUBLISHED"`.

### `POST /audit/sessions/{id}/publish`
Guard: `_is_corporate` (`audit:read:global`).
- `404` · `403 "Missing permission: audit:read:global (publish = corporate QA)"` · `409 "Sesi sudah PUBLISHED"` · `422 "Sesi harus SUBMITTED sebelum dipublish"` · `422` AggregationError · `422 "Belum ada nilai yang valid untuk di-score"`.
- Side-effect: membuat `Finding` + `auto_create_capa_ticket` untuk item gagal (F-03).

### `POST /audit/sessions/sync`
Body: `{ session_client_id, scores }`. → `{ session_id, upserted, conflicts, conflict_ids, server_now }`.
- `404 "Sesi dengan client_id tsb tidak ada di server (buat dulu via POST /audit/sessions)"`.

### `GET /audit/sessions/{id}/sync`
Query: `since` (datetime, wajib). → `{ scores, media_status, server_now }`.

### `GET /audit/sessions/{id}/conflicts`
→ `Envelope[list[SyncConflictOut]]` hanya `resolution="PENDING"`.

### `POST /audit/sessions/{id}/conflicts/{conflictId}/resolve`
Body: `{ winning_value }`.
- `404` · `403 "Missing scope hotel/region/global"` · `404 "Konflik tidak ditemukan"` · `409 "Konflik sudah di-resolve"` · `409 "Baris nilai yang dikonflik sudah tidak ada"` · `422` scoring.

---

## Analytics — `/analytics` (1)

### `GET /analytics/yoy`
Query: `hotel_id`, `department`, `years` (multi `?years=2024&years=2025`). → `Envelope[list[YoYPointOut]]`.
- `403 "Missing permission: audit scope hotel/region/global"` (hotel_id diberikan tanpa scope) · `403 "Missing permission: audit:read:global (korporat/eksekutif)"` (tanpa hotel_id, bukan korporat).
- Dua sumber (ARD-008): legacy `legacy_score_rows` 2024-26 + live `audit_sessions` PUBLISHED.

---

## CAPA — `/capa` (12)

Guard modul:
- `_require_read` — `capa:read:global|capa:approve` (global) atau `capa:read:hotel|capa:manage:hotel|capa:resolve:hotel` + scope
- `_require_manage` — `capa:approve` (global) atau `capa:manage:hotel` + scope
- `_require_resolve` — `capa:resolve:hotel` + scope
- `_require_approve` — `capa:approve`
- `_require_upload` — `capa:resolve:hotel|capa:manage:hotel|capa:approve` + scope

### `GET /capa/tickets`
Query: `hotel_id`, `priority` (1/2/3), `status` (∈ CAPA_STATUSES), `only_overdue=false`, `page` (per_page 50).
- `403 "Missing permission: capa:read (hotel/global)"` · `403 "Missing permission: capa scope hotel/region/global"` · `403 "Missing permission: capa:read:global (list tanpa filter hotel)"` · `422 "priority harus 1/2/3"` · `422 "status tidak dikenal: {status}"`.

### `POST /capa/tickets` — 201
Body: `{ finding_id, assigned_to }`.
- `404 "Finding tidak ditemukan"` · `403`. Delegasi `auto_create_capa_ticket`; mengembalikan tiket existing bila finding sudah punya tiket.

### `GET /capa/tickets/{id}`
→ `Envelope[dict]` — ticket + `hotel_code`, `assignee_name`, `overdue`, `sla_status`, `media_summary`, `media[]`, `history[]`.

### `POST /capa/tickets/{id}/assign`
Body: `{ assigned_to, note }`.
- `404` · `422 "Assignee tidak ditemukan / nonaktif"` · `409` InvalidTransitionError.

### `POST /capa/tickets/{id}/resolve`
Body: `{ note, media: [{ phase, sha256, size_bytes, mime_type, object_key }] }`. → `{ ticket, media[] }`.
- `422 "Bukti resolve hanya boleh phase=AFTER (foto perbaikan)"` (C1) · `409`.

### `POST /capa/tickets/{id}/verify/gm`
Body: `{ decision: APPROVE|REJECT, note }`. `409` InvalidTransitionError.

### `POST /capa/tickets/{id}/verify/qa`
Body: `{ decision: CLOSE|REOPEN, note }`.
- `403 "Missing permission: capa:approve"` · `409`.

### `POST /capa/tickets/{id}/escalate`
Guard: `capa:approve` atau `capa:manage:hotel`. Naikkan `escalation_level`. `409` (level max / CLOSED).

### `GET /capa/tickets/{id}/history`
→ `Envelope[list[CapaHistoryOut]]` (AppendOnly).

### `GET /capa/tickets/{id}/media`
→ `Envelope[CapaMediaListOut]` — `{ items, summary: { before_count, after_count, has_verified_after } }`.

### `POST /capa/tickets/{id}/media/{media_id}/presign`
→ `{ media_id, object_key, presigned_url, upload_status, expires_in: 420 }` (presigned PUT 7 menit, SigV4 inline).
- `404` · `409` (VERIFIED tidak bisa re-presign).

### `POST /capa/tickets/{id}/media/{media_id}/confirm`
Body optional: `{ object_key }`. Verifikasi size+mime+reachability (Range GET). → `Envelope[CapaMediaOut]`.
- `422 "object_key tidak cocok dengan media tersimpan"` · `503` StorageUnavailableError (MinIO unreachable — jujur, G4) · `409`.

---

## CRM — `/crm` (13)

Guard: `_scope_hotel_ids()` (None=global) + `_require_read`/`_require_manage` (`crm:read`/`crm:manage`).

### `GET /crm/leads`
Query: `status` (∈ LEAD_STATUSES), `source`, `owner_id`, `followup_due` (next_followup_at ≤ now & non-terminal), `hotel_id`, `page`.
- `403 "Missing permission: crm:read"` · `403 "Missing permission: crm scope hotel/region/global"` · `422 "status tidak dikenal"`.
- Isolasi otomatis: tanpa `hotel_id`, difilter `Lead.hotel_id IN scope`.

### `POST /crm/leads` — 201
Body: `{ hotel_id, source, institution_type, company_name, pic_name, pic_phone, pic_email, province_id, amount_est, next_followup_at }`.
- `404 "Hotel tidak ditemukan"` · `403`.

### `GET /crm/leads/{id}`
→ `Envelope[dict]` — lead + `hotel_code`, `owner_name`, `next_followup_human`, `activities[]`, `quotations[]`, `referrals[]`.

### `PATCH /crm/leads/{id}`
Body: `{ status, lost_reason, owner_id, next_followup_at, amount_est, pic_name, pic_phone, pic_email }`.
- `404` · `422 "Owner baru tidak ditemukan / nonaktif"` · `422` MissingLostReasonError (LOST wajib alasan) · `409` InvalidTransitionError.

### `POST /crm/leads/{id}/activities` — 201
Body: `{ type, note, next_followup_at }`.
- `422 "type tidak dikenal"` · `409 "Lead LOST terminal — tidak boleh aktivitas lanjutan"` · `409`.

### `POST /crm/leads/{id}/refer` — 201
Body: `{ to_hotel_id, commission_amount, note }`. → `Envelope[LeadReferralOut]`.
- `404 "Lead tidak ditemukan"` / `"Hotel tujuan tidak ditemukan"` · `409` ReferralError (single-hop/cross-property violation). Mengirim notif `CRM_REFERRAL` ke owner baru.

### `GET /crm/quotations`
Query: `lead_id`, `status`, `page`.

### `POST /crm/quotations` — 201
Body: `{ lead_id, event_date, event_name, package_type, pax_count, gross_amount, discount_amount }`.
- `404` · `409` SbmPaguExceededError / SbmRateMissingError / QuotationCreateError (validasi pagu SBM GOV, E3).

### `GET /crm/quotations/{id}`
→ `Envelope[QuotationDetailOut]` — quotation + `milestones[]` + `pdf_url`.

### `POST /crm/quotations/{id}/pdf`
→ `{ pdf_url }` (presigned GET). PDF ber-kop + barcode Code39, disimpan `quotations/{no}.pdf`.
- `404` · `503 "Object store tidak terjangkau: {exc}"`.

### `GET /crm/sbm-rates`
Query: `province_code`, `package_type`, `fiscal_year`, `is_active`. → `Envelope[list[SbmRateOut]]`.

### `PATCH /crm/sbm-rates/{id}`
Body: `{ max_rate_per_pax, fiscal_year, is_active }`.
- `403 "Missing permission: sbm:write"` · `403 "SBM master hanya dikelola corporate (zero-assignment role)"` · `404` · `409 "SBM rate utk provinsi/package/tahun itu sudah ada"`.

### `GET /crm/analytics/lost-reasons`
Query: `from_date`, `to_date`, `hotel_id`, `region_id` (default kuartal-berjalan).
- `403` · `404 "Region tidak ditemukan"`. Output: `{ total_leads, total_lost, lost_rate, total_lost_amount, breakdown[], trend[] }`.

---

## Billing — `/crm/billing` (3)

Guard: `_require_billing_read` (`crm:read` + scope), `_require_billing_manage` (`billing:manage` + scope).

### `GET /crm/billing`
Query: `quotation_id`, `status` (∈ MILESTONE_STATUSES), `due_before`, `page`.
- `403 "Missing permission: crm:read"` · `422 "status tidak dikenal"`. Scope via join Quotation → hotel.

### `POST /crm/billing/milestones` — 201
Body: `{ quotation_id, milestone_type (SPK|NPWP|BAST|LPJ), due_date, amount, doc_no }`.
- `403` · `404 "Quotation tidak ditemukan"` · `409` BillingQuotationError / BillingDuplicateError · `422` BillingMilestoneError.
- Hanya untuk quotation **ACCEPTED** (F-10).

### `PATCH /crm/billing/milestones/{id}`
Body: `{ status, doc_key, doc_no, paid_at }`.
- `403` · `404` · `409` BillingTransitionError (PAID terminal — transisi mundur ditolak) · `422`.

---

## Frontpage — `/frontpage` (1)

### `POST /frontpage/rfp` — **PUBLIC** — 201
Body: `{ company_name, institution_type, pic_name, phone, email, event_date, pax, package_type, city, target_hotel_code, notes }`. → `Envelope[RfpPublicOut]` — `{ ref_no, status }`.
- `503 "Identity ingest belum tersedia"` — user sistem `system.ingest@ehos.local` belum tersedia.
- Side-effect: buat `rfp_requests` + auto-lead CRM `source=RFP_PORTAL` bila target hotel valid (owner aktif). Kode invalid/tanpa target → RFP tetap `NEW` (validasi jujur).

---

## Translations — `/translations` (4)

Guard: `translations:read` / `translations:write`.

### `GET /translations`
Query: `locale` (wajib), `entity_type`, `since` (incremental). → `Envelope[TranslationBundle]` — `{ locale, items, updated_after }` (batas 2000 item).

### `PUT /translations`
Body: `{ locale, items: [{ entity_type, entity_id, field, value }] }`. → `{ upserted }`. Bulk UPSERT pada unique `(entity_type, entity_id, field, locale)`.

### `GET /translations/{entity_type}/{entity_id}`
Query: `locale`. → `Envelope[list[TranslationOut]]`.

### `DELETE /translations/{translation_id}` — 204
- `404 "Terjemahan tidak ditemukan"`.

---

## Notifications — `/notifications` (5)

### `GET /notifications`
Query: `unread_only`, `page`, `per_page` (1..100), `include_channels` (CSV `PUSH|EMAIL|WA`). → envelope manual `{ success, data, meta }`. Hanya notifikasi milik user sendiri.

### `POST /notifications/{notification_id}/read` — 204
- `404 "Notification not found"` — tak ditemukan atau milik user lain (ownership check).

### `POST /notifications/sweep` — worker
Guard: `require_permission("notifications:deliver")`. Query: `limit=200` (1..1000). Proses `QUEUED` EMAIL/WA → `SENT`/`FAILED` (max 3 attempts).

### `POST /notifications/remind-sla` — worker
Guard: `notifications:deliver`. Query: `window_hours=24` (1..72). Sweep notif SLA CAPA dalam window.

### `POST /notifications/remind-billing` — worker
Guard: `notifications:deliver`. Query: `days_before=14` (1..90). Sweep reminder billing milestone non-PAID (F-10).

---

## System — `/health` (1)

### `GET /health` — **PUBLIC**
→ `{ status: "ok", service }`.

---

## Referensi Guard Auth (app/api/deps.py + security_helpers.py)

| Simbol | Keterangan |
|---|---|
| `DbSession` | `Annotated[AsyncSession, Depends(get_db)]` |
| `oauth2_scheme` | OAuth2PasswordBearer(tokenUrl="/auth/login") |
| `get_current_user_id` / `get_current_user` / `CurrentUser` | Deps JWT |
| `require_permission(code)` | Factory → dep RBAC |
| `perms(session, user)` | Set permission codes user |
| `allowed_hotel_ids(session, user)` | Set hotel IDs dalam scope (None/empty = global korporat) |
| `user_scoped_to_hotel(session, user, hotel_id)` | True bila hotel dalam scope |
| `forbid(detail)` | Helper `403` |

Daftar kode permission yang dirujuk endpoint: `user:manage:global|hotel`, `user:read:global|region|hotel`, `master:write`, `checklist:read|write`, `audit:read:global|region|hotel`, `audit:run:hotel`, `capa:read:global|hotel`, `capa:manage:hotel`, `capa:resolve:hotel`, `capa:approve`, `crm:read`, `crm:manage`, `billing:manage`, `sbm:write`, `translations:read|write`, `notifications:deliver`. Matriks lengkap: [RBAC-AUTH.md](./RBAC-AUTH.md).