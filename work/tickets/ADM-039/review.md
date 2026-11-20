## Attempt 1 — 2026-11-20
Verdict: PASS

Reviewed product commit 269a82a (diff `origin/main...HEAD`), branch `claude/festive-volta-rikup7`.

### Checks (run by reviewer)
- `make typecheck` — exit 0, "Success: no issues found in 189 source files".
- `make test` (full suite, local Postgres) — exit 0, "3317 passed, 1 warning in 492.16s" (the warning is the existing `pty.forkpty` DeprecationWarning).
- `uv run ruff check . --exclude .github/scripts` — "All checks passed!" (the unexcluded failure in kit-managed `.github/scripts/risk_tier.py` is pre-existing, not in this diff).
- `uv run ruff format --check app tests migrations` — "364 files already formatted".
- `uv run alembic check` — "No new upgrade operations detected." (before and after the round trip).
- `uv run alembic heads` — single head `8c2f6a1d4e93`.
- Migration round trip on `org_admin`: `downgrade -1` (8c2f6a1d4e93 -> 5b7d1e9c3a42) left `to_regclass('usage_metrics')` NULL and 0 `admin:usage:%` permissions; `upgrade head` restored the table and 2 `is_admin` permissions.
- Determinism: the new tests (`test_usage_periods.py`, `test_usage_schemas.py`, `test_usage_metrics_api.py`, `test_usage_metrics_schema_db.py`, `test_usage_metrics_migration.py`, the usage cases in `test_audit_coverage.py`) run 3 times in a row: "112 passed" each time. Registry files plus the API file run together: "466 passed". Test keys come from a counter, there is no randomness, and audit rows are compared without depending on order.
- impl-log claims (ruff, format, mypy, alembic, 3317 passed) match my runs.

### Acceptance criteria
- [x] 1 CRUD list/detail/create/PATCH(+archive); 401/403/404/409/412/422 — evidence: `tests/test_usage_metrics_api.py` (`test_routes_401_without_token`, `test_reads_403_without_read`, `test_writes_403_with_read_only`, `test_create_duplicate_key_409`, `test_create_archived_key_still_409`, `test_create_concurrent_duplicate_409`, `test_create_422_cases`, `test_patch_422_cases`, `test_patch_key_or_unit_422`, `test_unknown_app_404_on_every_route`, `test_detail_other_app_metric_404`, `test_patch_if_match_stale_412`, `test_list_pagination_order_and_bounds`, …) all green.
- [x] 2 Audit row on every change, same transaction, with `reason` — evidence: `app/modules/usage/service.py` `create`/`update` call `AuditLogger.record` in the request session; `test_create_writes_audit_row_with_reason`, `test_patch_writes_updated_row`, `test_archive_and_restore_rows`, `test_noop_patch_writes_nothing`; `tests/test_audit_coverage.py` with 2 new `AUDITED` entries (`len == 32`, recounted from 30 after the merged tickets; the plan's "27 -> 29" was out of date, and impl-log records the recount) green.
- [x] 3 `x-permission` on every route — evidence: `tests/test_route_auth_classes.py` gets 4 `BEARER` entries (`read` on GETs, `write` on POST/PATCH); `tests/test_openapi_contract.py` `_EXPECTED_ERRORS` gets 4 entries; `test_openapi_if_match_etag_declared` green.
- [x] 4 `admin` app refused — evidence: `service.py` `_app` raises `NotFoundError` before any query; `test_admin_app_404_on_every_route` green.
- [x] 5 Period helper at Sun 23:59:59 / Mon 00:00 ICT and month end — evidence: `tests/test_usage_periods.py` (11 tests, including leap February, year rollover, the 16:59/17:00 UTC crossing, naive input rejected, parametrized `[start, end)` chain) green.
- [x] 6 Permissions seeded `is_admin`, granted to no role, covered by `admin:*` — evidence: `test_rbac_schema.py` `ADMIN_LEAF_PERMISSIONS` extended; `test_usage_metrics_migration.py::test_downgrade_removes_table_and_permissions` asserts `(key, True, 0)`; `test_admin_wildcard_allows_all` green.
- [x] 7 Migration round trip; `alembic check` clean — evidence: my own downgrade/upgrade run and `alembic check` above; the migration test is green.
- [x] 8 DB constraints back the API rules — evidence: `tests/test_usage_metrics_schema_db.py` (`test_check_constraints` parametrized, `test_unique_app_key`, `test_server_defaults`, `test_app_fk_restrict`) green. The model's CHECK texts match the migration character for character (`models.py` f-strings resolve to the same regexes and `9007199254740991`).

Ming verification after merge (carry into the PR): none required. Optional: after deploy, run `docs/deploy.md` query 2a and confirm `admin:usage:read` / `admin:usage:write` are not flagged.

### Issues
None.

### Scope
Every product, doc and test file in the diff is in the plan's file list. `tests/test_route_auth_classes.py::_fill` gains `metric_id`, which belongs to the planned registry edit and is noted in impl-log. The migration chains on `5b7d1e9c3a42` instead of the planned `c4e8a2f7b916` because ADM-033 and ADM-035 merged first; plan assumption A1 expected this. The `work/backlog.md`, `work/batches/2026-11-20-1.md` and `work/tickets/ADM-035/ticket.md` changes are the orchestrator's board bookkeeping (ADM-035 merged, ADM-039 claimed), not implementer changes. Changed High paths (`migrations/**`, `app/db/models.py`) are both named in the plan, which Ming approved on 2026-11-20.

### Rules (CLAUDE.md)
No violations found.
- R1: the router uses only `UsageMetricService` and `UsageMetricView` (a plain dataclass, so no ORM object in the router); the service raises only `AppError` subclasses (`NotFoundError`, `PreconditionFailedError`, `UsageMetricExistsError(ConflictError)`); the repository contains only SQLAlchemy.
- R2: hand-written migration with names that match the model, a working `downgrade()` (lossy cascade on `role_permissions` is documented in the docstring), and the model registered in `app/db/models.py`.
- R3: class 1 (Bearer) via router `dependencies=[Depends(current_admin)]` plus `require_permission` on each route.
- R4: create/update audit in the same transaction; a no-op PATCH writes nothing.
- R5/R11: `reason` is never logged (`test_reason_not_in_logs`); log lines carry only the app key, metric key and actor id.
- R6: only the standard error envelope.
- R9: plural path, `Page[T]` via `PageDep`, UUID ids, `timestamptz`, `PATCH`.

### Blockers from review agents
None — appsec-engineer attempt 1: PASS, no 🔴 or 🟡 (security-review.md); ADM-044 must enforce the app-admin field allowlist inside the service.

### Nits (do not block)
- `app/modules/usage/periods.py:20,22,28,30` compares with `period is UsagePeriod.X`. If a caller passes the stored plain string (`UsageMetric.period` is `Mapped[str]`) and bypasses mypy, `_local_start` falls through to `day` while `_next_local` falls through to `month`, a silently wrong pair. Suggest `period = UsagePeriod(period)` at the public entry points, or `==` comparisons. No caller exists yet (ADM-042 will be the first), so this does not block.
- `UsageMetric.period` / `.status` are typed `str`, not the enums. That is fine for now, but ADM-042 will need `UsagePeriod(metric.period)` when it calls the helper.
- `service.py` `create`: any `IntegrityError` inside the savepoint maps to `usage_metric_exists`. Today the only reachable one is the `(app_id, key)` unique (FK and CHECKs are covered by the pre-loaded app and schema validation), so it is correct as is.

### Follow-ups (out of scope)
- F1 `DELETE /apps/{key}` for an app with usage metrics returns the generic 409 message; add an `app_has_usage_metrics` reason (bug reports have the same gap).
- F2 admin-pan: usage-metric screens, and render `changes.reason` in the audit view.
- F3 optional `apps.usage_default_period` (batch Q1 answered "default": no column in v1).
