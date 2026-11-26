# Impl log — USR-40

## Attempt 1

### Files touched
- `app/modules/organizations/schemas.py`: `OrgAccess`, `MyOrgRead.access` (response model, R9 additive).
- `app/modules/organizations/repository.py`: `list_for_user_app(include_app_orgs=…)` returns `(org, is_member, roles)`; WHERE `is_member OR org_of_app_clause(app_id)` for holders; count and page share it.
- `app/modules/organizations/auth_service.py`: `list_mine` calls `AuthzService.app_super_admin_access`, maps `access`, holder-only info log (no email, no org names).
- `docs/consumers.md`, `docs/adr/0030-app-super-admin-role.md` (§8 accepted), `docs/spec-v1.md` (§5.16 Org list, OQ84 ticked).
- `tests/test_auth_orgs_super_admin.py` (new, 24 tests incl. mint consistency), `tests/test_auth_orgs.py` (`access: "member"` in the exact-shape test).

### Deviations / naming
- Names match what shipped: `AuthzService.app_super_admin_access(user_id, app)`, `org_of_app_clause(app_id)` in `rbac/repository.py` (imported, `app/modules/rbac/**` untouched; clause already has the `active` filter).
- Query-count tests do one warm-up request first (the first request loads CORS origins, one extra statement).
- `test_holder_on_admin_app_422` sets the seeded `admin` app origins in the test transaction to pass the origin check.
- Extra test `test_excluded_orgs_are_not_mintable` (suspended org -> 403 `org_access_denied`).
- Not done: `EXPLAIN` of the holder page query (not recorded). Optional router docstring edit not made.

### Checks
- ruff check: All checks passed; ruff format --check: 444 files already formatted; mypy app: no issues in 216 files.
- pytest: 4393 passed, 2 warnings (new file 24 passed; `tests/test_auth_orgs.py` green).
- alembic check (local dev DB): No new upgrade operations detected.

### Out-of-plan files
None.

### Follow-ups
- EXPLAIN / index review if orgs per app exceed ~10k (api consult rec 8).

### Scope self-check
Only plan files touched; no High path edited.
