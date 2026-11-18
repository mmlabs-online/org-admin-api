# ADM-021 — Security review

## Attempt 1

Scope: `git diff origin/main...HEAD` on branch `claude/blissful-wozniak-fx4lm9`, 4 commits. Reviewed `app/modules/audit/{models,repository,router,service}.py`, `migrations/versions/20261118_d9e4b2a61f73_adm021_audit_log_org_id_index.py`, the tests and the docs row. Severity is written as text: RED = blocker, YELLOW = should-fix, GREEN = note.

**Overall: PASS.** No blocker or should-fix security issue. Caveat on R8: `make check` was not green on the first full run. The failures look like flaky tests already in the suite, not caused by this diff (see note 7).

### Findings

**RED (blocker):** none.

**YELLOW (should-fix):** none.

**GREEN (notes):**

1. **Access control is correct.** `app/modules/audit/router.py:75-81`
   - The new `GET /audit-logs/{audit_log_id}` is behind the router-level `Depends(current_admin)` (line 18) and `require_permission("admin:audit:read")` via `_read_route`.
   - `require_permission` returns a fresh `responses` dict on every call (`app/modules/admin_auth/deps.py:116-120`), so the 404 entry does not leak into the list route's metadata.
   - The route is registered in the auth-class matrix (`tests/test_route_auth_classes.py:110`).
   - The permission check runs before the lookup. A caller without `admin:audit:read` gets 403 for any id, so there is no existence oracle. `test_get_audit_log_403_without_permission` covers both sides.
   - The path param is typed `uuid.UUID`, so malformed ids get 422 and never reach the DB.

2. **No injection or IDOR.** `app/modules/audit/repository.py:51-52, 70-79`
   - `session.get` and the `or_(and_(target_type == 'organization', target_id == :org), org_id == :org)` filter are fully parameterised ORM expressions.
   - `org_id` is UUID-validated at the router (lines 38-47).
   - Scope is global by design: `admin:audit:read` is held only via `admin:*`, and there are no org-scoped staff yet.
   - The OR is one element of the AND list, so it cannot widen the other filters. Tests confirm the combination.

3. **Data exposure is unchanged.** `app/modules/audit/router.py:81`
   - The single read uses the same `AuditLogRead` as the list (ip, user_agent, actor_label, changes), so it exposes nothing new.
   - Neither route sets `Cache-Control: no-store`, although rows carry IP and user agent (PII, spec §8 Q6). USR-10 and USR-17 set it. This was already true of the list, so it is out of scope here (follow-up).

4. **Migration is safe, with one locking side effect.** `migrations/versions/20261118_d9e4b2a61f73_adm021_audit_log_org_id_index.py:27-39`
   - Index-only change. `downgrade()` is lossless. The chain is linear (revises `c7a3e91f5d28`, single head). The model mirrors it (`models.py:70-76`).
   - A plain `CREATE INDEX` takes a SHARE lock on `audit_log` and blocks inserts while it builds. Every mutating admin action and every login writes an audit row in the same transaction (R4), so those requests stall for the build time.
   - The table is young and small, and the docstring documents the choice and points to the `CONCURRENTLY` procedure for large tables. Acceptable.

5. **No denial-of-service concern.** The OR filter is index-backed: a BitmapOr over `ix_audit_log_org_id` and `ix_audit_log_target`, asserted in `tests/test_audit_schema.py` (`test_org_filter_uses_indexes`). `limit` is still capped at 100. No new unbounded work.

6. **No secrets, config, env, dependency, Docker or CI changes.** Nothing to review under R5.

7. **`make check` was flaky (R8 caveat).**
   - First full run: 5 failed and 1 error. Failed: `test_admin_guards[role_create_admin_app-super]`, `test_audit_migrations::test_purge_migration_down_restores_original_function`, `test_entitlements_migration::test_downgrade_drops_table_and_permissions`, `test_join_requests_migration::test_downgrade_drops_table`, `test_rbac_default_role::test_set_default_role_if_null_skips_org_owner`. Error: `test_audit_identity::test_audit_add_fails_reuse_still_401_and_revoked`.
   - In isolation all of them pass, including the migration tests run together with the new `test_audit_org_index_migration.py`.
   - Second full run: 2550 passed, 1 failed (`tests/test_stats_repository.py::test_login_count_uses_action_index`, which passes on its own and asserts a planner index choice).
   - Different tests fail on each run, so this looks environmental or order-dependent, not caused by ADM-021.
   - The new `test_org_filter_uses_indexes` uses the same planner-assertion pattern.
   - Ruff, format and mypy were clean. The reviewer should confirm a green run (e.g. CI) before R8 is satisfied.

### Follow-ups (out of scope, R7)
- Add `Cache-Control: no-store` to `GET /api/v1/audit-logs` and `GET /api/v1/audit-logs/{audit_log_id}` (IP and user agent are PII, §8 Q6), to match USR-10 and USR-17.
- Make planner-dependent index tests deterministic: `ANALYZE audit_log` first, or assert "no Seq Scan" instead of naming an index (`test_login_count_uses_action_index`, `test_org_filter_uses_indexes`).
- Investigate order-dependent flakiness in the migration downgrade tests (`test_audit_migrations`, `test_entitlements_migration`, `test_join_requests_migration`) in the full suite.

**Overall: PASS** (security). Merge readiness still depends on a green `make check` or CI run (R8).

Orchestrator note: the appsec and reviewer subagents ran `make check` at the same time against the same local test database, which most likely explains the first-run failures.
