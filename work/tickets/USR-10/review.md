## Attempt 1
Verdict: PASS

Date: 2026-11-17. Diff: `git diff origin/main...HEAD` (HEAD 8945528; code in 94a0e2e).

### Checks (run by reviewer)
- `service postgresql start`, then `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, exit 0:
  - ruff check: All checks passed!
  - ruff format --check: 312 files already formatted
  - mypy app: Success: no issues found in 146 source files
  - pytest: 2319 passed, 1 warning (the existing pty forkpty DeprecationWarning) in 217.48s. `test_purge_batches_share_cutoff` did not flake, so no rerun was needed.
- `alembic upgrade head && alembic check`: "No new upgrade operations detected." There is no schema change, as the plan says.
- These results match the impl-log (same pass count and tool output), so I found no mismatch.

### Acceptance criteria
- [x] AC1: Bearer list with session id, device last-seen, IP and UA, returned as `Page[T]`. Evidence: `GET /{user_id}/sessions` at `app/modules/users/router.py:100`, `no-store` at `:107`. Repository `list_active_for_user` uses the same predicates as `_usable`. Tests: `test_list_returns_active_sessions`, `test_list_item_has_exact_keys`, `test_list_orders_and_paginates`, `test_list_excludes_inactive_sessions`, `test_list_unknown_user_404` and `test_list_bad_uuid_422`, all passing.
- [x] AC2: revoke one and revoke all, leaving shared devices' other accounts alone. Evidence: `router.py:134` and `:149`. The repository UPDATEs touch `account_sessions` only and are scoped by `user_id`. Tests: `test_revoke_one_revokes_only_that_session`, `test_revoke_all_revokes_every_device` (device rows untouched) and `test_revoke_all_shared_device_keeps_other_user` (B's `/auth/token` gives 200, device `revoked_at` stays NULL).
- [x] AC3: `admin:*` on both revokes and on the list (owner, 2026-11-17), and self gives 403 (OQ7). Evidence: `_sessions_route` → `require_permission("admin:*")`, and the BEARER map at `tests/test_route_auth_classes.py:81-83`. Tests: `test_routes_require_admin_wildcard`, `test_list_self_403` and `test_service_privileged_target_requires_wildcard` (the G5 check, defence in depth).
- [x] AC4: an audit row in the same transaction holding only ids and counts, with denied attempts following ADM-004b. Evidence: `staff_service.py` `_record` and `_lock_and_guard` (`record_denied` inside `except DENIED_ERRORS`). `SelfSessionChangeForbiddenError` is in `DENIED_ERRORS`. Tests: the AUDITED harness (`test_success_writes_one_row`, `test_failure_writes_no_row`, `test_no_secret_or_email_in_any_row` on both DELETE routes, count 25), `test_session_self_revoke_writes_denied_row`, `test_session_revoke_noops_write_no_row`, `test_revoke_audit_changes_exact_keys` (no target IP or UA in `changes`) and `test_revoke_all_audit_caps_ids`.
- [x] AC5: the tests cover list, revoke one and revoke all, another user's session (404), 401/403, the audit rows, and `/auth/token` returning 401 `session_invalid` after a revoke. Evidence: the tests above, plus `test_revoke_one_other_users_session_404`, `test_token_after_revoke_one_is_session_invalid`, `test_token_after_revoke_all_is_session_invalid`, and the 401 sweeps in `tests/test_route_auth_classes.py` (`session_id` was added to `_fill`).
- [x] AC6, reworded per the orchestrator's note: the docs say already-issued tokens, admin tokens included, stay valid until `exp`, at most `ACCESS_TOKEN_TTL_SECONDS` (12 h) plus 30 s. Evidence: the route descriptions (`router.py:65` `_REVOKE_LIMITS`), the §4 row and S8 in `docs/identity-architecture.md`, the "Lost or shared device" bullet in `docs/deploy.md`, and the §5.10 note in `docs/spec-v1.md`. The new text does not contain "15 min".

### Issues
None.

### Rules R1–R11
No violations found.
- R1: the router only maps HTTP and builds the schema, the service holds the guards and audit, and SQL stays in the repository. The service raises `AppError` subclasses only.
- R2: no schema change, and `alembic check` is clean.
- R3: class 1 with a declared `admin:*` permission on each route.
- R4: success rows are written in the request transaction, and denied rows go through `record_denied`.
- R5: no new config. The routes reuse `device_session_idle_days` and `device_session_max_days`.
- R6/R9: 204 on delete, 404/403/422 use the error envelope, paths are nested plural, the list returns `Page[T]`, ids are UUIDs.
- R7: every changed file is in the plan's file list. The ticket and backlog state rows plus the consult, plan, impl-log and security-review files are workflow artifacts.
- R8: `make check` is green and the new tests are present.
- R11: no hashes or `device_session_id` in responses, and no IP or UA in logs or in audit `changes`.

### Nits (do not block)
- `docs/identity-architecture.md:191`: the parenthesis after the self-guard codes still reads "nobody changes their own admin roles or status". It could add "or sessions". That line is also over the wrap width used elsewhere in the file.
- `docs/spec-v1.md:108`: "Whether that is acceptable for admin tokens: §8 OQ8." is from before this change and OQ8 is now answered. Optional cleanup.
- `test_list_returns_active_sessions` keys on `ip`, and `None` is one of the keys. That works, but it would break if the factory starts setting an IP.
- The appsec notes in `security-review.md` also apply here: G5 runs without the admin-app lock (unreachable while the routes need `admin:*`), revoke-one accepts an idle- or max-age-expired session that the list hides, and `revoke_all_accounts_of_user_ids` duplicates `revoke_all_accounts_of_user`.

### Follow-ups (out of scope)
- A `sid` claim and a `tokens_valid_after` check on the admin per-request reload, so that a staff revoke ends a live admin token (ADR-0005 amendment risk 4).
- `?include_revoked=` on the list (spec §5.10 "Default: non-revoked only").
- A concurrency test for revoke-all against login.
- Cleanup of stale `account_sessions` rows behind expired devices, and merging the two revoke-all repository methods.
- Spec Q6 (IP/UA as PII, truncation, read-audit of who viewed sessions).
- If the list or revoke permission is ever lowered below `admin:*`, take `lock_app("admin")` before the user lock for G5.
