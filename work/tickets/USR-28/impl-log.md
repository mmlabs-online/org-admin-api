# Implementation log — USR-28

## Attempt 1
Plan followed as written (email_match=exact|contains, POST /users/search, D43 + R9 exception, no ADR, no access-log change).

### Files touched
- app/modules/users/schemas.py: `EmailMatch`, `UserSearch` (AC1, AC2).
- app/modules/users/repository.py: `email_exact` flag; exact = `User.email == email` (CITEXT); contains branch unchanged.
- app/modules/users/service.py: `email_match` rule (422 `email_required`), exact uses `normalize_email` (imported from auth.service, not edited).
- app/modules/users/router.py: `email_match` query param, `_users_page` helper shared by GET and POST, `POST /search` (before `/{user_id}`), 4 KiB cap via `read_json_body`.
- tests/test_users_search.py (new, new tests incl. parametrised cases); tests/test_route_auth_classes.py (registry row).
- CLAUDE.md R9 exception; docs/identity-architecture.md row D43.

### D-number
D42 was the last row, no D43 anywhere else: D43 is free, no renumbering.

### Checks
- ruff check: All checks passed; ruff format --check: 450 files already formatted; mypy --strict: no issues in 216 source files.
- alembic check (local dev DB): No new upgrade operations detected.
- pytest: 4569 passed, 2 warnings in 769.19s.

### Out-of-plan files
- tests/test_audit_coverage.py: `test_every_mutating_bearer_route_is_mapped` requires every non-GET Bearer route to be audited; added a `READ_ONLY_POST` exclusion for `POST /users/search` (a read, D43). Test-only, 3 lines.

### Follow-ups
- Access-log query-string stripping (High, `app/core/`), separate ticket.
- admin-pan PAN-015c: switch the role-assign picker to `POST /users/search` with `email_match: "exact"`; then revisit GET `email` deprecation.

### Scope self-check
No High path edited; `normalize_email` and `read_json_body` imported only. No migration.
