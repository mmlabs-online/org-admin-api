# Review — USR-02b

## Attempt 1 — 2026-11-11
Verdict: PASS

Scope judged: slice 02b-1 only (plan.md). 02b-2 / 02b-3 are USR-02d / USR-02e and are not expected in this diff.
Base: `origin/main...HEAD` (branch `claude/blissful-wozniak-fx4lm9`, commits 287774d..a33b29c).

### Checks (run by reviewer)
- make check (ruff, format, mypy, pytest), with `APP_VERSION` / `DATABASE_URL` / `APP_ENV` unset, local Postgres started:
  - `ruff check .` → `All checks passed!`
  - `ruff format --check .` → `148 files already formatted` (impl-log says 147; the extra file is `impl-log.md` itself, written after the run. Ruff 0.17.4 also formats Python blocks in Markdown. Not a mismatch.)
  - `mypy app` → `Success: no issues found in 48 source files`
  - `pytest -q` → `242 passed in 6.21s` (matches impl-log)
- alembic check (`DATABASE_URL=…/org_admin_test uv run alembic check`) → `No new upgrade operations detected.` No migration in the diff, as planned (R2: constant only).
- `grep -n rbac app/api/router.py` → nothing. `grep -n "# R4" app/modules/rbac/service.py` → lines 42 (builder), 90 (create), 144 (update), 158 (delete).

### Acceptance criteria
- [x] 1 Flag + explicit `APP_ENV` + conditional mount — evidence: `app/core/config.py:52-53` (flag), `:71-83` (`_admin_apis_gate`, both flags, `model_fields_set`); `.env.example:18` (`ADMIN_RBAC_API_ENABLED=` no value); `app/main.py:40-41` (mount only in `create_app`); tests `test_config.py` (`test_rbac_flag_refused_in_staging_and_production`, `test_admin_flag_requires_explicit_app_env` ×2, `test_admin_flag_empty_app_env_refused` ×2, `test_no_flag_without_app_env_is_ok`, `test_flags_with_explicit_app_env_ok`), `test_rbac_apps_api.py › test_apps_api_not_mounted_by_default` (all 5 paths → 404). The existing users-flag staging/production test is still green.
- [x] 2 `UnprocessableError` + `IntegrityError` → 409 with a usable session — evidence: `app/core/errors.py:38-42`; `test_errors.py › test_unprocessable_error_envelope` (exact envelope); `test_create_app_integrity_race_is_409` (409, then a count query on the same session works); `test_build_role_insert_race_is_409`. Savepoints: `repository.py` `add_app`, `save`, `delete_app`, `add_role` all use `begin_nested()`.
- [x] 3 `permits` str guard + edge rows — evidence: `authz.py:161-168`; `test_permits` (+6 rows), `test_permits_rejects_str_granted`.
- [x] 4 `effective_roles` drops foreign prefixes — evidence: `authz.py:40-43`; `test_effective_roles_drop_foreign_prefix`.
- [x] 5 Builder refuses `admin`, insert race → 409, conditional default, idempotent `role_permissions` — evidence: `service.py:41-55` (admin refusal, `IntegrityError` → `ConflictError`), `repository.py` `set_default_role_if_null` (`UPDATE … WHERE default_role_id IS NULL`), `add_role_permissions` with `pg_insert(...).on_conflict_do_nothing()`; tests `test_build_refuses_admin_app`, `test_build_role_insert_race_is_409`, `test_build_does_not_overwrite_concurrent_default`; the older builder tests are still green.
- [x] 6 Apps endpoints per the API contract — evidence: `router.py` (5 routes; 201 POST, 204 DELETE, `Page[AppRead]`), `schemas.py` (`extra="forbid"` on `AppCreate`/`AppUpdate`), `repository.list_apps` ordered by key; tests `test_list_apps_paginated_and_sorted` (incl. `limit=101` → 422), `test_get_app`, `test_get_app_unknown_404`, `test_create_app_builds_member_role`, `test_create_app_duplicate_key_409` (incl. `admin`), `test_create_app_validation_422` (13 cases incl. `status`/`default_role_id`/`is_system`/`id` extras), `test_patch_app_fields`, `test_patch_app_rejects_key_and_unknown_fields`, `test_patch_app_null_only_for_default_role` (5), `test_patch_unknown_app_404`, `test_delete_app`, `test_delete_unknown_app_404`, `test_app_read_has_exact_keys`.
- [x] 7 `default_role_id` unknown / foreign / `is_admin` → 422 `unprocessable` — evidence: `service.py` `AppService.update` (`get_role(app.id, …)`, `role_has_admin_permission`), details `{field, reason}` with no values; tests `test_patch_default_role_unknown_or_foreign_422` (asserts exact details), `test_patch_default_role_with_admin_permission_422` (row unchanged), `test_patch_default_role_set_and_clear`.
- [x] 8 `admin` app protections — evidence: `service.py` `update` guard runs before the 422 checks, `delete` admin + assignments guards; tests `test_admin_app_cannot_be_deleted`, `test_admin_app_locked_fields_409` (4, row unchanged), `test_admin_app_same_value_patch_ok`, `test_delete_app_with_assignments_409`. A delete race hits the `role_assignments.role_id` FK (no `ondelete`), which gives `IntegrityError` → 409.
- [x] 9 Origins normalized and restricted — evidence: `app/modules/rbac/origins.py` matches Approach §4 (printable-ASCII and length check on the raw value, strict full-match, LDH labels, IP literals other than `127.0.0.1` refused, http loopback only, port rules, generic message); tests `test_normalize_origin_accepts` (10), `test_normalize_origin_rejects` (23, exact generic message), `test_normalize_origins_dedupes_and_limits`, `test_create_app_origins_normalized`, `test_patch_app_bad_origin_422` (body does not contain `example.com`).
- [x] 10 Declared permissions, `# R4` markers, `actor` parameter — evidence: `test_rbac_routes_declare_permissions` (exact 5-route map); the `# R4` greps above; `create/update/delete(..., actor: uuid.UUID | None = None)` and `build_default_role(app_key, actor=None)`.
- [x] 11 `make check` green, no migration — see Checks.

### Issues
None blocking, none should-fix.

### Scope
Every code, test and ADR file is in the plan's Files list. The `work/` changes (backlog, ticket states, USR-02c/02d/02e/ADM-002 tickets, consult notes) are orchestrator / planner bookkeeping from the PLAN stage.
**Out-of-plan `plan.md` edit — acceptable.** It is listed under `Out-of-plan files` in impl-log. I confirmed that ruff 0.17.4 reports the plan as committed in fd37891 as "would be reformatted" (Approach §1 code block), so `make check` could not be green without the edit. Correction to the orchestrator note: the edit is not purely whitespace. Ruff also added a trailing comma, so `git diff -w` still shows it. The meaning is unchanged (it is the same dict literal). No acceptance criterion, contract or decision text changed.

### Rules R1–R11
No violations found.
- R1: the router only builds schemas and calls `AppService`. Services raise only `AppError` subclasses and map `IntegrityError` → `ConflictError`. The ORM is only in the repository; the service's `session.refresh` use matches the existing builder pattern.
- R2: no schema change; `alembic check` is clean.
- R3: class 1. The `admin:apps:read|write` permissions are declared, and the default-off flag (refused in staging/production) is the gate until ADM-002.
- R4: markers on builder, create, update and delete.
- R5: new key in `.env.example` with no value.
- R6: envelope via `AppError`; 201/204/404/409/422.
- R9: `Page[T]`, `limit` ≤ 100, PATCH; `{key}` paths are owner question Q2 (default kept).
- R11: errors never echo the submitted value (origins message is generic, the validation handler drops `input`/`ctx`, details carry only field/reason); no logging added.

### Nits (do not block)
- `service.py` `AppService.list` shadows the builtin `list` inside the class body. Python 3.12 evaluates annotations eagerly, so any later method in the class annotated `list[...]` would resolve to the method and fail at import. Consider renaming it to `list_apps` when 02b-2 adds methods.
- `test_rbac_default_role.py › test_build_role_insert_race_is_409`: `scalar(count) is not None` is a weak assertion. It does prove the session is usable (an aborted transaction would raise), but comparing to an exact count, as the API race test does, would read clearer.
- `origins.py`: hosts that WHATWG parses as IPv4 but that contain a non-digit label (e.g. `0x7f.1`, `example.123`) are accepted. This is harmless today, because a browser never sends such an `Origin`, so the stored value can never match. Consider it in the USR-03 read-time re-validation.
- `name` accepts whitespace-only strings (`min_length=1`), the same as other modules.

### Follow-ups (out of scope)
- Exclude `*.md` from `ruff format` (`[tool.ruff] extend-exclude`), or have the planner run `ruff format` on its plan. Code blocks in plans and ADRs otherwise break `make check` (this attempt's out-of-plan edit).
- The plan's follow-ups carry over unchanged: USR-03 origin binding and read-time re-validation; the ADM-002 release checklist (review `apps.origins`, `default_role_id`, `join_policy`, `org_mode` and `admin:` rows created while the flag was on; whether `admin` origins need `admin:*`); DB-level `app_key` + composite FK; raise `RuntimeError` instead of `ValidationError` (which prints `input_value`) for refused flags.
