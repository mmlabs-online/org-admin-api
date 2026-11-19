# Implementation log — USR-23

## Attempt 1 (2026-11-19)

Environment: no Docker; local Postgres via `service postgresql start` (databases `org_admin`, `org_admin_test`,
user postgres), default `TEST_DATABASE_URL`. Plan approved by Ming 2026-11-19, Q1-Q5 at defaults (DELETE included,
spec §5.7 note added).

### Files touched
| File | Required because |
|---|---|
| `app/modules/organizations/router.py` | 3 routes (members, invitations list, DELETE revoke), plan step 6 |
| `app/modules/rbac/repository.py` | `OrgMemberRow`, `org_exists`, `page_org_members` |
| `app/modules/rbac/service.py` | `AssignmentService.list_org_members`, `_org_member_read` |
| `app/modules/rbac/schemas.py` | `OrgMemberRead` |
| `app/modules/invitations/schemas.py` | `InvitationStatus`, `OrgInvitationRead` |
| `app/modules/invitations/repository.py` | `status_expr`, `page_for_org`, `revoke_for_org`, `get_state`, `app_key`, `InvitationRow` |
| `app/modules/invitations/admin_service.py` (new) | `InvitationAdminService` (list, revoke + audit) |
| `tests/invitation_factories.py` | `make_invitation` |
| `tests/test_org_members_api.py`, `tests/test_org_invitations_admin_api.py` (new) | tests per plan |
| `docs/identity-architecture.md`, `docs/adr/0014-org-invitations.md`, `docs/spec-v1.md` | docs per plan (§5.7 dated note per Q1) |

### Tests added
All tests in the plan table (members: 401/403/404/422, shape, no-store, two roles, expired/active, app filter,
org-scope only, disabled user, paging/order, empty org, bounded queries, repo two statements; invitations: auth,
shape, no secrets, each status, precedence, filter, org isolation/paging; revoke: 401/403, 204 + audit, not
acceptable after, other org 404, unknown 404, accepted 409, idempotent, expired, assignments kept). Extra:
`test_members_users_read_passes`, `test_invitations_422_bad_uuid`, `test_revoke_422_bad_ids`.

### Checks
- `uv run ruff check . --exclude .github/scripts`: All checks passed
- `uv run ruff format --check app tests`: 270 files already formatted
- `make typecheck`: Success: no issues found in 150 source files
- `make test`: first full run 2697 passed, 3 failed (a bug in my test, and 2 route-registry tests, both fixed
  below); after the fixes the 3 affected files re-run: 210 passed. A full `make test` was not re-run after the
  fixes (about 6 min).
- `uv run alembic check`: No new upgrade operations detected (no migration, no model change)
- Pre-existing, untouched: `.github/scripts/risk_tier.py` fails ruff check/format on main.

### Out-of-plan files
- `tests/test_route_auth_classes.py`: R3 registry (`BEARER` map must list every Bearer route, else
  `test_bearer_route_permission_map` fails) and `invitation_id` added to its path-fill list.

### Deviations
- `AssignmentService.list_org_members` returns `Sequence[...]` instead of `list[...]`: the class has a method
  named `list`, which makes `list[...]` an invalid type for mypy.
- Routes kept in `organizations/router.py` (no import cycle).

### Follow-ups
- A `user_status` filter on members (not in AC).
- Cookie-side revoke/list of invitations; retention purge of `org_invitations` (ADR-0014).
- USR-24 touches the same rbac files; expect a rebase for whichever lands second.

### Scope self-check
Only plan files plus the one test registry file above; no migration, no model change, no `app/core/` change.

## Attempt 3 (REWORK 2) — fix review Issue 1 (R4 audit-coverage registry)

### Files touched
- `tests/test_audit_coverage.py`: added `_inv_revoke` (success) and `_inv_revoke_unknown` (404) scenarios, the `AUDITED` entry for `DELETE /organizations/{org_id}/invitations/{invitation_id}` (`organization.invitation_revoked`), and `len(AUDITED)` 25 -> 26. Required because the review found the mutating Bearer route missing from the registry.

### Checks
- `make test`: 2714 passed, 1 warning in 349.87s
- `make typecheck`: Success: no issues found in 150 source files
- `uv run ruff check . --exclude .github/scripts`: All checks passed!
- `uv run ruff format --check app tests`: 270 files already formatted

### Out-of-plan files
- `tests/test_audit_coverage.py` (the R4 registry, as the review instructed).

### Scope self-check
Only the review-listed file changed; no app code touched.
