# Review — ADM-004b

## Attempt 1 — 2026-11-15
Verdict: PASS

### Checks (run by reviewer)
- make check (`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, HEAD c16f677): ruff `All checks passed!`; format `417 files already formatted`; mypy `Success: no issues found in 98 source files`; pytest `1464 passed in 102.33s`. Exit 0. Matches impl-log (1464 passed; the 416 → 417 format count is only the files present at run time).
- alembic: `upgrade head` → `downgrade -1` (af3eb84d2d5e → c3f1a9d27b60) → `upgrade head` all OK; `alembic check`: `No new upgrade operations detected.`; `current` = `af3eb84d2d5e (head)`.
- Hand check of what alembic cannot see (local `org_admin`): `ck_audit_log_outcome_valid CHECK (outcome = ANY ('success','failure','denied'))`, default `'success'::text`, `ix_audit_log_outcome (outcome, occurred_at, id) WHERE outcome = ANY ('failure','denied')`. These match `app/modules/audit/models.py:44,70-76,86` and the migration `20261115_af3eb84d2d5e_add_audit_log_outcome.py:27-42`. The downgrade drops the index, then the column (`:45-48`).

### Acceptance criteria
- [x] AC1: one row per covered identity event, none for 429, Turnstile or reserve-None. Evidence: `tests/test_audit_identity.py` login success/failed (unknown, wrong pw, disabled), `test_login_refusals_write_no_row[blocked|turnstile_required|turnstile_failed|reserve_none]`, logout one/unknown/all, reuse cookie/login/already-revoked, reset, verify/idempotent/bad token. `tests/test_audit_coverage.py::test_every_mutating_identity_route_is_classified` and `test_identity_success_writes_one_row[*]`. Code: the failure row is written only after Argon2 (`app/modules/auth/service.py:168-196`). Phase 1, Turnstile and reserve-None raise earlier and write nothing (`:146-161`). Logout rows depend on `revoked is not None` / `user_id is not None` (`:236-258`). Reuse rows depend on `revoked is not None` (`app/modules/sessions/service.py:104-105`). Verify writes only inside the `email_verified_at is None` branch (`email_verification.py:113-120`).
- [x] AC2: denied attempts are recorded with `outcome=denied` and the HTTP error is unchanged. Evidence: `tests/test_audit_denied.py::test_denied_writes_one_row` covers 16 sites and checks status, code, details, envelope keys, action, actor, target, app_key, org_id and exact `changes`. Also `test_denied_leaves_state_unchanged`, `test_plain_permission_403_writes_no_row`, `test_non_guard_409_writes_no_row`, the inverted `test_audit_coverage.py::test_denied_guard_writes_row`, and `test_audit_logger.py::test_record_denied_skips_system_actor`. Call sites match the plan's table exactly: `rbac/service.py:343,678,738,789,850,884,919,989,1110,1262` and `users/service.py:70`. Each is an explicit `try/except DENIED_ERRORS → record_denied → raise` in a public mutator, never in `_guard_privileged` / `_grant_locked` / `_update` / `_create`. `DENIED_ERRORS` is defined at `app/core/errors.py:84-89`.
- [x] AC3: an audit write failure on a failure path never gives a 500. Evidence: `test_audit_add_fails_denied_still_403` (add fault, same body and message), `test_audit_commit_fails_denied_still_409` (commit fault), `test_audit_add_fails_login_still_401` (same body as the baseline 401, reservation counter = 1, no email in the log), `test_audit_add_fails_reuse_still_401_and_revoked` (401 `session_invalid`; device and account `revoked_at` re-read and set). Units: `test_record_failure_swallows_and_logs`, `test_record_failure_cancelled_propagates`, and `test_record_in_savepoint_keeps_outer_work` (flush-then-fail: the savepoint is rolled back, outer work kept). The success-path atomic behaviour inside login is pinned by `test_reuse_during_login_audit_failure_is_500` (plan A5).
- [x] AC4: no secret or unknown email in any row. Evidence: `test_no_secret_in_identity_rows` checks emails, `@`, the 3 passwords, both cookies, verify/reset tokens and both email HMACs against `changes`, `actor_label`, `user_agent` and `request_id` for all 6 event kinds.
- [x] AC5: `outcome` column and filter. Evidence: `test_audit_schema.py::test_outcome_default_and_check`, `test_outcome_partial_index_used`; `test_audit_logs_api.py::test_outcome_field_present`, `test_filter_by_outcome`, `test_invalid_outcome_422`; `router.py:31,42`; `repository.py:63-64`; `schemas.py:23`.
- [x] AC6: carried hardening. Evidence:
  - Key exemption: `_check_keys` exempts only when `action is EMAIL_RETENTION_PURGED`, the key is in `_ALLOWED_KEYS`, and the value is `{from: None, to: int (not bool)}` (`audit/service.py:55-74`). Tested by `test_allowed_keys_only_for_retention_ints`, which rejects the wrong action, str, bool, a non-summary and a flat value.
  - Scoped IPv6 returns `None`, IPv4-mapped becomes IPv4 (`:35-47`). Tests: `test_scoped_ipv6_dropped[2]`, `test_ipv4_mapped_normalised`.
  - C0 and DEL are stripped, then the UA is cut to 512, and an empty result becomes `None` (`:28,50-53`). Tests: `test_user_agent_control_chars_stripped[3]`, `test_denied_row_with_nul_user_agent`.
  - X-Request-ID is kept only from a trusted peer (`app/core/request_id.py:31-35`; `main.py:60`). Tests: `test_request_id.py::test_inbound_ignored_from_untrusted_peer`, `test_inbound_kept_from_trusted_peer`. Docs state it is a correlation id, not evidence (ADR-0009 amendment, deploy.md).
- [x] AC7: `make check` green (above).

### Mechanism checks requested by the orchestrator
- **`record_failure` takes primitives only and rolls back first only where that is safe.** `audit/service.py:147-182` builds the row before the rollback, then runs rollback → add → commit. It catches `Exception` only, so `CancelledError` propagates, and it never re-raises. At every call site the arguments are evaluated before the call: `user.id` (`auth/service.py:184`), `role.key`, `ra.org_id`, `app.key`, sorted permission keys. Login is safe: the reservation is committed at `:165` and only the `FOR SHARE` read is dropped. Denied sites are safe: no write precedes a guard (below). `record_failure` is never used on the reuse path.
- **Reuse uses a savepoint before the revoke commit.** `sessions/service.py:103-107`: `revoke_device`, then `record_in_savepoint` (`begin_nested`, `audit/service.py:223`), then `repo.commit()`.
- **Reuse inside login is atomic.** `during_login=True` → plain `record` (`sessions/service.py:121-124`), inside the login transaction.
- **Nothing before a guard is committed or undone unexpectedly.** I traced every listed site for writes before its guard:
  - `AppService._update` (`rbac/service.py:360-376`): `setattr` happens only after all checks.
  - `PermissionService._create` (`:693-704`), `PermissionService.update` (`:725-748`), `PermissionService.delete`, and `RoleService.create/update/delete` (`:844-925`): only `lock_app` (a `FOR NO KEY UPDATE` read), reads and pure computation before the guard.
  - `RoleService.replace_permissions` (`:959-1003`): reads only before the guard.
  - `AssignmentService._grant_locked` (`:1186-1201`): validation reads before `_guard_privileged`. `revoke`: reads only.
  - `UserService.set_status` (`users/service.py:46-75`): `lock_app` and `get_for_update` are locks only, and the status is set after the guard.
  - `current_admin` / `require_permission` write nothing (`grep` of `app/modules/admin_auth`, `app/api/deps.py`).
  - The other callers (CLI `bootstrap_super_admin`, membership join, `register`) use the private helpers (`_grant_locked`, `_create`), so they never reach `record_denied`. The SYSTEM_CLI path returns without a rollback (`audit/service.py:197`).
- **Identity events only when something changed.** `revoke_device` returns `None` when the device UPDATE matched nothing (`sessions/repository.py:99-101`). `revoke_account` uses `RETURNING user_id` (`:192-197`). Verify is idempotent. `test_reuse_already_revoked_no_second_row` and `test_logout_unknown_account_no_row` prove the negative cases.
- **Production line overrun.** `git diff --numstat` shows app +615 and migration +48 (+663 / −124). The impl-log counts +622. Either way it is well over the ~470 estimate. The growth is the 11 explicit `try/except` blocks the plan requires, formatted one argument per line (rbac +134, users +30), plus the shared `_build` / `_check_keys` helpers and docstrings (audit/service +196). I found no feature beyond the plan, so the overrun is accepted.
- **3 out-of-plan test edits.** `tests/test_login_throttle.py`, `test_auth_login.py`, `test_auth_cors.py`: each change only copies `user.email` (or `login_body(user)`) before a failed login, because `record_failure`'s rollback now expires ORM objects. No assertion changed. This is the same harness consequence the plan already allows for `test_admin_guards.py`, and `test_auth_login*.py` was pre-authorised "only if they break". Accepted.

### Issues
None blocking.

### Rules R1–R11
No violations found.
- R1: the session passthroughs (`rollback` / `commit` / `begin_nested`) live in `AuditRepository` (`repository.py:38-45`), and services take `AuditContext`, not `Request`.
- R2: one reversible migration, no drift.
- R3: no new routes.
- R4: success rows are written in the same transaction.
- R5: no new config.
- R6: error bodies are unchanged (asserted).
- R11: `audit_write_failed` logs the action and outcome only. No email, HMAC or token is in rows.

### Nits (do not block)
- `tests/test_audit_identity.py:236` asserts `status_code in {403, 429}` for all four refusals. Pinning the exact status and code per case (429 `rate_limited`, 403 `turnstile_required` / `turnstile_failed`) would be stricter.
- `tests/test_audit_identity.py:451` injects the fault in `add` before any SQL. The DB-level case (flush, then failure, inside the savepoint) is covered only by the unit test `test_record_in_savepoint_keeps_outer_work`.
- `app/core/client_ip.py:10-16`: `peer_is_trusted` parses the CIDRs on every request. This is a documented deviation and harmless at this volume.
- `tests/test_audit_denied.py:411`: `rows[-1]` relies on no audit rows existing before the request (UUIDv4 ids share one `occurred_at` inside the test transaction). It holds today because the factories write no audit rows.
- impl-log line counts (+622) differ slightly from numstat (+663).

### Follow-ups (out of scope)
- ADM-004c retention purge, plus the owner decisions from spec Q6.
- From the plan: `auth.signed_up`; audit reads of the audit log; the runtime DB role split; an alert on failure/denied volume; a request id derived from `CF-Ray`; read-time IP masking before widening `admin:audit:read`; an admin rate limiter.
- The "fixation" `revoke_device` in `start_for_login` (`sessions/service.py:141`) is not audited (an empty device). Consider whether it should be.
