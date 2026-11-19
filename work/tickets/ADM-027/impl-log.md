## Attempt 1 — 2026-11-19

Environment: no Docker; local Postgres started with `service postgresql start` (databases `org_admin`
and `org_admin_test`, default `TEST_DATABASE_URL`). No real Google credentials or network: tests use
`tests/fake_drive.py` through `httpx.MockTransport`. Plan approved by Ming 2026-11-19 with all defaults
(Q1-Q6); ADR number stays 0018.

### Files touched
| File | Required because |
|---|---|
| `app/core/config.py` | 4 `GOOGLE_DRIVE_*` keys, `drive_enabled`, `_drive_gate` (AC1, R5). High path. |
| `.env.example` | New keys, empty values (AC1, R5). High path. |
| `app/modules/attachments/{__init__,errors,store,drive,sweep}.py` | Port, Drive client, sweep (AC1-AC4). |
| `app/main.py` | Build store, `app.state.attachment_store`, log state, close in lifespan. |
| `app/cli.py` | `attachments-sweep`, `attachments-init-root`, httpx/httpcore logger silencing. |
| `docs/adr/0018-attachment-storage-google-drive.md` | New ADR. |
| `docs/deploy.md` | Attachments section and pointer bullet (AC5). |
| `work/ming-todo.md` | M-1 reworded. |
| `tests/fake_drive.py`, `tests/test_config.py`, `tests/test_attachments_drive.py`, `tests/test_attachments_sweep.py`, `tests/test_cli_attachments.py` | Tests. |

### Tests added / changed
- `tests/test_config.py` › Drive matrix (none, all valid, 14 partial subsets are covered by the 1-3 key subsets, malformed per key, no value echo, repr hidden).
- `tests/test_attachments_drive.py` › store disabled, app log state, token cache/invalid_grant, folders, create (body, no retry, cap, quota), read (stream, cap, 404, bounded retry, 401 refresh), delete 404, redirect, input validation, `read_capped`, listing, sentinel leak test.
- `tests/test_attachments_sweep.py` › dry run, apply, no checker, checker error, max deletes, grace floor, delete error.
- `tests/test_cli_attachments.py` › sweep (dry run, apply, no checker, not configured, usage, max-deletes, no Drive ids), init-root (create, no TTY, Drive error).

### Checks (actual output summary)
- ruff check (`. --exclude .github/scripts`): All checks passed
- ruff format --check app tests: 280 files already formatted
- typecheck (`make typecheck`, mypy strict on app): Success: no issues found in 155 source files
- test (full `make test`): 2833 passed, 1 warning in 379.00s
- No migration, so no `alembic check`. No routes, so the route-class and audit-coverage registries are unaffected (they passed).

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- Alembic `fileConfig` in the test setup disables existing loggers (`disabled=True`), so `caplog` sees nothing from `app.*` loggers unless a test re-enables them; the new tests do so locally.
- ADM-030 must wire a DB-backed `ReferenceChecker` into `attachments-sweep` and add the `deps.py` getter.
- Partial-subset test covers the 1-3 key subsets (14 in total, as the plan counts them).

### Scope self-check
- Could the diff be smaller? No; each piece maps to a plan item.
- Commits: one commit (plan allowed one PR; commit order split not needed).
