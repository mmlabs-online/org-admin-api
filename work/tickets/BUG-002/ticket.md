# BUG-002: `test_changing_push_writes_one_row` fails intermittently (`assert 'c' not in …`)

| Field | Value |
|---|---|
| Type | bug |
| State | REVIEW |
| Severity | S3 |
| Priority | P3 |
| Areas | api |
| Found in | local `make check` (main after BUG-001 work, 2026-11-18) |
| Introduced in | unknown release; test added in commit 0436f25 (USR-05b) |
| Attempts | 0 |
| Created | 2026-11-18 |
| PR | — |

## Report
Owner/orchestrator, 2026-11-18: during the BUG-001 implementation one full `make check` run failed in `tests/test_app_manifest_api.py::test_changing_push_writes_one_row` with `assert 'c' not in <audit row text>`; the next five runs were green. Test-only flake, no product impact.

## Environment
- Instance: local test DB (Postgres 16 in the cloud container) · full suite · `PUT /api/v1/apps/{key}/manifest` (R3 class 4, app credential) inside the test.

## Steps to reproduce
1. `service postgresql start`
2. Run `env -u APP_VERSION -u DATABASE_URL -u APP_ENV uv run pytest tests/test_app_manifest_api.py -k test_changing_push_writes_one_row` repeatedly.
3. Fails only when the random credential secret contains `_` close to its end (see Root cause). 30 isolated runs on 2026-11-18: 0 failures (the triggering case is rare).

## Expected
- The test asserts that neither the raw credential nor its secret appears in the audit row, and passes on every run.

## Actual
- Occasionally: `AssertionError: assert 'c' not in '<dump of audit_log columns>'` at `tests/test_app_manifest_api.py:548`.

## Root cause
**Suspected (strong evidence), not reproduced in 30 runs.** `tests/test_app_manifest_api.py:548` extracts the secret with `raw.rsplit("_", 1)[1]`. Credentials are `mmak_<key_id>_<secret>` where the secret is `secrets.token_urlsafe(32)` (`app/modules/app_credentials/codec.py:18`), whose alphabet includes `_`. When the secret itself contains `_`, `rsplit` returns only the tail after the last `_` — possibly 1–2 characters (here `c`), which naturally occurs in the hex/uuid text of the audit row dump. The single-character failure message matches this exactly. Product code is correct (no secret is written to the audit row); the test's secret extraction is wrong. The USR-05b review already noted this `rsplit` as a nit.

## Scope
- Fix the test's secret extraction (e.g. split on the known `mmak_<key_id>_` prefix, or use the secret returned by the factory) and make the check deterministic; nothing else (R7).

## Out of scope
- Any product change to the credential format or audit logging.

## Acceptance criteria
- [ ] A regression test reproduces the bug: it fails on `main` before the fix and passes after (name the test) — e.g. a deterministic test that builds a credential whose secret contains `_` near the end (monkeypatched `secrets.token_urlsafe`) and shows the old extraction yields a short fragment that matches the dump.
- [ ] The expected behaviour above holds: the full secret is checked, deterministically.
- [ ] `make check` passes (and `uv run alembic check` if a migration changed).

## Technical notes
- Files: `tests/test_app_manifest_api.py` (line ~545-548); possibly the credential factory in `tests/` to expose the secret; `app/modules/app_credentials/codec.py` read-only.
- Test level: pytest against the Postgres test DB.

## Dependencies
None.

## Notes / answers from owner
- 2026-11-18 (owner): record this flake as a bug.
- 2026-11-18 (owner): fix now (option C). Orchestrator: consult skipped — test-only fix, root cause in the ticket; the USR-05b review already flagged the `rsplit` nit.
