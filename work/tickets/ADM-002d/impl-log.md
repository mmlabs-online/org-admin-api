# Implementation log — ADM-002d

## Attempt 1 — 2026-11-15

### Files touched
| File | Required because |
|---|---|
| `app/core/access_token.py` | Item 1: `strict_aud: True`; docstring notes a list `aud` → `audience` |
| `tests/jwt_verifier.py` | Item 1: reference verifier mirrors `strict_aud` |
| `docs/consumers.md` | Item 1: checklist 4 (string-only `aud`), item 9 note, sketch `strict_aud` |
| `docs/identity-architecture.md` | Item 1: `mmlabs-auth` bullet |
| `migrations/versions/20261115_a7c3e5f19b42_admin_permissions_is_admin.py` (new) | Item 2: data backfill, documented no-op `downgrade()` (R2 exception), no audit row |
| `app/modules/rbac/service.py` | Item 2 (`_admin_app_permission_must_be_admin`, create forces/refuses, PATCH refuses), item 3 (drift check only `if origins`), item 8 (`org_mode` pinned), item 9 (`actor` dropped) |
| `app/modules/rbac/schemas.py` | Item 2: `PermissionCreate` docstring (optional per plan) |
| `app/modules/auth/deps.py` | Item 5: `get_mail_link_app_context` + `MailLinkAppContextDep`; `get_link_origin` depends on it |
| `app/modules/auth/router.py` | Item 5: signup, forgot, resend use `MailLinkAppContextDep`; docstrings |
| `app/core/config.py` | Item 6: `_LOOPBACK_HOSTS` + `_app_env_explicit_gate` (first model validator) |
| `docker-compose.yml` | Item 6: `api` gets `APP_ENV: local` |
| `.env.example` | Item 6: comment above `APP_ENV=` (no value change) |
| `app/modules/auth/repository.py` | Item 7: `EmailTokenRepository.invalidate_all(user_id)` |
| `app/modules/users/service.py` | Item 7: call it on DISABLED after `revoke_all_accounts_of_user` |
| `app/core/request_id.py` | Item 10: always UUID4; inbound parse, `_SAFE`, `trusted_proxy_cidrs`, `peer_is_trusted` removed |
| `app/main.py` | Item 10: `add_middleware(RequestIdMiddleware)` without kwargs |
| `docs/deploy.md` | Item 4 runbook (POSIX subshell, logout in EXIT trap, TTL note), `JWT_PUBLIC_KEYS_EXTRA` sentence; item 3 drift/break-glass wording; item 6 `APP_ENV` bullet; item 10 request-id bullet |
| `docs/adr/0008-admin-api-authentication.md` | Items 1, 2, 3, 5, 8: hardening amendment; drift bullet points to it |
| `docs/adr/0009-audit-log.md` | Item 10: generated request id amendment |
| tests (see below) | Planned tests |

### Tests added / changed
- `tests/test_access_token.py › test_list_audience_rejected[["admin"], ["stoktok","admin"]]`
- `tests/test_admin_auth.py › test_rejected_tokens_401[list_aud, list_aud_multi]` (table does not assert the log)
- `tests/test_token_verification.py › test_reference_rejects_list_audience`
- `tests/test_rbac_migrations.py › test_admin_permissions_backfill` (new file)
- `tests/test_rbac_permissions_api.py › test_create_admin_app_permission_forced_admin[omitted, null]`, `test_create_admin_app_permission_not_admin_422`, `test_patch_admin_app_permission_not_admin_422`
- `tests/test_cli_set_admin_origins.py › test_service_clear_on_drift[status_disabled, join_open, org_required, default_role_set]`, `test_service_clear_on_drift_already_empty_unchanged`, `test_service_refuses_missing_admin_on_clear`, `test_cli_clear_on_drift_exit_0` (existing `test_service_refuses_drift`, `test_cli_drift_exit_1` unchanged and green)
- `tests/test_auth_app_context.py › test_admin_context_refused_on_mail_routes[signup, forgot, resend]`, `test_admin_context_refusal_matches_unknown_app`, `test_admin_context_refused_even_if_link_origin_overridden`
- `tests/test_config.py › test_app_env_required_for_remote_db[db, 10.0.0.5, localhost+db]`, `test_app_env_socket_dsn_fails_closed`, `test_app_env_default_ok_for_loopback[localhost, 127.0.0.1, [::1]]`, `test_app_env_explicit_ok_for_remote_db[local, test, production]`, `test_app_env_from_env_counts_as_explicit`, `test_app_env_empty_counts_as_unset`, `test_app_env_error_does_not_echo_dsn`
- `tests/test_users_admin.py › test_disable_invalidates_email_tokens`, `test_disable_leaves_other_users_tokens`
- `tests/test_rbac_apps_api.py › test_admin_app_locked_fields_409[org_mode required]` (new param), `test_admin_app_same_value_patch_ok` (body + `org_mode: none`)
- `tests/test_rbac_default_role.py › test_build_default_role_has_no_actor_param`
- `tests/test_request_id.py`: rewritten — kept `test_generated_when_absent`; new `test_inbound_ignored[trusted, untrusted]`, `test_same_inbound_gives_distinct_ids`; flipped `test_echoed_on_error_response`, `test_echoed_on_health`; deleted `test_valid_inbound_kept`, `test_invalid_inbound_replaced`, `test_inbound_kept_from_trusted_peer`, `test_inbound_ignored_from_untrusted_peer` (folded)
- `tests/test_audit_coverage.py › test_row_carries_request_context`, `tests/test_auth_join.py › test_join_writes_audit_row`: flipped (stored == echoed, ≠ inbound, UUID4)

Net +35 tests (1587 → 1622).

### Checks (actual output summary)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff "All checks passed!", "454 files already formatted", mypy "Success: no issues found in 100 source files", pytest "1622 passed, 1 warning" (pre-existing forkpty DeprecationWarning).
- `alembic upgrade head` (e91b6c3d58a2 → a7c3e5f19b42), `alembic check`: "No new upgrade operations detected." `alembic downgrade -1` → `e91b6c3d58a2`; `alembic upgrade head` → `a7c3e5f19b42 (head)`; `alembic check` clean again.
- Runbook block (extracted from `docs/deploy.md`): `sh -n` OK, `dash -n` OK; shellcheck not installed. Dry run under `sh` with stub `curl`/`stty` on PATH: the token only reached curl through `-H @file`, the EXIT trap called `/auth/logout`, and all temp files were removed. The python3 JSON line escapes `"` and `\` correctly.
- One-off sanity check (temporary test file, deleted): without the disable, the issued reset/verify tokens redeem successfully, so `test_disable_invalidates_email_tokens` really tests the invalidation.

### Out-of-plan files
- `work/tickets/ADM-002d/plan.md` — whitespace only (3 blank lines inside two Python snippets). ruff 0.17 formats Python code blocks in Markdown, and `ruff format --check .` (part of `make check`) failed on the plan as committed. Without the change, `make check` is red.

### Deviations
- Item 6 socket DSN case: `PostgresDsn` already rejects an empty host (`postgresql+asyncpg://u:p@/x?host=/run/pg`) in field validation, so this case can never reach the gate. It still fails closed, but not with the "APP_ENV must be set" message. It is tested separately as `test_app_env_socket_dsn_fails_closed` (any `ValidationError`) and is not a param of `test_app_env_required_for_remote_db`.
- `PostgresDsn.hosts()` returns `[::1]` (bracketed) for IPv6; it is covered by the loopback set.

### Follow-ups (noticed, not done)
- Exclude `work/**/*.md` (or all Markdown) from `ruff format`, or have the planner run ruff on plan snippets: Python blocks in ticket docs can break `make check`.
- `.env.example` holds values (`APP_ENV=local`, `APP_DEBUG=true`, …), although R5 says "keys without values". This predates the ticket.
- The PR body should note the visible behaviour change: an inbound `X-Request-ID` is no longer echoed.

### Scope self-check
- Could the diff be smaller? No.
- Diff size: production `app/` +92 / −41 (incl. ~20 docstring lines); migration +43; tests +515 / −36 (incl. the new 90-line `test_rbac_migrations.py`); docs/config +94 / −36.
