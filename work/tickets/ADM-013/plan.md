Status: READY

# Plan — ADM-013: OpenAPI contract that clients can generate types from

## Spec summary
- Ticket goal: "The generated OpenAPI document describes the real error shape and headers, so clients such as `admin-pan` can generate types without hand-written overrides. It can also be dumped offline as a stable snapshot."
- Spec §2 Goals: "One admin API that the mmlabs admin web UI (and internal tools) use for cross-product administration."
- CLAUDE.md R6: "**One error shape**: `{"error": {"code", "message", "details"}}` via `app/core/errors.py`."
- CLAUDE.md R9: "No breaking changes inside v1." Everything in this plan changes documentation only, plus one internal refactor of how `Origin` is read. Runtime responses do not change.
- Owner note (ticket, 2026-11-16): "`admin-pan` currently hand-writes the error type and has to cast the `origin` header, which browsers send themselves."

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Every documented 4xx/5xx response, across all routers (admin included), references an `ErrorEnvelope` schema `{error: {code, message, details}}`. | `tests/test_openapi_contract.py::test_every_error_response_references_envelope` walks every operation in `create_app().openapi()`. Every response whose status is >= 400 must have `content.application/json.schema == {"$ref": "#/components/schemas/ErrorEnvelope"}`. Spot checks: `GET /api/v1/users` (admin 401/403/422), `POST /api/v1/auth/password/forgot` (403/422/429), `GET /api/v1/apps/{key}/roles` (404/429). `test_error_envelope_component` checks the component's shape. `test_real_error_bodies_match_envelope` checks that real 401/403/404/409/422/429 bodies validate against `ErrorEnvelope`. No 5xx is documented (see Assumption A1), and `test_no_5xx_documented` pins that. |
| 2 | `HTTPValidationError` and `ValidationError` are gone, and 422 references `ErrorEnvelope` (`code=validation_error`). | `test_no_fastapi_validation_schemas`: neither name appears in `components.schemas`, and no `$ref` anywhere in the JSON ends with either name. `test_422_is_envelope_validation_error`: `POST /api/v1/organizations` has a 422 that references the envelope, and its description mentions `validation_error`. The existing `tests/test_errors.py::test_validation_error_details_have_no_input_or_ctx` confirms the runtime shape. |
| 3 | `origin` is no longer declared as a required parameter on `password/forgot` and `verify-email/resend`. The server still checks it, and the existing tests prove that. | `test_origin_header_not_declared`: no operation has a parameter with `in == "header"` and `name.lower() == "origin"`. This covers forgot, resend, signup and every cookie route. These existing tests must pass unchanged: `test_password_reset_api.py::test_forgot_unregistered_origin_403_no_side_effects` (includes `origin=None`), `test_email_verification_api.py::test_resend_requires_app_header_and_origin` (includes `origin=None`), the `test_auth_app_context.py::test_missing_or_null_origin_403` / `test_origin_of_other_app_403` / `test_origin_is_normalized`, `test_forgot_403_when_link_origin_overridden`, `test_signup_sends_verification_link_to_app_origin` and `test_forgot_link_uses_request_origin`. |
| 4 | `make openapi` / `python -m app.cli openapi` writes `create_app().openapi()` offline, with no database or secrets and all routers mounted. Output is byte-identical across runs. | Run `make openapi OUT=/tmp/a.json && make openapi OUT=/tmp/b.json && cmp /tmp/a.json /tmp/b.json`. Postgres must be stopped during this run. Tests: `test_cli_openapi.py::test_dump_deterministic_in_process`, `test_subprocess_dump_offline_deterministic` (scrubbed env, unreachable DB, run twice, `cmp`), `test_dump_ignores_env_overrides` and `test_dump_contains_all_routers`. |
| 5 | Tests: a schema test for the points above and a determinism test for the dump. | `tests/test_openapi_contract.py` and `tests/test_cli_openapi.py` exist and pass. `make check` is green. |
| 6 | Optional: document `X-Request-ID` as a response header; the envelope may carry the request id. Record the decision. | Decision: **header documentation only**. The envelope does not change (Assumption A3). `test_x_request_id_documented_on_every_response`: every response object of every operation has `headers["X-Request-ID"]` with `schema == {"type": "string", "format": "uuid"}`. |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/core/errors.py` | Add Pydantic models `ErrorBody` (`code: str`, `message: str`, `details: Any`, where `details` is required but may be null) and `ErrorEnvelope` (`error: ErrorBody`). Handlers and `_envelope()` stay unchanged. | AC1/AC2: a single source for the documented shape (R6). |
| `app/core/openapi.py` (new) | `install_openapi(app)` overrides `app.openapi` with a cached, post-processed schema. `render_openapi(app) -> bytes` returns the deterministic JSON. | AC1, AC2, AC6, plus the AC4 rendering. |
| `app/main.py` | In `create_app`, call `install_openapi(app)` after the routers are included. | Applies the global pass to every router, including future ones. |
| `app/modules/auth/deps.py` | `get_app_context` and `get_link_origin` read `Origin` through `request.headers.get("origin")` and stop declaring a `Header()` parameter. `get_link_origin` raises `OriginNotAllowedError("Origin is not allowed")` if the header is missing. That case cannot happen after `MailLinkAppContextDep`, but the code fails closed. | AC3: the doc no longer models `Origin`. Runtime checks are unchanged. |
| `app/cli.py` | Add the `openapi [--output PATH]` subcommand, `OPENAPI_USAGE`, and a module docstring bullet. Dispatch it in `amain()` before the grant-super-admin fallthrough. Imports stay lazy. | AC4. |
| `Makefile` | Add `openapi` to `.PHONY` plus the target `openapi: ## Write the OpenAPI document: make openapi [OUT=path]`, which runs `uv run python -m app.cli openapi $(if $(OUT),--output $(OUT))`. | AC4. |
| `tests/test_openapi_contract.py` (new) | The schema tests listed below. | AC1–3, AC5, AC6. |
| `tests/test_cli_openapi.py` (new) | The dump and determinism tests listed below. | AC4, AC5. |

No router files change. Existing per-route `responses=` descriptions (`_ERR`, `_ERROR_RESPONSES`, `_with_409`, `_WRITE`, `_REVOKE`) stay as they are. The global pass adds the envelope content to them.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_openapi_contract.py › test_error_envelope_component` | `components.schemas.ErrorEnvelope` requires `error`, which is a `$ref` to `ErrorBody`. `ErrorBody` requires `code`, `message` and `details`. `code` and `message` are strings. `details` is unconstrained (any JSON, nullable). |
| `› test_every_error_response_references_envelope` | AC1. Every response with status >= 400 on every operation uses the envelope `$ref` for `application/json`. Asserts at least one admin operation and one auth operation were checked, so the test cannot pass on an empty walk. |
| `› test_no_5xx_documented` | Pins Assumption A1: no response key is >= 500. |
| `› test_no_fastapi_validation_schemas` | AC2. `HTTPValidationError` and `ValidationError` are absent from `components.schemas`, and `json.dumps(schema)` contains no `/HTTPValidationError"` or `/ValidationError"` ref. |
| `› test_422_is_envelope_validation_error` | AC2. `POST /api/v1/organizations` and `POST /api/v1/auth/password/forgot` have a 422 envelope whose description contains `validation_error`. |
| `› test_origin_header_not_declared` | AC3. No `origin` header parameter appears anywhere. Explicitly checks forgot, resend and signup. |
| `› test_x_mm_app_still_documented` | Guard: `X-MM-App` is still a documented (optional) header on cookie routes, for example `POST /api/v1/auth/password/forgot`. |
| `› test_x_request_id_documented_on_every_response` | AC6, header documentation. |
| `› test_schema_generation_does_not_mutate_shared_dicts` | Generating `openapi()` on two fresh apps gives equal results. `app.modules.auth.router._ERR` and `app.modules.admin_auth.deps._ERROR_RESPONSES` have no `content` or `headers` keys afterwards, which proves deep-copy hygiene. |
| `› test_existing_extensions_preserved` | `x-permission` on admin operations and the `bearerAuth` / device-cookie security schemes are still present. This duplicates a little of `test_route_auth_classes`, but on purpose, so the post-processing pass has its own guard. |
| `› test_real_error_bodies_match_envelope` | Runtime/doc agreement. Uses the existing `client` / `admin_client` fixtures, or a minimal `FastAPI()` with `register_error_handlers` in the style of `tests/test_errors.py`. Gets a real 401 (admin route without token), 403 (cookie route without `X-MM-App`), 404 (unknown path), 409 (`ConflictError`), 422 (bad org body) and 429 (`RateLimitedError`). For each, `ErrorEnvelope.model_validate(resp.json())` must succeed and `set(resp.json()["error"]) == {"code", "message", "details"}`. |
| `tests/test_cli_openapi.py › test_dump_deterministic_in_process` | AC4. `render_openapi` on two separately created apps (from the dump's Settings) gives identical bytes, ending in `\n`. |
| `› test_cli_openapi_writes_file` | Running `amain(["openapi", "--output", str(tmp_path/"o.json")])` returns `EXIT_OK`, and the file equals `render_openapi(...)` and parses as JSON. |
| `› test_cli_openapi_stdout` | With no `--output`, stdout contains only the JSON document (`capsys`). |
| `› test_cli_openapi_usage_exit_64` | Unknown flag or missing `--output` value: prints `OPENAPI_USAGE` and returns `EXIT_USAGE`. |
| `› test_cli_openapi_unwritable_path_exit_1` | `--output` points into a non-existent directory: prints one fixed message with no traceback or path echo beyond the convention, and returns `EXIT_FAILURE`. |
| `› test_dump_ignores_env_overrides` | With `monkeypatch.setenv` for `APP_NAME`, `API_PREFIX` and `API_DOCS_ENABLED=false`, the output is unchanged. |
| `› test_dump_contains_all_routers` | The paths include `/healthz`, `/.well-known/jwks.json`, `/api/v1/auth/login`, `/api/v1/organizations`, `/api/v1/users`, `/api/v1/apps/{key}/roles` and the audit/assignments routes. |
| `› test_subprocess_dump_offline_deterministic` | AC4 "without database or secrets". Runs `sys.executable -m app.cli openapi --output <f>` twice. The env holds only `PATH`, `HOME`, `PYTHONPATH=<repo root>` and `DATABASE_URL=postgresql+asyncpg://u:p@127.0.0.1:1/none` (unreachable, loopback). `cwd=tmp_path`, so no `.env` is read. Both runs must exit 0, the files must be byte-equal, and both must equal the in-process render. |

## Approach
1. **Envelope models (R6).** In `app/core/errors.py`, add:
   ```python
   class ErrorBody(BaseModel):
       code: str
       message: str
       details: Any  # required key; list for validation_error, object or null otherwise
   class ErrorEnvelope(BaseModel):
       error: ErrorBody
   ```
   Give each model a short docstring, which becomes the schema description, and list known codes in the `code` field description (`validation_error`, `unprocessable`, `not_found`, `conflict`, `unauthorized`, `permission_denied`, `rate_limited`, ...). Runtime handlers stay as they are, so no response changes (R9).
2. **`app/core/openapi.py`.**
   - `install_openapi(app: FastAPI) -> None` defines `custom() -> dict[str, Any]`. If `app.openapi_schema` is set, it returns it. Otherwise it builds `schema = copy.deepcopy(get_openapi(title=app.title, version=app.version, routes=app.routes, ...same args FastAPI.openapi passes...))`, or equivalently calls `FastAPI.openapi(app)` and deep-copies the result. The deep copy is required because FastAPI response entries can share module-level dicts (`_ERR`, `_ERROR_RESPONSES`). It then post-processes, stores the result in `app.openapi_schema` and returns it. Install with `app.openapi = custom  # type: ignore[method-assign]`.
   - Post-processing, in this order:
     a. Generate the components from Pydantic with `ErrorEnvelope.model_json_schema(ref_template="#/components/schemas/{model}")`. Pop `$defs` into `components.schemas` (`ErrorBody`) and add `ErrorEnvelope` itself. Create `components` and `schemas` if missing.
     b. For each `paths.*.<method>.responses.<code>`: if `code` parses as an int >= 400, set `content = {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}}`. For 422, replace FastAPI's default description `"Validation Error"` with `"Validation failed (code=validation_error; also code=unprocessable for refused data)"`. Keep any other route-specific description.
     c. Delete `HTTPValidationError` and `ValidationError` from `components.schemas`. Assert, in a test rather than at runtime, that no ref to them remains.
     d. For every response object (all codes), merge `headers["X-Request-ID"] = {"description": "Server-generated request id (UUID4); quote it to support.", "schema": {"type": "string", "format": "uuid"}}` without overwriting other headers.
   - `render_openapi(app: FastAPI) -> bytes` returns `(json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")`.
   - Type everything as `dict[str, Any]` so `mypy --strict` passes.
3. **`create_app`.** Call `install_openapi(app)` after the last `include_router`. `app.openapi()` works when `openapi_url=None`, so `API_DOCS_ENABLED` has no effect on the dump. `/openapi.json`, when enabled, serves the same post-processed document because FastAPI's route calls `app.openapi()`.
4. **Remove the `Origin` declaration (AC3).** In `get_app_context`, drop the `origin` `Header()` parameter and pass `request.headers.get("origin")` to `AppContextService.check`. Header lookup is case-insensitive and returns the first value, the same as FastAPI's scalar `Header()`. Change `get_link_origin` to `(request: Request, _app: MailLinkAppContextDep) -> str`: read `request.headers.get("origin")`, raise `OriginNotAllowedError("Origin is not allowed")` if it is `None`, and otherwise return `normalize_origin(origin)`. Keep the docstring. The dependency order (`MailLinkAppContextDep` before `get_link_origin`, and the resend docstring guarantee at `router.py:315`) does not change. `X-MM-App` stays declared as an optional header (Assumption A5).
5. **CLI `openapi` (AC4).**
   - In `amain()`, add `if argv[:1] == ["openapi"]: return _openapi(argv[1:])` before the fallthrough. Make `_openapi` synchronous and call it from `amain`.
   - Parse with `_Parser`/argparse: `--output PATH`, optional. On a parse error, print `OPENAPI_USAGE` and return `EXIT_USAGE`.
   - Settings: `Settings.model_construct(app_env="local")`. That gives code defaults only and ignores `.env` and process env, so the document describes the code, not the shell. Do **not** call `_load_settings`/`get_settings`. If `model_construct` turns out to be unsuitable for `create_app` (some default needs validator coercion), fall back to `Settings(_env_file=None, app_env="local")` and record in `impl-log.md` why `test_dump_ignores_env_overrides` was adapted.
   - Do the lazy imports inside the function: `from app.main import create_app`, `from app.core.openapi import render_openapi`. Note that importing `app.main` still builds the module-level `app` from `get_settings()`, and `app.db.session` builds a lazy engine from it. Neither connects to the database, which the subprocess test proves.
   - Write bytes to `--output` (`Path.write_bytes`) or to `sys.stdout.buffer`. Stdout carries nothing else. Logs such as the ephemeral-key warning go to stderr.
   - Wrap failures in the existing convention: any `Exception` prints one fixed line (for example `MSG_OPENAPI_FAILED = "error: could not write the OpenAPI document"`) and returns `EXIT_FAILURE`, with no traceback.
6. **Makefile target** as listed in the files table. The default output is stdout and no snapshot is committed (Assumption A2).
7. Run `make check`. Expected test churn: none in existing tests. If an existing test asserted on FastAPI's default 422 or on `HTTPValidationError`, update it and record that in `impl-log.md`. A grep found none.

Rules: R1 holds (no layering change; deps only change how a header is read). R3 holds (no auth class changes; the cookie-route `Origin` check is unchanged). R5 holds (the dump reads no secrets and no env). R6 is strengthened, not changed. R7 holds (`operationId` and `Retry-After`/`WWW-Authenticate` documentation are follow-ups). R8: `make check` must be green. R9 holds (documentation-only, additive; runtime bodies are unchanged). R11 holds (Origin is still enforced before any side effect). No migration (R2 n/a).

## Specialist input used
`consult-api-platform-engineer.md`:
- **A (ErrorEnvelope + global post-processing): adopted.** I adopted the global `openapi()` pass over per-route edits and over `include_router(responses=...)`, for the reason the consult gives: public routes would otherwise get a spurious 401/422. I also adopted the deep-copy risk note. I **rejected** the shared `ERROR_RESPONSES` export: the global pass makes it redundant, and editing every router widens the diff (R7). Instead of routing `_envelope()` through the model, runtime/doc agreement is guarded by `test_real_error_bodies_match_envelope`, which leaves runtime code untouched.
- **A, optional 429 `Retry-After` / 401 `WWW-Authenticate` header docs: deferred** to follow-up F2. They are not in the ticket's AC.
- **B (stable `operationId`): deferred** to follow-up F1. It is not in the ticket's acceptance criteria, it changes generated client function names, and the consult itself asks the owner which id style admin-pan wants (Assumption A4).
- **C (Origin): adopted, in a variant.** The header is read through `Request` in both `get_app_context` and `get_link_origin`, so no dependency declares it. This removes it from the document at the source, with no doc-stripping hack that could hide a mismatch. `get_link_origin` fails closed when `Origin` is missing. Signup is covered too, because it uses `get_link_origin` through `get_signup_hook` (consult question 6).
- **D (offline deterministic dump): adopted.** That covers the `amain` dispatch, lazy imports, `sort_keys`, no `get_settings`, the subprocess test with a scrubbed env, and no committed snapshot. Settings use `model_construct` (code defaults only) rather than `Settings(_env_file=None, ...)`, so shell env cannot change the document.
- **E (X-Request-ID): adopted for the header documentation; the envelope field is deferred.** The field would need an R6 amendment and churn in the exact-equality body tests, as the consult notes.
- **Finding 6 / question 1 (500): adopted.** No 5xx is documented and no catch-all handler is added.

## Assumptions
Planner decisions taken on the consult's recommendation. **The owner should confirm or override them.**
- **A1. 5xx stays undocumented.** Unhandled errors currently return Starlette's plain-text 500, not an envelope, so documenting 500 as `ErrorEnvelope` would be false. AC1 ("every documented error response (4xx/5xx)") holds because no 5xx is documented. A catch-all JSON 500 handler would change runtime behaviour, so it is follow-up F3.
- **A2. No committed snapshot.** `make openapi` writes to stdout by default (`OUT=path` writes a file). There is no `docs/openapi.json` and no drift test against one. Determinism is proven by tests. admin-pan can generate from the dump or from `/openapi.json`. A committed snapshot plus a CI drift check is follow-up F4 if the owner wants it.
- **A3. X-Request-ID: header documentation only.** It is documented on every response (all status codes). The error body does **not** gain a `request_id` field, because that would amend R6's wording and the exact-body tests. Follow-up F5.
- **A4. `operationId`s are not changed in this ticket.** Follow-up F3 from USR-03b (stable ids) is split into F1 here. The ticket's AC do not require it, and admin-pan's preferred style is unknown. FastAPI's current ids (`<fn>_<path>_<method>`) remain.
- **A5. `X-MM-App` stays documented as an optional header**, as it is today. The server answers 403 (`app_header_required`) when it is missing. Marking it required in the doc is a contract choice for the owner, and is part of follow-up F2.
- **A6. `details` is documented as a required key with an unconstrained (any, nullable) value.** The server always sends the key, but its type varies by code (list for `validation_error`/`unprocessable`, object for `rate_limited`/`permission_denied`, otherwise null). Tightening it would break clients under R9.
- **A7. Removing the `Origin` parameter covers every operation**, not only forgot and resend. Signup and the other cookie routes declared it as optional through `get_app_context`. Browsers set `Origin` themselves (it is a forbidden header for scripts), so clients should not model it. The server check is unchanged.
- **A8.** The dump describes the code defaults (`app_name`, `api_prefix=/api/v1`, version `0.1.0`), whatever the shell or `.env` contains.

## Blocking questions
None

## Split proposal
None. The estimated size is about 120 lines of app code plus about 220 lines of tests, which fits one cycle.

Follow-ups (not in this diff, R7):
- F1: stable, curated `operationId`s (`generate_unique_id_function` plus a uniqueness/style test), once the owner or admin-pan picks a style. This is a client-visible rename.
- F2: document `Retry-After` on 429, `WWW-Authenticate` on 401, and possibly `X-MM-App` as required on cookie routes.
- F3: a catch-all JSON 500 handler in the envelope, then document 5xx.
- F4: a committed `docs/openapi.json` snapshot plus a CI drift check.
- F5: `request_id` inside the error envelope (needs an R6 amendment and test updates).

## Data model / migration
None

## API contract
None. No endpoint is added or changed and runtime responses are identical. Only the generated OpenAPI document changes:

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| (all) | (all) | `origin` header parameter no longer documented | 4xx bodies documented as `ErrorEnvelope`; `X-Request-ID` header documented on every response; `HTTPValidationError`/`ValidationError` removed | unchanged | unchanged |
