# Security review — USR-02b

## Attempt 1 — 2026-11-11

**Verdict: no 🔴 blockers.** PASS from the security side, with 💭 notes and follow-ups. `make check` green (ruff, format, mypy, 242 tests). Every focus item was checked against `origin/main...HEAD`.

**Verified correct:**
- **`_admin_apis_gate`** (`app/core/config.py:71-83`): either flag with `APP_ENV` `staging`/`production` → `ValidationError`. A flag on without an explicit `APP_ENV` is refused — env and `.env` sources land in `model_fields_set`, and `env_ignore_empty=True` makes `APP_ENV=` "unset", so that is refused too (tests cover both flags). Case variants (`Production`, `PRODUCTION`) fail the `Literal`, so they can't slip through.
- **Router mounting:** `rbac_router` mounted only when the flag is on (`app/main.py:40-41`); not-mounted case tested (`test_apps_api_not_mounted_by_default`); every route declares `admin:apps:read` or `admin:apps:write` (R3; tested).
- **USR-02 carry-overs:** `permits()` `str` guard raises `TypeError`; the ADR-0003 §3 edge-case table is pinned in tests; `effective_roles` drops foreign prefixes like `effective_permissions`; `build_default_role` refuses `admin` (409); a member-role insert race → savepoint → 409; the conditional `UPDATE … WHERE default_role_id IS NULL` is tested for not overwriting a concurrent default; `role_permissions` insert uses `ON CONFLICT DO NOTHING`.
- **Admin-app locks:** `join_policy` ≠ `closed`, `default_role_id` ≠ NULL, `status` ≠ `active` → 409; same-value PATCH is a no-op; deleting `admin` → 409.
- **Delete with assignments:** pre-check 409; `role_assignments.role_id` is `NO ACTION`, so a concurrently created assignment makes the delete raise `IntegrityError` inside the savepoint → 409 (no silent cascade).
- **Default role checks:** unknown role or another app's role → 422 (backed by the composite FK); a role holding any `is_admin` permission → 422; `details` hold only `{field, reason}`, never the submitted value.
- **PATCH mass assignment:** `extra="forbid"` on `AppCreate` and `AppUpdate`, so `key`, `id`, `is_system`, `default_role_id` (on create) → 422; the update loop only touches `AppUpdate` fields; explicit null only for `default_role_id`.
- **Origins normalization** (`app/modules/rbac/origins.py`): printable-ASCII + length check runs before lowercasing — Unicode rejected, including the Kelvin-sign / IDN case-folding trick. Rejected: whitespace/control chars, userinfo (`@`, `\@`), percent-encoding, path/query/fragment, trailing dot / empty label, leading hyphen, `null`, `*`, `*.x`, non-loopback IPv4/IPv6 literals, port 0, leading-zero ports, ports > 65535, empty port, `http` on a non-loopback host. Default ports dropped; dedupe after normalization. The 422 path never echoes the value (generic message + handler stripping `input`/`ctx`; tested with `"example.com" not in resp.text`).
- **Savepoints / `IntegrityError` mapping:** `add_app`, `add_role`, `save`, `delete_app` use `begin_nested()`, so the request session stays usable and races → 409. On any `AppError` the request dependency rolls back the whole transaction, so a failed member-role build also rolls back the app insert.

### Findings

💭 **Hosts whose last label is numeric/hex are accepted** — `app/modules/rbac/origins.py:28-32`. **Risk:** `https://0x7f.1`, `https://a.123`, `https://1.2.3.0x4` pass (not every label is all-digits). Browsers (WHATWG "ends in a number") parse these as IPv4 or reject them and serialize `Origin` as e.g. `https://127.0.0.1`, so the stored entry can never match a real `Origin` — no bypass, just a misleading entry hiding a loopback/IP intent. **Fix:** after the label check, refuse a final label that is all digits or `0x[0-9a-f]*` unless the host is `127.0.0.1`:
```python
last = labels[-1]
if (last.isdigit() or re.fullmatch(r"0x[0-9a-f]*", last)) and host != "127.0.0.1":
    raise ValueError(_MESSAGE)
```
Add `https://0x7f.1` and `https://a.123` to the reject tests.

💭 **The admin app's `origins` and `org_mode` stay editable** — `app/modules/rbac/service.py:110-116`. **Risk:** once USR-03 echoes `apps.origins` with credentials, adding an origin to `admin` widens credentialed CORS on admin/identity surfaces; `org_mode=required` on `admin` could break admin token issuance. Not a blocker now (unauthenticated, but the gate keeps it to local/test; the plan defers it to ADM-002, comment at line 125). **Fix (ADM-002):** changing `admin` `origins`/`org_mode` requires `admin:*`; consider locking `org_mode` to `none` now alongside the other admin locks.

💭 **Unmapped FK violation in `_build_for_app`** — `app/modules/rbac/repository.py:101-106`, `service.py:58-62`. **Risk:** a permission deleted concurrently while `add_role_permissions` runs raises an FK violation outside a savepoint → 500 instead of 409. Not exploitable; error-shape consistency only (R6). **Fix:** wrap the role_permissions insert/delete in `begin_nested()` and map `IntegrityError` → `ConflictError("Role catalog changed concurrently")`.

💭 **`Settings` validators can be bypassed in code** — `app/core/config.py:13-16`. **Risk:** `Settings.model_copy(update={"admin_rbac_api_enabled": True, "app_env": "production"})`, or direct attribute assignment (`validate_assignment` off), skips `_admin_apis_gate`, and `create_app(settings)` trusts what it receives. Code/test-only, not reachable from env — low risk. **Fix (optional):** in `create_app`, re-check `if (settings.admin_users_api_enabled or settings.admin_rbac_api_enabled) and settings.app_env in {"staging", "production"}: raise RuntimeError(...)` before including routers, or set `validate_assignment=True`.

### Follow-ups
1. **USR-02d (TOCTOU on default roles):** the "default role never holds `is_admin`" check runs only when `default_role_id` is set. The role/permission edit endpoints must also refuse to add an `is_admin` permission to any role that is some app's `default_role_id` (422), in the same transaction (lock the role row, or check inside the insert) — otherwise ADR-0003 §9 can be bypassed afterwards. USR-02d grant paths also need escalation guards: granting `<app>:*`/`is_admin` permissions or assigning `admin:super-admin` requires `admin:*`, plus a last-super-admin guard.
2. **USR-03 (CORS echo):** decide whether loopback origins (`http://localhost`, `127.0.0.1`, `[::1]`) are echoed with credentials in staging/production (a local process on a user's machine would get credentialed access). Suggest ignoring loopback entries unless `APP_ENV` is `local`/`test`, or refusing them at write time there.
3. **ADM-002:** remove both flags; `require_permission` on `/apps` (`admin:apps:read`/`admin:apps:write`); `admin:*` guard for the admin app's `origins`; audit entries (R4, once ADM-004 lands) on app create/update/delete and on `build_default_role`.
4. **Deploy (`mmlabs-online/deployment`):** ensure production sets `APP_ENV=production` explicitly — the gate refuses the flags there only when `APP_ENV` says so; with `APP_ENV=local` set by mistake the flag would be accepted.
5. **USR-02e / USR-05:** any manifest/catalog path calling `build_default_role` must handle its 409 (admin app, races) without a partial commit.
