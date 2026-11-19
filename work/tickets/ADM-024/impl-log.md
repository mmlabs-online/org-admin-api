
## Attempt 1 — 2026-11-19

### Files touched
| File | Required because |
|---|---|
| `app/core/etag.py` | `version_etag`, `IfMatch`, `parse_if_match`, OpenAPI constants (AC1, AC2) |
| `app/core/errors.py` | `PreconditionFailedError` (412, current ETag header), `_HTTP_CODES[412]`, ErrorBody description (AC4) |
| `app/core/cors.py` | `If-Match` allowed on the admin policy (AC4) |
| `app/db/base.py` | `bumped_updated_at()` for the conditional UPDATE; `TimestampMixin` untouched |
| `app/modules/organizations/{repository,service,router}.py` | `update_if_version`, `current_version`, conditional branch, ETag on GET/POST/PATCH, 412 and header declared |
| `app/modules/entitlements/{repository,service,router}.py` | same, plus new `get` service and `GET /{entitlement_id}` route (AC1) |
| `CLAUDE.md` | R6 row only: added `412 (stale If-Match, ADM-024)` (owner-approved) |
| `docs/spec-v1.md`, `docs/adr/0011-admin-cors.md`, `docs/consumers.md` | dated notes per plan step 9 |
| tests: `test_etag.py`, `test_organizations.py`, `test_entitlements_api.py`, `test_entitlements_repository.py`, `test_admin_cors.py`, `test_cors.py`, `test_openapi_contract.py` | plan test list |

### Tests added / changed
- test_etag.py: version_etag, parse_if_match (absent, star, round trip, lists, never-match cases)
- test_organizations.py: ETag on POST/GET/PATCH, matching/stale/list/star/weak+malformed/noop/no-header/404-beats-412/403+401/lost race, repo mismatch
- test_entitlements_api.py: new GET (etag, 404s, 401/403), POST etag, PATCH etag, matching, stale (two clients), star, weak, noop, no-header, 404/403
- test_entitlements_repository.py: update_if_version mismatch
- test_admin_cors.py / test_cors.py: allow-headers value updated, `if-match` allowed preflight; disallowed case now `if-none-match`
- test_openapi_contract.py: new route in `_EXPECTED_ERRORS`, 412 + If-Match + ETag declarations

### Checks (actual output summary)
- make check (ruff, format, mypy, pytest): all green; pytest 2652 passed.
- alembic check: not run separately (no model/migration change).

### Out-of-plan files
- `tests/test_route_auth_classes.py` — its route-to-permission map must list the new `GET .../entitlements/{entitlement_id}` (admin:entitlements:read) or two tests fail.

### Follow-ups (noticed, not done)
- `If-Match` on DELETE, and on other PATCH routes (apps, roles, users); `If-None-Match`/304 on single-resource GETs.
- `parse_if_match`: a `*` inside a list makes the whole header never match (plan wording was ambiguous; test list says `'*, "1"'` never matches).

### Scope self-check
- Could the diff be smaller? No; mirrored org/entitlement changes and the listed tests.
- Diff size: not measured (no git repo here).
