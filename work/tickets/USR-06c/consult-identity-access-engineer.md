## identity-access-engineer consult: USR-06c (org-exact roles in tokens and default-role hygiene)

I read: ticket USR-06c, ADR-0013, ADR-0014, the USR-06 and USR-06b security reviews and consults, `app/modules/tokens/service.py`, `app/modules/rbac/{repository,service,authz,models,schemas}.py`, `app/modules/invitations/{service,rules}.py`, `app/modules/organizations/auth_service.py`, `docs/consumers.md`, `docs/identity-architecture.md` D7/D9/D10, spec §5.8, and USR-05.

### Findings

**F1. The token builds `roles` as the union and throws away the org-exact read.**
- In `app/modules/tokens/service.py:88-90`, `org_role_keys(...)` is used only as a gate. `roles` is then overwritten with `effective_roles(..., org_id)`, which is app scope plus the org.
- So the org-exact data needed for AC1 is already computed on every org-token request. It is simply not emitted.

**F2. The `roles` claim is a v1 contract.**
- The union meaning is documented in four places: spec §5.8 claims, D7/D10, ADR-0013 §6 and `docs/consumers.md:373-379` ("`roles` mixes scopes").
- Changing `roles` to org-exact in org tokens would change the meaning of an existing claim. R9 does not allow that inside v1.
- An additive claim is R9-safe:
  - `tests/test_auth_token.py:90` asserts the exact claim set only for non-org tokens.
  - PyJWT and `mmlabs-auth` ignore unknown claims.

**F3. There is no data definition of "org-level permission" today.**
- ADR-0014 §11 uses "permissions of the owner role, minus `<app>:org:create`" as the definition. It has known holes:
  - **Sparse owner role (N-2):** an org-level permission the owner role does not hold is not caught.
  - **No owner role at app creation:** `_create` → `_build_for_app` runs before staff can set `org_owner_role_id`.
  - **Coupling:** editing the owner role silently changes what the next default-role rebuild includes.
  - **Not declarable:** neither the CLI nor the USR-05 manifest can declare it.

**F4. There is a third re-widening path the ticket does not name.**
- The operator CLI `register-app` (`RbacService.register`, `app/modules/rbac/service.py:574-586`) creates permissions with `is_admin=False`. Whenever anything is added, it calls `_build_for_app(app, set_default=False)`.
- It accepts `--org-mode required`, but it has no way to declare a permission org-level. For calechip, every CLI-registered permission lands in `<app>:member` today.
- So AC2 must cover this path too, not only seed, app creation and the manifest.

**F5. The `set_default_role_if_null` bypass is real.**
- Staff can `PATCH org_owner_role_id = <app>:member` while `default_role_id` is NULL. `_update` (lines 428-435) only compares the owner role with the current default.
- A later `build_default_role` then sets default = member = owner (`repository.py:101-108`; the WHERE clause checks only `default_role_id IS NULL`).
- All writers lock the app row (`lock_app`), so there is no race. The gap is purely a missing condition.

**F6. The USR-06b guard (`invitations/service.py:133-150`, `rules.roles_overlap`) is an owner-derived proxy for the F3 definition.** Once a real marker exists, the overlap test can be restated exactly.

**F7. The residual risk after any fix: staff-granted app-scope roles that hold org-level permissions.**
- The union `roles` still carries such roles into every org token.
- `org_roles` removes this for consumers that follow the rule. Consumers that keep reading `roles` for org decisions stay exposed.

### Recommendations

**R1. Token: add the claim `org_roles` (AC1, R9). Do not change `roles` and do not add a scope marker in the token.**
- `org_roles`: sorted role keys held in exactly the `org` org. Use the same `org_role_keys` result already used for the 403 gate, so the gate and the claim come from one read.
- Present if and only if `org` is present. It is never empty, because the gate guarantees it.
- Keep `roles` as the D10 union.
- Do not nest it (for example `org: {id, roles}`): that would break the `org` string claim.
- Consumer rule for `consumers.md`:
  - Authorize anything inside the org using only the permissions of `org_roles`, mapped through the role catalog.
  - Use `roles` only for app-level features, such as creating or picking an org.
  - Keep "scope all data by `org`".
- Needs one spec §5.8 claims line plus amendments to D7/D10, ADR-0013 §6 and `consumers.md:373-379`.
- Tests:
  - An org token has `org_roles` equal to org-exact roles.
  - A role held only at app scope (for example `<app>:member`) appears in `roles` but not in `org_roles`.
  - A role held in org B does not appear in an org-A token.
  - A non-org token has no `org_roles` (the existing exact-set test stays green).

**R2. Classify org-level permissions with a data marker on the permission, not derived from the owner role (AC2; D9 "policy is data").**
- Column: `permissions.scope varchar NOT NULL DEFAULT 'app'` with `CHECK (scope IN ('app','org'))`. Keep it orthogonal to `is_admin`, with no combined CHECK.
- Put it on permissions, not roles. Roles mix permissions, and `build_default_role` selects permissions.
- API changes (additive): `PermissionRead.scope`, optional `PermissionCreate.scope` (default `app`), `PermissionUpdate.scope`. Add `scope` to the audit diff allowlist for permissions (R4).
- Reserved keys (422):
  - `<app>:org:create` must be `app` (the org-create check is app-scope).
  - `<app>:org:invite` must be `org`.
- Changing scope from `app` to `org` on an `org_mode = required` app:
  - Refuse with 409 while the default role holds the permission. This mirrors the `is_admin` guard at `service.py:799-806`.
- Changing scope from `org` to `app`:
  - It makes the permission eligible for the default role on the next rebuild.
  - Treat it like `is_admin` true→false and require `admin:*` (`require_super`).
- Migration (R2, hand-reviewed, working `downgrade()` that drops the column; it is lossy for `scope`, so document that):
  - Add the column with default `'app'`.
  - Backfill only for `org_mode = required` apps: `<app>:org:invite` plus the permissions held by `org_owner_role_id`, excluding `<app>:org:create`, become `'org'`. This keeps today's ADR-0014 meaning.
  - Do not touch role contents in the migration. The guard (R5) fails closed if the default role still holds them.
- Do not expose `scope` in the public role catalog in 06c. Make that an optional additive follow-up for `mmlabs-auth`.

**R3. Make `build_default_role` respect scope (AC2).**
- In `_build_for_app`, when `app.org_mode == required`, the target is `is_admin = false AND scope = 'app'`. `org_mode = none` keeps the current query unchanged.
- This one change covers every path: app creation (`_create`), `build_default_role`, CLI `register-app` (F4) and the future USR-05 manifest.
- `_set_role_permissions` sets an exact target, so a rebuild also removes org-scope permissions the member role already holds.
- CLI changes:
  - Add `--org-permission <key>` (allowed only when the resulting `org_mode` is `required`), which creates the permission with `scope='org'`.
  - Refuse (409) when a `--permission`/`--org-permission` names an existing key with the other scope. This mirrors the `is_admin` refusal at lines 530-537.
- Add one line to the USR-05 ticket: the manifest must carry `scope`.
- Other write paths that should enforce the same invariant (planner's call; the R5 guard is the backstop):
  - `PATCH default_role_id` to a role holding org-scope permissions on an org app → 422 `role_has_org_permissions`.
  - `replace_permissions` adding an org-scope permission to the default role of an org app → 409.
- Tests:
  - Build for an org app excludes `scope='org'` and removes it from an existing member role.
  - `org_mode = none` builds are unchanged, even with an `org`-scoped permission present.
  - CLI register on an org app does not widen the member role.

**R4. Owner ≠ default on every write path (AC3). Do both parts.**
- Repository: in `set_default_role_if_null`, add `App.org_owner_role_id.is_distinct_from(role_id)` to the WHERE clause.
  - The no-op leaves the default NULL, so join answers `join_unavailable` (fail closed).
  - Log a warning with ids only (R11).
- Database: add the CHECK `ck_apps_default_ne_org_owner`: `default_role_id IS NULL OR org_owner_role_id IS NULL OR default_role_id <> org_owner_role_id`.
  - The model text must match the migration exactly, because Alembic does not compare CHECKs.
  - In `upgrade()`, first count violating rows and abort with a clear message (same pattern as the 06a orphan check).
  - `downgrade()` drops the constraint.
- `_update` already maps `IntegrityError` to 409. `set_default_role_if_null` runs outside a savepoint, but the WHERE clause means the CHECK can never fire there.
- Tests:
  - Owner = member with default NULL, then `build_default_role` → default stays NULL.
  - Inserting or updating so that default = owner directly → `IntegrityError` (schema test).
  - The existing 422 `same_as_default_role` tests stay.

**R5. Relax `org_invite_unavailable` but keep it as a fail-closed backstop (AC4, ADR-0014 §11).**
- Replace `roles_overlap(default, owner)` with "the default role holds no `scope='org'` permission".
- Drop the "owner role is a superset" precondition and the `org:create` exception (the reserved-scope rule covers it).
- Keep the other conditions: `org_mode`, roles set and present, admin-free default role.
- Keep `join_policy = open` (B1 joins on accept). Removing it is a separate behaviour change (R7).
- The error code and the create/accept placement do not change, so there is no API change.
- With R3, a correctly configured org app keeps invitations working after a rebuild. That fixes "the manifest disables invitations".
- Update ADR-0014 §11 and Consequences, and `consumers.md:183-191` (the staff configuration becomes "mark org-level permissions `scope=org`").
- Tests:
  - An invitation works after `build_default_role` on an org app where the owner role and a member-level permission overlap, as long as that permission is `app`-scoped.
  - The guard still refuses when the default role holds an `org` permission (set by hand).

**R6. Docs (AC1, AC4, AC5).**
- New ADR-0015 "Org-scoped permissions and the `org_roles` claim", covering R1–R5 and the rejected alternatives:
  - Redefining `roles`: breaks R9 and D10.
  - Owner-derived classification: F3.
- Amend ADR-0013 (§6, Consequences), ADR-0014 §11, identity-architecture D7/D10 and the data-model line for `permissions`, and the spec §5.8 claims line (owner approval).
- `consumers.md`:
  - The org-authorization rule from R1.
  - Remove the "manifest re-widens" warnings at lines 142-145 and 189-191.
  - A follow-up for `mmlabs-auth`: expose `org_roles`, add a `require_org_permission(k)` that uses only `org_roles`, and optionally read the catalog `scope`.

### Risks
- **Consumer adoption.** `org_roles` protects only consumers that use it. calechip has not shipped against org tokens, so set the rule now. A consumer reading `roles` for org decisions is still exposed to staff-granted app-scope roles that hold org permissions (F7).
- **Backfill correctness.** If calechip in production has a sparse owner role, the owner-derived backfill under-marks permissions. The guard (R5) then passes while the member role still holds an unmarked org permission. Staff must review the marked set after deploy; add a `deploy.md` query.
- **Scope flips are a new privilege boundary.** Without the R2 guards, a scope change plus a rebuild can silently widen or narrow the member role.
- **CLI behaviour change for org apps.** Permissions registered without `--org-permission` count as app-level and go into the member role. This is correct by definition, but it is an operator footgun, so document it in the CLI help.
- **Scope creep.** R3's extra write-path invariants and the R2 privilege rule enlarge the diff. The R5 guard is the minimal backstop if the planner trims them.

### Open questions (owner)
1. **Blocking for AC1:** approve adding to spec §5.8 claims: "`org_roles` (org-exact role keys; only when `org` is present)". Every option that meets AC1 needs a spec change. The only alternative, redefining `roles`, is a breaking change under R9. Recommended: approve `org_roles`.
2. Approve the data marker `permissions.scope` (`app|org`) over owner-derived classification? Recommended: yes.
3. What is calechip's production state (permissions, owner role, default role)? It decides whether the migration backfill matters and whether the guard will start refusing after deploy.
4. Should changing scope from `org` to `app` require `admin:*`, like the `is_admin` boundary? Recommended: yes.
5. Should a role holding `scope='org'` permissions be refused for app-scope (`org_id` NULL) assignment on `org_mode = required` apps? That is the only way to make the legacy `roles` union safe as well. Recommended: follow-up ticket, not 06c (R7).
6. Should the CLI gain `--org-permission` in 06c, or should org permissions be created only through the admin API? Recommended: add the flag, because F4 is an active widening path.

### Relevant files
- /home/user/org-admin-api/app/modules/tokens/service.py (lines 84-107)
- /home/user/org-admin-api/app/modules/rbac/service.py (`_build_for_app` 262-289, `_update` 405-435, `register` 479-610, permission update 799-818)
- /home/user/org-admin-api/app/modules/rbac/repository.py (`set_default_role_if_null` 101-108, `org_role_keys` 309-329)
- /home/user/org-admin-api/app/modules/rbac/models.py (`App` constraints, `Permission`)
- /home/user/org-admin-api/app/modules/invitations/service.py (`_unavailable_reason` 133-150), /home/user/org-admin-api/app/modules/invitations/rules.py
- /home/user/org-admin-api/app/cli.py (`--permission`, `--org-mode`)
- /home/user/org-admin-api/docs/consumers.md (lines 132-191, 373-379), /home/user/org-admin-api/docs/adr/0013-org-scoped-identity.md, /home/user/org-admin-api/docs/adr/0014-org-invitations.md, /home/user/org-admin-api/docs/spec-v1.md (line 67)
- /home/user/org-admin-api/work/tickets/USR-05/ticket.md (the manifest must carry `scope`)
