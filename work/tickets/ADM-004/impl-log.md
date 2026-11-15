# Implementation log — ADM-004

## Attempt 1 — 2026-11-15

### Files touched
| File | Required because |
|---|---|
| `migrations/versions/20261115_c3f1a9d27b60_create_audit_log.py` (new) | AC1/AC2/AC10, R2: table, 8 CHECKs, 5 indexes, `audit_log_immutable()` + 2 triggers (hand-written), seed `admin:audit:read`; downgrade in reverse order |
| `app/modules/audit/models.py` (new) | `AuditLog` model mirroring the migration (no `TimestampMixin`, no FKs) |
| `app/modules/audit/actions.py` (new) | `AuditAction`, `AuditTarget`, `ActorKind` |
| `app/modules/audit/context.py` (new) | HTTP-free `AuditContext` (+ `NONE`) and `EndUser` |
| `app/modules/audit/deps.py` (new) | `audit_context` / `AuditContextDep` (only routers read the request) |
| `app/modules/audit/diff.py` (new) | field allowlists, `snapshot`, `diff`, `change`, JSON coercion |
| `app/modules/audit/repository.py` (new) | `AuditRepository.add` / `list` only; `AuditLogFilter` |
| `app/modules/audit/service.py` (new) | `AuditLogger.record` (forbidden-key guard, IP validation, UA cut to 512, flush, never commits); `AuditLogService.list` (`from > to` → 422) |
| `app/modules/audit/schemas.py` (new) | `AuditLogRead` |
| `app/modules/audit/router.py` (new) | `GET /api/v1/audit-logs`, Bearer + `admin:audit:read` |
| `app/modules/audit/recorder.py` (deleted) | replaced by `AuditLogger` |
| `app/core/request_id.py` (new) | pure-ASGI `RequestIdMiddleware` (AC7) |
| `app/main.py` | add `RequestIdMiddleware` after CORS (outermost); include `audit_router` |
| `app/db/models.py` | register `AuditLog` |
| `app/modules/organizations/router.py`, `service.py` | `principal`/`ctx` on mutators; `organization.created/updated/deleted` (update no-op → no row) |
| `app/modules/users/router.py`, `service.py` | `ctx`; `user.disabled/enabled` after the write; same-status no-op → no row |
| `app/modules/rbac/router.py`, `assignments_router.py` | `ctx: AuditContextDep` passed to every mutator |
| `app/modules/rbac/service.py` | record once per public mutator after the last guard/write; private `AppService._create/_update`, `PermissionService._create` used by `register`; `app.registered` and `app.admin_origins_set` summary rows; `role_assignment.granted` (assign, bootstrap) / `.revoked`; all `# R4 … ADM-004` markers replaced |
| `app/modules/membership/service.py` | `JoinService(session)`; `join(..., ctx)` records `app.joined` (actor `EndUser`) |
| `app/modules/auth/router.py` | drop `LogAuditRecorder` / `get_audit_recorder`; `join_app` takes `ctx` |
| `app/modules/auth/retention.py` | non-dry run: one `email_retention.purged` row + commit |
| `tests/test_route_auth_classes.py` | `BEARER` gains `GET /api/v1/audit-logs` |
| `tests/test_auth_join.py` | `FakeRecorder`/`recorder` fixture and the 2 recorder tests removed; `recorder.calls` assertions now count `app.joined` rows; 3 new tests |
| `tests/test_cli_register_app.py`, `test_cli_set_admin_origins.py`, `test_cli_grant_super_admin.py`, `test_cli_cleanup.py` | audit-row tests |
| `tests/test_audit_schema.py`, `test_audit_logger.py`, `test_audit_coverage.py`, `test_audit_logs_api.py`, `test_request_id.py` (new) | AC1–AC10 |
| `docs/adr/0009-audit-log.md` (new) | decisions (Approach §8) |
| `docs/identity-architecture.md` | S8 rewritten; §4 row for `GET /api/v1/audit-logs` |
| `docs/deploy.md` | "Audit log (ADM-004)" section; the two "Until ADM-004 …" lines replaced |

### Tests added / changed
- `test_audit_schema.py` › `test_valid_row_accepted_with_defaults`, `test_update_refused`, `test_delete_refused`, `test_truncate_refused`, `test_check_action_format[4]`, `test_check_target_pair[2]`, `test_check_changes_object[4]`, `test_check_actor_consistency[7]` (also covers `actor_kind_valid`, `actor_label_length`, `app_key_format`), `test_check_request_id_format[4]`
- `test_audit_logger.py` › `test_forbidden_change_key_rejected[8]` (asserts no SQL ran), `test_values_json_coerced`, `test_empty_changes_stored_as_null`, `test_user_agent_truncated`, `test_invalid_ip_dropped[4]`
- `test_audit_coverage.py` › `test_every_mutating_bearer_route_is_mapped`, `test_success_writes_one_row[16]`, `test_failure_writes_no_row[16]`, `test_denied_guard_writes_no_row`, `test_noop_writes_no_row`, `test_strict_rollback_leaves_no_row`, `test_row_carries_request_context`, `test_no_secret_or_email_in_any_row`
- `test_audit_logs_api.py` › `test_list_newest_first_paged`, `test_filter_actor_user_id`, `test_filter_actor_kind`, `test_filter_action`, `test_filter_target`, `test_filter_app_key`, `test_time_range_half_open`, `test_unknown_app_key_empty_page`, `test_422_validation[9]` (naive `from`/`to`, unknown action/actor_kind/target_type, bad uuid, bad app_key, limit 101, offset -1), `test_422_from_after_to`, `test_401_without_token`, `test_403_without_permission`, `test_permission_seeded_not_granted`
- `test_request_id.py` › `test_generated_when_absent`, `test_valid_inbound_kept`, `test_invalid_inbound_replaced[4]`, `test_echoed_on_error_response`, `test_echoed_on_health`
- `test_auth_join.py` › `test_join_writes_audit_row`, `test_join_already_member_no_audit_row`, `test_join_closed_no_audit_row`; removed `test_join_audit_ids_only`, `test_join_default_recorder_logs_ids_only`
- `test_cli_register_app.py` › `test_register_audit_created` (also checks the update row), `test_register_audit_unchanged_none`, `test_register_audit_dry_run_none`
- `test_cli_set_admin_origins.py` › `test_set_admin_origins_audit_updated`, `test_set_admin_origins_audit_unchanged_none`, `test_set_admin_origins_audit_dry_run_none`
- `test_cli_grant_super_admin.py` › `test_grant_audit_row`, `test_already_held_no_audit_row`
- `test_cli_cleanup.py` › `test_cleanup_audit_summary`, `test_cleanup_dry_run_no_audit_row`

### Checks (actual output summary)
Run as `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` against the local Postgres (already running; nothing started).
- make check: `ruff check` All checks passed!; `ruff format --check` 404 files already formatted; `mypy app` Success: no issues found in 98 source files; `pytest` **1394 passed** in 91.65s (baseline before the change: 1274 passed)
- alembic check: `No new upgrade operations detected.` (after upgrade to head, and again after the cycle)
- migration cycle on `org_admin`: upgrade `5e2a9c7d1f04 -> c3f1a9d27b60` → downgrade `-1` (table, function and seed permission gone: `|0|0`) → upgrade head (`audit_log|2 triggers|1 permission`); the test session fixture also runs downgrade base → upgrade head on `org_admin_test`

### Out-of-plan files
None.

### Deviations from the plan
- IP validation lives in `AuditLogger.record` (not in `audit_context`), so the logger tests `test_invalid_ip_dropped` / `test_user_agent_truncated` exercise it directly and the CLI path gets the same hygiene. Same effect for HTTP rows.
- Forbidden-key guard exempts the two retention table names `email_send_log` and `email_tokens` (counts only), because the plan's cleanup row shape `{"email_send_log": …, "email_tokens": …}` would otherwise match `email|token`.
- `email_retention.purged` is written on every non-dry run, also when 0 rows were deleted (plan §4 wording; AC5 says "state-changing run" — flag for the reviewer).
- `test_422_naive_datetime`, `test_422_unknown_action`, `test_422_limit_over_100` are one parametrized `test_422_validation` (9 cases).
- `test_auth_join.py`: besides removing the fixture and the 2 recorder tests, the existing `recorder.calls` assertions were translated to `app.joined` row counts (no assertion dropped).
- `app.registered` on create stores the full allowlisted app snapshot (`{from: null, to: v}`) plus `permissions_added` / `member_permissions`; on update the name/join_policy diffs and `origins {from: removed, to: added}`.

### Follow-ups (noticed, not done)
- `occurred_at = now()` is the transaction start: rows written in one transaction (e.g. two CLI runs inside one test transaction) share a timestamp and are ordered by random UUID `id`. Fine for one-row-per-request; a `clock_timestamp()` default would give a stable order if ever needed.
- Starlette `ServerErrorMiddleware` 500s do not carry `X-Request-ID` (accepted in the plan).
- Plan follow-ups unchanged: `org_id` filter, keyset pagination, `Expose-Headers: X-Request-ID`, runtime/migration DB-role split, auditing reads.

### Scope self-check
- Could the diff be smaller? No: the rest is per-call-site wiring the coverage test requires.
- Diff size: production (app + migration) +1056 / −121 (incl. deleted `recorder.py`); tests +1103 / −106; docs +77 / −4.
