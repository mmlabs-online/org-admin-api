Status: READY

# Plan — USR-23: Staff read of an organization's members and invitations (admin Bearer)

Risk tier: **High** (touches `app/modules/rbac/**`; areas auth/security). Ming approves at PLAN, `appsec-engineer`
security review applies, no agent merges. No migration.

## Scope summary
Ticket (goal): "Staff can see, in an organization's detail, who its members are (with roles and joined date) and its
invitations with their status (admin-pan PAN-018)."

Scope sources relied on:
- spec §5.1: "CRUD for organizations (slug, name, description, status `active`/`suspended`)." and "Invitations: USR-06b."
- spec §5.3: "users get roles per app, optionally limited to one organization. Every admin `/api/v1` endpoint declares
  the `admin:*` permission it needs".
- ADR-0014 line 60: "Follow-ups: retention purge of `org_invitations`, **list/revoke endpoints** and member management."
  (this ticket implements the staff list and revoke follow-up).
- CLAUDE.md R3 class 1 (Bearer, `aud=admin`, `require_permission("admin:…")`), R4 (audit in the same transaction),
  R9 (`/api/v1/<plural-noun>`, `Page[T]`, `limit` ≤ 100), R11 ("never log passwords, tokens or full emails").
- Ticket owner note 2026-11-19: "no masked email hint — the invitations list shows no invitee email; no new column."

**Scope conflict to flag (spec wins unless Ming says otherwise):** spec §5.7 "Later (not v1)" lists "Org members view".
The ticket was requested by Ming's admin-pan batch on 2026-11-19, so the default is that the ticket supersedes that
line for the staff read only — see Blocking question 1.

`work/decisions.md`: nothing on USR-23, members view or invitation listing.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | `GET /api/v1/organizations/{org_id}/members`: Bearer, `admin:users:read`, `Cache-Control: no-store`, `Page[OrgMemberRead]` one row per org-scoped assignment with exactly `assignment_id, user_id, email, display_name, user_status, app, role_id, role_key, role_name, granted_by, created_at, expires_at, active` | `tests/test_org_members_api.py` › `test_members_item_shape` (exact key set), `test_members_cache_control_no_store`, `test_members_two_roles_two_rows` |
| 2 | Members filters `app` and `active`; paging; no N+1 | `test_members_active_filter`, `test_members_app_filter_and_total`, `test_members_paging`, `test_members_bounded_queries`, `test_page_org_members_two_statements` |
| 3 | Members are only this org's org-scoped rows (no app-scope, no other org) | `test_members_only_this_org_scoped_rows` |
| 4 | `GET /api/v1/organizations/{org_id}/invitations`: Bearer, `admin:organizations:read`, `Page[OrgInvitationRead]` with exactly `id, app, role_id, role_key, invited_by, created_at, expires_at, accepted_at, accepted_by, revoked_at, status`; `status` derived on the DB clock; optional `status` filter | `tests/test_org_invitations_admin_api.py` › `test_invitations_item_shape_exact_keys`, `test_invitations_each_status`, `test_invitations_status_filter`, `test_invitations_status_precedence` |
| 5 | Never the token, token hash or email HMAC; no invitee email (AC3, no new column) | `test_invitations_item_shape_exact_keys`, `test_invitations_body_has_no_secrets` (raw token, hex of `token_hash` and `email_hmac` absent from `resp.text`); `git diff --stat` shows no `migrations/` or model change |
| 6 | `DELETE /api/v1/organizations/{org_id}/invitations/{id}` → 204, revokes, audited in the same transaction, `admin:organizations:write` (default: included, Q2) | `test_revoke_pending_204_and_audit`, `test_revoke_then_not_acceptable`, `test_revoke_other_org_404`, `test_revoke_accepted_409`, `test_revoke_already_revoked_204_no_second_audit`, `test_revoke_expired_204` |
| 7 | 401, 403, 404 unknown org, 422 validation on every route | `test_*_401_without_token`, `test_*_403_*`, `test_*_404_unknown_org`, `test_*_422_*` in both test files |
| 8 | `make check` green | reviewer runs `make check`; `uv run alembic check` clean (no drift expected) |

## Ming verification after merge
- admin-pan PAN-018 renders the members and invitations tabs against the deployed API (hosted service).

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/modules/organizations/router.py` | Add `GET /{org_id}/members`, `GET /{org_id}/invitations`, `DELETE /{org_id}/invitations/{invitation_id}` to the existing Bearer router (router-level `current_admin`, `_route` helper, `_NOT_FOUND`) | AC1, AC2, AC4; keeps routes in R3 class 1 | No |
| `app/modules/rbac/repository.py` | Add `OrgMemberRow` type alias near line 45; `org_exists(org_id)` (plain, no lock) near line 516; `page_org_members(...)` after `page_role_holders` (line 541-571), reusing `_is_active()` (line 529) | Members query, single DB-clock definition of `active` | **Yes** (`app/modules/rbac/**`) |
| `app/modules/rbac/service.py` | Add `AssignmentService.list_org_members(...)` after `list` (line 1411-1419) and `_org_member_read(row)` next to `_role_holder_read` (line 1370) | R1 service layer, org 404 | **Yes** |
| `app/modules/rbac/schemas.py` | Add `OrgMemberRead` after `RoleHolderRead` (line 298-311) | Exact AC field names (`assignment_id`, `role_name`) | **Yes** |
| `app/modules/invitations/schemas.py` | Add `InvitationStatus` (`StrEnum`) and `OrgInvitationRead` | AC2 response, 422 on bad `status` | No |
| `app/modules/invitations/repository.py` | Add `status_expr()` (static CASE), `page_for_org(...)`, `revoke_for_org(...)`, `get_state(...)`, `app_key(app_id)` | Explicit-column list query; atomic revoke | No |
| `app/modules/invitations/admin_service.py` (new) | `InvitationAdminService`: `list_for_org`, `revoke` (org 404, revoke rules, audit) | R1; keeps the staff path separate from the cookie `InvitationService` | No |
| `tests/invitation_factories.py` | Add `make_invitation(db, org, role, *, created_at, expires_at, accepted_by=None, revoked=False)` inserting an `OrgInvitation` with random 32-byte hashes; returns `(invitation, raw_token)` | Status fixtures without the cookie flow | No |
| `tests/test_org_members_api.py` (new) | Members endpoint tests | R8, AC5 | No |
| `tests/test_org_invitations_admin_api.py` (new) | Invitations list + revoke tests | R8, AC5 | No |
| `docs/identity-architecture.md` | Add one endpoint-table row in §4 (next to line 173) for the three routes with auth class and permissions | D14: every endpoint declares its auth class | No |
| `docs/adr/0014-org-invitations.md` | Line 60: note staff list/revoke done in USR-23 (cookie-side revoke still a follow-up) | Keep ADR follow-ups accurate | No |
| `docs/spec-v1.md` | §5.7 line 134: only if Ming confirms Q1 default, add a dated note that the staff read of org members/invitations ships in v1 (USR-23); the rest of "Org members view" (member management) stays Later | Resolve the scope conflict in the source of truth | No |

The implementer must not touch other files without recording why in `impl-log.md`. `work/backlog.md` is updated by
the orchestrator on `main`, not in this diff.

## Data model / migration
None. Existing indexes cover both queries: `ix_role_assignments_org_id` (members `WHERE org_id`),
`ix_org_invitations_org_id_created_at` (invitations filter and sort). Permissions `admin:users:read`,
`admin:organizations:read`, `admin:organizations:write` are already seeded
(`migrations/versions/20261115_5e2a9c7d1f04_seed_admin_permissions.py:29-31`). Audit action
`ORGANIZATION_INVITATION_REVOKED` and `AuditTarget.INVITATION` already exist (`app/modules/audit/actions.py:26,38`).
No new column (AC3).

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/organizations/{org_id}/members` | query: `limit` (1..100), `offset`, `app` (`Query(pattern=APP_KEY_RE)`), `active` (bool) | 200 `Page[OrgMemberRead]`, header `Cache-Control: no-store` | 200, 401, 403, 404 (code `not_found`, "Organization {id} not found"), 422 | `admin:users:read` |
| GET | `/api/v1/organizations/{org_id}/invitations` | query: `limit`, `offset`, `status` (`pending`\|`accepted`\|`revoked`\|`expired`) | 200 `Page[OrgInvitationRead]`, header `Cache-Control: no-store` | 200, 401, 403, 404, 422 | `admin:organizations:read` |
| DELETE | `/api/v1/organizations/{org_id}/invitations/{invitation_id}` | — | 204 empty | 204, 401, 403, 404 (unknown org, unknown invitation or one of another org: "Invitation not found"), 409 (code `conflict`, `details=[{"field": "invitation_id", "reason": "invitation_accepted"}]`), 422 | `admin:organizations:write` |

`OrgMemberRead` (rbac/schemas.py), field order as listed:
`assignment_id: UUID`, `user_id: UUID`, `email: str`, `display_name: str | None`,
`user_status: UserStatus` (description "Account status; disabled users are listed."), `app: str` (app key),
`role_id: UUID`, `role_key: str`, `role_name: str`, `granted_by: UUID | None`,
`created_at: datetime` (description "When the assignment was first granted; kept when an expired assignment is
re-granted in place."), `expires_at: datetime | None`,
`active: bool` (same description as `RoleHolderRead.active`: "Not expired (database clock); ignores user, org and app
status, so not 'effective in tokens'.").
Route description: "`total` counts assignments, not distinct users: a user with two roles in the org appears twice."
Order: `created_at DESC, id`. Unknown but well-formed `app` → empty page.

`OrgInvitationRead` (invitations/schemas.py): `id: UUID`, `app: str`, `role_id: UUID`, `role_key: str`,
`invited_by: UUID | None`, `created_at: datetime`, `expires_at: datetime`, `accepted_at: datetime | None`,
`accepted_by: UUID | None`, `revoked_at: datetime | None`, `status: InvitationStatus` (description "Computed at read
time on the database clock: accepted > revoked > expired > pending. An invitation can read pending while its
inviter has since lost the right to invite; accepting it is then refused."). No `email*`, `token*`, `org_id`, `app_id`.
Order: `created_at DESC, id`. Default: all statuses (Q4).

`InvitationStatus(StrEnum)`: `PENDING="pending"`, `ACCEPTED="accepted"`, `REVOKED="revoked"`, `EXPIRED="expired"`.

## Tests to write
Helpers: copy `_capture_sql` and `_set_created` from `tests/test_rbac_role_holders_api.py:53-84`; clients via
`make_client(admin_permissions=[...])` (`tests/conftest.py`); orgs/apps/roles/users via `tests/rbac_factories.py`
(`make_org`, `make_app`, `make_role`, `make_user`, `assign`); invitations via the new `make_invitation`.

| Test (file › name) | Covers |
|---|---|
| `test_org_members_api.py` › `test_members_401_without_token` | 401 |
| › `test_members_403_with_organizations_read_only` | `admin:organizations:read` alone → 403, `details.required == "admin:users:read"` |
| › `test_members_admin_star_passes` | `admin:*` → 200 |
| › `test_members_404_unknown_org` | 404 envelope, code `not_found` |
| › `test_members_422_invalid_params` (parametrized: bad UUID path, `limit=101`, `limit=0`, `offset=-1`, `active=maybe`, `app=Bad Key`) | 422 |
| › `test_members_item_shape` | exact key set; values (`app`, `role_key`, `role_name`, `email`, `user_status`, `granted_by`) |
| › `test_members_cache_control_no_store` | header |
| › `test_members_two_roles_two_rows` | one user, two roles in the org → 2 rows, `total == 2` |
| › `test_members_expired_active_false_and_filter` | expired assignment `active=false`; `active=true`/`false`/omitted counts |
| › `test_members_app_filter_and_total` | two apps; `app=` filters rows and `total`; unknown well-formed app → empty page 200 |
| › `test_members_only_this_org_scoped_rows` | app-scope assignment (`org_id NULL`) and another org's assignment absent |
| › `test_members_disabled_user_listed` | `user_status == "disabled"` row present |
| › `test_members_paging_and_order` | `limit`/`offset`/`total`, `created_at DESC, id` order (set `created_at` via `_set_created`), offset beyond total → empty items, correct total |
| › `test_members_empty_org` | existing org without assignments → 200, `total == 0` |
| › `test_members_bounded_queries` | statement count equal for `limit=1` and `limit=6` (no N+1) |
| › `test_page_org_members_two_statements` | repository issues exactly 2 statements |
| `test_org_invitations_admin_api.py` › `test_invitations_401_without_token` | 401 (GET and DELETE) |
| › `test_invitations_403_without_organizations_read` | `admin:users:read` only → 403 on GET |
| › `test_invitations_404_unknown_org` | 404 |
| › `test_invitations_422_invalid_params` (bad UUID, `limit=101`, `status=open`) | 422 |
| › `test_invitations_item_shape_exact_keys` | key set exactly the 11 fields |
| › `test_invitations_body_has_no_secrets` | raw token, `token_hash.hex()`, `email_hmac.hex()` and the invitee email not in `resp.text` |
| › `test_invitations_cache_control_no_store` | header |
| › `test_invitations_each_status` | four fixtures (pending; accepted; revoked; expired with `created_at`/`expires_at` in the past) → each `status` |
| › `test_invitations_status_precedence` | accepted with `expires_at` now past → `accepted`; revoked and past → `revoked` |
| › `test_invitations_status_filter` | `status=` each value returns only matching rows and matching `total` |
| › `test_invitations_only_this_org_and_paging` | other org's invitation absent; `limit`/`offset`/`total`; order `created_at DESC, id` |
| › `test_revoke_401_403` | no token → 401; `admin:organizations:read` only → 403 `details.required == "admin:organizations:write"` |
| › `test_revoke_pending_204_and_audit` | 204; `revoked_at` set; one `organization.invitation_revoked` audit row with target invitation id, `org_id`, `app_key`, `changes == {"reason": "staff"}`, staff actor; no hash in the audit row |
| › `test_revoke_then_not_acceptable` | after revoke `InvitationRepository.get_pending_for_update(token_hash, app_id)` is `None`; list shows `revoked` |
| › `test_revoke_other_org_404` | invitation of org B via org A path → 404, row unchanged, no audit |
| › `test_revoke_unknown_invitation_404` / `test_revoke_unknown_org_404` | 404 |
| › `test_revoke_accepted_409` | 409 `conflict`, `details[0].reason == "invitation_accepted"`, row unchanged, no audit |
| › `test_revoke_already_revoked_204_no_second_audit` | second DELETE 204, `revoked_at` unchanged, still one audit row |
| › `test_revoke_expired_204` | expired unrevoked → 204, audited, status becomes `revoked` |
| › `test_revoke_keeps_role_assignments` | revoking does not touch `role_assignments` (count unchanged) |

## Approach
1. **Schemas.** `OrgMemberRead` in `app/modules/rbac/schemas.py`; `InvitationStatus` and `OrgInvitationRead` in
   `app/modules/invitations/schemas.py` (plain fields, built from explicit columns, never `from_attributes` on the ORM
   entity).
2. **Members repository** (`rbac/repository.py`). `OrgMemberRow = tuple[RoleAssignment, str, str, bool, str, str, str | None, str]`
   (assignment, app key, role key, active, role name, email, display name, user status). `page_org_members(org_id, *,
   app_key, active, limit, offset)`: filters `[RoleAssignment.org_id == org_id]` + `App.key == app_key` when given +
   `_is_active()` / `not_(_is_active())`. Count: `select(func.count()).select_from(RoleAssignment).join(Role).join(App)
   .where(*filters)` (joins needed for the `app` filter so `total` matches). Rows: `_assignment_rows().add_columns(
   Role.name, User.email, User.display_name, User.status).join(User, ...)` — explicit `User` columns only, never
   `select(User)`; order `RoleAssignment.created_at.desc(), RoleAssignment.id`. `org_exists(org_id)`: `select(exists()
   .where(Organization.id == org_id))`, mirroring `user_exists` (line 504).
3. **Members service** (`rbac/service.py`). `AssignmentService.list_org_members(org_id, *, app_key, active, limit,
   offset)`: `if not await self.repo.org_exists(org_id): raise NotFoundError(f"Organization {org_id} not found")`
   (same message as `OrganizationService.get`), then the page, mapped by `_org_member_read`.
4. **Invitations repository** (`invitations/repository.py`). `status_expr()` returns one `case(...)` on
   `func.now()`: `accepted_at IS NOT NULL` → accepted; `revoked_at IS NOT NULL` → revoked; `expires_at <= now()` →
   expired; else pending. Use it for the output column, the `status` filter and the count (one definition).
   `page_for_org(org_id, *, status, limit, offset)`: `select(OrgInvitation.id, App.key, OrgInvitation.role_id,
   Role.key, OrgInvitation.invited_by, created_at, expires_at, accepted_at, accepted_by, revoked_at,
   status_expr().label("status"))` joined to `Role` on `(role_id, app_id)` and `App` on `app_id`; never selects
   `email_hmac`/`token_hash`; order `created_at DESC, id`. `revoke_for_org(org_id, invitation_id) -> uuid | None`:
   one conditional `UPDATE ... SET revoked_at = now() WHERE id = :id AND org_id = :org AND accepted_at IS NULL AND
   revoked_at IS NULL RETURNING app_id` (no read-then-write; atomic against `consume()`, line 100-113). `get_state(org_id,
   invitation_id)` selects `accepted_at, revoked_at` only (for the no-row case). `app_key(app_id)` selects `App.key`.
5. **Invitations admin service** (new `invitations/admin_service.py`). `InvitationAdminService(session)` with
   `InvitationRepository`, `OrganizationRepository`, `AuditLogger`. `_require_org` raises
   `NotFoundError(f"Organization {org_id} not found")`. `list_for_org` maps rows to `OrgInvitationRead`. `revoke(org_id,
   invitation_id, *, actor: Actor, ctx: AuditContext)`: org check; `app_id = revoke_for_org(...)`; if a row: audit
   `ORGANIZATION_INVITATION_REVOKED`, `target=(AuditTarget.INVITATION, invitation_id)`, `app_key=await app_key(app_id)`,
   `org_id=org_id`, `changes={"reason": "staff"}` (same transaction, R4; call style as `_supersede`,
   `invitations/service.py:263-273`, actor = staff principal); if no row: `get_state` → `None` →
   `NotFoundError("Invitation not found")`; accepted → `ConflictError("Invitation was already accepted",
   details=[{"field": "invitation_id", "reason": "invitation_accepted"}])`; already revoked → return (idempotent 204,
   no audit). Expired but unrevoked is revoked like a pending one. Do not touch `role_assignments`. No logging of row
   contents (R11).
6. **Router** (`organizations/router.py`). Three routes using `_route(PERM, {...})`: members `_route("admin:users:read",
   _NOT_FOUND)` with `response: Response` setting `Cache-Control: no-store`; invitations `_route(READ, _NOT_FOUND)` also
   `no-store`; DELETE `status_code=204`, `_route(WRITE, {404: "Organization or invitation not found", 409: "Invitation
   already accepted (code conflict, reason invitation_accepted)"})`, deps `principal: AdminDep, ctx: AuditContextDep`.
   Routers only call services (R1). Check imports for cycles: `organizations.router` → `invitations.admin_service` →
   `organizations.repository` and `rbac.service`; neither imports `organizations.router`. If mypy or import order
   complains, move the three routes to `app/modules/organizations/members_router.py` with the same prefix/dependency
   and register it in `app/api/router.py` (record in impl-log).
7. **Docs**: `docs/identity-architecture.md` §4 row; ADR-0014 line 60; spec §5.7 note only per Q1.
8. **Tests** as listed, then `make check` and `uv run alembic check`.

Rules: R1 (layering above), R2 (no schema change), R3 (class 1, router-level `current_admin` + per-route
`require_permission`; 401/403 come before 404 by dependency order), R4 (revoke audited in the request transaction),
R5/R11 (no secrets in responses, errors, logs or audit `changes`), R6 (`NotFoundError`/`ConflictError` envelope, no new
code in `app/core/`), R7 (no cookie-side revoke, no inviter email, no new index), R8 (tests above), R9 (plural nouns,
`Page[T]`, UUIDs, `timestamptz`).

## Specialist input used
- api-platform-engineer: adopted routes on the organizations Bearer router, `no-store` via `Response`, SQL CASE status
  with accepted > revoked > expired > pending, `StrEnum` filter, `created_at DESC, id` order, nullable inviter/accepter,
  include DELETE with a conditional UPDATE. Put the members query in the rbac repository instead of
  `organizations/repository.py` so `_is_active()` stays the single definition.
- database-optimizer: adopted no migration, one CASE for column/filter/count, explicit columns, `page_role_holders`
  pattern, `total` counts assignments, explicit past `created_at`/`expires_at` in fixtures. Rejected a new
  `(org_id, created_at, id)` index (R7; not needed at expected size).
- security-architect: adopted exact key-set tests, raw-token/hash absence in `resp.text`, `no-store` on both GETs,
  org-scoped revoke (IDOR → 404), conditional UPDATE (no 500 from the CHECK on a race), no hashes in audit `changes`,
  revoke does not touch assignments, `created_at` described as "first granted". Already-revoked: chose idempotent 204
  (Q3).
- identity-access-engineer: adopted new read models with exact AC names, `active` description carried over from
  `RoleHolderRead`, documented "pending can be unusable" in the `status` description, reason `"staff"` to tell it apart
  from `"superseded"`, `get_pending_for_update` check after revoke, endpoint-table and ADR-0014 doc updates. Kept
  invitations under `admin:organizations:read` as ticketed (Q5). Already-revoked → 204 rather than 409 (Q3). Did not
  add a `user_status` filter (not in AC; follow-up).

## Assumptions
- `Role.name` exists and is non-null (consults; `RoleCatalog` already exposes names). If nullable, `role_name: str | None`.
- `AuditLogger.record` accepts the `AdminPrincipal` (`Actor`) as `actor` (identity consult finding 5; same as
  `OrganizationService.delete`).
- Members and invitations of suspended orgs and of disabled apps are listed (staff need to see them).
- Members include org-scoped `admin`-app assignments if any exist (consistent with USR-17).
- An unknown but well-formed `app` filter value returns an empty page, not 404.
- `status` filter is a single value (not a list).
- **Hotspots:** USR-24 (PLANNING, "Optimistic concurrency on PUT role permissions") very likely edits
  `app/modules/rbac/service.py`, `app/modules/rbac/repository.py`, `app/modules/rbac/schemas.py` and possibly
  `docs/identity-architecture.md`; expect a rebase. Additions here are append-only in separate functions to keep
  conflicts trivial.
- Size: ~350 lines production/docs + ~450 lines tests. Over the ~400 guide mainly from tests that copy an existing
  pattern; kept as one ticket (see Split proposal).

## Blocking questions
Status is READY: each question has a default that makes the plan implementable. Ming confirms or overrides in the
batch approval round.

1. **Spec conflict.** Spec §5.7 lists "Org members view" as Later (not v1). Ship this staff read in v1?
   *Default: yes — the ticket (Ming's admin-pan batch) supersedes §5.7 for the read-only staff view; the implementer
   adds a dated note to §5.7; member management stays Later.*
2. **Include the optional DELETE (revoke)?** *Default: yes — cheap (audit action, target and the predicate already
   exist), and the admin panel needs a way to cancel a wrong invitation.*
3. **DELETE on a non-pending invitation.** *Default: accepted → 409 `conflict`, reason `invitation_accepted`, nothing
   changes; already revoked → idempotent 204, no second audit row; expired but unrevoked → revoked and audited, 204.*
4. **Default of the invitations list.** *Default: all statuses (newest first); admin-pan passes `status=pending` for a
   pending-only view.*
5. **Permission for the invitations list.** It shows `invited_by`/`accepted_by` user ids (opaque UUIDs, no emails).
   *Default: keep `admin:organizations:read` as ticketed; resolving ids to emails needs `admin:users:read` via
   `/users/{id}`.*

## Split proposal
None. Fallback if the implementer finds the diff clearly too large for one review: ship the two GETs as USR-23 and
move the DELETE (step 5 revoke, its route, its tests) to a follow-up USR-23b.
