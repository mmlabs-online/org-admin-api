
## Attempt 1

### Files touched
- `app/modules/rbac/schemas.py`: `AppFields` base (11 fields) + `AppRead(AppFields)` adds required `member_count: int` (ge=0). Required because: additive read-model field.
- `app/modules/rbac/service.py`: `AppService.member_counts(apps)` pass-through to `RbacRepository.member_counts`; `Sequence` import. Required because: R1 (router calls service).
- `app/modules/rbac/router.py`: `_app_read` / `_app_read_counted` helpers; list (one batched call per page), get, register, update return `member_count`. Required because: AC 1-3.
- `tests/test_rbac_apps_api.py`: `APP_READ_KEYS` gains `member_count`; 12 new member_count tests (all plan tests incl. dashboard parity, apps:read-only, query counts).

### Check results
`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff + format + mypy clean, `2281 passed, 1 warning in 216.54s`. No flake observed. (After this run only the new list test's tail loop was tidied; re-run below.)

### Out-of-plan files
None.

### Follow-ups
None.

### Scope self-check
Only the four planned files changed; no migration, repository, or route/permission change.
