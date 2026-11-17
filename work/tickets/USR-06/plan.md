Status: READY

# Plan — USR-06: Organizations and org-aware tokens (first consumer: calechip)

> The owner confirmed Q1–Q3 with "defaults OK" (2026-11-17, recorded in ticket.md); this plan covers slice **USR-06a** (invitations are USR-06b).

## Spec summary
- spec §5.1: "CRUD for organizations (slug, name, description, status `active`/`suspended`). Paginated list. > 2026-11-11 (D10, D15): this table moves to the Identity module (USR-06), gains `created_by`, keeps `description` and `status`; apps use it per `apps.org_mode`."
- spec §5.3 Membership: "each app has … an `org_mode` (`none` | `required`; calechip = `required`) … Joining assigns the default role at app scope (`org_id NULL`). Policy and role contents are data."
- spec §5.8: "**Per-app access tokens:** an app asks for its own short-lived JWT for a chosen account (and org, if any), carrying only that app's roles (D7)." "Claims: `sub`, `email`, `email_verified`, `aud`, `roles`, `org` (if scoped), `iat`, `exp`." "**Organizations:** optional per app (`org_mode`); one `organizations` table owned by this Identity module (D10, D15, USR-06)."
- identity-architecture.md D10: "Org usage per app: `apps.org_mode` (`none` default, `required` for calechip). No org = `org_id NULL`. `organizations` lives in the Identity module. Org-scoped apps: join gives an app-scope role (e.g. with `calechip:org:create`); org roles from creating an org (owner) or invitation; token `roles` = app-scope + selected-org roles."
- identity-architecture.md D15: "the existing `organizations` table (ADM-001) moves to the Identity module and gains `created_by`; `description` and `status` are kept."
- identity-architecture.md §4 endpoints: `POST /api/v1/auth/orgs {account_id, name, slug}` (cookie; needs `<app>:org:create`; creator gets the app's org owner role), `GET /api/v1/auth/orgs?account_id=&app=`, `POST /api/v1/auth/orgs/{id}/invitations {email, role}`, `POST /api/v1/auth/invitations/{token}/accept`.
- spec §8 OQ1 (open): "Organizations platform-wide (shared across apps) or per app? Working assumption: platform-wide + `apps.org_mode`; adding `organizations.app_key` later must be purely additive."

## Acceptance criteria → verification
Scope of this plan = **USR-06a** (ticket AC1, AC2, AC3, AC5, the org-create/token part of AC6 and AC7). AC4 and the invitation parts of AC6/AC7 move to USR-06b (see `## Split proposal`).

| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | (AC1) `organizations` is owned by Identity (D15): `created_by uuid NULL → users ON DELETE SET NULL` added; `description`, `status` kept; existing rows kept with `created_by NULL`; migration reversible; admin CRUD keeps working (R9; `created_by` added to `OrganizationRead` as an optional field only) | `tests/test_orgs_migration.py::test_upgrade_keeps_existing_org_and_downgrade_reverses`; `tests/test_organizations.py` (existing tests unchanged and green); `test_admin_org_read_has_optional_created_by`; `alembic check` clean in `make check`/CI |
| 2 | (AC2) `role_assignments.org_id` has FK → `organizations.id` `ON DELETE RESTRICT`; "no org" stays NULL; migration aborts loudly on orphan `org_id`s; admin `DELETE /api/v1/organizations/{id}` of an org with any role assignment → 409 `org_has_assignments`, nothing deleted, no audit row; org without assignments → 204 as before | `tests/test_orgs_schema.py::test_role_assignments_org_fk_restrict`, `::test_org_id_null_means_app_scope`; `tests/test_orgs_migration.py::test_upgrade_fails_on_orphan_org_id`; `tests/test_organizations.py::test_delete_org_with_assignments_409`, `::test_delete_org_without_assignments_204` |
| 3 | (AC3) "org owner role per app": `apps.org_owner_role_id` (nullable, same-app composite FK, only when `org_mode = required`, never on `admin`, never a role with `is_admin` permissions), settable by staff via `PATCH /api/v1/apps/{key}`; role delete / `is_admin` changes that would break it are refused (mirrors `default_role_id` guards) | `tests/test_rbac_apps_api.py::test_patch_org_owner_role_*` (happy, unknown role, other app's role, admin-perm role, org_mode none, admin app); `tests/test_rbac_roles_api.py::test_delete_org_owner_role_409`, `::test_add_admin_permission_to_org_owner_role_409`; `tests/test_orgs_schema.py::test_org_owner_requires_org_mode_check` |
| 4 | (AC3) `POST /api/v1/auth/orgs` (cookie class): requires app-scope `<app>:org:create` (checked with app-scope permissions only, never the app+org union); creates the org with `created_by`; creator gets `apps.org_owner_role_id` **in that org**; 201 | `tests/test_auth_orgs.py::test_create_org_grants_owner_role_in_org`, `::test_create_org_requires_org_create_permission_403`, `::test_create_org_not_member_403`, `::test_create_org_no_owner_role_configured_403`, `::test_create_org_owner_role_with_admin_perm_403`, `::test_create_org_slug_taken_409`, `::test_create_org_validation_422`, `::test_create_org_on_org_less_app_422`, `::test_create_org_unverified_403`, `::test_create_org_bad_account_401`, `::test_create_org_per_user_cap_403` |
| 5 | (AC3 "list mine per app") `GET /api/v1/auth/orgs?account_id=` returns `Page[…]` of orgs where the account's user holds an unexpired **org-exact** role of the calling app (from `X-MM-App`), with the caller's roles in each org | `tests/test_auth_orgs.py::test_list_my_orgs_only_calling_app`, `::test_list_excludes_orgs_with_only_other_app_roles`, `::test_list_excludes_expired`, `::test_list_paginates`, `::test_list_on_org_less_app_422` |
| 6 | (AC5) `POST /api/v1/auth/token` with `org_id`: user must hold an unexpired role of this app **in that org** (org-exact) and the org must be `active`; else one uniform 403 `org_access_denied` for unknown / suspended / non-member org; token `org` = org UUID string, `roles` = app-scope + that org's roles; no `org_id` → app-scope roles only, no `org` claim; `org_mode = none` app (incl. `admin`) with `org_id` → 422 `org_not_supported` | `tests/test_auth_token.py::test_token_with_org_has_org_claim_and_union_roles`, `::test_token_without_org_app_scope_only`, `::test_token_app_member_without_org_role_403`, `::test_token_unknown_org_403_same_as_non_member`, `::test_token_suspended_org_403`, `::test_token_org_less_app_ignores_org_id_422`, `::test_token_admin_audience_org_id_422`, `::test_token_other_app_role_in_org_does_not_count` |
| 7 | (AC6, 06a part) org creation writes, in the same transaction, `organization.created` (actor `EndUser`, `app_key`, `org_id`) and `role_assignment.granted` for the owner grant (`org_id`, `role_key`); no email/token in `changes`; refusals write nothing | `tests/test_auth_orgs.py::test_create_org_audit_rows`, `::test_create_org_refusal_writes_no_audit`; `tests/test_audit_coverage.py` passes with the new route registered |
| 8 | (R3) both new routes are cookie class: in `COOKIE` set, 400 `app_header_required` without `X-MM-App`, 401 `session_invalid` without cookie, unregistered Origin refused, no `x-permission` | `tests/test_route_auth_classes.py` (routes added to `COOKIE`, `_fill`/sweep supplies `account_id`) |
| 9 | (R8) `make check` green | `make check` |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `migrations/versions/20261116_<rev>_usr06a_org_foundation.py` | new revision (down_revision = current single head, verify with `alembic heads`) | AC1–AC3 schema, R2 |
| `app/modules/organizations/models.py` | add `created_by` column; module docstring "owned by Identity (D15)" | AC1 |
| `app/modules/organizations/schemas.py` | `OrganizationRead.created_by: uuid.UUID \| None = None` (additive); new `AuthOrgCreate`, `AuthOrgRead`, `MyOrgRead` (reuse slug/name validators) | AC1, AC4, AC5 |
| `app/modules/organizations/repository.py` | `has_role_assignments(org_id)`, `count_created_by(user_id)`, `list_for_user_app(user_id, app_id, limit, offset)` (org-exact, unexpired, with role keys) | AC2, AC4, AC5 (R1: SQL only here) |
| `app/modules/organizations/service.py` | admin `delete` → 409 `org_has_assignments` (pre-check + IntegrityError mapping) | AC2 |
| `app/modules/organizations/auth_service.py` (new) | `OrgSelfService.create(...)`, `.list_mine(...)` for end users | AC4, AC5 (R1) |
| `app/modules/organizations/auth_router.py` (new) | cookie routes `POST`/`GET /api/v1/auth/orgs` | AC4, AC5, R3 |
| `app/modules/organizations/errors.py` (new) | `OrgCreateForbiddenError` (403 `org_create_forbidden`), `OrgCreateUnavailableError` (403 `org_create_unavailable`), `OrgLimitReachedError` (403 `org_limit_reached`) | AC4, R6 |
| `app/core/errors.py` | `OrgHasAssignmentsError(ConflictError)` code `org_has_assignments` (sibling of `RoleHasAssignmentsError`) | AC2, R6 |
| `app/api/router.py` | include `organizations.auth_router` | AC4, AC5 |
| `app/modules/rbac/models.py` | `RoleAssignment.org_id` FK (`ondelete="RESTRICT"`), drop "FK added in USR-06" comment; `App.org_owner_role_id` + `fk_apps_org_owner_role_same_app` (`use_alter`) + CHECK `org_owner_requires_org_mode` | AC2, AC3 |
| `app/modules/rbac/schemas.py` | `AppUpdate.org_owner_role_id`, `AppRead.org_owner_role_id` (optional, additive) | AC3 |
| `app/modules/rbac/service.py` | `AppService.update`: validate `org_owner_role_id` (same app, no `is_admin` perms, `org_mode=required`, not `admin`; refuse `org_mode→none` while set); role delete refuses the org owner role; refuse `is_admin` additions/flips that would reach the org owner role (mirror default-role guards at lines ~393–403, ~769–774, ~951, ~1023) | AC3 |
| `app/modules/rbac/repository.py` | `org_role_keys(user_id, app_key, org_id)` — **org-exact** (`org_id = :org`, not the union), unexpired (DB clock), org `status='active'` | AC6 (security F1/F2) |
| `app/modules/rbac/authz.py` | `AuthzService.org_role_keys(...)` passthrough | AC6 |
| `app/modules/rbac/actor.py` | `SYSTEM_ORG_CREATE = SystemActor("system:org_create", privileged=False)` | AC4 (owner grant via `_grant_locked`, like `SYSTEM_JOIN`) |
| `app/modules/audit/diff.py` | add `org_owner_role_id` to the app snapshot fields; `created_by` to `ORG` snapshot | AC3/AC1 audit of admin PATCH/CRUD stays complete (R4) |
| `app/modules/tokens/service.py` | org branch per AC6; `org` claim; log `org_id` | AC6 |
| `app/modules/tokens/errors.py` | `OrgAccessDeniedError` (403 `org_access_denied`); `OrgNotSupportedError` docstring ("app does not use organizations") | AC6, R6 |
| `app/core/rate_limit.py` | scopes `org_create`, `orgs_list` | abuse limits (security R-h) |
| `app/core/config.py`, `.env.example` | `ORG_CREATE_RATE_LIMIT`, `ORG_CREATE_RATE_WINDOW_SECONDS`, `ORGS_LIST_RATE_LIMIT`, `ORGS_LIST_RATE_WINDOW_SECONDS`, `ORG_CREATE_MAX_PER_USER` (defaults 10 / 3600 / 60 / 60 / 20) | R5 |
| `app/db/models.py` | no new model class; confirm imports unchanged | R2 |
| `tests/test_auth_orgs.py` (new) | endpoint tests | AC4, AC5, AC7 |
| `tests/test_orgs_schema.py` (new) | FK/CHECK/index tests | AC2, AC3 |
| `tests/test_orgs_migration.py` (new) | upgrade/downgrade with data, orphan abort (copy `tests/test_audit_migrations.py` pattern) | AC1, AC2, R2 |
| `tests/test_auth_token.py` | replace "any org_id → 422" test with the AC6 matrix | AC6 |
| `tests/test_organizations.py` | delete-with-assignments 409; `created_by` optional in read | AC1, AC2 |
| `tests/test_rbac_apps_api.py`, `tests/test_rbac_roles_api.py` | `org_owner_role_id` guards | AC3 |
| `tests/test_route_auth_classes.py` | add 2 routes to `COOKIE`; sweep fills `account_id` query | R3 |
| `tests/test_audit_coverage.py` | register `POST /auth/orgs` | R4 |
| `docs/identity-architecture.md` | §3 ownership note (D15 = ownership, folder kept), §4: drop `app=` (from `X-MM-App`), `org_access_denied`, `org_owner_role_id`; mark invitation rows "USR-06b" | docs follow code |
| `docs/spec-v1.md` | §5.1 note: "done in USR-06a: owned by Identity, `created_by` added" (reconcile, as the ticket's spec reference asks) | ticket "§5.1 (reconcile)" |
| `docs/consumers.md` | lines 128 and 312; `org` claim; `org_access_denied`; calechip flow (join → `POST /auth/orgs` → `GET /auth/orgs` → token with `org_id`); "scope all data by the `org` claim; `roles` mixes scopes"; ~15 min revocation lag | AC6 consumers |
| `docs/adr/0013-org-scoped-identity.md` (new) | reserved permission key `<app>:org:create` (checked at app scope; later `<app>:org:invite` in-org), `apps.org_owner_role_id`, org-exact reads, RESTRICT/409, platform-wide orgs (OQ1 working assumption) | ADR-0001 §6 says Identity does not interpret app catalogs; this is a new convention (architect F6/R-E) |
| `work/tickets/USR-06/ticket.md` is **not** edited by the implementer | — | orchestrator updates state |

## Tests to write
| Test (file › name) | Covers |
|---|---|
| test_orgs_migration › test_upgrade_keeps_existing_org_and_downgrade_reverses | AC1, R2 (pre-existing org row + org-scoped assignment survive; downgrade drops FK/columns; re-upgrade works) |
| test_orgs_migration › test_upgrade_fails_on_orphan_org_id | AC2 preflight raises with a clear message, nothing deleted or NULLed |
| test_orgs_schema › test_role_assignments_org_fk_restrict | AC2 (raw DELETE of org with assignment → FK violation) |
| test_orgs_schema › test_org_id_null_means_app_scope | AC2 (NULLS NOT DISTINCT unchanged) |
| test_orgs_schema › test_created_by_set_null_on_user_delete | AC1 |
| test_orgs_schema › test_org_owner_requires_org_mode_check / test_org_owner_role_same_app_fk | AC3 |
| test_organizations › test_delete_org_with_assignments_409 / test_delete_org_with_only_expired_assignment_409 / test_delete_org_without_assignments_204 / test_admin_org_read_has_optional_created_by | AC1, AC2 |
| test_rbac_apps_api › test_patch_org_owner_role_happy / _unknown_role_422 / _other_app_role_422 / _admin_perm_role_422 / _org_mode_none_422 / _admin_app_422 / _switch_org_mode_none_while_set_422 / _audited | AC3, R4 |
| test_rbac_roles_api › test_delete_org_owner_role_409 / test_add_admin_permission_to_org_owner_role_409 / test_flip_permission_is_admin_held_by_org_owner_role_409 | AC3 |
| test_auth_orgs › create: happy (201, owner role in org, `created_by`), requires `<app>:org:create` (member without it → 403 `org_create_forbidden`), not member → 403 `not_member`, owner role unset → 403 `org_create_unavailable`, owner role gained `is_admin` perm → 403 `org_create_unavailable`, slug taken → 409, validation 422, org-less app → 422 `org_not_supported`, unverified → 403 `email_unverified`, bad account → 401, per-user cap → 403 `org_limit_reached`, rate limit → 429, app-scope `<app>:org:create` held only in some org (org-scoped) does **not** allow create | AC3/AC4, security F1 |
| test_auth_orgs › list: only calling app's org-exact roles, excludes other-app roles in the same org, excludes expired, excludes app-scope-only users (empty page, not 404), pagination, org-less app 422, includes `status` | AC5 |
| test_auth_orgs › test_create_org_audit_rows / test_create_org_refusal_writes_no_audit / test_audit_changes_have_no_email | AC7, R4, R11 |
| test_auth_token › AC6 matrix (see AC table row 6) incl. `test_token_other_app_role_in_org_does_not_count` and `test_token_app_scope_only_member_with_valid_org_403` (the F2 regression) | AC6 |
| test_route_auth_classes › existing sweeps with 2 new COOKIE routes | R3 |

## Approach
1. **Migration** (one revision, three ordered steps, see `## Data model / migration`). Preflight orphan check before the FK. Docstring states the lossy parts of downgrade (`created_by`, `org_owner_role_id`).
2. **Models/schemas**: mirror migration names exactly (`alembic check` clean; CHECK texts identical, covered by schema tests since Alembic does not compare CHECKs).
3. **RBAC config (AC3)**: `org_owner_role_id` guards in `AppService.update` and role/permission mutators, mirroring every existing `default_role_id` guard. Audited through the existing `app.updated` diff (R4).
4. **Org-exact read**: `RbacRepository.org_role_keys` with `RoleAssignment.org_id == :org` (never the `_active_assignments` union), joins `organizations` with `status = 'active'`, filters `App.key`, DB clock for expiry. Used for the token org check. Never use `effective_*` with an org for an authorization decision (security F1/F2, identity F1).
5. **`POST /auth/orgs`** (`OrgSelfService.create`): order — `org_mode = none` → 422; `lock_app(app.key)` (ADR-0003 §10 lock order: app first); account → active user → verified email (as in join); app-scope roles empty → 403 `not_member` (with `join_available`, same as token); `permits(effective_permissions(user, app, None), f"{app.key}:org:create")` else 403 `org_create_forbidden`; owner role: `app.org_owner_role_id` set and `role_has_admin_permission` false, else 403 `org_create_unavailable` (log warning like `join_default_role`); `count_created_by(user) >= ORG_CREATE_MAX_PER_USER` → 403 `org_limit_reached`; slug pre-check + IntegrityError → 409 `conflict`; insert org (`created_by=user.id`); `AssignmentService._grant_locked(app, role, user.id, org.id, None, actor=SYSTEM_ORG_CREATE)` (same precedent as `JoinService`); audit `organization.created` and `role_assignment.granted` with `actor=EndUser(user.id)`, `app_key`, `org_id`, `changes` allowlisted (name, slug / role_key) — same transaction (R4). Response 201 `AuthOrgRead` incl. `roles` (caller's org-exact role keys) so the SPA can request an org token next.
6. **`GET /auth/orgs`**: `org_mode = none` → 422; account/user/verified checks; `list_for_user_app` (org-exact, unexpired, role app = calling app, `DISTINCT` org, ordered `name, id`); `Page[MyOrgRead]`. No app-scope membership required (org-only members can still list; see assumption A5).
7. **Routers (R1, R3)**: copy `auth/router.py` shape: `AppContextDep` before `DeviceDep`, `DeviceCookieDoc`, `rate_limit(...)`, rotation `Set-Cookie` echo, `Cache-Control: no-store`, documented error responses, no `require_permission`, no `x-permission`. `account_id` for GET is a query param validated after the dependencies (or the sweep fills it).
8. **Token (AC6)** in `TokenService.issue`: keep `org_id` + `org_mode == none` → 422 `org_not_supported` early (also covers `aud=admin`); existing checks unchanged in order (session, user, verified, app-scope membership → `not_member`); then if `org_id`: `org_role_keys` empty → 403 `org_access_denied` (`details={}`; identical for unknown/suspended/non-member); `roles = effective_roles(user, app, org_id)`; add `"org": str(org_id)`; log `org_id`. No audit row (read; ADR-0009).
9. **Admin delete (AC2)**: `OrganizationService.delete` checks `has_role_assignments` → `OrgHasAssignmentsError`; also map `IntegrityError` on flush (race) to the same 409. No audit on refusal.
10. **Docs + ADR-0013** as listed. Note in consumers.md: revocation lag ≤ token TTL; apps must scope data by `org`.
11. Rules: R1 (routers HTTP only; services raise `AppError`), R2 (migration + downgrade + `alembic check`), R3 (cookie class), R4 (same-transaction audit), R5 (new limits from env), R6 (only 401/403/404/409/422 + existing 400/429 from shared deps), R7 (invitations not touched), R8 (tests above, `make check`), R9 (admin CRUD only additive except the agreed 409 — see Q3), R11 (no emails/tokens in logs or audit).

## Specialist input used
- **Adopted (all five):** org-exact reads for token and future invite checks, never the union (identity F1/R-a, security F1/F2/R-a, architect R-G); one uniform 403 for unknown/suspended/non-member org (security R-b, api "token" table, identity R-a) — code named `org_access_denied`; FK `ON DELETE RESTRICT` + admin 409 `org_has_assignments` + loud migration preflight (architect R-B, db Rec 2, identity R-h, security R-j); `created_by → users SET NULL` + partial index (db Rec 1); keep `app/modules/organizations/` in place, D15 = ownership + docs (architect R-A); `apps.org_owner_role_id` with same-app composite FK and `is_admin` guard (architect R-D, identity R-c); `<app>:org:create` checked at app scope only (identity R-b, security R-i); drop `app=` from `GET /auth/orgs`, take the app from `X-MM-App` (api, security R-e); app-lock order and `_grant_locked` reuse with a non-privileged system actor (identity R-d); per-IP rate limit + per-user org cap (security R-h); audit actions and no email/token in `changes` (architect R-H, security R-k); `org` claim = UUID string (architect R-G); split (architect R-I, identity "Ticket size").
- **Adopted into USR-06b (not this slice):** invitations table shape, body token, atomic single-use accept, email binding, subset-of-inviter role rule, `email_send_log` purpose `invite`, uniform invalid-token error, invite limits (architect R-F, db Rec 3, api, identity R-e/R-f, security R-d/R-f/R-g/R-h).
- **Rejected:** `roles.is_org_owner` flag (db Rec 4) — `apps.org_owner_role_id` follows the existing `default_role_id` precedent and needs no partial unique; `roles.scope app|org` marker (security R-c) — larger RBAC model change, deferred as a follow-up; CASCADE on org delete (api-platform) — silently drops grants without per-row audit, three consults prefer RESTRICT (owner to confirm, Q3); moving the model file to another folder (db Finding 1) — churn, `organizations` is the reference module; `NOT VALID` + `VALIDATE` FK (identity R-h) — table is tiny, plain add is fine (db Rec 2); `org_suspended` distinct 403 (api) — would be an org-existence oracle; promoting `_grant_locked` to public (architect R-A) — refactor outside scope, follow-up (membership already uses the same call); `_grant_locked` refusing suspended orgs for staff grants (security R-j) — changes staff assignment behaviour, belongs with USR-06b accept.

## Assumptions
- A1. D15 "moves to the Identity module" = ownership + docs; `app/modules/organizations/` stays (reference module), table not renamed. All consults agree.
- A2. OQ1 stays open; orgs are platform-wide; `slug` stays globally unique and user-chosen (same validator as admin). A later `organizations.app_key` = nullable column + backfill + `(app_key, slug)` uniqueness, recorded in ADR-0013.
- A3. Admin-created orgs keep `created_by = NULL` (admin create unchanged).
- A4. Calechip's owner role is configured by staff through `PATCH /api/v1/apps/calechip` (no data migration; until set, `POST /auth/orgs` answers 403 `org_create_unavailable`). The `<app>:org:create` permission and its place in the default role are catalog data created by staff (D9: role contents are data). Recommend (consumers.md) that for `org_mode = required` apps the default role holds only app-level permissions plus `<app>:org:create`.
- A5. In 06a "app member" still means "holds an app-scope role" for `/token` (with or without `org_id`) and for `POST /auth/orgs`; `GET /auth/orgs` lists by org-exact roles only. Whether invite-accept also joins the app is decided in USR-06b.
- A6. Suspended orgs: token → uniform 403; listed in `GET /auth/orgs` with `status` (only the caller's own orgs, so no oracle); staff may still assign roles in them (unchanged).
- A7. Rate-limit defaults: `org_create` 10/h per IP, `orgs_list` 60/min per IP, `ORG_CREATE_MAX_PER_USER` 20 total.
- A8. The new 403/409 codes (`org_access_denied`, `org_create_forbidden`, `org_create_unavailable`, `org_limit_reached`, `org_has_assignments`) use only R6 status codes.
- A9. Doc conflict (identity F9, security F10): CLAUDE.md R11 says `mm_device`, `Path=/api/v1/auth`; spec §5.8 and D20 say `__Host-mm_device`, `Path=/`, which the code implements. Follow D20; record a follow-up for the owner to fix CLAUDE.md (planner/implementer must not edit it).

## Blocking questions
**Answered by the owner on 2026-11-17 ("defaults OK"):** Q1 split approved; Q2 `apps.org_owner_role_id`; Q3 admin delete → 409 `org_has_assignments`. Kept below for the record.

1. **Split.** May USR-06 be split into **USR-06a** (this plan: org foundation, owner role config, create/list, org tokens) and **USR-06b** (invitations), with no AC dropped (mapping in `## Split proposal`)? Member management (list/remove members, leave, revoke invitations, last-owner guard, ownership transfer) is not in the spec or ticket; it would need an `/idea` + spec line first and is only listed as a follow-up. *Recommended: yes.*
2. **Org owner role mechanism (AC3).** Store it as data: new nullable `apps.org_owner_role_id` (same-app FK, settable by staff via `PATCH /api/v1/apps/{key}`, refused for roles holding `is_admin` permissions), instead of a fixed key convention like `<app>:org-owner`? *Recommended: `apps.org_owner_role_id`* (follows `default_role_id`; D9 "policy is data").
3. **Admin `DELETE /api/v1/organizations/{id}` behaviour change (AC2, R9).** With the new FK, deleting an org that has role assignments must either return **409 `org_has_assignments`** (staff revoke assignments or suspend the org instead; nothing is lost silently) or **cascade** and delete everyone's org roles. Today it returns 204. Is the 409 acceptable inside v1? *Recommended: 409 (RESTRICT), same as `role_has_assignments`.* admin-pan must be told.

Questions for USR-06b (do **not** block 06a; needed before 06b is planned):
- B1. Does accepting an invitation also join the app (default role, following `join_policy`; a `closed` app refuses and the invitation is not consumed)? *Recommended: yes.*
- B2. Who may invite and to which roles: anyone holding `<app>:org:invite` **in that org** (org-exact), roles of the same app with no `is_admin` permission and a subset of the inviter's org permissions? *Recommended: yes.*
- B3. Accept takes the token in the request **body** (`POST /api/v1/auth/invitations/accept {account_id, token}`) instead of the path in identity-architecture §4, to keep the secret out of logs. *Recommended: body.*
- B4. Invitation link target: a registered origin of the inviting app + fixed path with `#token=` (like verify-email), TTL 7 days, caps (per inviter ~20/day, per org ~50 pending, per target email 1/min and 3/day, counted in the Gmail daily cap). Store only the email HMAC (no pending-invitation list showing emails)? *Recommended: yes to all.*

## Split proposal
| Slice | Title | ACs (from the ticket) | Contents |
|---|---|---|---|
| **USR-06a** (this plan) | Org foundation, org owner role and org-scoped tokens | AC1; AC2; AC3; AC5; AC6 (org created + owner grant); AC7 (create org → owner role; token roles with/without org; no role in org → 403; org-less app `org_id` → 422) | migration (`created_by`, FK RESTRICT + preflight, `apps.org_owner_role_id`), admin delete 409, `PATCH /apps/{key}` `org_owner_role_id`, `POST`/`GET /api/v1/auth/orgs`, `/auth/token` with `org_id`, audit, docs, ADR-0013 |
| **USR-06b** | Org invitations (create, accept by verified email) | AC4; AC6 (invitation created/accepted, grant); AC7 (invite/accept) | `org_invitations` table (token hash, email HMAC, same-app role FK, partial unique pending), `POST /api/v1/auth/orgs/{org_id}/invitations`, `POST /api/v1/auth/invitations/accept` (body token, atomic single use, email binding, uniform invalid-token error), invite email template + `email_send_log` purpose `invite`, limits, org-exact `<app>:org:invite` check, subset-of-inviter rule, implicit join per B1, audit, tests incl. concurrent accept and cross-org escalation. Depends on USR-06a and answers B1–B4. |
| Follow-up (not a slice of this ticket) | Org member management | none (not in spec) | list/remove members, leave, revoke invitations, last-owner guard, ownership transfer, end-user rename/suspend — needs `/idea` + spec line first. |

## Data model / migration
One revision `usr06a_org_foundation` (down_revision = current single head; verify with `alembic heads`).

Step 1 — `organizations.created_by`:
- `created_by uuid NULL`, FK `fk_organizations_created_by_users` → `users.id` `ON DELETE SET NULL`; no backfill (existing rows NULL; metadata-only add).
- Index `ix_organizations_created_by` on `(created_by)` `WHERE created_by IS NOT NULL` (FK SET NULL scans, per-user cap count).

Step 2 — `role_assignments.org_id` FK:
- Preflight: `SELECT count(*) FROM role_assignments ra WHERE ra.org_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM organizations o WHERE o.id = ra.org_id)`; if > 0 raise `RuntimeError("USR-06a: N role_assignments rows reference missing organizations; resolve them before upgrading")`. Never delete or NULL them (NULL would widen an org role to app scope).
- `fk_role_assignments_org_id_organizations` → `organizations.id` `ON DELETE RESTRICT`. Reuses existing `ix_role_assignments_org_id`; `uq_role_assignments_user_role_org` (`NULLS NOT DISTINCT`) unchanged; "no org" = NULL.

Step 3 — `apps.org_owner_role_id`:
- `org_owner_role_id uuid NULL`.
- FK `fk_apps_org_owner_role_same_app` `(org_owner_role_id, id)` → `roles(id, app_id)`, NO ACTION (same as `fk_apps_default_role_same_app`, so app delete still cascades), `use_alter=True` in the model.
- CHECK `ck_apps_org_owner_requires_org_mode`: `org_owner_role_id IS NULL OR org_mode = 'required'`.

Downgrade (reverse order): drop CHECK, FK, column `apps.org_owner_role_id`; drop `fk_role_assignments_org_id_organizations`; drop index, FK, column `organizations.created_by`. Lossy only for `created_by` and `org_owner_role_id` values (stated in the docstring; production never downgrades past this, as with earlier revisions). No change to `audit_log` (no FKs; snapshot `org_id`).

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/orgs` | headers `X-MM-App`, `Origin`, cookie `__Host-mm_device`; body `AuthOrgCreate {account_id: uuid, name: str, slug: str, description?: str}` (`extra=forbid`) | 201 `AuthOrgRead {id, name, slug, description, status, created_at, updated_at, roles: [str]}` | 400 `app_header_required`; 401 `session_invalid`; 403 `origin_not_allowed`, `email_unverified`, `not_member`, `org_create_forbidden`, `org_create_unavailable`, `org_limit_reached`; 409 `conflict` (slug); 422 `validation_error`, `org_not_supported`; 429 | cookie class (R3-2); app-scope `<app>:org:create` checked in the service |
| GET | `/api/v1/auth/orgs?account_id=&limit=&offset=` | same headers/cookie | 200 `Page[MyOrgRead {id, name, slug, status, roles: [str]}]`, ordered `name, id` | 400; 401; 403 `origin_not_allowed`, `email_unverified`; 422 `validation_error`, `org_not_supported`; 429 | cookie class (R3-2); own data only |
| POST | `/api/v1/auth/token` (changed) | `TokenRequest {account_id, audience, org_id?}` (unchanged shape) | 200 `TokenResponse`; JWT gains `org` when `org_id` given | adds 403 `org_access_denied`; 422 `org_not_supported` now only for `org_mode = none` apps | cookie class (unchanged) |
| PATCH | `/api/v1/apps/{key}` (changed, additive) | `AppUpdate.org_owner_role_id?: uuid \| null` | `AppRead` gains `org_owner_role_id` | 422 `unknown_role`, `role_has_admin_permissions`, `org_mode_required`, admin-app refusal | Bearer, existing `admin:apps:write` (unchanged) |
| DELETE | `/api/v1/organizations/{id}` (changed) | — | 204 | adds 409 `org_has_assignments` (pending Q3) | Bearer, existing `admin:organizations:write` |
| GET | `/api/v1/organizations[/{id}]` (changed, additive) | — | `OrganizationRead` gains optional `created_by` | unchanged | unchanged |
