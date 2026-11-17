# Implementation log — USR-10

## Attempt 1

### Files touched
| File | Required because |
|---|---|
| `app/core/errors.py` | `SelfSessionChangeForbiddenError` (`self_session_change_forbidden`), added to `DENIED_ERRORS` (OQ7, ADM-004b) |
| `app/modules/audit/actions.py` | `USER_SESSION_REVOKED`, `USER_SESSIONS_REVOKED` (R4) |
| `app/modules/sessions/repository.py` | `list_active_for_user`, `revoke_account_of_user`, `revoke_all_accounts_of_user_ids` (R1) |
| `app/modules/sessions/staff_service.py` (new) | `StaffSessionService`: guards, lock, audit (R1, R4, OQ7) |
| `app/modules/sessions/schemas.py` (new) | `UserSessionRead`, allowlisted fields |
| `app/modules/users/router.py` | three `admin:*` routes under `/users/{user_id}/sessions`, `no-store` on GET, token-tail wording in descriptions |
| `tests/test_user_sessions_admin.py` (new) | endpoint and service tests from the plan |
| `tests/test_route_auth_classes.py` | 3 BEARER entries (`admin:*`), `session_id` in `_fill` |
| `tests/test_audit_coverage.py` | 2 `AUDITED` entries + scenarios, count 25, `test_session_self_revoke_writes_denied_row`, `test_session_revoke_noops_write_no_row` |
| `docs/identity-architecture.md` | §4 row, self-guard code, S8 actions + token-tail |
| `docs/deploy.md` | "Lost or shared device" bullet in "Revoking live tokens (incident)" |
| `docs/spec-v1.md` | §5.10 USR-10 note, OQ7 marked answered, "Open" line trimmed |

### Tests added
All tests listed in the plan (`tests/test_user_sessions_admin.py`, plus the audit-coverage and route-auth-class additions).
Test-only notes: audit rows are read per target user and ordered by action, because rows of one transaction share `occurred_at` and the test DB can hold committed rows from other tests.

### Check results
`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` on the final tree:
- ruff check: All checks passed!
- ruff format --check: 312 files already formatted
- mypy: Success: no issues found in 146 source files
- pytest: 2319 passed, 1 warning in 214.79s (the warning is the existing pty forkpty DeprecationWarning)
- `test_purge_batches_share_cutoff` did not flake.
(An earlier full run had 8 failures in the new test file, caused by the test's audit lookups; fixed and the full check re-run green.)

### Out-of-plan files
None.

### Follow-ups
As in the plan (`sid` claim / admin token revoke, `?include_revoked=`, revoke-all vs login concurrency test, stale `account_sessions` cleanup, spec Q6).

### Scope self-check
No migration, no model change, no config change, existing repository methods untouched. No commit made (per instruction).
