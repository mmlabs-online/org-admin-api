## Attempt 1 — 2026-11-17
Verdict: PASS

### Checks (run by reviewer)
- `service postgresql start` then `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff "All checks passed!", format "279 files already formatted", mypy "Success: no issues found in 127 source files", pytest "2125 passed, 1 warning in 176.50s" (the warning is the existing forkpty DeprecationWarning). Exit 0. The known flake did not fail. This matches impl-log.md (2125 passed).
- `alembic upgrade head && alembic check`: "No new upgrade operations detected." No migration in the diff, and none is needed (no schema change).

### Acceptance criteria
- [x] AC1: app credential accepted only on the manifest route and only for its own app. Evidence: `tests/test_app_credential_auth.py` `test_valid_credential_200`, `test_all_401_answers_identical` (11 cases, identical body, `WWW-Authenticate: Bearer realm="app"`, `details is None`), `test_wrong_app_403_and_denied_row` (403 `app_credential_mismatch`, one `denied` row, actor `app:A/<key_id>`, B unchanged), `test_wrong_app_unknown_path_app_same_403`, `test_disabled_app_403`, `test_disabled_between_auth_and_lock_403`. Route class: `tests/test_route_auth_classes.py` `test_app_credential_allow_list_is_exact` (dependency appears on exactly one route), `test_admin_jwt_on_manifest_401`, `test_credential_on_bearer_routes_401` (all BEARER and SELF_SCOPED routes), `test_manifest_no_cors`, `test_openapi_bearer_scheme` (`appCredential`, no `x-permission`). All pass.
- [x] AC2: manifest shape, `<app>:` namespace only (422), `scope` required on org apps (422). Evidence: `app/modules/rbac/schemas.py` (`ManifestPermission/Role/Member/Put`, `extra="forbid"`, limits); `app/modules/rbac/manifest.py` `_shape_errors` and the mode-dependent checks; tests `test_namespace_422_permissions/_roles/_member`, `test_validation_reasons` (parametrized), `test_extra_field_is_validation_error`, `test_org_app_scope_required_422`, `test_limits_422`.
- [x] AC3: idempotent push, new permissions added, member rebuilt via `_build_for_app` unless overridden, 409 on `is_admin`/scope flips, removed permissions reported and still held. Evidence: `test_push_twice_is_idempotent`, `test_new_permission_reaches_member`, `test_override_narrows_never_widens`, `test_rebuild_readds_staff_removed_only_when_creating`, `test_flip_is_409_is_admin`, `test_flip_is_409_scope`, `test_requires_staff_409`, `test_not_in_manifest_reported_and_still_held`, `test_extra_roles_created_and_additive`, `test_role_not_manifest_managed_409` (system/default/owner), `test_member_is_org_owner_409`, `test_never_touches_app_fields`.
- [x] AC4: the role catalog ETag changes when catalog-visible content changes. Evidence: `test_etag_changes_on_member_reaching_addition`, `test_etag_changes_on_new_role`, `test_etag_unchanged_on_repush_admin_only_and_description` (304 on re-push).
- [x] AC5: one audit row per changing push and none for a no-op. Evidence: `test_changing_push_writes_one_row` (system actor label, target app, allowed `changes` keys, no raw value or secret in any column), `test_noop_writes_no_row`, `tests/test_audit_coverage.py::test_app_credential_routes_are_audited` (422 writes none).
- [x] AC6: `docs/auth-integration.md` covers the format, rules, the deploy-time push (0600 header file, `curl -f`, deploy job only, push before rollout), responses and ETag behaviour. It is linked from `docs/consumers.md`, `docs/deploy.md` and `docs/identity-architecture.md` §7.
- [x] AC7: required tests are present and green (namespace, idempotency, is_admin excluded from member (`test_is_admin_excluded_from_member`), pushing twice never adds `scope = org` to member (`test_org_app_push_twice_never_adds_org_scope_to_member`), ETag, wrong app → 403, route-class tests).
- [x] AC8 (appsec carry-overs): `hmac.compare_digest` at `app/modules/app_credentials/service.py:158` (`test_secret_compared_with_compare_digest`); single 401; rate limit before auth (`test_rate_limit_before_auth`: 401, 401, 429, then 429 with a valid credential); `last_used_at` only on success (`test_last_used_at_set_on_success_only`); schema-invalid body without a credential → 401 (`test_schema_invalid_body_without_credential_is_401`); `test_no_secret_in_logs`.
- [x] AC9: the `docs/deploy.md` issue snippet has no unchecked pipeline (`curl -o "$resp"` and python are chained with `&&`), includes the `[ -s "$cred" ]` guard, removes both files on failure with an exit 1 and a revoke message, and has the orphan `key_id` revoke line. Avoiding `pipefail` is the plan's justified deviation.
- [x] AC10: `make check` is green (see above).

### Issues
None blocking.

### Scope
Every changed file is in the plan's file table. The two out-of-list items, `app_credentials/errors.py` and the ADR-0016 status line, are marked plan deviations. `work/backlog.md` and `ticket.md` only carry state updates.

### Rules R1–R11
No violations found.
- R1: the router only calls `ManifestService.sync` (`manifest_router.py`). The dependency only calls `AppCredentialService.authenticate`. Services raise only `AppError` subclasses. The repository holds the SQL (`get_with_app`, `touch_last_used`, `roles_holding`).
- R2: no schema change, and `alembic check` is clean.
- R3: class 4 is on exactly one route, pinned by tests. There is no `current_admin` on that route, no CORS, and the per-IP rate limit runs before auth.
- R4: a changing push writes one row in the same transaction. The wrong-app path writes a `denied` row via `record_failure`.
- R5: two optional settings in `config.py`, and their keys are in `.env.example` without values.
- R6: errors use the shared envelope.
- R9: the documented D25 exception.
- R11: only the hash is compared, with `compare_digest`. Logs carry only the reason and `key_id`.

### Nits (do not block)
- `app/modules/app_credentials/service.py:154`: an unknown `key_id` returns 401 before any hashing, while a known one hashes first. That leaves a small timing difference between "unknown key_id" and "wrong secret". `key_id` is not secret (ADR-0016), so this is acceptable.
- `app/modules/rbac/manifest.py:41-42` imports the private helpers `_set_role_permissions` and `_violates_reserved` from `rbac/service.py`. The plan specifies this reuse; making them public later would be cleaner.
- `app/modules/rbac/manifest.py:159` and `:209` call `get_role_by_key` per role twice (once in the conflict checks, once in apply). The cost is bounded by the 50-role limit.
- `tests/test_app_manifest_api.py::test_changing_push_writes_one_row` takes the secret with `raw.rsplit("_", 1)[1]`. This is only correct if the secret alphabet has no `_`. Asserting `raw not in dump` already covers the full value.

### Follow-ups (out of scope)
- Same list as plan.md / impl-log.md: `mmlabs-auth` `push_manifest` helper, credential `expires_at`/`label`, global body-size limit, per-credential rate-limit bucket, deployment-repo workflow step, `actor_kind` filter for machine rows, 05a `assert` tidy-up and 2-active concurrency test, spec §5.3 one-line addition (owner item 3).
