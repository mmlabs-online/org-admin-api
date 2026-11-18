Status: READY

# Plan — BUG-002: `test_changing_push_writes_one_row` fails intermittently (`assert 'c' not in …`)

## Spec summary
This is a test-only bug (ADR-0012). No spec section changes. These are the rules it relies on:
- ADR-0016 §3, as encoded in `app/modules/app_credentials/codec.py`: the credential format is `mmak_<key_id>_<secret>`, where "`key_id` is 16 hex chars", and the secret is `secrets.token_urlsafe(32)`, i.e. 43 chars from `[A-Za-z0-9_-]` (regex `^mmak_([0-9a-f]{16})_([A-Za-z0-9_-]{43})\Z`). Because the secret alphabet contains `_`, `raw.rsplit("_", 1)[1]` is not the secret.
- Ticket scope: "Fix the test's secret extraction (e.g. split on the known `mmak_<key_id>_` prefix, or use the secret returned by the factory) and make the check deterministic; nothing else (R7)."

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | A regression test reproduces the bug: it fails before the fix and passes after | `tests/test_app_manifest_api.py::test_audit_secret_check_handles_underscore_in_secret`. It fixes the secret to `"A" * 35 + "_success"` (43 chars, valid for the codec regex). With the old line 548 (`raw.rsplit("_", 1)[1]`), the extracted fragment is `success`, which is always in the dump (`row.outcome == "success"`), so the test fails deterministically. With the fix, it passes. The reviewer confirms by temporarily restoring the old extraction line in `_assert_secret_not_in` and running `uv run pytest tests/test_app_manifest_api.py -k underscore_in_secret`. The test must fail with `assert 'success' not in …`. Then the reviewer reverts. The implementer records this before/after run in `impl-log.md`. |
| 2 | The full secret is checked, deterministically | Code read: the check strips the exact prefix `f"mmak_{env.key_id}_"` and asserts `len(secret) == 43` before `assert secret not in dump`. `test_changing_push_writes_one_row` and the new test both pass. |
| 3 | `make check` passes | Run `make check`. No migration, so `alembic check` is unchanged. |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `tests/test_app_manifest_api.py` | Add a module helper `_assert_secret_not_in(env, dump)` that does the correct extraction. Move the shared body of `test_changing_push_writes_one_row` into `_assert_push_audited_without_secret(client, db_session, env)`. Call that helper from the existing test and from a new regression test that pins the secret through `monkeypatch`. | The buggy extraction is at line 548. The regression test must go through the same code path as the flaky test. |
| `work/tickets/BUG-002/impl-log.md` | Implementer log | Workflow |

No `app/` changes. `tests/app_credential_factories.py` and `app/modules/app_credentials/codec.py` are read-only.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_app_manifest_api.py › test_audit_secret_check_handles_underscore_in_secret` | Regression test (fails before the fix). The credential secret contains `_` and its tail after the last `_` (`success`) is guaranteed to be in the audit dump. The full-secret check must still pass. |
| `tests/test_app_manifest_api.py › test_changing_push_writes_one_row` (existing, refactored) | Same assertions as today, now through the shared helper with the correct extraction. |

## Approach
1. Add a helper in `tests/test_app_manifest_api.py`:
   ```python
   def _assert_secret_not_in(env: Env, raw: str, dump: str) -> None:
       prefix = f"mmak_{env.key_id}_"
       assert raw.startswith(prefix)
       secret = raw.removeprefix(prefix)
       assert len(secret) == 43  # token_urlsafe(32); may itself contain "_"
       assert raw not in dump
       assert secret not in dump
   ```
   The helper takes the secret only by stripping the known prefix, never by `rsplit("_")`.
2. Move lines 526–548 of `test_changing_push_writes_one_row` (body, PUT, the row assertions, building `dump`) into `async def _assert_push_audited_without_secret(client, db_session, env) -> None`. That helper ends with `_assert_secret_not_in(env, raw, dump)`. The existing test becomes `env = await _env(db_session)` followed by `await _assert_push_audited_without_secret(client, db_session, env)`. The assertions do not change, apart from the corrected secret check.
3. Add `test_audit_secret_check_handles_underscore_in_secret(client, db_session, monkeypatch: pytest.MonkeyPatch)`:
   - `secret = "A" * 35 + "_success"` (43 chars, matches `[A-Za-z0-9_-]{43}`)
   - `monkeypatch.setattr(codec.secrets, "token_urlsafe", lambda _n: secret)` (`from app.modules.app_credentials import codec`). This patches only while creating the credential: call `env = await _env(db_session)`, then `monkeypatch.undo()`, so nothing else in the request uses the stub.
   - `assert env.headers["Authorization"].endswith("_success")` (guard: the stub took effect)
   - `await _assert_push_audited_without_secret(client, db_session, env)`
   - Docstring: one line referencing BUG-002.
4. Run the before/after check for AC1: temporarily put `assert raw.rsplit("_", 1)[1] not in dump` back in place of the prefix-strip, see the new test fail, restore the fix, and record both runs in `impl-log.md`.
5. `make check`.

Rules: R7 (test file only, no product change). R8 (`make check` green). R5/R11: the stub secret is a fixed non-secret test literal, so nothing real is logged.

Same-pattern sweep (done at planning). `grep` for `rsplit(` / `split("_"` in `tests/` found these other uses:
- `tests/test_app_credentials_api.py:56`: `raw.split("_")[1] == body["key_id"]`
- `tests/test_app_credentials_api.py:61, 218`: `raw.split("_", 2)[2]`
- `tests/test_app_credentials_codec.py:12`: `raw.split("_")[1] == key_id`

All of them are correct: `mmak` and the hex `key_id` contain no `_`, so index 1 is always the key_id and `split("_", 2)[2]` is always the full secret. Only `tests/test_app_manifest_api.py:548` has the bug. No other files are in scope.

## Specialist input used
None. The orchestrator skipped the consult (test-only fix, root cause in the ticket). The fix is the one the USR-05b review nit pointed to (`work/tickets/USR-05b/review.md:41`).

## Assumptions
- The audit row's `outcome` column is always `"success"` for this push. The existing test already asserts this, so the `success` tail is guaranteed to be in the dump.
- Patching `secrets.token_urlsafe` through `codec.secrets` patches the shared `secrets` module. That is acceptable because it is undone right after `_env(...)`, before the request.

## Blocking questions
None.

## Split proposal
None. About 30 changed lines in one test file.

## Data model / migration
None.

## API contract
None.
