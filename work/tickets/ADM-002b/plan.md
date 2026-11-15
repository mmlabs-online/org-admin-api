Status: READY

# Plan — ADM-002b: Enforce admin RBAC guards and mount the users/RBAC APIs

## Spec summary
- §5.2: "Only authenticated mmlabs staff can call `/api/v1/*`. Health endpoints stay public."
- §5.3: "One model for every mmlabs app, the admin API included (app `admin`). … Every admin `/api/v1` endpoint declares the `admin:*` permission it needs. A bootstrap super-admin (`admin:*`) exists."
- §5.3 membership: "a default role `<app>:member` holding all its permissions except those flagged `is_admin`". The `is_admin` flag is the boundary between app members and app admins. Moving a permission across it, or adding one to a role that already has members, is an escalation (USR-02d).
- §6.1 (line 85): "API docs: Swagger UI at `/docs` and `/openapi.json` are public in production too (owner, 2026-11-11; temporary — revisit when ADM-002 mounts the admin routes)." This ticket mounts those routes, so this is the revisit (see Assumptions A1).
- CLAUDE.md R1, R3, R6, R7, R8, R11. ADR-0003 §10 (lock the app, then decide). ADR-0008 and its ADM-002c amendment (admin origins).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Each guard site has a test for the allowed path and the refused path | `tests/test_admin_guards.py` holds one parametrised matrix (site × {leaf holder → 403 `admin_wildcard_required` + nothing written; `admin:*` holder → success}). It covers every row of the guard table in **Approach §3**. The reviewer ticks each table row against a test id. |
| 2 | Tests for self-grant, `is_admin` 409, `admin:*` only on super-admin, last super-admin and self-disable | `test_admin_guards.py` › `test_self_grant_*`, `test_self_revoke_*`, `test_self_status_change_*`, `test_is_admin_onto_assigned_role_409`, `test_is_admin_flip_on_assigned_role_409`, `test_wildcard_only_on_super_admin_422`, `test_last_super_admin_*`, plus the serialized concurrent pair in `test_rbac_locking.py` |
| 3 | Flags removed; the ADM-002 route-enumeration test is still green with every router mounted | `grep -rn "admin_users_api_enabled\|admin_rbac_api_enabled\|ADMIN_USERS_API_ENABLED\|ADMIN_RBAC_API_ENABLED\|_admin_apis_gate" app tests .env.example docs` returns nothing except the ADM-002b history line in ADR-0008/deploy.md. `test_config.py` › `test_admin_api_flags_removed`. `test_route_auth_classes.py` passes with plain `create_app()` |
| 4 | The escalation guards land with their tests in the **same change** that removes the flags | One commit `ADM-002b: …` contains the guards, the user-status guards, the origin-collision checks and the flag removal. The reviewer checks `git log` on the ticket branch: no commit mounts the routers without the guards |
| 5 | `make check` green | `make check` output in impl-log |
| 6 | (Scope) Pre-removal audit recorded | The impl-log has the output of the **Approach §8** SQL run against the local DB (user ids only, no emails), and `docs/deploy.md` has the same SQL |
| 7 | (Scope) Owner decision on `/docs` and `/openapi.json` | `test_docs_off_in_production`, `test_docs_on_by_default_outside_production`, `test_docs_explicit_override`, and the deploy.md row |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/rbac/actor.py` (new) | `SystemActor(name, privileged)`, `SYSTEM_CLI` (privileged), `SYSTEM_JOIN` (not privileged), `Actor = AdminPrincipal \| SystemActor`, plus helpers `is_super(actor)`, `require_super(actor)` and `actor_user_id(actor)` | G0: an HTTP-free actor (R1); a required argument with an explicit system actor, so nothing fails open |
| `app/core/errors.py` | Add `AdminWildcardRequiredError` (403 `admin_wildcard_required`), `SelfAssignmentForbiddenError` (403 `self_assignment_forbidden`), `SelfStatusChangeForbiddenError` (403 `self_status_change_forbidden`), `LastSuperAdminError` (409 `last_super_admin`), `RoleHasAssignmentsError` (409 `role_has_assignments`) | Stable codes inside the R6 envelope (see **Approach §5**) |
| `app/modules/rbac/service.py` | Required keyword `actor` on every mutator; the guards in **Approach §3**; `_validate_admin_origins` extracted from `set_admin_origins` and reused by `update`; `_counting_super_admin` gets the verified-email condition (through the repository); `granted_by = actor_user_id(actor)`; replace all eight `# ADM-002 guard` comments | Core of the ticket |
| `app/modules/rbac/repository.py` | Add `permission_held_by_assigned_role(permission_id)` and `user_has_privileged_assignment(user_id)`; `_counting_super_admin` adds `User.email_verified_at IS NOT NULL` | G3 flip-409, G5 and the verified count |
| `app/modules/users/service.py` | `set_status(user_id, status, *, actor)`: lock order, self refusal, `admin:*` for privileged targets, last-super-admin guard, session revocation on disable | G5 (USR-02c F2) |
| `app/modules/users/router.py` | PATCH takes `principal: AdminDep` and passes `actor=principal`; docstring no longer says "flag-gated" | G0 |
| `app/modules/rbac/router.py` | Each mutating route takes `principal: AdminDep` and passes it; `update_app` also passes `app_env` from `request.app.state.settings`; add 409 to `responses` where new 409s can occur; fix the docstring | G0 |
| `app/modules/rbac/assignments_router.py` | POST/DELETE take `principal: AdminDep`; fix the docstring | G0 |
| `app/modules/rbac/catalog_router.py` | Docstring only: drop the reference to `admin_rbac_api_enabled` | Flag removal |
| `app/modules/membership/service.py` | `_grant_locked(..., actor=SYSTEM_JOIN)` | The actor is now required (join keeps `granted_by = NULL`) |
| `app/cli.py` | Pass `actor=SYSTEM_CLI` wherever the CLI reaches a mutator, if needed. `register` passes it internally, so the CLI itself probably needs no change; record what was done in impl-log | The actor is now required |
| `app/core/config.py` | Delete `admin_users_api_enabled`, `admin_rbac_api_enabled` and `_admin_apis_gate`. Add `api_docs_enabled: bool \| None = None` and a `docs_enabled` property (`api_docs_enabled` when set, else `app_env != "production"`) | Flag removal; docs decision |
| `app/main.py` | Mount `users_router`, `rbac_router` and `assignments_router` unconditionally. Pass `docs_url="/docs" if docs else None, openapi_url="/openapi.json" if docs else None`; replace the Swagger comment | Flag removal; docs decision |
| `.env.example` | Remove `ADMIN_USERS_API_ENABLED=` and `ADMIN_RBAC_API_ENABLED=`; add `API_DOCS_ENABLED=` with a comment | R5 |
| `tests/conftest.py` | `_override_admin` and `make_client` / `make_strict_app` / `make_strict_client` take `admin_user: User \| None = None` (the principal uses `admin_user.id`, else `uuid4()`); `make_client` asserts every settings override key is in `Settings.model_fields` | Self-guard and `granted_by` tests; stale flag kwargs fail loudly |
| `tests/test_admin_guards.py` (new) | Guard matrix and invariants (see **Tests to write**) | AC1, AC2 |
| `tests/test_users_admin.py` | Remove the flag tests (l.60–87) and the flag kwarg; add the disable/enable guard tests here or in `test_admin_guards.py` | AC3 |
| `tests/test_route_auth_classes.py` | Remove `FLAGS`; `_app()` = `create_app()` (test settings, docs on); keep `DOCS`; add `test_docs_off_in_production` | AC3, AC7 |
| `tests/test_config.py` | Delete the 5 flag gate tests; add `test_admin_api_flags_removed` and the `docs_enabled` default/override tests | AC3, AC7 |
| `tests/test_rbac_apps_api.py`, `test_rbac_permissions_api.py`, `test_rbac_roles_api.py`, `test_rbac_assignments_api.py`, `test_rbac_locking.py`, `test_role_catalog_api.py`, `test_auth_cors.py`, `test_admin_auth.py`, `test_cli_grant_super_admin.py` | Drop the flag kwargs; delete the four "not mounted by default" / `test_flag_off_404` tests (`test_every_bearer_route_401_without_token` covers them); update the revoke-409 code to `last_super_admin`; adjust any `granted_by is None` assert to the actor id; add the concurrent pair to `test_rbac_locking.py` | AC2, AC3 |
| `tests/test_cli_register_app.py`, `tests/test_cli_set_admin_origins.py` | Only if the extracted `_validate_admin_origins` or the `SYSTEM_CLI` plumbing changes behaviour: it must not. Keep them green with no change if possible | Regression |
| `docs/adr/0008-admin-api-authentication.md` | Add `## Amendment — admin RBAC guards (ADM-002b, 2026-11-15)` covering the actor model, G1–G6, error codes, check order, the stale-permission window, docs off in production. Fix §6 "with all flags on" | Rule 7: a decision that outlives the ticket |
| `docs/identity-architecture.md` | §4: remove "flag-gated until ADM-002b" from the three admin rows; add one paragraph on the `admin:*` guards and the new codes | Docs |
| `docs/deploy.md` | "Admin access": users/RBAC are live in production; `/docs` and `/openapi.json` are off in production (`API_DOCS_ENABLED=true` re-enables them); add a "Pre-ADM-002b audit (read-only)" subsection with the **Approach §8** SQL to run with the one-off `psql`; in "Bootstrap", remove "`ADMIN_RBAC_API_ENABLED` is not needed"; add `API_DOCS_ENABLED` to "Required runtime config" as optional | Docs |
| `work/tickets/ADM-002d/ticket.md` + `work/backlog.md` row | **Not by the implementer.** The orchestrator creates ADM-002d (`/ticket`) from **Split proposal** | Split |

No migration.

## Tests to write
Fixtures: `make_client(admin_permissions=[...], admin_user=<User>)`. "Leaf" means the principal holds the route's leaf permission only, for example `["admin:roles:manage"]`. "Super" means `["admin:*"]`. "Nothing written" means re-read the row or rows and assert they are unchanged; where a timestamp exists, assert `updated_at` is unchanged.

| Test (file › name) | Covers |
|---|---|
| `test_admin_guards.py › test_guard_matrix[site-leaf]` (parametrised over every row of the Approach §3 table) | 403 `admin_wildcard_required`, `details == {"required": "admin:*"}`, nothing written |
| `test_admin_guards.py › test_guard_matrix[site-super]` | the same request succeeds for super |
| `test_admin_guards.py › test_leaf_allowed_on_ordinary_objects` | leaf `roles:manage` can still create a non-admin permission or role and PUT non-admin permissions; leaf `roles:assign` can grant and revoke a plain role; leaf `apps:write` can PATCH a non-admin app; leaf `users:write` can disable an ordinary user (no over-blocking) |
| `… › test_role_put_adds_is_admin_leaf_403` / `test_role_put_removes_is_admin_leaf_403` | both diff directions (G2) |
| `… › test_role_put_unchanged_super_admin_ok` | re-PUT of super-admin with the same set: no 409, no write |
| `… › test_permission_is_admin_false_to_true_leaf_403` / `…_true_to_false_leaf_403` | both flip directions |
| `… › test_permission_description_only_leaf_ok` | description edit of a non-admin-app `is_admin` permission by a leaf holder → 200 |
| `… › test_is_admin_flip_on_assigned_role_409` | super flips false→true on a permission held by an assigned role → 409 `role_has_assignments` |
| `… › test_is_admin_onto_assigned_role_409` | super PUTs a new `is_admin` permission into a role with assignments → 409 `role_has_assignments`; the same PUT into an unassigned role → 200 |
| `… › test_wildcard_only_on_super_admin_422` | super PUTs `admin:*` into another admin-app role → 422 `wildcard_super_admin_only` |
| `… › test_grant_renewal_branch_guarded` | expired duplicate of a privileged role: leaf → 403, the row is unchanged (`expires_at`, `granted_by`); super → renewed, `granted_by == super.id` |
| `… › test_granted_by_is_actor` | grant by super with `admin_user=u` → response and row `granted_by == u.id` |
| `… › test_sock_puppet_grant_refused` | leaf `roles:assign` grants `admin:super-admin`, an admin-app leaf role, or a `stoktok:*`-holding role to *another* user → 403 each |
| `… › test_self_grant_admin_role_403` | super with `admin_user=u` grants an admin-app role to `u` → 403 `self_assignment_forbidden`; a leaf gets 403 `admin_wildcard_required` |
| `… › test_self_grant_non_admin_app_role_ok` | super grants `stoktok:admin` (an `is_admin` role) to self → 201 (the rule is admin-app only) |
| `… › test_self_revoke_admin_role_403` | super revokes own admin-app assignment → 403 `self_assignment_forbidden`, the row is kept |
| `… › test_last_super_admin_revoke_409` | actor `admin:*` via override, not a counting super-admin; the target is the only counting super-admin → 409 `last_super_admin` |
| `… › test_unverified_super_admin_not_counted` | two super-admins, one unverified: revoking or disabling the verified one → 409 |
| `… › test_admin_app_patch_leaf_403` / `test_admin_app_same_value_patch_leaf_ok` | G1 on the admin app; a no-op PATCH stays 200 with no write |
| `… › test_admin_origins_patch_super_validated` | super PATCH of admin origins: apex → 422 `apex_not_allowed`; 3 origins → 422 `too_many_origins`; an origin held by `stoktok` → 409 `origin_taken`; a valid one → 200 and `mark_apps_changed` |
| `… › test_app_create_origin_taken_409` / `test_app_patch_origin_taken_409` / `test_app_create_admin_origin_taken_409` | G6 against another app (any status) and against `admin`; loopback origins are exempt from the uniqueness check |
| `… › test_disable_self_403` / `test_enable_self_403` | `admin_user=u` PATCH own status → 403 `self_status_change_forbidden`, unchanged |
| `… › test_disable_privileged_user_leaf_403` / `test_reenable_disabled_staff_leaf_403` | G5, including re-enable of a disabled super-admin or staff user (leaf `users:write` → 403; super → 200) |
| `… › test_disable_last_super_admin_409` | 409 `last_super_admin`, the user stays active |
| `… › test_disable_revokes_account_sessions` | the user has account sessions on 2 devices: after disable, all have `revoked_at`; device sessions and other users' account sessions are untouched; `/auth/token` with the old cookie → refused |
| `… › test_disabled_user_pending_reset_token_unusable` | a reset token issued before disable → reset refused (identity consult risk; confirms existing behaviour) |
| `test_rbac_locking.py › test_concurrent_disable_pair_serialized` | two committed super-admins A and B on two connections: A disables B, B disables A, serialized by `lock_app("admin")`. Exactly one succeeds; the other → 409 `last_super_admin`; one super-admin stays active (uses the existing two-engine pattern) |
| `test_rbac_locking.py › test_concurrent_revoke_and_disable_serialized` | revoke of A's super-admin and disable of B racing → one 409 |
| `test_rbac_locking.py › test_user_status_takes_admin_lock_first` | SQL capture: the `lock_app("admin")` statement precedes the user `FOR NO KEY UPDATE` |
| `test_config.py › test_admin_api_flags_removed` | `admin_users_api_enabled` and `admin_rbac_api_enabled` are not in `Settings.model_fields` |
| `test_config.py › test_docs_enabled_defaults` / `test_docs_enabled_override` | production → False, local/test/staging → True; an explicit value wins |
| `test_route_auth_classes.py › test_every_route_is_classified` (existing, now `create_app()`) | route enumeration with all routers mounted |
| `test_route_auth_classes.py › test_docs_off_in_production` | `create_app(<valid production settings>)`: no `/docs`, `/openapi.json` or `/docs/oauth2-redirect` routes, and `GET /docs` → 404 R6 envelope |
| `test_rbac_assignments_api.py › existing revoke last-super-admin test` | code now `last_super_admin` |
| Existing suites | Green after the flag kwargs are dropped (regression for every non-guard behaviour) |

## Approach
Implement in this order and land it as **one commit** (AC4). Write `test_admin_guards.py` first against the flags-on app, then remove the flags.

### 1. Actor (G0, R1)
- `app/modules/rbac/actor.py`:
  - `@dataclass(frozen=True) class SystemActor: name: str; privileged: bool`.
  - `SYSTEM_CLI = SystemActor("system:cli", True)` and `SYSTEM_JOIN = SystemActor("system:join", False)`.
  - `Actor = AdminPrincipal | SystemActor`. `AdminPrincipal` is already a frozen, HTTP-free dataclass. Importing it from `admin_auth.service` creates no cycle (identity consult finding 3).
  - `is_super(a)` is `a.privileged` for a `SystemActor`, else `permits(a.permissions, "admin:*")`.
  - `require_super(a)` raises `AdminWildcardRequiredError("Requires admin:*", details={"required": "admin:*"})`.
  - `actor_user_id(a)` is `None` for a `SystemActor`, else `a.user_id`. `token_id` is never used in guard logic.
- Every public mutator, and `_grant_locked`, takes `*, actor: Actor` with **no default**: `AppService.create/update/delete`, `PermissionService.create/update/delete`, `RoleService.create/update/delete/replace_permissions`, `AssignmentService.assign/revoke/_grant_locked`, `UserService.set_status`. `RoleCatalogService.build_default_role` gets one as well, if it has callers. mypy `--strict` then flags any caller that forgets it.
  - `register` and `set_admin_origins` pass `SYSTEM_CLI` to their internal calls.
  - `bootstrap_super_admin` passes `SYSTEM_CLI`.
  - Membership join passes `SYSTEM_JOIN`. It is not privileged, so if a default role ever held `is_admin`, the grant would fail closed. Join already refuses that case first.
- Routers declare `principal: AdminDep` and pass `actor=principal`. They keep the router-level `Depends(current_admin)`, so the enumeration test still sees it. FastAPI caches the dependency per request.

### 2. Check order at every site
Lock the app (ADR-0003 §10) → 404 → request-shape 422s that need no privilege (prefix, wildcard, unknown keys, `wildcard_super_admin_only`) → **403 guards** → state 409s / state 422s → write. Authorized callers are all staff, so returning 404 before 403 hides nothing that matters (api-platform consult rec 3).

### 3. Guard table
`S` means `require_super(actor)`. "Privileged role" means `app.key == "admin"` or `repo.role_has_admin_permission(role.id)`, evaluated under the lock.

| Site | Rule (in order after lock/404/422) | Closes |
|---|---|---|
| `AppService.create` | G6: non-loopback `data.origins` held by any other app (any status, `admin` included) → 409 `conflict` with details `[{field:"origins", reason:"origin_taken"}]` (the same shape as the CLI). No S: a new app has no admins | k |
| `AppService.update` | Compute `changes` (fields whose value differs). If `app.key == "admin"` and `changes` → S (G1; a no-op PATCH stays allowed with no write). Then the existing admin invariants (409). If `"origins" in changes`: for admin, run `_validate_admin_origins(origins, app_env)` (apex, max 2, loopback in staging/prod; 422s as in `set_admin_origins`); for every app, run the origin-taken check excluding `app.key` → 409 | j, k |
| `AppService.delete` | No new guard (`admin` is already refused; apps with assignments are refused). Actor is required (R4 later) | — |
| `PermissionService.create` | S if `app.key == "admin"` or the resolved `is_admin` (this includes `<app>:*`) | b, c-prep |
| `PermissionService.update` | S if (`app.key == "admin"` and something changes) or (`"is_admin" in fields` and `data.is_admin != perm.is_admin`). Then the existing default-role 409. Then G3: false→true and `repo.permission_held_by_assigned_role(perm.id)` → 409 `role_has_assignments`, also for super | d (both directions) |
| `PermissionService.delete` | S if `app.key == "admin"` | e |
| `RoleService.create` | S if `app.key == "admin"` | b |
| `RoleService.update` | S if `app.key == "admin"` and the name changes | e |
| `RoleService.delete` | S if `app.key == "admin"` | e |
| `RoleService.replace_permissions` | After the unknown-key 422: if `app.key == "admin"` and `"admin:*"` is in the target set and `role.key != "admin:super-admin"` → 422 `unprocessable` `[{field:"permission_keys", reason:"wildcard_super_admin_only"}]` (G3, also for super). Diff the target against `role_permission_keys`, and load the removed permissions with `permissions_by_keys` to read their `is_admin`. S if (`app.key == "admin"` and the diff is non-empty) or any added or removed permission is `is_admin`. Then the existing 409s (default role; super-admin keeps `admin:*`). Then G3: any **added** `is_admin` permission and `role_has_assignments(role.id)` → 409 `role_has_assignments`, also for super. Diff-based, so an unchanged re-PUT never refuses | a, c |
| `AssignmentService._grant_locked` (insert **and** expired-renewal branch; the guard sits before `find_assignment`) | After the org/expiry 422s, for a privileged role: S. Then, if `app.key == "admin"` and `user_id == actor_user_id(actor)` → 403 `self_assignment_forbidden` (also for super). `granted_by = actor_user_id(actor)` on both branches. Remove the "may not grant permissions they do not hold" comment: admin principals only hold `admin:` keys, so S covers it (identity consult) | f, g, i |
| `AssignmentService.revoke` | For a privileged role: S. Self (admin app and own row) → 403 `self_assignment_forbidden`. Then the existing last-super-admin check → `LastSuperAdminError` (409 `last_super_admin`) | h |
| `UserService.set_status` | `repo.lock_app("admin")` → `users.get_for_update(user_id)` → 404 → unchanged status: return (no write, no guard). Target == actor → 403 `self_status_change_forbidden` (disable **and** enable). `user_has_privileged_assignment(target)` → S (disable **and** re-enable, G5). When disabling: if the target's app-scope super-admin assignment is counting and `count_other_active_super_admins == 0` → 409 `last_super_admin` (reuse the existing repository pair through `find_assignment(user, super_role, None)`). Write the status; on disable, `SessionRepository.revoke_all_accounts_of_user(user.id)` in the same transaction | l, m, n, o |

`user_has_privileged_assignment(user_id)`: EXISTS over `role_assignments` joined to roles and apps where `apps.key = 'admin'` or the role holds an `is_admin` permission. **Expired rows are included**, because an expired row can be renewed and failing safe costs little.

`permission_held_by_assigned_role(permission_id)`: EXISTS over `role_permissions` joined to `role_assignments` on `role_id`.

`_counting_super_admin`: add `User.email_verified_at.isnot(None)`. Such a user cannot get a token (D16), so it must not satisfy the guard.

### 4. Sessions on disable
`revoke_all_accounts_of_user` revokes the user's account sessions on every device. Device sessions themselves stay, because they may carry other users' accounts (identity consult finding 4). Admin tokens stop at once (per-request reload). App access tokens already issued last until `exp` (≤ 15 min), as documented in deploy.md.

### 5. Error codes (R6 envelope, stable `code`)
| Code | Status | Why |
|---|---|---|
| `admin_wildcard_required` | 403, `details: {"required": "admin:*"}` | A `PermissionDeniedError` subclass with the same details shape as `RequirePermission`, so the UI can show "needs super-admin"; distinct from a missing route permission |
| `self_assignment_forbidden` | 403, `details: null` | A policy on *who* acts, not the data state, so 403. It applies to super-admins too |
| `self_status_change_forbidden` | 403, `details: null` | Same reasoning; one code for disable and enable |
| `last_super_admin` | 409 | The data state forbids it. Also replaces the existing revoke `conflict` code; that route was never mounted in production, so this is not a v1 break (api consult risk) |
| `role_has_assignments` | 409, details `[{field, reason:"role_has_assignments"}]` (`field` = `permission_keys` or `is_admin`) | The state of the role forbids it, for every actor |
| `unprocessable` / `wildcard_super_admin_only` | 422 | The same family as the existing `wildcard_must_be_admin` |
| `conflict` / `origin_taken` | 409 | The same shape as `register-app` and `set-admin-origins` (the api consult said 422, but the code uses 409 on both CLI paths; consistency wins) |

The existing generic 409 `conflict` messages are unchanged. Routes that can now return a new 409 or 403 list it in `responses`.

### 6. Flag removal
- Delete both fields and `_admin_apis_gate`. `extra="ignore"` keeps a stale deploy env harmless (identity finding 9).
- Mount the three routers unconditionally after `api_router`. Remove the `.env.example` lines, the docstrings and the comments.
- Remove the flag tests; drop every flag kwarg. `make_client` asserts override keys are known, so stale kwargs fail loudly.

### 7. Docs in production
- `api_docs_enabled: bool | None = None`. The property `docs_enabled` returns the explicit value, else `app_env != "production"`.
- `create_app` passes `docs_url` and `openapi_url` accordingly (`redoc_url` stays `None`).
- The `DOCS` allow-list stays for non-production; the new test asserts no doc routes in production.

### 8. Pre-removal audit (read-only)
- The implementer runs this in `make db-up` local `org_admin` (and the test DB after migration) and pastes the output into impl-log, with user ids only.
- The same block goes into deploy.md under "Pre-ADM-002b audit (read-only)", for the owner to run later through the one-off `psql` from "Operations". **No ops command in this ticket.**
- Production should show only the bootstrap super-admin, because the flags were refused there.

```sql
BEGIN READ ONLY;
-- 1. Admin-app assignments (flag self-grants and non-bootstrap NULL granted_by)
SELECT ra.id, ra.user_id, r.key AS role, ra.granted_by, ra.created_at, ra.expires_at,
       (ra.granted_by = ra.user_id) AS self_granted, u.status, (u.email_verified_at IS NOT NULL) AS verified
FROM role_assignments ra JOIN roles r ON r.id = ra.role_id JOIN apps a ON a.id = r.app_id
JOIN users u ON u.id = ra.user_id
WHERE a.key = 'admin' ORDER BY ra.created_at;
-- 2a. Admin-app permissions outside the seed or not is_admin
SELECT p.key, p.is_admin, p.created_at FROM permissions p JOIN apps a ON a.id = p.app_id
WHERE a.key = 'admin' AND (p.is_admin = false OR p.key NOT IN (
  'admin:*','admin:organizations:read','admin:organizations:write','admin:users:read',
  'admin:users:write','admin:apps:read','admin:apps:write','admin:roles:manage','admin:roles:assign'));
-- 2b. Admin-app roles other than super-admin, with permissions; any non-super role holding admin:*
SELECT r.key, r.created_at, array_agg(p.key ORDER BY p.key) AS perms
FROM roles r JOIN apps a ON a.id = r.app_id
LEFT JOIN role_permissions rp ON rp.role_id = r.id LEFT JOIN permissions p ON p.id = rp.permission_id
WHERE a.key = 'admin' AND r.key <> 'admin:super-admin' GROUP BY r.key, r.created_at;
-- 3. Roles holding an is_admin permission that have assignments; default roles holding one (expect none)
SELECT a.key AS app, r.key AS role, count(DISTINCT ra.id) AS assignments,
       (a.default_role_id = r.id) AS is_default
FROM roles r JOIN apps a ON a.id = r.app_id
JOIN role_permissions rp ON rp.role_id = r.id JOIN permissions p ON p.id = rp.permission_id AND p.is_admin
LEFT JOIN role_assignments ra ON ra.role_id = r.id
GROUP BY a.key, r.key, a.default_role_id, r.id
HAVING count(ra.id) > 0 OR a.default_role_id = r.id;
-- 4. Admin app state; origins held by more than one app
SELECT key, status, join_policy, org_mode, default_role_id, origins FROM apps WHERE key = 'admin';
SELECT o AS origin, array_agg(key) AS apps FROM apps, unnest(origins) o GROUP BY o HAVING count(*) > 1;
-- 5. Disabled users still holding admin-app assignments (re-enable candidates)
SELECT u.id, r.key FROM users u JOIN role_assignments ra ON ra.user_id = u.id
JOIN roles r ON r.id = ra.role_id JOIN apps a ON a.id = r.app_id
WHERE a.key = 'admin' AND u.status = 'disabled';
ROLLBACK;
```

If the seed list in 2a differs from migration `5e2a9c7d1f04`, the implementer uses the migration's list and notes it.

### Rules
- **R1:** the actor is a plain dataclass, services raise `AppError` subclasses, and the new queries live in the repository.
- **R3:** every endpoint keeps its class; enumeration runs with everything mounted.
- **R4:** comments stay; this ticket adds no audit table (ADM-004).
- **R5:** a new env key goes into `.env.example`; no secrets.
- **R6:** new codes inside the envelope.
- **R7:** extras go to ADM-002d.
- **R8:** see Tests.
- **R11:** sessions are revoked on disable; no emails in errors, logs or the audit output.

## Specialist input used
- **security-architect:** adopted the G0–G6 baseline and the full path list (a–o), the verified-email count, the audit SQL (§8) and docs off in production. For OQ1, admin origins via the API are allowed for super with the CLI validation, and the CLI stays the documented path. OQ2: G5 is adopted (owner default). OQ3: yes, removal is guarded too. OQ4: yes, the count requires a verified email. OQ5: owner default A1. The DB-level origin guard and the advisory lock are a follow-up (risk accepted, documented in the ADR amendment).
- **identity-access-engineer:**
  - Adopted: a required actor with a system sentinel; the per-site rules; the diff-based PUT; self-revoke refused; disable lock order; reuse of `revoke_all_accounts_of_user`; reuse of `app_keys_holding_origins`.
  - Changed: self-disable is 403 rather than 409, because it is a policy on the actor, consistent with self-grant. Join gets a non-privileged `SYSTEM_JOIN` rather than a bare sentinel.
  - Deferred to ADM-002d: forcing `is_admin=true` on admin-app permission create, and the re-read of `effective_permissions` after the lock (optional; the stale window is documented).
  - Moved to ADM-002d: `strict_aud`, the seed fix, `--clear`.
- **api-platform-engineer:**
  - Adopted: the flag-surface inventory; `principal: AdminDep` while keeping the router-level dependency; the distinct 403 codes; `last_super_admin`; `role_has_assignments`; `wildcard_super_admin_only` as 422; the fixture with a real user id; `make_client` rejecting unknown overrides (this answers OQ4: it is needed to make the flag removal verifiable); `api_docs_enabled`.
  - Rejected: `origin_taken` as 422. The code returns 409 on both CLI paths.
  - Changed: the check order puts request-shape 422s before 403 (they need no privilege and reveal nothing).
  - OQ3: the `role_has_assignments` 409 applies to every actor (ticket wording).

## Assumptions
These are owner defaults, recorded rather than blocking.
- **A1:** `/docs` and `/openapi.json` are off in production by default and on elsewhere; `API_DOCS_ENABLED=true` re-enables them. This changes spec §6.1 line 85. **Follow-up for product-manager:** update that line (the spec is not edited here).
- **A2:** Self-revoke of admin-app roles is refused for everyone, super-admins included. To step down, another super-admin revokes the role.
- **A3:** Disabling or re-enabling a user who holds any admin-app or `is_admin` role (expired rows included) requires `admin:*`.
- **A4:** The super-admin count requires `email_verified_at IS NOT NULL` and `status = 'active'`.
- **A5:** Admin-app origins can be changed through the API with `admin:*` and the same validation as the CLI. `set-admin-origins` stays the documented path in deploy.md and ADR-0008.
- **A6:** The `role_has_assignments` 409 (adding an `is_admin` permission to an assigned role, or flipping one to `is_admin` while an assigned role holds it) applies to every app, `admin` included, and to every actor. The workaround is revoke → edit → re-grant. The owner may relax it later for the admin app.
- **A7:** Permissions are read before the app lock, so a concurrent revoke of `admin:*` can pass one request. This is accepted for v1 and documented in the ADR amendment.
- **A8:** The origin-uniqueness check races across different app locks. This is accepted (staff-only, deploy serialization) and goes to a follow-up for a DB-level guard.
- **A9:** Estimated size is about 330–380 changed production lines (actor ~40, errors ~30, rbac service ~130, repository ~30, users service ~45, routers ~45, config/main ~25, membership/cli ~5) and about 800+ test lines. This is above the ~400 guideline, but AC4 forbids splitting the guards from the flag removal, and the peripheral items have already moved to ADM-002d.

## Blocking questions
None.

## Split proposal
This plan covers **ADM-002b**: the guards, user-status guards, origin checks, flag removal, docs setting, audit SQL and docs.

**ADM-002d (new ticket, P2, areas auth/security; depends on ADM-002b)** gates nothing in this ticket. Its scope:
1. Add `strict_aud: True` to `app/core/access_token.py` decode options, with test cases for a list `aud` (`["admin"]` and `["admin","x"]` → reason `audience`), and mirror it in `docs/consumers.md` and the mmlabs-auth note (ADM-002 security 🟡1).
2. A new data migration `UPDATE permissions SET is_admin = true` for every admin-app row, with a documented no-op `downgrade()` (an R2 exception), so that seed migration `5e2a9c7d1f04` stays untouched (🟡2). Optionally, force `is_admin=true` on API creation of admin-app permissions.
3. `set-admin-origins --clear` works on a drifted admin row: it still locks and requires the row, skips only the drift check when `origins == []`, and writes only `origins`. Needs a test and ADR-0008/deploy.md wording (ADM-002c 🟡1).
4. deploy.md runbook hardening:
   - keep the admin Bearer token out of shell history and `ps` (capture it into a variable, send it via `-H @file` with mode 0600, then unset/rm);
   - run the block in a subshell with `trap` cleanup, ending with `/auth/logout`;
   - add a note that `JWT_PUBLIC_KEYS_EXTRA` must hold only this service's retired or next keys (ADM-002c 🟡2/💭, ADM-002 note).
5. Refuse signup and forgot-password with `X-MM-App: admin` (ADM-002c 💭).

**Other follow-ups (not ADM-002d):**
- product-manager: spec §6.1 line 85 (A1).
- A DB-level, cross-app origin-uniqueness guard or advisory lock (A8).
- An optional post-lock re-read of `admin:*` (A7).
- ADM-004 right after this ticket, since misuse cannot be reconstructed without the audit log.
- The ops-workflow command for the audit SQL.

## Data model / migration
None. No schema change: the verified-email count, the new EXISTS queries and the settings field need no migration, and `alembic check` must stay clean.

## API contract
No new endpoints. Each existing mutating route now forwards the principal, and the outcomes below are new. Every route keeps its `x-permission`.

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/apps` | `AppCreate` | `AppRead` | 201; 409 `conflict`/`origin_taken` (new) | `admin:apps:write` |
| PATCH | `/api/v1/apps/{key}` | `AppUpdate` | `AppRead` | 200; 403 `admin_wildcard_required` (admin app, a change); 422 `apex_not_allowed` / `too_many_origins` / `loopback_not_allowed` (admin origins); 409 `origin_taken` | `admin:apps:write` (+ `admin:*` for admin) |
| POST | `/api/v1/apps/{key}/permissions` | `PermissionCreate` | `PermissionRead` | 201; 403 `admin_wildcard_required` (admin app, `is_admin`, `<app>:*`) | `admin:roles:manage` (+ `admin:*`) |
| PATCH | `/api/v1/apps/{key}/permissions/{id}` | `PermissionUpdate` | `PermissionRead` | 200; 403 (admin app change, `is_admin` change); 409 `role_has_assignments` (false→true on an assigned role) | `admin:roles:manage` (+ `admin:*`) |
| DELETE | `/api/v1/apps/{key}/permissions/{id}` | — | — | 204; 403 (admin app) | `admin:roles:manage` (+ `admin:*`) |
| POST / PATCH / DELETE | `/api/v1/apps/{key}/roles[/{id}]` | `RoleCreate` / `RoleUpdate` | `RoleRead` | 201/200/204; 403 (admin app) | `admin:roles:manage` (+ `admin:*`) |
| PUT | `/api/v1/apps/{key}/roles/{id}/permissions` | `RolePermissionsReplace` | `RoleRead` | 200; 422 `wildcard_super_admin_only`; 403 (admin app diff, `is_admin` added/removed); 409 `role_has_assignments` | `admin:roles:manage` (+ `admin:*`) |
| POST | `/api/v1/users/{id}/role-assignments` | `AssignmentCreate` | `AssignmentRead` (`granted_by` = actor) | 201; 403 `admin_wildcard_required`, 403 `self_assignment_forbidden`; 409 existing | `admin:roles:assign` (+ `admin:*` for privileged roles) |
| DELETE | `/api/v1/users/{id}/role-assignments/{aid}` | — | — | 204; 403 both codes; 409 `last_super_admin` | `admin:roles:assign` (+ `admin:*`) |
| PATCH | `/api/v1/users/{id}` | `UserStatusUpdate` | `UserRead` | 200; 403 `self_status_change_forbidden`, 403 `admin_wildcard_required` (privileged target); 409 `last_super_admin` | `admin:users:write` (+ `admin:*` for privileged targets) |
| GET | `/docs`, `/openapi.json` | — | — | 404 in production unless `API_DOCS_ENABLED=true` | public (non-production) |
