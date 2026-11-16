## Attempt 1 — 2026-11-16

### Files touched
| File | Required because |
|---|---|
| app/core/errors.py | `ErrorBody` / `ErrorEnvelope` models (AC1/AC2) |
| app/core/openapi.py (new) | global post-processing and deterministic render (AC1, AC2, AC4, AC6) |
| app/main.py | `install_openapi(app)` after the routers |
| app/modules/auth/deps.py | `Origin` read from `Request`, no longer a declared header (AC3) |
| app/cli.py | `openapi [--output]` subcommand (AC4) |
| Makefile | `make openapi [OUT=path]` |
| tests/test_openapi_contract.py (new) | schema tests |
| tests/test_cli_openapi.py (new) | dump and determinism tests |

### Tests added / changed
- tests/test_openapi_contract.py: all planned tests (envelope component, every error response, no 5xx, no FastAPI validation schemas, 422 description, origin not declared, X-MM-App documented, X-Request-ID, no shared-dict mutation, extensions preserved, real bodies match envelope).
- tests/test_cli_openapi.py: all planned tests (in-process determinism, file, stdout, usage 64, unwritable path 1, env overrides ignored, all routers, subprocess offline run twice).
- No existing test changed.

### Checks (actual output summary)
- make check: ruff clean, format clean, `mypy: Success: no issues found in 102 source files`, `1736 passed`.
- One earlier full run showed `tests/test_cli_audit_purge.py::test_purge_batches_share_cutoff` failing once (batch count 2 != 1). It passes in isolation and in the final full run; it looks timing-dependent and unrelated to this diff.
- alembic check: n/a (no migration).

### Notes
- `Settings.model_construct(app_env="local")` works with `create_app`, so no fallback was needed.
- 422 description: the global pass replaces both FastAPI's "Validation Error" and the routers' generic "Error envelope" with the validation_error text; other route-specific descriptions are kept.

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- Plan follow-ups F1-F5 (operationIds, Retry-After/WWW-Authenticate docs, JSON 500 handler, committed snapshot + CI drift check, request_id in envelope).
- `test_purge_batches_share_cutoff` looks flaky under the full suite.

### Scope self-check
- Could the diff be smaller? No.
- Diff size: about +330 lines including tests, -6 lines.
