# consult-api-platform-engineer — USR-07

## Findings
- `app/cli.py` handles one subcommand. `_parse_email` builds the whole parser, and `USAGE` and the exit codes (0, 1, 2, 3, 64) are fixed for grant-super-admin. The pattern to copy: a quiet argparse `_Parser`, a CLI-only engine with `hide_parameters`, `_Fail` for fixed messages, and a catch-all that never prints `str(exc)`. Results come back as plain dataclasses (`BootstrapResult`), so the CLI never touches the ORM.
- `AppService.create` does not lock the app row. A concurrent insert raises `IntegrityError`, which becomes a 409. It already builds `<key>:member` through `_build_for_app`.
- `AppService.update` locks the app and writes only the fields that are set, with no write when nothing changes.
- `PermissionService.create` locks the app and checks that the key prefix is the app key. `<app>:*` is forced to `is_admin`.
- `build_default_role` locks the app and makes the member role equal every permission with `is_admin=false`, removing any others. It sets `default_role_id` only when it is NULL.
- `AppCreate` and `AppUpdate` already validate names (`AppName`: NFC, length, Unicode categories) and origins (`normalize_origins`). Origin error messages never echo the submitted value.
- CORS caching: `mark_apps_changed` only refreshes the origin cache in the same process. The CLI's origin changes reach the API through the 60 s TTL.

## Recommendations
1. **Business logic in a service (R1, AC2).** Add `AppService.register(...)` to `rbac/service.py`, returning a frozen `RegisterResult` like `BootstrapResult`. Steps:
   - refuse key `admin` (409)
   - `lock_app(key)`: if missing, `create` (defaults open/none); otherwise `update` with only the fields explicitly passed
   - for each permission not already present, `PermissionService.create` with `is_admin=False`
   - `build_default_role`
   - return the outcome (created, updated or unchanged), origins, member permissions, and lists of what was added and removed
2. **Validation.** Build `AppCreate`/`AppUpdate` from the CLI arguments and catch `ValidationError`. Print one fixed message per field (`--name`, `--origin`, `--permission`) and exit 64. Never print the pydantic error, because it contains `input_value`.
3. **Update semantics on re-run.**
   - Origins are replaced, not merged: the command states the full set. Require at least one `--origin`.
   - `--join-policy` and `--org-mode` use argparse `default=None`. They are applied on create, and on update only when passed, so a re-run never silently resets `approval` to `open`.
   - Status is never changed. If the app is `disabled`, print a warning.
   - Permissions are additive only. Never delete them or flip `is_admin`.
4. **Exit codes.** 0 for created, updated or unchanged; 64 for usage or validation errors; 1 for conflict, config, `DB_ECHO` or unexpected errors. Possibly 4 for the `admin` app (see open questions). Keep grant-super-admin's `USAGE` and its tests unchanged.
5. **Output.** Lines for `created|updated|unchanged app <key>`, name, status, join_policy, org_mode, one `origin:` line each, `member role:` with sorted permissions, `+`/`-` lines for changes, and "CORS applies within 60 s; loopback ignored in production".
6. **Make target.** Use `make register-app ARGS='…'`, since names with spaces and repeated flags don't fit cleanly into make variables.
7. **Tests** (in-process `amain` with `_scope(db_session)`):
   - create, then a re-run reports unchanged with no `updated_at` bump
   - origins replaced
   - origin rejected (exit 64, value not echoed)
   - `admin` refused
   - permission ends up in the member role; `<app>:*` and is_admin permissions are excluded
   - wrong prefix gives exit 64 or 1
   - passing no policy flag keeps an existing `approval`
   - DB error gives exit 1 with no leak
   - lock is taken on update
   - subprocess usage check gives exit 64
8. **Size: M.** Roughly 120 lines of CLI code, 60 of service code, 300 of tests, plus the runbook entry.

## Risks
- **Re-running undoes manual edits.** `build_default_role` removes anything added to the member role by hand (D9). Acceptable while the CLI is the only writer, but the runbook should say so.
- **Dropped origins.** Replacing origins can drop one by accident; the `-` lines in the output reduce this.
- **Changing `org_mode`** on an app that already has members is risky.
- **Repeated build on create.** The member role is built twice on the create path (inside `create`, then again after permissions). Harmless, but it does the work twice.

## Open questions
- If `register-app` is re-run against a disabled app, should it refuse (exit 1) or warn and exit 0?
- Should `--org-mode` be refused on update, so that changing it waits for the admin API?
- Should `admin` get its own exit code (4) or the generic 1?
- Should an `--admin-permission` flag exist now, or wait for ADM-002?
