# Autentikasi & RBAC

Referensi alur autentikasi, model role/permission, constraint A3-A6, dan pola isolasi tenant.

## 1. Alur Autentikasi

**Token (JWT — `app/core/security.py`):**
- **Access token** — TTL ringkas (15 mnt): dipakai tiap request sebagai `Authorization: Bearer`.
- **Refresh token** — TTL 30 hari: hanya untuk rotasi via `POST /auth/refresh`; hash-nya tersimpan di `user_sessions` sehingga bisa di-revoke saat logout.
- Payload: `{ sub, type, jti, iat, exp }` — `jti` (uuid) unik membedakan rotasi dalam detik yang sama (reuse-detection).

**Password:** argon2 (`hash_password`/`verify_password`). Semua user seeder ber-must_change_password (rotasi saat login pertama).

**Endpoint terkait:** `POST /auth/login` (public), `POST /auth/refresh` (public), `POST /auth/logout`, `GET /auth/me`, `GET /auth/hotels`, `POST /auth/switch-hotel`, `PATCH /auth/locale`.

**Login audit:** tiap percobaan login dicatat ke `login_audits` (email attempted, sukses/alasan, IP, user-agent). Alasan: `WRONG_PASSWORD`, `ACCOUNT_LOCKED`, `INACTIVE`.

> **OPEN/known:** LOCKout (5×/15 menit) bukan bagian implementasi saat ini.

**Hotelscope aktif:** user bisa punya beberapa hotel primer/skunder. `POST /auth/switch-hotel` menukar `is_primary` pada `user_hotel_assignments`.

## 2. Role & Scope Level (`roles`)

| Role | `scope_level` | Keterangan |
|---|---|---|
| `ROOT_ADMIN` | 0 | Sovereign — semua permission (A4) |
| `CORP_EXEC` | 1 | Eksekutif korporat (global) |
| `CORP_AUDITOR` | 1 | QA/audit korporat (global) |
| `REGIONAL_ROM` | 2 | Regional Operations Manager (scope region) |
| `HOTEL_GM` | 3 | General Manager (scope hotel) |
| `HOTEL_HOD_TECH` | 4 | HOD teknis (Housekeeping/Kitchen/Security) |
| `HOTEL_SALES` | 4 | Sales hotel |
| `HOTEL_FINANCE` | 4 | Finance hotel |
| `PUBLIC_CLIENT` | 5 | Klien publik (RFP) |

`scope_level` makin kecil = makin luas. Relasi: `users` 1—N `user_roles` N—1 `roles`; assignment hotel via `user_hotel_assignments`, region via `user_region_assignments`.

## 3. Matriks Permission (36 kode)

`ROOT_ADMIN` menerima **semua** permission. Tabel berikut menampilkan korporat; role unit (REGIONAL_ROM/HOTEL_GM/HOTEL_HOD_TECH/HOTEL_SALES/HOTEL_FINANCE) dan PUBLIC_CLIENT diisi melalui editor permission per role (`PUT /roles/{role_id}/permissions`, khusus ROOT_ADMIN).

| Kode permission | ROOT | CORP_EXEC | CORP_AUDITOR |
|---|---|---|---|
| user:manage:global | ✓ | | |
| user:manage:hotel | ✓ | | |
| user:read:global | ✓ | ✓ | ✓ |
| user:read:region | ✓ | | |
| user:read:hotel | ✓ | | |
| rbac:manage | ✓ | | |
| master:read | ✓ | ✓ | ✓ |
| master:write | ✓ | | ✓ |
| checklist:read | ✓ | | ✓ |
| checklist:write | ✓ | | ✓ |
| audit:read:global | ✓ | ✓ | ✓ |
| audit:read:region | ✓ | | |
| audit:read:hotel | ✓ | | |
| audit:run:hotel | ✓ | | |
| capa:read:global | ✓ | ✓ | ✓ |
| capa:read:hotel | ✓ | | |
| capa:manage:hotel | ✓ | | |
| capa:resolve:hotel | ✓ | | |
| capa:approve | ✓ | | ✓ |
| crm:read | ✓ | ✓ | |
| crm:manage | ✓ | | |
| billing:manage | ✓ | ✓ | |
| sbm:read | ✓ | ✓ | |
| sbm:write | ✓ | ✓ | |
| rfp:submit | ✓ | | |
| analytics:read:global | ✓ | ✓ | ✓ |
| analytics:read:region | ✓ | | |
| analytics:read:hotel | ✓ | | |
| audit_log:read | ✓ | ✓ | |
| ingest:run | ✓ | | |
| notifications:read | ✓ | ✓ | ✓ |
| notifications:deliver | ✓ | ✓ | |
| translations:read | ✓ | ✓ | ✓ |
| translations:write | ✓ | | ✓ |
| profile:manage | ✓ | ✓ | ✓ |

Sumber: `app/seed/rbac.py` (seeder meng-*upsert* role & permission + rebuild binding role-permission per run — idempoten, H4).

## 4. Constraint A3 & A4 (delegated admin & sovereign)

**A3 — Delegated admin scope (hotel-scoped admin):**
- Tidak boleh membuat/mengelola role corporate/GM (`FORBIDDEN_FOR_DELEGATE`) → 403.
- Operasi user dibatasi ke hotel dalam scope delegasi → 403 bila hotel target di luar.
- Tidak bisa menonaktifkan akun sendiri.

**A4 — ROOT_ADMIN sovereign:**
- `PUT /roles/{role_id}/permissions` hanya ROOT_ADMIN (`user:manage:global`).
- Permission/hak ROOT_ADMIN tidak bisa diubah oleh siapa pun (403).

## 5. Pola Isolasi Tenant (A5)

Empat pola yang dipakai konsisten di endpoint (implementasi: `app/api/security_helpers.py`):

1. **Global override** — user dengan `*:read:global` / `*:approve` melewati seluruh filter hotel/region.
2. **Region scope** — via `user_region_assignments` → semua hotel di region tersebut.
3. **Hotel scope** — via `user_hotel_assignments` → hanya hotel yang ditugaskan.
4. **Zero-assignment = korporat** — `allowed_hotel_ids()` mengembalikan setet kosong untuk ROOT_ADMIN/CORP_EXEC/CORP_AUDITOR; pemanggil memperlakukan "global".

**Aturan list tanpa filter hotel:** daftar global (mis. list audit session / list CAPA) **wajib** permission global eksplisit (`audit:read:global`, `capa:read:global`) — bukan sekadar scope kosong.

**Pemetaan guard per modul:**

| Modul | Guard baca | Guard tulis/aksi |
|---|---|---|
| Users | `user:read:global|region|hotel` | `user:manage:global|hotel` + A3 |
| Master | — | `master:write` |
| Checklist | `checklist:read` | `checklist:write` |
| Audit | `audit:read:global|region|hotel` | `audit:run:hotel` (publish: `audit:read:global`) |
| CAPA | `capa:read:global|hotel` + scope | `capa:manage:hotel` / `capa:resolve:hotel` / `capa:approve` |
| CRM | `crm:read` + scope | `crm:manage` + scope |
| Billing | `crm:read` + scope | `billing:manage` + scope |
| SBM | `crm:read` (list) | `sbm:write` + zero-assignment (korporat) |
| Translations | `translations:read` | `translations:write` |
| Notifications (worker) | inbox = CurrentUser | `notifications:deliver` |

## 6. Tabel Terkait

- `users`, `roles`, `permissions`, `roles_permissions`, `user_roles`, `user_hotel_assignments`, `user_region_assignments`, `user_sessions`, `login_audits` — lihat [DATABASE.md](./DATABASE.md#domain-1--users--rbac-9-tabel).