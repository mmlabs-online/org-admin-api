# Review — ADM-004

## Attempt 1 — 2026-11-15
Verdict: PASS

Reviewed commit `990cfbd` ("ADM-004: audit log table, logger, admin and CLI events, read endpoint") against `origin/main`. Out of scope as agreed: identity events, denied attempts, `outcome`, retention (ADM-004b).

### Checks (run by reviewer)
- make check (`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, local Postgres): `ruff check` All checks passed!; `ruff format --check` 405 files already formatted; `mypy app` Success: no issues found in 98 source files; `pytest` **1394 passed** in 87.55s. Exit 0. The pass count matches impl-log.md (1394). The format count is 405 here and 404 in the log. Formatting is clean either way, so this does not block.
- alembic check: on `org_admin`, `upgrade head`, then `alembic check` gave "No new upgrade operations detected."
- Migration cycle (`org_admin`): `downgrade -1` (`c3f1a9d27b60 -> 5e2a9c7d1f04`) left table, function and seed permission gone (`|0|0`). `upgrade head` brought back `audit_log|2 triggers|1 permission`. `alembic check` was clean again.
- Append-only probe (psql, rolled-back transaction on `org_admin`): `UPDATE`, `DELETE` and `TRUNCATE audit_log` each raised `audit_log is append-only`. `INSERT` of a `user` row with no user id was refused by `ck_audit_log_actor_consistent`. A `request_id` with a trailing newline was refused by `ck_audit_log_request_id_format`.

### Acceptance criteria
- [x] AC1 table + migration + downgrade, no drift. Evidence: `migrations/versions/20261115_c3f1a9d27b60_create_audit_log.py:34-130` (columns, 8 CHECKs, 5 indexes, trigger, seed) and `:133-148` (reverse downgrade). The model `app/modules/audit/models.py:21-85` matches the migration. `test_audit_schema.py` passes, and so do the cycle and alembic check above.
- [x] AC2 append-only.
  - The repository exposes only `add`/`list` (`app/modules/audit/repository.py:31-63`).
  - A grep of `app/` finds no other write to `AuditLog`.
  - The triggers are at migration `:112-123`.
  - `test_update_refused` / `test_delete_refused` / `test_truncate_refused` pass, and the reviewer's psql probe confirms them.
- [x] AC3 every mutating Bearer route is mapped, writes one row on success and none on failure or no-op.
  - `tests/test_audit_coverage.py:269-271` asserts `set(AUDITED) == {BEARER non-GET}` and that there are 16 routes. `BEARER` itself is the deny-by-default map checked by `test_bearer_route_permission_map`.
  - `test_success_writes_one_row[16]`, `test_failure_writes_no_row[16]` (using `make_client`, which does not roll back), `test_denied_guard_writes_no_row` (403 `admin_wildcard_required`, 409 `last_super_admin`), `test_noop_writes_no_row` and `test_strict_rollback_leaves_no_row` all pass.
  - Call sites record after the last guard and after the write's `IntegrityError → ConflictError` block. They are on the request session (flush, no commit):
    - organizations: `organizations/service.py:40-41, 54, 61-69`
    - users: `users/service.py:61-75`, after the self, G5 and last-super-admin guards and after the session revocation
    - apps: `rbac/service.py` AppService create/update/delete
    - permissions: create, update (no-op returns first), delete
    - roles: create, update (only when the name changed), delete, `replace_permissions` (only when keys were added or removed)
    - assignments: `assign` (after `get_assignment_row`), `revoke` (after the delete)
  - Internal helpers (`_grant_locked`, `_build_for_app`, `_set_role_permissions`, `_create`, `_update`) never record.
- [x] AC4 join. `membership/service.py:99-110` records `app.joined` (actor `EndUser`, target `role_assignment`) after `_grant_locked`. The already-member path and every refusal return or raise before it. `AuditRecorder`/`LogAuditRecorder` are gone: `recorder.py` is deleted, and the remaining `grep recorder` hits are unrelated signup-hook test fakes. `test_join_writes_audit_row`, `test_join_already_member_no_audit_row` and `test_join_closed_no_audit_row` pass.
- [x] AC5 CLI rows are `system:cli`. `register` (`rbac/service.py` `if outcome != UNCHANGED` → `app.registered`, `actor=SYSTEM_CLI`, `AuditContext.NONE`), `set_admin_origins` (only when `UPDATED`), `bootstrap_super_admin` (only when `GRANTED`, via `_record_grant(..., SYSTEM_CLI, AuditContext.NONE)`) and retention (`auth/retention.py:80-87`) all write as `system:cli`. `test_cli_*` audit tests pass (created, unchanged → none, dry-run → none, already-held → none). See the judgment on deviation 3 below.
- [x] AC6 request context. `audit/deps.py:9-19` uses the same `client_ip` as the rate limiter. `AuditLogger.record` validates the IP and cuts the UA to 512 (`audit/service.py:24-30, 57, 69`). `test_row_carries_request_context` passes.
- [x] AC7 request id.
  - `app/core/request_id.py:16-35` keeps the first `x-request-id` when it fullmatches `[A-Za-z0-9._-]{1,64}` and otherwise generates a uuid4. It sets `scope["state"]` and appends to `http.response.start`. It is the outermost middleware (`main.py:59-60`).
  - The charset rules out header injection, and the DB CHECK uses the same pattern.
  - `test_request_id.py` passes: absent, 64-character, invalid ×4 (65 characters, space, `<`, tab), 404 envelope, health.
- [x] AC8 no secrets.
  - Allowlists are in `audit/diff.py:12-16`.
  - The forbidden-key guard runs before any SQL (`audit/service.py:19, 50-52`).
  - Assignment changes carry `user_id`, never the email (`rbac/service.py` `assignment_changes`).
  - `test_no_secret_or_email_in_any_row` (scans every row for `_hash`, `token`, `password`, `cookie`, `@` and every fixture email) and `test_forbidden_change_key_rejected[8]` pass.
- [x] AC9 `GET /api/v1/audit-logs`.
  - Bearer `current_admin` + `require_permission("admin:audit:read")` (`audit/router.py:18-21`).
  - Filters are typed with enums, UUIDs, the `APP_KEY_RE` pattern and `AwareDatetime` with `alias="from"`/`"to"` (`:25-33`).
  - Filters combine with AND, with `>= from` and `< to`, ordered `occurred_at DESC, id DESC`, `count(*)` + limit/offset (`audit/repository.py:36-63`).
  - `from > to` gives 422 `unprocessable` (`audit/service.py:83-90`).
  - `test_list_newest_first_paged` (includes a timestamp tie), `test_filter_*`, `test_time_range_half_open`, `test_unknown_app_key_empty_page`, `test_422_validation[9]`, `test_422_from_after_to`, `test_401_without_token` and `test_403_without_permission` pass. `test_route_auth_classes.py` has the route in `BEARER`.
- [x] AC10 permission is seeded and granted to no role. Migration `:125-130`. `test_permission_seeded_not_granted` passes.
- [x] AC11 `make check` is green (above).
- [x] `ctx` is required on mutators: every public mutator takes `ctx: AuditContext` as a keyword-only argument with no default. That covers the organization, user, app, permission, role and assignment mutators, `replace_permissions` and `join`. The CLI methods use `AuditContext.NONE` internally.

### Judgment on impl-log deviations
1. **IP validation moved into `AuditLogger.record`.** Accepted. HTTP rows get the same result, and CLI rows get the same hygiene.
2. **Forbidden-key exemption for `email_send_log` / `email_tokens`.** Accepted.
   - It is an exact-match frozenset, not a pattern.
   - The values are integer counts built only in `retention.py:84` (`change(None, c.rows)`), so no secret can travel under those keys from the current call site.
   - The exemption is global to every caller, though, so a 💭 nit below suggests a narrower shape. It does not block.
3. **`email_retention.purged` is written on every non-dry run, including zero deletions.** Accepted as compliant with the plan, with a note.
   - Plan Approach §4 explicitly says "`EmailRetentionService.run` (not a dry run) records `email_retention.purged` after its loop". The AC5 line "Unchanged runs … write none" was written for register-app and set-admin-origins, whose outcome enums have an `UNCHANGED` state. Cleanup has no such outcome, so the plan is internally inconsistent here and the implementer followed the Approach.
   - A cleanup is an operator-triggered retention action. A row saying "ran, purged 0" is useful retention evidence, and it carries no PII.
   - Not a blocker. The owner may prefer `if not dry_run and any(c.rows for c in counts)` for strict AC5 wording (see Follow-ups).

### Issues
None blocking.

### Rules R1–R11
No violations found.
- R1: only `audit/deps.py` reads the request, and services get the HTTP-free `AuditContext`.
- R2: one migration, reversible, registered in `app/db/models.py`.
- R3: the new route is Bearer with a declared permission.
- R4: implemented, and the coverage test enforces it for future routes.
- R5: no new config.
- R6: 422 envelopes.
- R7: no login/logout or denied-attempt writes. The out-of-plan files are workflow artifacts only (`work/backlog.md`, the ADM-004b ticket, consults).
- R8: tests per AC.
- R9: `Page[T]`, limit ≤ 100, UUIDs, `timestamptz`.
- R11: allowlists plus the guard, no emails.

### Nits (do not block)
- `app/modules/audit/service.py:21`: the global `_ALLOWED_KEYS` exemption could be dropped by renaming the retention summary keys so they do not match the pattern (e.g. `send_log_rows`, `email_token_rows`). Alternatively, the retention row could pass a dedicated `counts=` argument instead of `changes`.
- `app/modules/audit/service.py:24-30`: Python 3.12 `ipaddress.ip_address` accepts a scoped IPv6 such as `fe80::1%eth0`, which Postgres `inet` rejects at flush, turning the mutation into a 500. This is reachable only via a trusted proxy's `CF-Connecting-IP` or a link-local peer, so it is very unlikely. Dropping `scope_id` (or treating it as invalid) would close it.
- `app/modules/auth/retention.py:80-87`: the batches commit before the summary row, so if the audit write fails, the deletions stay without a row. This is inherent to batched deletes. One option is to record the row in the last batch's transaction.
- Most `test_failure_writes_no_row` cases fail at the first lookup (404). The "record after the IntegrityError block" ordering is shown by code review and by the 409 pre-check cases, not by a forced IntegrityError. Acceptable.

### Follow-ups (out of scope)
- Owner decision: should a zero-deletion cleanup write `email_retention.purged`? The current choice is yes, and the plan's AC5 wording should be aligned either way.
- Carried from the plan: `org_id` filter, keyset pagination, `Expose-Headers: X-Request-ID`, the runtime/migration DB-role split (deployment repo), auditing reads, a `clock_timestamp()` ordering for multi-row transactions, and the request id on Starlette 500s.
