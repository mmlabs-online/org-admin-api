# Review — USR-23

## Attempt 1 — 2026-11-19
Verdict: REWORK

### Checks (run by reviewer)
Run by the reviewer on branch `claude/festive-volta-rikup7` at `c8d6fee`, product diff `d541b6f`. Local Postgres, default `TEST_DATABASE_URL`.
- `make typecheck`: `Success: no issues found in 150 source files`
- `uv run ruff check . --exclude .github/scripts`: `All checks passed!`
- `uv run ruff format --check app tests migrations`: `292 files already formatted`
- `uv run ruff check .` (full): fails only in `.github/scripts/risk_tier.py`, which is pre-existing on main (kit file from PR #71) and not a ticket blocker.
- `make test`: **`1 failed, 2711 passed`**. The failure is `FAILED tests/test_audit_coverage.py::test_every_mutating_bearer_route_is_mapped`, with `Extra items in the right set: ('DELETE', '/api/v1/organizations/{org_id}/invitations/{invitation_id}')`. See Issue 1.
- `uv run alembic check`: `No new upgrade operations detected.`

impl-log.md says plainly that a full `make test` was not re-run after the route-registry fix. The full run shows the suite is red.

### Acceptance criteria
- [x] AC1: members GET with Bearer, `admin:users:read`, `no-store`, `Page[OrgMemberRead]` with the exact field set. Evidence: `app/modules/organizations/router.py` `list_org_members`, `app/modules/rbac/schemas.py` `OrgMemberRead`, plus the tests `test_members_item_shape`, `test_members_cache_control_no_store` and `test_members_two_roles_two_rows` (all pass).
- [x] AC2: members filters `app` and `active`, paging, no N+1. Evidence: `test_members_expired_active_false_and_filter`, `test_members_app_filter_and_total`, `test_members_paging_and_order`, `test_members_bounded_queries` and `test_page_org_members_two_statements` (all pass). `page_org_members` reuses `_is_active()` and selects explicit `User` columns.
- [x] AC3: only this org's org-scoped rows. Evidence: `test_members_only_this_org_scoped_rows`.
- [x] AC4: invitations GET with `admin:organizations:read`, the exact 11 keys, DB-clock `status` and a `status` filter. Evidence: `InvitationRepository.status_expr()` is the single CASE on `func.now()`. Tests `test_invitations_item_shape_exact_keys`, `test_invitations_each_status`, `test_invitations_status_precedence` and `test_invitations_status_filter` pass.
- [x] AC5: no token, token hash, email HMAC or invitee email, and no new column. Evidence: `page_for_org` selects explicit columns only. `test_invitations_body_has_no_secrets` passes. The diff touches no `migrations/` or model file, and `alembic check` is clean.
- [ ] AC6: DELETE revoke returns 204, is audited in the same transaction, and needs `admin:organizations:write`. The behaviour itself is verified by `test_revoke_pending_204_and_audit`, `test_revoke_accepted_409`, `test_revoke_already_revoked_204_no_second_audit`, `test_revoke_expired_204`, `test_revoke_other_org_404` and `test_revoke_keeps_role_assignments` (all pass). However, the repo-wide R4 audit-coverage registry is not updated (Issue 1).
- [x] AC7: 401, 403, 404 and 422 on every route. Evidence: `test_members_401/403/404/422_*`, `test_invitations_401/403/404/422_*` and `test_revoke_401_403`, `test_revoke_unknown_*_404`, `test_revoke_422_bad_ids`.
- [ ] AC8: `make check` green. FAILED, see Issue 1.

Ming verification after merge (for the PR): admin-pan PAN-018 renders the members and invitations tabs against the deployed API.

### Issues
#### Issue 1 — 🔴 blocker — tests / R4 audit coverage / R8
- **Where**: `tests/test_audit_coverage.py:349-437` (`AUDITED` registry) and `:466-468` (`test_every_mutating_bearer_route_is_mapped`).
- **Expected**: every mutating Bearer route in `tests/test_route_auth_classes.py` `BEARER` also appears in `AUDITED`, with a success scenario and a failure scenario, and `make test` is green.
- **Actual**: the diff adds `("DELETE", f"{V1}/organizations/{{org_id}}/invitations/{{invitation_id}}")` to `BEARER` but not to `AUDITED`, so `make test` fails (`1 failed, 2711 passed`).
- **Fix instruction**: in `tests/test_audit_coverage.py`:
  1. Add a success scenario, e.g. `async def _inv_revoke(s) -> Call`. It creates an org, an app with a role (the existing `_role(s)` helper works) and a pending invitation through `tests/invitation_factories.make_invitation(s, org, role, created_at=<db now - 1h>, expires_at=<db now + 1d>)`. It returns `(f"{V1}/organizations/{org.id}/invitations/{inv.id}", None)`.
  2. Add a failure scenario that writes no audit row and returns 404, e.g. `_inv_revoke_unknown`: an existing org and `uuid.uuid4()` as the invitation id.
  3. Add the entry `("DELETE", f"{ORG_ID}/invitations/{{invitation_id}}"): ({"organization.invitation_revoked"}, _inv_revoke, _inv_revoke_unknown, 404)`. Use whatever constant matches the `BEARER` key exactly.
  4. Bump `assert len(AUDITED) == 25` to `26`.
  5. Record the file under `Out-of-plan files` in impl-log.md.
  6. Re-run the **full** `make test` and paste its summary line.

  `test_success_writes_one_row` also asserts that `row.changes` is truthy. `{"reason": "staff"}` satisfies this.

### Rules (CLAUDE.md)
- R8: violated while `make test` is red (Issue 1).
- R1: no violation. The routers call only services. `InvitationAdminService` raises `NotFoundError`/`ConflictError`. The repositories hold all the SQL.
- R2: no violation (no schema change; `alembic check` clean).
- R3: no violation. All three routes are on the Bearer organizations router with `require_permission` per route, and they are registered in `tests/test_route_auth_classes.py`.
- R4: the revoke writes its audit row in the request transaction, right after the conditional UPDATE (`app/modules/invitations/admin_service.py`). The coverage registry is missing (Issue 1).
- R5/R11: no violation. No hashes or tokens appear in responses, errors or audit `changes`, and nothing is logged.
- R6, R7, R9: no violation. The out-of-plan `tests/test_route_auth_classes.py` change is justified in impl-log. The `docs/spec-v1.md` §5.7 note follows the approved Q1 default.

### Blockers from review agents
None — appsec-engineer attempt 1: PASS, no 🔴 (see security-review.md).

### Nits (do not block)
- 💭 `test_invitations_body_has_no_secrets` asserts that `"token"` and `"email"` do not appear anywhere in the body. That will break if a future field or description contains those words. A key-set check already covers this, so the substring asserts on the raw/hex values would be enough.
- 💭 `InvitationAdminService._require_org` loads the full `Organization` entity through `OrganizationRepository.get`. An `exists()` check like `RbacRepository.org_exists` would be lighter. Behaviour is the same.

### Follow-ups (out of scope)
- Pre-existing: `ruff check .` / `ruff format --check .` fail on the kit-managed `.github/scripts/risk_tier.py` (from PR #71). Fix it in spaceM project-kit and re-sync.
- A `user_status` filter on members (from impl-log).
- Cookie-side list/revoke of invitations, and a retention purge of `org_invitations` (ADR-0014).
- USR-24 touches the same rbac files, so expect a rebase.
