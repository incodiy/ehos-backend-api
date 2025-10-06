# Seeder & Real Simulation

Semua data awal dihasilkan dari `app/seed/` (17 modul + `runner.py`) sesuai **Aturan 2 H1-H4**: komprehensif per modul, chained via real service (bukan duplikasi logika), variasi situasi produksi, deterministik & idempoten.

## Jalankan

```bash
python -m app.seed.runner
```

Satu session DB (`SessionLocal`), commit di akhir. Aman dijalankan ulang (idempoten).

## Urutan Eksekusi (runner)

```
1  seed_master                    master.py + data.py — brand/provinsi/region/hotel/departemen (dari Excel)
2  seed_system_ingest_user        identity.py — system.ingest@ehos.local (FK non-null sebelum user riil)
3  seed_audit_ingest              audit_ingest.py — sesi scoring 2026 dari workbook (PUBLISHED)
4  seed_crm_leads                 crm_ingest.py — lead cross-sell XSELL-GOV-2026 dari Excel
5  seed_legacy_ops                legacy_ingest.py — baris historis dm_audit_ops 2024-26
6  seed_rbac                      rbac.py — 9 role + 36 permission + relasi
7  seed_users                     users.py — 14 user dev (email→user_id map)
8  seed_checklist_bank            checklist_bank.py — 7 template LOCKED v2026.1 & versi historis
9  seed_translations              translations.py — ~650 baris en (override + identity)
10 seed_capa_scenarios            capa.py — 10 tiket SLA%05d + media
11 seed_crm_scenarios             crm_scenarios.py — kanban CRM-8A per hotel
12 seed_crm_referrals             crm_scenarios.py — referral CRM-8B-REF-01/02
13 seed_rfp_scenarios             rfp_scenarios.py — RFP-8C
14 seed_sbm_rates                 sbm_rates.py — provinsi × 3 paket × 2 tahun
15 seed_quotation_scenarios       quotation_scenarios.py — Q-8D
16 seed_lost_reason_scenarios     lost_reason_scenarios.py — CRM-8E LOST historis
17 seed_billing_milestones        billing_scenarios.py — Q-8F + BM-8F

★  FinaI sweep (mensimulasikan worker): remind_sla(h24) + crm_followup_reminders() + remind_billing_milestones(d14)
★  session.commit()
```

## Kredensial Dev (seed)

Password semua user: **`Ehos#2026!`** (must_change_password=True). User sistem `system.ingest@ehos.local` memakai hash acak tak-tertebak, `is_active=False`.

| Email | Role | Scope |
|---|---|---|
| `root.admin@ehos.local` | ROOT_ADMIN | global (sovereign) |
| `corp.exec@ehos.local` | CORP_EXEC | global |
| `corp.auditor@ehos.local` | CORP_AUDITOR | global QA |
| `rom.jawa@ehos.local` | REGIONAL_ROM | region (hotel CWS, SBAI) |
| `gm.cws@ehos.local` | HOTEL_GM | CWS |
| `gm.sqyo@ehos.local` | HOTEL_GM | SQYO |
| `gm.cluster@ehos.local` | HOTEL_GM | **cluster** SBAI + ZHBA (1 user, 2 hotel — H2) |
| `hod.hk.cws@ehos.local` | HOTEL_HOD_TECH | CWS |
| `hod.kfb.cws@ehos.local` | HOTEL_HOD_TECH | CWS |
| `hod.srm.cws@ehos.local` | HOTEL_HOD_TECH | CWS |
| `sales.cws@ehos.local` | HOTEL_SALES | CWS |
| `sales.tele@ehos.local` | HOTEL_SALES | semua hotel ber-lead |
| `finance.cws@ehos.local` | HOTEL_FINANCE | CWS |
| `client.public@ehos.local` | PUBLIC_CLIENT | — |

## Profil Data Master (sumber Excel)

- **9 brand** (SBN, GSB, SBO, SBX, ZST, SBC, SBR, SBH, SBRD) + alias label ("Hotel Ciputra"→SBN, "Swiss-Belvillas"/"MAUA"→SBR).
- **29 provinsi** · **14 region** (BALI, JAKARTA, BATAM, SUMBAGBEL, NTB, JATENG, JABAR, KALIMANTAN, PAPUA, BANTEN, JATIM, NTT, MALUKU, SULAWESI).
- **Hotel:** ~106 hotel dari `crm/Master Data Hotel.xlsx` (`md_hotel`), geo POINT SRID 4326 (+fallback koordinat SBAY/SCTH/SBKB), status Active→ACTIVE / Inactive→TERMINATED, departemen per hotel (GM, HOUSEKEEPING, KITCHEN_FB, SECURITY_RISK).

## Ringkasan Per Modul

| Modul | Nama data | Idempotensi | Chaining / catatan |
|---|---|---|---|
| `master.py` | Brand/provinsi/region/hotel/departemen | UPSERT by code / `(hotel_id, code)` | openpyxl + strict fail-invalid |
| `identity.py` | `system.ingest@ehos.local` | `on_conflict_do_nothing` (email) | FK non-null pra-user |
| `rbac.py` | 9 role + 36 perm + binding | UPSERT role/perm; rebuild binding per run | is_system=True |
| `users.py` | 14 user + assignment + FK repoint | upsert email / `(user,hotel,role)`; `on_conflict_do_nothing` | repoint auditor/lead/batch FK (delete-free) |
| `checklist_bank.py` | 7 template `v2026.1` LOCKED | upsert `(dept,name,version)` / `(template,code)` / `(section,code)` | semua LOCKED + locked_at now (B3) |
| `translations.py` | ~650 baris en | `on_conflict_do_update` per 4-tuple | CURATED_EN; identity bila sudah EN |
| `audit_ingest.py` | Sesi scoring 2026 per hotel | DELETE+recreate by `(hotel,dept,date,type)` | form TRAFFIC_LIGHT / BINARY_COUNT |
| `crm_ingest.py` | `XSELL-GOV-2026-{n}` | DELETE `LIKE 'XSELL-GOV-2026%'` + `on_conflict_do_nothing` | normalisasi WON→CONFIRMED |
| `capa.py` | `SLA00001..SLA00010` | skip bila `receipt_id LIKE 'SLA%'` | origin AUDIT ke finding bebas / MANUAL; media deterministik |
| `crm_scenarios.py` | `CRM-8A-{code}-{0..6}` | upsert lead_no; aktivitas DELETE+insert | 7 varian status/followup; `refer_cross_property` service nyata |
| `crm_referrals` | `CRM-8B-REF-01/02` | hapus referral+aktivitas → re-run service | komisi Rp5jt vs tanpa komisi |
| `rfp_scenarios.py` | `RFP-8C-*` | internal `submit_rfp` (upsert ref_no, dedup lead+notif) | valid target→ASSIGNED; invalid/tanpa→NEW |
| `sbm_rates.py` | prov × 3 paket × 2 tahun | `on_conflict_do_update(prov,package,fyear)` | tier A/B/C; faktor FULLDAY 1.0/HALFDAY 0.92/FULLBOARD 1.12; 2026 ×0.95 |
| `quotation_scenarios.py` | `Q-8D-*` | `_upsert_quotation` by no | service nyata; non-DRAFT→pdf_key |
| `lost_reason_scenarios.py` | `CRM-8E-{code}-{Q}{i}` | `on_conflict_do_update(lead_no)` + rebuild aktivitas | 4 hotel × 3 kuartal = 12 LOST; created_at −75 hari |
| `billing_scenarios.py` | `Q-8F-CWS-01` + `BM-8F-*` | DELETE scoped `doc_no LIKE 'BM-8F-%'` lalu recreate | service nyata; due ≤ 12 hari (dalam window reminder 14 hari) |

## Cakupan Simulasi (H2-H3)

- **Chained FK end-to-end:** brand → hotel → region → `audit_sessions` → `findings` → `capa_tickets` → `capa_media`; CRM: `leads` → `quotations` → `billing_milestones`.
- **Variasi:** multi-status (OPEN/AWAITING_*/CLOSED), multi-SLA (24/48/168 jam), multi-prioritas, multi-escalation level (0-3), overdue mapperwakili (hampir-overdue, overdue, escalation), multi-locale (`id`/`en`), multi-unit/region, legacy 2024-26 + live 2027, tanpa target hotel (ujian empty/NEW), kondisi negatif (invalid code → NEW; email kosong → placeholder).
- **Cluster GM:** 1 user, 2 hotel (SBAI + ZHBA).
- **Origin:** AUDIT (auto-CAPA dari temuan) dan MANUAL; WHISTLEBLOWER dapat dibuat manual.

## Catatan Operasional

- Reset dev aman: `DROP SCHEMA public CASCADE` → `alembic upgrade head` → seed (test suite terpapar artefak persisten dari run sebelumnya).
- Seeder tidak menghapus data bisnis milik pengguna — reset hanya menyentuh row milik seeder (mis. `BM-8F-*`), menghormati imutabilitas finansial (PAID tidak di-delete).