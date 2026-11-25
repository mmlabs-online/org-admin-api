## Attempt 1 — 2026-11-25

### Files touched
| File | Required because |
|---|---|
| `app/core/text.py` | `trimmed`, `DisplayName` moved here; `collapse_spaces`, `reject_blank_lookalikes`, `cap_combining_marks`, `UserDisplayName` (plan) |
| `app/core/config.py`, `.env.example`, `app/core/rate_limit.py` | 4 `profile_*` settings, keys, `"profile"` scope (Q8) |
| `migrations/versions/20261125_d4a8e2c6f175_usr33_users_display_name_length.py` | data step + `ck_users_display_name_length` (R2); down_revision `c91e4b7a3d58` |
| `app/modules/users/models.py` | CHECK in `__table_args__` (alembic check) |
| `app/modules/users/schemas.py` | `UserUpdate` (replaces `UserStatusUpdate`), `DisplayNameSet` |
| `app/modules/users/repository.py` | `set_display_name` (strict `updated_at` bump) |
| `app/modules/users/service.py` | `update`, `set_display_name` (G5, audit, log) |
| `app/modules/users/profile_service.py` (new) | self-service rules |
| `app/modules/users/auth_router.py` (new), `app/api/router.py` | `POST /auth/profile` |
| `app/modules/users/router.py` | PATCH takes `UserUpdate` |
| `app/modules/audit/actions.py` | `USER_DISPLAY_NAME_CHANGED` |
| `app/modules/usage/schemas.py` | import `DisplayName`/`trimmed` from core; local copies deleted |
| `docs/spec-v1.md`, `docs/identity-architecture.md`, `docs/adr/0005-access-token-signing.md`, `docs/consumers.md`, `CLAUDE.md` | plan docs list (D40) |
| tests: `test_text.py`, `test_auth_profile.py` (new), `test_users_admin.py`, `test_users_model.py`, `test_auth_token.py`, `test_route_auth_classes.py`, `test_audit_coverage.py` | plan test list / registries |

### Tests added / changed
- `tests/test_text.py` › `test_user_display_name_accepts|normalises|rejects`, `test_display_name_usage_unchanged`
- `tests/test_users_model.py` › `test_display_name_check_rejects_empty`
- `tests/test_auth_profile.py` › 32 cases (set/clear/no-op/bump/normalise, 422 no echo, multi-account, 401/403 set, admin app, IP and per-user 429, token claim, USR-29 member read, audit row, non-vacuous log test)
- `tests/test_users_admin.py` › staff set/clear/both/no-op/422/403/404/G5 wildcard/audit/log (11 tests) and `test_user_diff_tuple_excludes_display_name`
- `tests/test_audit_coverage.py` › `_id_profile` scenario, PATCH action set; `tests/test_route_auth_classes.py` › COOKIE entry; `tests/test_auth_token.py` › dropped `""` param.
- Log tests re-enable the module loggers (Alembic `fileConfig` disables them) and assert one captured `display_name_changed` record from the app module.
- Audit lookups filter by target id (committed rows from other tests leak into the shared DB).

### Checks (actual output summary)
- typecheck: `uv run mypy app` -> Success: no issues found in 211 source files
- format:check / lint: `ruff format --check` clean; `ruff check .` only the 13 known errors in `.github/scripts/risk_tier.py` (untouched)
- alembic: `upgrade head` / `downgrade -1` / `upgrade head` OK; `alembic check` -> No new upgrade operations detected; head `d4a8e2c6f175`
- test: full suite `4161 passed, 2 warnings in 820.93s`

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- Extract the four copies of "resolve account on device" into one helper.
- USR-32 review nits 2 and 3 (101-char API case, logger re-enable) in `test_auth_token.py`.
- Staff UIs must show email/id next to the name (admin-portal).

### Scope self-check
- Could the diff be smaller? No.
- Diff size: ~+460 / -46 lines tracked, plus 3 new source files and 1 new test file.
