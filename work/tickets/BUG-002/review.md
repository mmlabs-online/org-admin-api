# Review — BUG-002

## Attempt 1 — 2026-11-18
Verdict: PASS

### Checks (run by reviewer)
- `service postgresql start`; `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` → exit 0 (ruff, format check, mypy --strict, pytest): `2425 passed, 1 warning in 279.15s` (the warning is the pre-existing forkpty DeprecationWarning in `test_cli_create_account`). This matches impl-log (2425 passed).
- alembic check: not applicable (no migration, no model change).
- Regression check (reviewer-run): I temporarily replaced the prefix-strip in `_assert_secret_not_in` with the old `raw.rsplit("_", 1)[1]` (and dropped the length assert), then ran `pytest tests/test_app_manifest_api.py -k "underscore_in_secret or changing_push"` → `1 failed, 1 passed`. `test_audit_secret_check_handles_underscore_in_secret` failed with `AssertionError` at `tests/test_app_manifest_api.py:531` (`'success' not in '… post_synced success app …'`). I restored the file with `git checkout`, and the working tree was clean. The same command after the fix → `2 passed`.

### Acceptance criteria
- [x] A regression test reproduces the bug (fails before, passes after). Evidence: `tests/test_app_manifest_api.py::test_audit_secret_check_handles_underscore_in_secret`, before/after runs above.
- [x] The full secret is checked, deterministically. Evidence: `_assert_secret_not_in` (`tests/test_app_manifest_api.py:525-531`) strips the exact `mmak_<key_id>_` prefix, asserts `len(secret) == 43`, then checks both `raw` and `secret` against the dump. The existing `test_changing_push_writes_one_row` now goes through the same helper.
- [x] `make check` passes. Evidence: exit 0, above.

### Issues
None.

### Scope (R7)
The changed files are `tests/test_app_manifest_api.py` (in the plan), `work/backlog.md` (BUG-002 row, workflow) and the ticket folder files. There are no `app/` changes and no out-of-plan code.

### Rules R1–R11
No violations found. The change is test-only. The stub secret is a fixed non-secret literal (R5/R11).

### Nits (do not block)
- `monkeypatch.undo()` reverts every patch made by this test's `monkeypatch` fixture. That is safe today because `tests/conftest.py` does not use `monkeypatch`. The stub only affects `token_urlsafe` (`key_id` uses `token_hex`, which calls `token_bytes`, so it is not touched).
- The ticket's `Attempts` field and the backlog row still say 0/REVIEW. The orchestrator updates those.

### Follow-ups (out of scope)
- None.
