## Attempt 1

## Security review: ADM-013, attempt 1 (branch claude/blissful-wozniak-fx4lm9, `origin/main...HEAD`)

**Verdict: PASS (security).** I found no blockers and nothing that should be fixed before merge. The only open item is that `make check` did not come back green in my run, for reasons that look environmental (see the last section). R8 still needs a clean `make check` from the reviewer.

### Findings

- 💭 **The CLI dump still builds a second app from the real environment.** `app/cli.py:745` imports `app.main`, and `app/main.py:113` already has `app = create_app()` at module level, which uses `get_settings()`. `app/db/session.py:12` also calls `get_settings()` at import. So `python -m app.cli openapi` reads `.env` and the process environment, checks them, and builds a full app before it builds the `model_construct` one. You can see this in my run: `jwt_signing_key_ephemeral kid=…` was logged twice.
  - Risk: nothing leaks into the document, which comes only from the `model_construct(app_env="local")` app. The engine is created but never connects, and only key ids are logged, never key material. The only effect is that a bad environment makes the command fail, with the fixed `MSG_OPENAPI_FAILED` line. The comment "neither `.env` nor the process env may change the document" holds for the output. It does not hold for whether the command succeeds.
  - Fix: none needed. Optional follow-up below.
- 💭 **`--output` overwrites the target path.** `app/cli.py:752` uses `Path(output).write_bytes(document)`, which overwrites without asking and follows symlinks.
  - Risk: none in practice. The operator picks the path, it is a local CLI with no HTTP surface, and the content is public schema.
  - Fix: none needed.
- 💭 **The `X-Request-ID` description is accurate.** `app/core/openapi.py:15-18` says "server-generated UUID4", and `app/core/request_id.py` does generate a new `uuid4` per request without reading a client value. No header-injection or spoofing concern. The optional "request id inside the error envelope" item was not implemented (`errors.py` handlers are unchanged), so runtime error bodies are unchanged.

### Verified OK

- **The Origin check behaves the same and fails closed** (`app/modules/auth/deps.py:67-79`, `103-109`).
  - `get_app_context` now reads `request.headers.get("origin")`. Before, it used `Header()` with `str | None = None`. Both return the first `Origin` value, so the value passed to `AppContextService.check` is the same, including when a request sends `Origin` twice.
  - `check` (`app/modules/auth/service.py:83-97`) is unchanged. A missing Origin, `"null"`, an unparseable Origin or one not in the app's allow-list each give 403 `origin_not_allowed`.
  - `get_link_origin` used to declare `origin: Annotated[str, Header()]`. It now re-reads the same first header value after `MailLinkAppContextDep` has already checked it, and raises `OriginNotAllowedError` if it is missing. That is unreachable, but it fails closed rather than with a 422. The mailed link origin is the same value that was checked, so there is no gap between check and use.
  - A grep found no other `Origin` header parameters. `X-MM-App` is still declared and still checked.
  - Existing tests still cover this: `tests/test_auth_app_context.py:69` (None, "null", malformed and foreign origins → 403), `tests/test_email_verification_api.py:142-173` (signup), and `tests/test_password_reset_api.py:325-350` (forgot, with no side effects).
  - Cookie CORS (`app/core/cors.py`) is not touched.
- **No change to runtime request handling.** The `install_openapi` override (`app/core/openapi.py:47-71`) runs only when the schema is generated. The schema is deep-copied before post-processing, so FastAPI's shared response dicts are not changed.
- **The docs are still gated.** `app/main.py:49-50` still mounts `/docs` and `/openapi.json` only when `docs_enabled` is true, which means off in production unless `API_DOCS_ENABLED=true`. Replacing `app.openapi` does not add a route.
- **No information leak in the document.** I generated it with `uv run python -m app.cli openapi --output …` (exit 0) and searched it:
  - No `servers` block, no DSN, no `localhost` or `postgres`, no "secret".
  - `info` contains only the title and `0.1.0`.
  - The only `password_hash` match is the existing `UserRead` description saying it is never returned.
  - `HTTPValidationError` is gone. The only header parameters left are `X-MM-App` (optional) on the 8 cookie routes. No `origin` header parameter remains.
- **The CLI does not leak on failure** (`app/cli.py:735-760`). Every exception gives one fixed stderr line with no path and no traceback, and a test covers this (`test_cli_openapi_unwritable_path_exit_1`). Usage errors exit 64. The command needs no database and no secrets, which the subprocess test confirms with an unreachable DSN.
- **Makefile.** `make openapi` passes `$(OUT)` to `--output`. The developer sets it, and there is no new injection surface beyond normal make variable use.
- **No dependency, migration, Docker, CI or env-key changes in the diff.**

### `make check`

Lint, format, mypy and the new tests passed. Pytest gave **1733 passed, 3 failed**:
- `test_two_accounts_on_one_device`
- `test_seed_admin_app`
- `test_every_bearer_route_401_with_cookie_only[DELETE-/api/v1/apps/{key}]`

When I re-ran those three on their own, I got `DeadlockDetectedError` on the shared `org_admin_test` database. Another `make check` (the reviewer's) was running against the same database at the same time (pids 10440/10483). None of the three tests touch OpenAPI or the Origin code paths, so I believe this is contention, not the diff. I did not re-run, to avoid adding to the contention. **The reviewer must confirm a clean `make check` on its own before PASS (R8).**

### Follow-ups (out of scope)

1. Move the module-level `app = create_app()` (`app/main.py:113`) behind an ASGI factory (`uvicorn app.main:create_app --factory`) or a lazy getter. Then CLI commands and tests that import `app.main` stop building a full app from the real environment.
2. Optional, as the ticket allows: put `request_id` inside the error envelope and add it to the `ErrorBody` schema. It would help support match client errors to server logs without exposing anything sensitive.
