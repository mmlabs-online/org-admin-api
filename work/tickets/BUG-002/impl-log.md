# Implementation log — BUG-002

## Attempt 1

### Files touched
| File | Change | Required because |
|---|---|---|
| `tests/test_app_manifest_api.py` | Added `_assert_secret_not_in` (strips exact `mmak_<key_id>_` prefix, asserts secret length 43, checks raw and secret absent from dump); moved the body of `test_changing_push_writes_one_row` into `_assert_push_audited_without_secret`; added `test_audit_secret_check_handles_underscore_in_secret`; imported `codec` | Plan |

### Tests added
- `tests/test_app_manifest_api.py::test_audit_secret_check_handles_underscore_in_secret` (regression; secret pinned to `"A"*35 + "_success"` via monkeypatch of `codec.secrets.token_urlsafe`, undone right after the credential is created).

### Regression evidence
- Before fix (helper temporarily using `raw.rsplit("_", 1)[1]`): `pytest tests/test_app_manifest_api.py -k "underscore_in_secret or changing_push"` -> `1 failed, 1 passed`; failure `AssertionError: assert 'success' not in '2026-11-18 ...'` in the new test.
- After fix (prefix strip): same command -> `2 passed, 36 deselected`.

### Checks
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` -> exit 0 (ruff, format, mypy --strict, pytest): `2425 passed, 1 warning in 281.07s` (the warning is a pre-existing forkpty DeprecationWarning in test_cli_create_account).
- No migration; `alembic check` not applicable.

### Out-of-plan files
None.

### Follow-ups
None.

### Scope self-check
Test file only, no `app/` change, no commit made (as instructed).
