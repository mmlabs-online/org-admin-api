# Review — ADM-013

## Attempt 1 — 2026-11-16
Verdict: PASS

### Checks (run by reviewer)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, run 3 times in full:
  - Run 1: ruff, format and mypy green. pytest `2 failed, 1733 passed, 1 error` (`test_audit_identity.py::test_logout_all_row`, `::test_reuse_on_cookie_route_row` [error], `test_rbac_permissions_api.py::test_create_permission_integrity_race_is_409`).
  - Run 2: pytest `1 failed, 1735 passed` (`test_cli_audit_purge.py::test_purge_batches_share_cutoff`, the flake the implementer reported).
  - Run 3 (final): `All checks passed!`, `mypy: Success: no issues found in 102 source files`, `1736 passed`.
  - Two extra pytest-only runs on the branch: `1736 passed` both times. The failing tests pass in isolation (`62 passed`). The audit and purge failures come from tests that run alphabetically *before* the new test modules. Their symptoms (extra `App` row, a different batch split, DBAPI error) look like DB concurrency. They do not trace to the diff, whose only runtime change is how `Origin` is read. origin/main was 3/3 green on a `git archive` copy, so this is intermittent flakiness that I could not attribute to this diff. Logged as a follow-up.
- `make openapi` with `DATABASE_URL` pointing at an unreachable port (`127.0.0.1:1`), then again with `APP_NAME=zzz`: both exit 0, `cmp` reports identical bytes. Postgres was left running (the reviewer does not stop services), so the offline claim relies on the unreachable-DSN run plus `test_subprocess_dump_offline_deterministic`.
- alembic check: n/a (no schema change).

### Acceptance criteria
- [x] `ErrorEnvelope` `{error: {code, message, details}}` is referenced by every documented 4xx/5xx response, admin routers included. Evidence: `app/core/openapi.py:25-43` (global post-processing) and `app/core/errors.py:109-130`. Tests: `test_openapi_contract.py::test_error_envelope_component`, `::test_every_error_response_references_envelope` (asserts `/api/v1/users`, `/api/v1/auth/password/forgot` and `/api/v1/apps/{key}/roles` were walked) and `::test_no_5xx_documented`. In the dumped document every 4xx response points to the envelope (counts: 422×36, 403×33, 401×31, 429×11, 409×8, 404×1). Runtime agreement: `::test_real_error_bodies_match_envelope` (401/403/404/409/422/429).
- [x] `HTTPValidationError` and `ValidationError` are removed, and 422 references the envelope with `code=validation_error`. Evidence: `::test_no_fastapi_validation_schemas`, `::test_422_is_envelope_validation_error`. The dumped component list has neither name, and the forgot 422 description reads "Validation failed (code=validation_error; ...)".
- [x] `origin` is no longer declared on forgot or resend (or anywhere else), and the server still checks it. Evidence: `app/modules/auth/deps.py:67-78` and `:103-109` read it from `request.headers`. `get_link_origin` fails closed on a missing header. `::test_origin_header_not_declared` passes, and the existing origin tests pass unchanged (`test_forgot_unregistered_origin_403_no_side_effects`, `test_resend_requires_app_header_and_origin`, `test_auth_app_context.py`: 55 passed in the targeted run).
- [x] `make openapi` / `python -m app.cli openapi` writes the document offline and deterministically, with all routers mounted. Evidence: `app/cli.py:735-760` and the `Makefile` `openapi` target. Settings come from `Settings.model_construct(app_env="local")`, so env and `.env` are ignored. Tests: `test_cli_openapi.py::test_dump_deterministic_in_process`, `::test_subprocess_dump_offline_deterministic` (scrubbed env, unreachable DB, run twice, bytes equal to the in-process render), `::test_dump_ignores_env_overrides`, `::test_dump_contains_all_routers`, plus the usage (64) and failure (1) tests. Reviewer `cmp` run above.
- [x] Tests: a schema test and a determinism test exist and pass. Evidence: `tests/test_openapi_contract.py` and `tests/test_cli_openapi.py` are green.
- [x] Optional `X-Request-ID`: documented on every response as `string/uuid`. The envelope is unchanged, and the decision is recorded (plan A3). This matches `app/core/request_id.py`, where the id is always a server-generated UUID4. Evidence: `::test_x_request_id_documented_on_every_response`.

### Issues
None.

### Rules R1–R11
No violations found. R1: the deps change only alters how a header is read, with no layering change. R3/R11: the cookie-route `Origin` + `X-MM-App` check is unchanged, and the normalization and allow-list still run in `AppContextService.check`. R5: the dump reads no env or secrets. R6: strengthened (documented, with runtime unchanged). R7: every changed file is in the plan, and nothing is out-of-plan. R8: new code has tests and the final `make check` is green. R9: documentation-only change, no runtime break.

### Nits (do not block)
- `app/core/openapi.py:48-66` re-lists `get_openapi` arguments by hand. It omits `separate_input_output_schemas` and `external_docs`, which FastAPI's own `openapi()` passes. Both are at their defaults today, so the output is the same. Calling `FastAPI.openapi(app)` and deep-copying its result (the plan's alternative) would avoid drift on future FastAPI upgrades.
- `tests/test_openapi_contract.py::test_every_error_response_references_envelope` and `::test_no_5xx_documented` call `int(code)`. A future `"default"` or `"4XX"` response key would crash the test rather than fail it clearly. Production code already guards this with `isdigit()`.
- `test_cli_openapi_stdout` uses `capfd`, while the plan named `capsys`. That is correct given the write goes to `sys.stdout.buffer`.

### Follow-ups (out of scope)
- Plan F1–F5 (stable `operationId`s, `Retry-After`/`WWW-Authenticate` docs, JSON 500 handler, committed snapshot + CI drift check, `request_id` in the envelope).
- Only one 404 is documented across the API. For example, `GET/PATCH /api/v1/organizations/{id}` and `/api/v1/users/{user_id}` return 404 at runtime but do not document it. Generated clients will not see those error types. Add per-route `responses={404: ...}` in a follow-up.
- Intermittent suite flakiness under full runs: `test_cli_audit_purge.py::test_purge_batches_share_cutoff`, `test_audit_identity.py::test_logout_all_row` / `test_reuse_on_cookie_route_row`, `test_rbac_permissions_api.py::test_create_permission_integrity_race_is_409`. They pass in isolation, and the evidence suggests a DB-isolation or concurrency leak between tests. Worth a dedicated ticket.
