# Security review — ADM-039 (appsec-engineer)

## Attempt 1 (commit 269a82a)

**Verdict: PASS. No 🔴 and no 🟡 in scope.** Every focus area checks out. Four 💭 notes and three follow-ups below; ADM-044 must act on two of them. This was a read-only review: I did not run `make check`, pytest or alembic, so the R8 gate still depends on the reviewer's green run.

### Findings

🟢 **R3 auth class 1 (Bearer)**
- `router.py:347-349` puts `Depends(current_admin)` on the whole router. That verifies the JWT with `audience=ADMIN_APP_KEY` (`admin_auth/deps.py:68-73`).
- Each route declares exactly one permission: GET list and GET detail need `admin:usage:read` (`router.py:376`, `:422`); POST and PATCH need `admin:usage:write` (`router.py:400`, `:442`).
- No `requires_super` is set. `admin:*` covers both permissions through `permits` (`rbac/authz.py:23`).
- The registry in `tests/test_route_auth_classes.py` lists all four routes plus the `metric_id` fill.
- An `mmak_`/`mmdk_` key is not a JWT, so it fails `verify_access_token` and gets 401. Machine keys cannot reach these routes.
- Because the permission dependency runs before the handler, a caller without the permission gets 403 before any app lookup. Unknown app names are not revealed to unauthorised callers.

🟢 **`admin` app returns 404 before any query** (`service.py:615-616`). All four routes go through `_app()`, which refuses `admin` first. Covered by `test_admin_app_404_on_every_route`.

🟢 **Permission seed migration** (`migrations/versions/20261120_8c2f6a1d4e93_adm039_usage_metrics.py`)
- Upgrade (`:835-841`): bound parameters only, `is_admin=true`, `ON CONFLICT (app_id, key) DO NOTHING`, and no `role_permissions` insert, so no role gets these permissions.
- Downgrade (`:844-850`): deletes the two keys, then drops the table. The `role_permissions` rows cascade through the composite FK (`rbac/models.py:181-185`); this loss is documented in the docstring.
- It matches the ADM-029 precedent. It chains linearly from `5b7d1e9c3a42`, and nothing else revises that revision, so there is a single head.

🟢 **Mass assignment**
- `UsageMetricCreate` and `UsageMetricUpdate` both set `extra="forbid"` (`schemas.py:522`, `:535`).
- Create does not accept `status`, so a new metric is always `active`. PATCH rejects `key` and `unit` as extra fields.
- The service copies only fields in `_FIELDS = (display_name, default_limit, period, status)` (`service.py:605`, `:698-700`).
- Explicit `null` is rejected (`schemas.py:543-548`), and a body with only `reason` gives 422 (`:550-554`).

🟢 **Reason handling**
- Validation: NFC, trimmed to 1-500 characters, no Cc/Cf/Zl/Zp/Co/Cs/Cn characters (`schemas.py:505-511`). The custom error messages never include the value, and the 422 handler drops `input` and `ctx` (`core/errors.py:194-197`).
- Storage: only in `changes.reason` of the audit rows (`service.py:666`, `:726`).
- Output: `UsageMetricRead` has no `reason` field, and the log lines carry only the app key, metric key and actor id (`service.py:668-673`, `:728-733`). Covered by `test_reason_not_in_logs`.
- CSV export JSON-encodes `changes`, so the cell starts with `{` and cannot be read as a formula.

🟢 **Audit is written in the same transaction (R4)**
- `AuditLogger.record` adds and flushes but never commits, and it raises on error (`audit/service.py:130-154`). The metric change and its audit row therefore commit or fail together.
- On create, the insert runs in a savepoint and the audit row is written after it, in the outer transaction.
- A no-op PATCH writes nothing, which matches ADR-0024 §6.

🟢 **If-Match is atomic**
- `get_for_update` takes `FOR NO KEY UPDATE` scoped to `(app_id, id)` (`repository.py:298-304`) with `populate_existing`.
- The precondition is then compared against the locked `updated_at` (`service.py:687-695`), so the check and the write happen under one row lock.
- `parse_if_match` uses strong comparison, and a malformed tag matches nothing, which gives 412.

🟢 **Input bounds**
- `default_limit` is `strict=True, ge=0, le=2**53-1` (`schemas.py:518`), backed by a DB CHECK (`models.py:139`, migration `:818-821`).
- The key and unit regexes are anchored. Pydantic uses Rust regex, where `$` is a true end of string, and Postgres `~` is not newline-sensitive by default, so a trailing newline cannot slip past either check.
- The model and migration CHECK texts match. The path `key` is validated with `APP_KEY_RE`, and list pagination uses `PageDep` (≤100).

🟢 **Period helper timezone** (`periods.py`)
- A naive datetime is refused (`:186-187`).
- Truncation happens in local ICT time, then the result converts to UTC. Asia/Ho_Chi_Minh has no DST, so aware wall-clock `timedelta` arithmetic is exact.
- The month rollover handles December, and `replace(month+1)` is safe because the day is already 1. Bounds are `[start, end)`.

💭 **ADM-044 could skip checks through the actor-generic service** (`service.py:576-579`, `:676-703`)
- This is acceptable today because the only caller is a permission-gated Bearer route, and ADR-0024 §7 records the design.
- The risk: the service applies every field in `UsageMetricUpdate`, including `status` and `display_name`, for any `Actor`. It also trusts the `app_key` it is given.
- Required when ADM-044 lands:
  - The app-admin field allowlist (`default_limit`, `period` only) must be enforced inside the service, keyed on the actor type, and must fail closed. A route-level schema alone is not enough.
  - The credential's own app must be checked against `app_key` before calling the service (403 `app_credential_mismatch`).
  - The verified `on_behalf_of` user must hold `<app>:usage:manage`.
- Suggested shape for ADM-044:
  ```python
  if not isinstance(actor, AdminPrincipal):
      disallowed = set(sent) - {"default_limit", "period", "reason"}
      if disallowed: raise PermissionDeniedError(...)
  ```

💭 **The audit key `changes.reason` now has two meanings** (`audit/service.py:221` vs `usage/service.py:666`). `record_denied` writes `changes.reason` as a machine error code; usage rows now write free-text staff input under the same key. Not exploitable: it is JSON-encoded, CSV-escaped, and only visible to audit readers. The risk is that admin-pan or other audit consumers treat it as a trusted code or render it as HTML. Plan follow-up F2 must render it as text.

💭 **Any IntegrityError on create becomes 409** (`service.py:653-657`). The catch also covers a concurrent app-delete FK violation, which is reported as `usage_metric_exists`. Misleading but harmless, since the metric is not created. Optionally, check `exc.orig` for the constraint name `uq_usage_metrics_app_id_key`.

💭 **The seed keeps a row that already exists.** If someone created `admin:usage:*` by hand with `is_admin=false` before this migration, `ON CONFLICT DO NOTHING` keeps it as is. The downgrade would then delete that hand-made row. Same as the ADM-015 and ADM-029 precedent; low risk.

### Follow-ups (out of scope)
1. ADM-044: the service-level allowlist and app/credential binding described above. This must be in that ticket's plan.
2. F2 (admin-pan): render `changes.reason` as plain text and tell free-text reasons apart from denial codes.
3. Plan F1: `DELETE /apps/{key}` with metrics currently returns a generic 409. A specific `app_has_usage_metrics` reason would help (functional, not security).
