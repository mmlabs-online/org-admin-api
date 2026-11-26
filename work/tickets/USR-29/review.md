# Review — USR-29

## Attempt 1 — 2026-11-21
Verdict: PASS

Reviewed commit 9c4956b (branch `claude/festive-volta-rikup7`) against `origin/main`. Batch bookkeeping under `work/` and the USR-30 consults/plan are out of scope for this review.

### Checks (run by reviewer)
- `uv run ruff check app tests migrations`: All checks passed. `ruff format --check app tests migrations`: 398 files already formatted. (`make check` lint fails only on the untouched kit file `.github/scripts/risk_tier.py`, which is pre-existing.)
- `uv run mypy app`: Success, no issues found in 201 source files.
- `uv run alembic heads`: `a4c81e6d27f3 (head)`. `uv run alembic check`: No new upgrade operations detected.
- Full `uv run pytest -q`: **3811 passed, 1 warning in 761.82s**. This matches impl-log.
- Migration round-trip on the test DB: `tests/test_app_credential_scope_migration.py::test_org_members_scope_round_trip` ran in the full suite (downgrade to `911eb99ddf8b`, then upgrade head) and passed.

### Acceptance criteria
- [x] AC1 members, paging, stable order: `tests/test_app_org_members.py:71` `test_lists_members_one_row_per_user`, `:102` `test_total_counts_distinct_users`, `:114` `test_paging_is_stable_by_user_id`, `:130` `test_limit_over_100_is_422`. Query: `app/modules/rbac/repository.py` `page_app_org_members` (one aggregate row per user, `ORDER BY User.id`, explicit columns).
- [x] AC2 mismatch, scope and 401s: `:238` `test_other_app_credential_403_mismatch_audited`, `:254` `test_manifest_and_releases_keys_403_scope`, `:270` `test_disabled_app_403`. The registry entry in `tests/test_route_auth_classes.py` (`APP_CREDENTIAL[("GET", ORG_MEMBERS)]`, `lock is False`) runs the parametrized admin-JWT, deployer-key, no-key and no-CORS 401 tests.
- [x] AC3 membership rules: `:137` `test_user_without_key_role_not_listed`, `:157` `test_expired_assignment_ignored`, `:174` `test_disabled_user_listed_minimised`. Nulling for disabled users: `app/modules/organizations/app_service.py` (items loop).
- [x] AC4 one 404 and org_mode 422: `:191` `test_unknown_unrelated_suspended_org_same_404` compares the three bodies byte for byte. Also `:212` `test_org_with_only_expired_key_roles_is_404` and `:229` `test_org_mode_none_422`. One code path: `RbacRepository.org_related_to_app`.
- [x] AC5 audit, logs, no-store: `:340` `test_success_writes_one_audit_row_counts_only`, `:363` `test_404_writes_one_denied_row`, `:373` `test_no_email_in_logs`, `:408` `test_cache_control_no_store`. Also `tests/test_audit_coverage.py` `test_org_members_read_route_is_audited`.
- [x] AC6 filters and docs: `:279` `test_status_filter`, `:293` `test_status_filter_invalid_422`, `:300` `test_updated_since_user_change_and_new_grant`, `:328` `test_updated_since_requires_timezone_422_no_echo`. The `docs/consumers.md:215` subsection is present.
- [x] AC7 runtime path: `app/modules/app_credentials/service.py` `authenticate` (the `RUNTIME_SCOPES` branch returns before `confirm`). `deps.py` `RequireAppCredential.__init__` refuses `lock=True`. Tests: `test_runtime_scope_takes_no_app_lock`, `test_runtime_touch_throttled_60s`, `test_revoked_runtime_key_401`, `test_require_app_credential_refuses_lock_for_runtime_scope`, `:417` per-IP 429 before auth, `:429` per-key 429 with a second key still allowed, `test_issue_org_members_scope`, `test_org_members_scope_refused_for_admin_app`, and `tests/test_rate_limit.py` subject-limit tests.
- [x] AC8 migration: `migrations/versions/20261121_a4c81e6d27f3_usr29_org_members_read_scope.py` deletes first in the downgrade and uses literal scope lists. Its CHECK text is byte-identical to `app/modules/app_credentials/models.py` (`"scope IN ('manifest', 'releases', 'org-members:read')"`). The round-trip test passed and `alembic check` is clean.

### Ming verification after merge
- Issue an `org-members:read` key for `simap` and store it as `MMLABS_ORG_MEMBERS_CREDENTIAL` in simap's backend deploy env only. Then call the route once from the simap backend.
- Run `alembic upgrade head` on production (deploy step).

### Issues
None.

### Rules (CLAUDE.md)
No violations found.
- R1: the router calls only the rate limiter and the service. The service is HTTP-free and raises `AppError`s. The repository holds the SQL.
- R2: the migration has a working downgrade, and the model is already registered.
- R3: the CLAUDE.md R3 (a) text, ADR-0025 and the registry are updated. Ming approved this at the gate.
- R4: one row per call, plus denied rows.
- R5: four config keys, and `.env.example` lists them without values.
- R6: 404 uses `NotFoundError`; 422 and 429 use the envelope.
- R7: every changed product file is in the plan's file list.
- R9: `Page[T]`, `limit` ≤ 100, UUID, and the `apps/{key}` exception D18.
- R11: no emails in logs or audit rows (tested).

### Blockers from review agents
None.

### Nits (do not block)
- `app/modules/organizations/app_service.py` imports the private `_org_not_supported` from `auth_service` (deviation recorded in impl-log). A later cleanup could make it a public helper or an error class.
- `app_service.py`: the `app is None` branch maps to 422, but it is unreachable after `authenticate`. It is harmless.
- On the 404 path, `record_failure` rolls back that request's throttled `last_used_at` touch. This is accepted in the plan's Assumptions and in ADR-0025.

### Follow-ups (out of scope)
- From the plan: pending invitations in this read; a `name` token claim; an alert on many distinct `org_id`s per key; admin-pan offering the scope; tombstones for removal sync.

### Review agents (attempt 1)
- appsec-engineer: `Verdict: PASS` — no 🔴, no 🟡; 4 💭 notes (see `security-review.md`). No blockers from review agents.
