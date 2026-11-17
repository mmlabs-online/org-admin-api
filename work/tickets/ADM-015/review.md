# Review — ADM-015: Dashboard stats endpoint for the admin UI

## Attempt 1
Verdict: PASS

Date: 2026-11-17

### Checks (run by reviewer)
- `service postgresql start`; `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff "All checks passed!", "309 files already formatted", mypy "Success: no issues found in 144 source files", pytest "2270 passed, 1 warning in 210.07s", exit 0. The known flake `test_purge_batches_share_cutoff` did not fail. Matches impl-log (2270 passed).
- `alembic upgrade head` then `alembic check`: "No new upgrade operations detected." `alembic heads`: single head `e2b7a94d1c58`.

### Acceptance criteria
- [x] Bearer admin endpoint returning one snake_case document with users by status + unverified, orgs by status, apps by status, sign-ins 24 h (succeeded/failed from `auth.login_succeeded`/`auth.login_failed`), sign-ups per month, members per app (OQ5): evidence `app/modules/stats/router.py:36-72`, `app/modules/stats/service.py:47-71`; tests `tests/test_stats_api.py` › `test_dashboard_shape`, `test_user_counts`, `test_org_counts`, `test_app_counts`, `test_sign_ins_24h_counts`, `test_sign_ups_by_month_api`, `test_members_per_app_api`; `tests/test_rbac_member_counts.py` (5 tests: app+org scope counted once, expired excluded, disabled excluded / unverified counted, admin app counts staff, `app_ids` filter), all passed.
- [x] Single document, not `Page[T]`, recorded as an R9 exception: `test_dashboard_shape` asserts no `items/total/limit/offset`; `CLAUDE.md` R9 line appended "(…; D26)"; `docs/identity-architecture.md` §4 row and D26 in §8.
- [x] Permission follows OQ4 (`admin:stats:read`): `require_permission("admin:stats:read")` at `app/modules/stats/router.py:41`; migration `migrations/versions/20261117_e2b7a94d1c58_adm015_stats_permission.py` seeds it (`is_admin`, no role); tests `test_403_without_stats_permission` (details.required), `test_stats_permission_allows` (real minted token), `test_wildcard_allows`, `tests/test_route_auth_classes.py` BEARER entry, `tests/test_rbac_schema.py` leaf set, `tests/test_stats_migration.py` downgrade/upgrade.
- [x] Read-only, no audit row, `Cache-Control: no-store`: `router.py:44`; `test_cache_control_no_store`, `test_no_audit_row_written`; GET only, so `test_audit_coverage` needs no entry.
- [x] Tests for each counter, 401/403 and the 24 h boundary: `test_401_without_token`, `test_403_without_stats_permission`, `test_sign_ins_24h_boundary` (24 h counted inclusive, 23h59m counted, 24h1s excluded); HCM month boundary and 12-month window at the repository level (`tests/test_stats_repository.py` › `test_sign_ups_month_boundary_in_hcm`, `test_sign_ups_window_and_zero_fill`).
- [x] Query cost bounded (plan AC 6 wording): audit count is `action IN (…) AND occurred_at >= since` (`app/modules/audit/repository.py:81-89`), EXPLAIN with seqscan off uses `ix_audit_log_action` (`test_login_count_uses_action_index`); sign-up join range is sargable on `created_at` (`app/modules/stats/repository.py:11-22`); `test_dashboard_query_count` asserts 7 statements with 1 and 5 extra apps (no N+1).

### Scope
All changed code, test and doc files are in the plan's file list. `work/backlog.md`, `work/tickets/ADM-015/ticket.md`, `work/tickets/ADM-016/ticket.md`, `work/tickets/USR-10/ticket.md` changes are orchestrator commits (state change, owner answers recorded), not implementer code. No unjustified out-of-plan change.

### Issues
None.

### Rules R1–R11
No violations found. R1: router only maps the view to schemas; service has no HTTP; SQL in repositories (stats, rbac, audit). R2: data-only migration with a working downgrade (tested); no model change; `alembic check` clean. R3: class 1 Bearer with `current_admin` + `require_permission`, in the BEARER map. R4: read-only, tested. R5: no new config; time zone / month count are owner product rules as named constants. R6: errors come from the existing guards. R7: no ADM-016 field touched. R8: green `make check`; 404/validation not applicable (no input), as the plan records. R9: exception recorded (D26). R10/R11: not touched.

### Nits (do not block)
- `tests/test_stats_repository.py` › `test_login_count_uses_action_index` EXPLAINs a hand-written text mirror of the query; compiling the real `count_actions_since` statement (e.g. via a small statement builder) would stop drift. Already noted by the implementer.
- `tests/test_stats_api.py` › `test_sign_ups_by_month_api`: the 60-day user assertion (`deltas[-3] + deltas[-2] + deltas[-4] >= 1`) is loose, and the planned "gap month shows 0" is not asserted at the API level (covered by `test_sign_ups_window_and_zero_fill`).
- `tests/test_stats_api.py` › `test_org_counts`: `== 0 + 1` reads oddly; `== 1`.
- `tests/test_stats_api.py` › `test_sign_ins_24h_counts`: the plan's `auth.login_failed` row with `outcome = 'failure'` is not seeded (only anonymous actor).
- `app/modules/audit/repository.py:1-3`: docstring rewrap leaves a short dangling line.

### Follow-ups (out of scope)
- `admin:stats:read` is granted to no role: only `admin:*` holders see the dashboard until a super-admin adds it to a staff role (tell the admin-pan side; plan owner decision 2).
- `users.created_at` has no index; fine at current size, revisit if the users table grows large (§6 p95).
- `docs/spec-v1.md` §8 OQ7 is answered in `work/tickets/USR-10/ticket.md` but still unticked in the spec; tick it with USR-10.
