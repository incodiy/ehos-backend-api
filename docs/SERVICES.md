# Service Layer — Referensi

Semua business logic berada di `app/services/` (19 modul). Dokumen ini memuat fungsi publik, state machine eksak, exception + mapping HTTP, dan 5 background sweep.

## Konvensi Exception

| Exception | Dasar | HTTP |
|---|---|---|
| `InvalidTransitionError` | ValueError | 409 |
| `MissingLostReasonError` | ValueError | 422 |
| `ReferralError` | ValueError | 409 |
| `BillingMilestoneError` | ValueError | 422 |
| `BillingTransitionError` | BillingMilestoneError | 409 |
| `BillingQuotationError` | ValueError | 422/409 |
| `BillingDuplicateError` | BillingMilestoneError | 409 |
| `InvalidScoreValue` / `InvalidNA` | ValueError | 422 |
| `AggregationError` | ValueError | 422 |
| `SbmRateMissingError` / `SbmPaguExceededError` / `QuotationCreateError` | ValueError | 409 |
| `DeliveryError` | RuntimeError | 5xx/non-2xx internal |
| `ProviderNotConfiguredError` | DeliveryError | 5xx |
| `StorageUnavailableError` | RuntimeError | 503 (jujur — G4) |

---

## 1. `scoring.py` — Evaluator rubrik per item (F-01)

| Fungsi | Signatur |
|---|---|
| `evaluate_item` | `(*, rubric_type, value, max_score, na_allowed=False, is_na=False) -> ItemEvaluation` |
| `evaluate_multi_room` | `(*, max_score, na_allowed=False, rooms: list[dict]) -> ItemEvaluation` |
| `resolve_tier_key` | `(department, brand_tier) -> iterator[(dept, tier), (dept, None)]` |
| `find_locked_template` | `async (session, department, brand_tier)` — template LOCKED terbaru eksak → universal |

`ItemEvaluation(frozen)`: `achieved`, `max_possible`, `is_na`; `.ratio = achieved/max_possible` (None bila max ≤ 0). Rubrik: TRAFFIC_LIGHT / BINARY_COUNT / NUMERIC_SCALE / MULTI_ROOM (detail nilai di [FEATURES.md §2a](./FEATURES.md#2-scoring--agregasi--verdict-f-01f-02f-03)).

## 2. `aggregation.py` — Agregasi bottom-up berbobot (F-02)

| Fungsi | Signatur |
|---|---|
| `compute` | `(items_by_section: dict, sections: list, scores: dict) -> SessionScore` |
| `aggregate_session` | `async (session, session_id) -> SessionScore` (loader DB → delegasi `compute`) |

Rumus: node ratio = Σ(weight×ratio)/Σ(weight); total = round(total_ratio×100, 2); item tak dinilai dilaporkan; legacy (score tersimpan tanpa value) di-clamp ke `[0, max_score]`.

## 3. `verdict.py` — PASS/FAIL + hazard (F-02/F-03)

| Fungsi | Signatur |
|---|---|
| `compute_verdict` | `(score: SessionScore, threshold: float = 0.80) -> Verdict` |

Hazard = item life-safety gagal (ratio 0). Blocking: `has_unscored_items`, `life_safety_hazard`, `below_threshold`. Tanpa item → `pass_fail=None, reason=["no_scored_items"]`.

## 4. `capa_lifecycle.py` — Four-Eyes lifecycle (F-03/F-04)

**Transisi eksak:**

| Aksi | Valid dari | Ke |
|---|---|---|
| `resolve` | OPEN, AWAITING_GM | AWAITING_GM |
| `gm_approve` | AWAITING_GM | AWAITING_QA |
| `gm_reject` | AWAITING_GM | OPEN |
| `qa_close` | AWAITING_QA | CLOSED |
| `qa_reopen` | AWAITING_QA | OPEN |
| `escalate` | OPEN, AWAITING_GM, AWAITING_QA | (level+1) |

| Fungsi | Signatur |
|---|---|
| `assert_approval_gate` | `async (session, ticket, actor_id)` — approver ≠ assignee; WHISTLEBLOWER ≠ reporter; ≥1 media AFTER `VERIFIED` |
| `verify_ticket` | `async (session, ticket, action, actor_id, note=None)` |
| `transition_ticket` | `async (…)` — append `CapaStatusHistory`; set `submitted_at`/`closed_at` |
| `resolve_ticket` | `async (session, ticket, actor_id, note, media)` — reg. media AFTER sebagai PENDING |
| `assign_ticket` | `async (…)` — tanpa transisi status; CLOSED tak bisa di-assign |
| `escalate_ticket` | `async (…)` — `escalation_level += 1` |

## 5. `capa_trigger.py` — Auto-CAPA (F-03)

| Fungsi | Signatur |
|---|---|
| `resolve_department_id` | `async (session, hotel_id, department) -> uuid | None` |
| `auto_create_capa_ticket` | `async (session, finding, actor_id, department=None)` |

Idempoten per finding; SLA map: CRITICAL→(P1,24h), MAJOR→(P2,48h), MINOR→(P3,168h); `receipt_id = CAPA-{finding.id.hex[:7].upper()}`.

## 6. `capa_media.py` — Media split-path (C2/ARD-005)

| Fungsi | Signatur |
|---|---|
| `ensure_owned` | `(media, ticket_id)` |
| `presign_ticket_media` | `async (session, media, ticket_id, actor_id) -> (CapaMedia, url)` — VERIFIED tidak bisa re-presign |
| `confirm_ticket_media` | `async (…)` — verify size+mime+reachability → VERIFIED/FAILED; propagasi `StorageUnavailableError` |
| `before_after_summary` | `(medias) -> dict` |

## 7. `sla_escalation.py` — SLA class & eskalasi (F-03)

| Konstanta | Nilai |
|---|---|
| `SLA_CLASS_HOURS` | `{1: 24, 2: 48, 3: 168}` |
| `FOLLOW_UP_MAX_HOURS` | 336 |
| `MAX_ESCALATION_LEVEL` | 3 |
| `RECEIVER_ROLE_FOR_LEVEL` | `{1: HOTEL_GM, 2: REGIONAL_ROM, 3: CORP_EXEC}` |

| Fungsi | Signatur |
|---|---|
| `sla_class_hours` / `sla_status` | status: ON_TRACK / AT_RISK / OVERDUE |
| `resolve_escalation_receivers` | `async (session, ticket, level) -> list[User]` |
| `notify_escalation` | `async (…) -> int` |
| `escalate_one_level` | `async (…) -> list[User]` |
| `find_sla_breaches` | `async (session, now=None, limit=200) -> list[CapaTicket]` |

## 8. `delivery.py` — SMTP/WhatsApp + sweep (7e)

| Fungsi | Signatur |
|---|---|
| `send_email` | `async (to, subject, body)` — aiosmtplib; `ProviderNotConfiguredError` bila `smtp_host` kosong |
| `send_whatsapp` | `async (phone, message)` — HTTP POST ke `wa_api_url`, Bearer token, timeout 15s |
| `run_delivery_sweep` | `async (session, limit=200) -> dict` — QUEUED EMAIL/WA → SENT/FAILED; max 3 attempts; `last_error` 500 char |

## 9. `notifications.py` — Template, inbox, remind_sla (F-03/F-22)

- **Registry (`NOTIFICATION_TEMPLATES`)** + mapping `TEMPLATE_TYPE`: `capa_escalate→CAPA_ESCALATE`, `capa_sla_reminder→CAPA_SLA`, `crm_followup→FOLLOWUP_CRM`, `crm_referral→CRM_REFERRAL`, `rfp_new→RFP_INTAKE`, `billing_reminder→BILLING_REMINDER`.
- `template_uuid(key)` = UUIDv5 `(NAMESPACE_URL, "ehos:notification_template:{key}")` → `translations.entity_id`.
- **Render precedence:** override DB locale → override DB `id` → konstanta (fallback en→id); variabel hilang tetap literal (`_SafeFormat`).

| Fungsi | Signatur |
|---|---|
| `render_text` | `(template, context) -> str` |
| `render_notification` | `async (session, key, context, locale="id") -> {title, body}` |
| `ticket_context` / `human_due` | konteks CAPA / `%d %b %Y %H:%M` |
| `create_notification` | `async (session, *, key, user, context, entity_type, entity_id, locale=None) -> list[Notification]` |
| `list_user_notifications` | `async (session, user_id, *, unread_only=False, page=1, per_page=50)` |

## 10. `crm_pipeline.py` — Kanban lead + follow-up (F-07/F-08)

**Set eksak:**

```python
LEAD_STATUSES      = {"LEAD","CONTACTED","PROSPECT","CONFIRMED","LOST"}
TERMINAL_STATUSES  = {"LOST"}
NO_FOLLOWUP_STATUSES = {"CONFIRMED","LOST"}
ALLOWED_TRANSITIONS = {LEAD→{CONTACTED,PROSPECT,CONFIRMED,LOST},
                       CONTACTED→{PROSPECT,CONFIRMED,LOST},
                       PROSPECT→{CONFIRMED,LOST},
                       CONFIRMED→{LOST},
                       LOST→{}}
LEAD_NO_PREFIXES = {"RFP_PORTAL":"RFP","CROSS_SELLING":"XSELL","REFERRAL":"REF","MANUAL":"MAN"}
```

| Fungsi | Signatur |
|---|---|
| `create_lead` | `async (session, *, lead_no, hotel_id, source, institution_type, company_name, owner_id, pic_*, province_id, amount_est, next_followup_at, created_by)` |
| `change_status` | `async (…)` — LOST wajib `lost_reason` |
| `add_activity` | `async (session, lead, *, activity_type, note, actor_id, next_followup_at=None)` |
| `resolve_crm_owner` | `async (session, hotel_id) -> User | None` |
| `refer_cross_property` | `async (session, lead, *, to_hotel_id, commission_amount, note, actor_id, target=None) -> (LeadReferral, User)` |
| `generate_lead_no` | `(hotel_code, source) -> {PREFIX}-{hotel_code}-{YYMMDD}-{uuid5 upper}` |

## 11. `rfp_pipeline.py` — RFP intake (F-11)

| Fungsi | Signatur |
|---|---|
| `submit_rfp` | `async (session, *, company_name, institution_type, pic_name, phone, email, event_date, pax, package_type, city, target_hotel_code, notes, actor_id, ref_no=None, lead_no=None) -> (RfpRequest, Lead|None, User|None)` |

`ref_no = RFP-{YYMMDD}-…`; email kosong → `rfp-{ref}@noemail.invalid`; notif `RFP_INTAKE` dedup per RFP.

## 12. `quotation_pipeline.py` — Quotation + SBM (F-09/E2/E3)

| Fungsi | Signatur |
|---|---|
| `generate_quotation_no` | `() -> Q-{YYMMDD}-{uuid5 upper}` |
| `resolve_sbm_rate` | `async (session, hotel, package_type, fiscal_year)` |
| `create_quotation` | `async (…)` — guard pax≥1, diskon≤gross, pagu GOV, snapshot E3 |
| `_upsert_quotation` | idempoten (H4) keyed `quotation_no` |

## 13. `billing_pipeline.py` — Billing milestone (F-10)

**Set eksak:**

```python
MILESTONE_TYPES    = {"SPK","NPWP","BAST","LPJ"}
MILESTONE_STATUSES = {"EXPECTED","UPLOADED","PAID","OVERDUE"}
BILLING_TRANSITIONS = {EXPECTED→{UPLOADED,OVERDUE}, OVERDUE→{UPLOADED,PAID},
                       UPLOADED→{PAID}, PAID→{} }   # PAID terminal
```

| Fungsi | Signatur |
|---|---|
| `create_billing_milestone` | `async (session, *, quotation, milestone_type, due_date, amount=None, doc_no=None, actor_id)` — syarat `ACCEPTED`; anti-dup `(quotation_id, milestone_type)` |
| `update_billing_milestone` | `async (…)` — UPLOADED wajib `doc_key`; PAID kunci `paid_at` |
| `resolve_billing_recipients` | `async (session, hotel_id) -> dict` — finance + GM hotel |

## 14. `crm_analytics.py` — Lost reason analytics (F-07)

| Fungsi | Signatur |
|---|---|
| `compute_lost_reason_analytics` | `async (session, *, from_dt, to_dt, hotel_ids=None)` |
| `date_to_range_utc` | `(from_date, to_date) -> (dt, dt)` — `[from, to)` UTC |

## 15. `analytics.py` — YoY (F-21/ARD-008)

| Fungsi | Signatur |
|---|---|
| `compute_yoy` | `async (session, hotel_id=None, department=None, years=None) -> list[dict]` |

Legacy dept map: `HOUSEKEEPING→HOUSEKEEPING`, `SECURITY RISK MANAGEMENT→SECURITY_RISK`, `KITCHEN & FB→KITCHEN_FB`.

## 16. `pdf_report.py` — PDF engine audit (F-02)

- `render_audit_report(data: dict, locale="id") -> bytes` — identitas, verdict shaded, hazard merah, breakdown, findings, jumlah CAPA P1, detail per item multi-page.
- **Sanitasi emoji→ASCII** (`EMOJI_MARKERS`: ✅→[OK], ❌→[NO], ⏳→[TODO], 🚧→[WIP], 🔒→[LOCKED], ⚠→[WARN], 🔴→[CRIT], 🟠→[MAJ], 🟡→[MIN], 🔵→[INFO], 🎯→[GOAL], 🔄→[OPEN], ✔→[OK], ➡→→, ⭐→[STAR], →, —) lalu `cp1252 replace`.
- Blok internal diekspor ke `pdf_quotation`: `Canvas`, `_build_pdf`, `LABELS_ID/EN`.

## 17. `pdf_quotation.py` — PDF quotation (F-09)

- `render_quotation_pdf(lead, hotel, quotation, province_name=None) -> bytes` — kop + biaya + status pagu + **barcode Code39** (`*data*`, NARROW=1.2, WIDE=2.4, GAP=1.2, QUIET=12, BAR_H=42). Diskonto menampilkan `PENDING APPROVAL GM` vs `APPROVED`; pagu `Rp{value:,.0f}`.

## 18. `report_i18n.py` — Lokalisasi laporan (F-22)

- `load_report_translations(session, template_id, locale="en") -> {items, sections}` — dari tabel `translations`; kanonikal id fallback (F3).

## 19. `media_storage.py` — Presigned & verify (C2/ARD-005)

| Fungsi | Signatur |
|---|---|
| `presigned_put_url` / `presigned_get_url` | `(object_key, content_type=None, *, now=None) -> str` |
| `upload_bytes` | `(object_key, content, content_type)` — upload server-side (PDF quotation) |
| `verify_object` | `(object_key, expected_size, expected_mime) -> bool` — Range GET `bytes=0-0`, timeout 5s, cek Content-Range/Length + content-type substring |

SigV4 query-signing dilakukan inline (hmac/hashlib, `UNSIGNED-PAYLOAD`, region `us-east-1`, `PRESIGN_EXPIRES=420` detik) — tanpa dep eksternal.

---

## Background Sweep (5)

| Sweep | File | Callable | Params | Dedup key |
|---|---|---|---|---|
| CAPA SLA reminder | notifications.py | `remind_sla` | `window_hours=24, limit=500` | `{receipt_id}:{due_at:YYMMDDHH}` (type CAPA_SLA) |
| CRM follow-up | crm_pipeline.py | `crm_followup_reminders` | `now=None, limit=500` | `{lead_no}:{next_followup_at:YYMMDDHH}` (type FOLLOWUP_CRM) |
| Billing reminder | billing_pipeline.py | `remind_billing_milestones` | `days_before=14, limit=500` | `bm:{milestone_id}:{due:YYMMDD}` (type BILLING_REMINDER) |
| Delivery | delivery.py | `run_delivery_sweep` | `limit=200` | status-driven QUEUED→SENT/FAILED (max 3 attempts) |
| SLA escalation | sla_escalation.py | `run_sla_escalation` | `actor_id=None, now=None` | parial — level-cap/CLOSED di-skip |

Semua sweep memanggil `session.commit()` internal dan aman dijalankan berulang. Dipicu oleh RQ worker/scheduler atau endpoint worker `POST /notifications/*` (guard `notifications:deliver`).