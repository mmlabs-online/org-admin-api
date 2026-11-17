## Attempt 1 — 2026-11-17
Verdict: PASS

### Checks (run by reviewer)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, first run: ruff clean, format clean (259 files), mypy clean (116 source files), pytest **1 failed, 1986 passed**. The failure was `tests/test_cli_audit_purge.py::test_purge_batches_share_cutoff` (`[5] == [1, 2, 2]`). Neither that file nor `app/modules/audit/purge.py` is in this diff. The test passes alone and together with `tests/test_permission_scope_migration.py` (30 passed).
- `make check`, second run: **exit 0**. ruff, format and mypy clean, `1987 passed`. So the first failure is a flaky test that already existed (see Follow-ups), not something this diff caused.
- Alembic on the dev DB: `upgrade head` → `d6f03e8b2a15 (head)`. `check` → "No new upgrade operations detected." `downgrade -1` → `c5e91d3a7b24`. `upgrade head` → `d6f03e8b2a15 (head)`. `check` → "No new upgrade operations detected."
- These results match the impl-log claims: 1987 passed, alembic clean.

### Acceptance criteria
- [x] AC1: an org token exposes org-exact roles separately, and `roles` is unchanged. Evidence: `app/modules/tokens/service.py:88-93` reuses the gate's `org_role_keys` result. Tests: `tests/test_auth_token.py::test_token_with_org_has_org_claim_and_union_roles` (`org_roles == ["calechip:org-owner"]`, `roles` still the union), `::test_org_roles_excludes_app_scope_only_role`, `::test_org_roles_role_in_both_scopes_in_both_claims`, `::test_org_roles_excludes_other_org_role` and `::test_token_without_org_app_scope_only` (no `org_roles`). The design is recorded in the new `docs/adr/0015-org-roles-claim-and-permission-scope.md`, in spec §5.8 and §5.3 (both marked "owner, 2026-11-17", matching the ticket Notes), and in ADR-0003, 0005, 0013, 0014 and identity-architecture.md.
- [x] AC2: the default-role build for org apps excludes `scope = 'org'`, and `none` apps are unchanged. Evidence: `app/modules/rbac/service.py:299-311` (`_build_for_app`, shared by `build_default_role`, app create and CLI register). Tests: `tests/test_rbac_default_role.py::test_org_app_build_excludes_org_scope`, `::test_org_app_rebuild_removes_org_scope_from_member`, `::test_none_app_build_unchanged_with_org_scope_present`, `::test_org_app_build_refused_when_member_is_owner`, and `tests/test_cli_register_app.py::test_register_org_app_does_not_widen_member`, which also covers the create path and a re-run. The other write paths are guarded too: PATCH app gives 422 `role_has_org_permissions` (`service.py:464-473`, `tests/test_rbac_apps_api.py::test_patch_default_role_with_org_permission_422`, `::test_patch_org_mode_required_default_holds_org_permission_422`). PUT role permissions gives 409 (`service.py:1186-1191`, `tests/test_rbac_roles_api.py::test_put_org_permission_on_org_app_default_role_409`, `_none_app_default_role_ok`, `_owner_role_ok`). A permission flipped app→org while the default role holds it gives 409 (`service.py:925-931`, `tests/test_rbac_permissions_api.py::test_patch_scope_to_org_held_by_default_409`). I grepped every write to `role_permissions`: only `_build_for_app` and `replace_permissions` call `_set_role_permissions`, and both are guarded.
- [x] AC3: owner≠default is enforced in three layers. Layer 1: `app/modules/rbac/repository.py:124` (`is_distinct_from`, returns bool), tested by `tests/test_rbac_default_role.py::test_set_default_role_if_null_skips_org_owner`. Layer 2: the DB CHECK `ck_apps_default_role_not_org_owner` in the model and the migration with the same text, tested by `tests/test_rbac_schema.py::test_apps_default_not_owner_check_rejects_equal`, `::test_apps_default_not_owner_check_allows_nulls` and `::test_check_texts`. Layer 3: the existing 422 `same_as_default_role`. The migration preflight aborts and never auto-fixes: `tests/test_permission_scope_migration.py::test_preflight_aborts_on_default_equals_owner`.
- [x] AC4: the guard is kept as defense in depth and restated by scope. `app/modules/invitations/service.py:146-147` now returns `default_role_org_permission`, and `roles_overlap` is removed. ADR-0014 §11 is rewritten and has a removal criterion. Tests: `tests/test_invitations_create.py::test_create_unavailable_when_default_holds_org_permission`, `::test_create_ok_when_default_shares_app_scope_permission_with_owner`, `::test_create_ok_after_build_default_role`, and `tests/test_invitations_accept.py::test_accept_unavailable_when_default_widened_not_consumed`.
- [x] AC5: `docs/consumers.md` gives the rule: authorize org data only from `org_roles` through the catalog, scope by `org`, and reject a token that has `org` but no `org_roles`. It also lists the `mmlabs-auth` follow-up, which is repeated in the impl-log follow-ups.
- [x] AC6: tests for all six named cases exist and are green (listed above). The end-to-end case is `tests/test_invitations_accept.py::test_invite_accept_token_after_build_default_role`: build → invite → accept → `org_roles == ["calechip:org-viewer"]`, with `calechip:member` only in `roles`.
- [x] Plan AC4: scope create/patch rules. Evidence: `tests/test_rbac_scopes.py` and `tests/test_rbac_permissions_api.py` (`test_create_scope_default_org_on_org_app`, `_default_app_on_none_app`, `_reserved_keys_scope_derived`, `_reserved_scope_violation_422`, `test_patch_scope_reserved_violation_422`, `_null_422`, `_to_app_on_org_app_requires_super`, `_audited`, `test_read_includes_scope`). `diff.PERMISSION` includes `scope` (R4).
- [x] Plan AC10: migration. Evidence: backfill-table, none-app, `role_permissions`-untouched and roundtrip tests in `tests/test_permission_scope_migration.py`, plus my manual round trip above.

### Issues
#### Issue 1 — 🟡 should-fix — plan test missing and not disclosed
- **Where**: `tests/test_rbac_default_role.py`
- **Expected**: plan "Tests to write" lists `test_app_create_org_mode_required_builds_scoped_member` (the `AppService._create` path through the admin API).
- **Actual**: the test does not exist, and impl-log.md says "5 new" without naming the missing one. The behaviour is still covered indirectly: `tests/test_cli_register_app.py::test_register_org_app_does_not_widen_member` creates a new `org_mode = required` app through `register` → `_create` → `_build_for_app`, and the build filter lives in the shared `_build_for_app`. So this does not block.
- **Fix instruction** (only if another attempt happens anyway): add the test. POST `/api/v1/apps` with `org_mode = "required"`, add one `scope = "app"` and one `scope = "org"` permission, rebuild, and assert that the member role holds only the `app` one. Otherwise list the omission under the impl-log's "Not done from the plan".

### Scope
Every changed production, test and doc file is in the plan's file table. `work/backlog.md`, `work/tickets/USR-06c/*` and the one Notes line in `work/tickets/USR-05/ticket.md` are orchestrator or plan-mandated files. The impl-log discloses the plan deviation of not adding the `admin:*` `Case` in `tests/test_admin_guards.py` (it is covered in `test_rbac_permissions_api.py` instead).

### Rules R1–R11
No violations found.
- R1: SQL only in `rbac/repository.py`. Rules are in `rbac/service.py` and the pure `rbac/scopes.py`. The routers are untouched, and the service raises `AppError` subclasses only.
- R2: hand-written revision `d6f03e8b2a15` with a preflight, a deterministic backfill, both CHECKs and a working `downgrade()` (lossy for `scope` only, documented). The CHECK texts in the model match the migration, and `alembic check` is clean.
- R3: no new endpoint, and no auth class changed.
- R4: scope changes appear in the permission audit diff. The CLI audit gains `org_permissions_added`. The org→app flip with `admin:*` writes a denied audit row.
- R6 and R9: existing error shapes. Additive only: one claim, one response field, two optional request fields, one CLI flag. The role catalog is unchanged.
- R11: `org_roles` is not logged, and the token log line is unchanged.

### Nits (do not block)
- `AppService.register` reports `reserved_scope` and `scope_mismatch` with field `org_permissions` / `permissions` no matter which list held the key. The CLI maps by reason, so the output is unaffected.
- The CLI path to the `member_is_org_owner` refusal runs after the permission inserts and relies on the transaction rollback. That is correct, but it is not "before the first write" as the plan's test table says.

### Follow-ups (out of scope)
- A flaky test that already existed: `tests/test_cli_audit_purge.py::test_purge_batches_share_cutoff` failed once in a full `make check` (`[5] == [1, 2, 2]` summary batches) and passed on rerun. It needs a `/bug` investigation.
- These are carried from the impl-log:
  - the `mmlabs-auth` `org_roles` accessor, `require_org_permission` and rejection of `org` without `org_roles`
  - a calechip migration note
  - refuse app-scope grants of roles that hold `org` permissions on org apps
  - retire the S1 guard
  - optionally expose `scope` in the catalog
  - a deployment-repo input for `--org-permission`
  - `not_member` for users who hold only org roles
