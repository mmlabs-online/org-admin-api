USR-06c security-architect consult: the S1 fix design

## Findings

**F1. Root cause: the token loses track of which scope a role came from.** `app/modules/tokens/service.py:88-90` checks org-exact access through `org_role_keys`. It then emits `roles = effective_roles(user, app, org_id)`, which is app-scope roles plus org roles from `_active_assignments` (`app/modules/rbac/repository.py:281-285`, the `org_id IS NULL OR org_id = :org` branch). A consumer that maps `roles` to permissions through the catalog (the step 7 checklist item in `docs/consumers.md:364`) cannot tell which roles were granted in the org. Identity's own decisions are already org-exact (`org_permission_keys`, `org_role_keys`). The leak is only on the consumer side, so only something the consumer can read in the token closes S1.

**F2. A scope marker alone does not close S1 for consumers.** A marker on permissions or roles is server-side data. Consumers would still get one mixed `roles` list. To act on it the server would have to drop app-scope roles from org tokens. That changes what `roles` means under D10 and spec §5.8, and it also drops the legitimate app-level permissions bundled in the same role. An additive `org_roles` claim alone does close S1 for consumers that adopt it. But it fixes neither the default-role re-widening (AC2) nor N-2. Consumers that keep reading `roles` also stay exposed. **Conclusion: do both, because they do different jobs.**
- The `org_roles` claim is the consumer-facing fix.
- `permissions.scope` is server-side hygiene and gives a precise definition of "org-level permission". AC2, the guard and USR-05 need that definition.

**F3. There is no reliable definition of "org-level permission" today.** The 06b guard infers it as "owner-role permissions minus `<app>:org:create`" (`app/modules/invitations/service.py:133-150`, `roles_overlap`). That inference fails open when the owner role is sparse (N-2). Naming conventions do not work either, because `calechip:projects:delete` is org-level. An explicit column is the only robust definition.

**F4. There are three write paths that rebuild `<app>:member`, not one.** All three call `_build_for_app`, whose target is "every non-admin permission" (`app/modules/rbac/service.py:282`):
- `build_default_role` (staff endpoint, `:252`)
- `AppService._create` (`:339`)
- the register/CLI path (`:575-586`), which also creates permissions with `PermissionCreate(key, is_admin=False)` and no scope.

USR-05 will be a fourth caller. `_set_role_permissions` also *removes* permissions that are not in the target, so filtering the target tightens existing member roles.

**F5. `set_default_role_if_null` (`app/modules/rbac/repository.py:101-108`) bypasses the owner≠default guard** in `AppService._update` (`app/modules/rbac/service.py:428-435`).

**F6. Trap in the suggested CHECK.** `CHECK (default_role_id IS DISTINCT FROM org_owner_role_id)` is **false when both are NULL**. It would reject every app that has no default and no owner role, and the migration would fail on existing rows. Use `default_role_id <> org_owner_role_id` instead. A CHECK that evaluates to NULL passes, so NULL on either side passes. You can also spell it out: `default_role_id IS NULL OR org_owner_role_id IS NULL OR default_role_id <> org_owner_role_id`. Inside the repository WHERE clause, `App.org_owner_role_id.is_distinct_from(role_id)` is correct, because `role_id` is never NULL there.

## Recommendations

**R-A (AC1, R9; needs a spec §5.8 line): add an `org_roles` claim.**
- Emit it only together with `org`.
- Value: sorted org-exact role keys, prefix-filtered like `roles`.
- Reuse the result of the `org_role_keys` call already made at `app/modules/tokens/service.py:88`. Do not run a second query.
- Leave `roles` unchanged (app-scope plus org) for v1 compatibility.
- Do not add an `app_roles` claim. A token without `org_id` already gives the app-scope view.
- Record the decision in a new ADR (or an ADR-0013 amendment) and in D7/D10.
- `docs/consumers.md` rule: "org-scoped authorization uses `org_roles` only. `roles` is for app-level features. Scope data by `org`."

**R-B (AC2): add `permissions.scope`.**
- Column: `String NOT NULL`, CHECK `scope IN ('app','org')`, server_default `'app'`.
- Service default when a permission is created: `'org'` if `app.org_mode = 'required'`, else `'app'`. This fails closed, so a forgotten classification never widens the member role.
- Invariants (CHECK or service): `<app>:org:create` is always `'app'`; `<app>:org:invite` is always `'org'`.
- `_build_for_app` target for `org_mode = required` apps: `is_admin = false AND scope = 'app'`. Apps with `org_mode = none` are unchanged (scope is ignored).
- Apply the same filter on every caller listed in F4.
- Expose `scope` on the staff permission create/PATCH schemas as an additive field.
- Mirror the existing `is_admin`-flip guard (G3). Refuse to flip a permission to `'org'` while it is held by the default role of an org-mode app (409). The alternative is to strip it from the role with an audit entry, but refusing is simpler.
- Refuse PATCH `default_role_id` to a role holding any `scope='org'` permission on an org-mode app (422 `role_has_org_permissions`).

**R-C (AC3): two layers for owner≠default.**
1. Add `App.org_owner_role_id.is_distinct_from(role_id)` to the `set_default_role_if_null` WHERE clause. When the update affects 0 rows because of this, log a warning. The default then stays NULL, which already gives `join_unavailable` and fails closed.
2. Add a DB CHECK `ck_apps_default_ne_org_owner` using the `<>` form from F6.
   - The migration preflights and **raises** if any row violates it. It must never NULL or fix rows (same pattern as the USR-06a orphan preflight).
   - `downgrade()` drops the CHECK.
   - Map the resulting `IntegrityError` to 409 in `_update` (already done generically at `:450-453`).

**R-D (AC4): keep the USR-06b guard, but make it exact.**
- Replace `roles_overlap` with: "the default role holds no `scope='org'` permission" (plus the existing checks: org_mode, join_policy, roles set, default role has no admin permission).
- This removes the superset precondition, so N-2 is fixed, and the guard stays fail-closed. Update ADR-0014 §11 and drop the precondition text in `consumers.md:183-191`.
- Reason to keep it: until mmlabs-auth and calechip read `org_roles`, a consumer that reads `roles` still widens invitees if the default role is ever contaminated (for example by staff edits).
- Write the removal condition into the ADR: the guard may be relaxed once every org-mode consumer pins an mmlabs-auth version that authorizes on `org_roles`.

**R-E (USR-05 interaction; record in the USR-05 ticket now):**
- The manifest declares `scope` per permission. For `org_mode = required` apps, a missing `scope` is either 422 or defaults to `'org'`. Prefer 422: explicit beats an implicit default.
- An app-overridden default role in a manifest goes through the same "no `scope='org'` permission" check.
- The manifest cannot change `org_owner_role_id`, `default_role_id` or `org_mode`.
- Add a USR-05 test: pushing a manifest twice never adds an org permission to `<app>:member`.

**R-F (migration of existing data, R2).** Use one revision with `upgrade()` in this order:
1. Add the `scope` column with default `'app'`.
2. Backfill org-mode apps with SQL only:
   - `scope='app'` if the key is `<app>:org:create`, or if the permission is held by the default role and **not** by the owner role.
   - Everything else in an org-mode app becomes `'org'`.

   This keeps today's correctly configured apps working. Overlap (contamination) lands as `'org'`, so the precise guard refuses invitations until staff rebuild the default role through the audited endpoint.
3. **Do not rewrite `role_permissions` in the migration.** Migrations are not audited (R4).
4. Run the owner≠default preflight, then add the CHECK.

`downgrade()` drops the CHECK, then the column. Re-upgrading recomputes the same values deterministically. Add a migration roundtrip test that seeds overlapping and non-overlapping org apps.

**R-G (AC5).** In `consumers.md`, write the `org_roles` rule, mark the "`roles` mixes scopes" note (`:377-379`) as legacy, and add the fail-closed rule for org apps: if `org` is present but `org_roles` is absent, reject the token. Put the mmlabs-auth follow-up in the follow-ups list, not the diff (R7):
- an `org_roles` accessor;
- a `require_org_permission` that maps **only** `org_roles` through the catalog;
- failing closed on a missing claim;
- a release before calechip relies on it.

**R-H (AC6; additions to the ticket's test list):**
- `org_roles` is absent on non-org tokens.
- A role held both at app scope and in the org appears in both claims.
- An expired org role and a suspended org are handled as today.
- The `scope='org'` default applies when a permission is created on an org-mode app.
- `register`/CLI does not widen the member role.
- The CHECK passes when both columns are NULL.
- Each of the three rebuild paths excludes org-scope permissions; `org_mode = none` is unchanged (the regression case).

## Risks
- **Adoption gap.** S1 closes only for consumers that switch to `org_roles`. Until mmlabs-auth and calechip ship, the residual protection is R-B, R-D and the CHECK. **A staff-granted app-scope role carrying org permissions remains a hole for consumers that read `roles`.** Optional extra layer (owner decision, a new 422 on the admin grant endpoint): refuse app-scope grants of roles holding `scope='org'` permissions on org-mode apps.
- **Tightening.** On the next `build_default_role` run, the member roles of org apps lose `'org'` permissions. Calechip features that relied on member-held org permissions at app scope stop working. This is intended, but tell the calechip owner.
- **Backfill heuristic.** Permissions held by neither role become `'org'`. Staff must reclassify any of them that are truly app-level. List them in the deploy log.
- **Token size and caching.** Negligible. Role changes still take up to 15 minutes to propagate (the token TTL).

## Open questions
1. **Owner approval of the spec §5.8 claims line `org_roles` (blocking per the ticket).** Is merging the PR with the spec diff enough, or should the planner return BLOCKED first?
2. Does calechip (or any `org_mode = required` app) exist in the production `org_admin` DB, and what are its default and owner role contents? This decides whether R-F's backfill changes any behaviour.
3. For permissions created on org-mode apps without an explicit `scope` (staff API, CLI): default to `'org'` (recommended) or reject with 422?
4. Should the app-scope grant guard (first bullet under Risks) be in scope here, or recorded as an accepted residual risk in ADR-0014?
5. Should the role catalog expose permission scope? This could be additive, such as a top-level `org_permissions` list, but the catalog's `permissions` are plain strings, so a per-item field would break the shape. I recommend deferring it, because `org_roles` makes it unnecessary.

Files referenced:
- /home/user/org-admin-api/app/modules/tokens/service.py
- /home/user/org-admin-api/app/modules/rbac/service.py
- /home/user/org-admin-api/app/modules/rbac/repository.py
- /home/user/org-admin-api/app/modules/rbac/models.py
- /home/user/org-admin-api/app/modules/invitations/service.py
- /home/user/org-admin-api/docs/consumers.md
- /home/user/org-admin-api/docs/adr/0013-org-scoped-identity.md
- /home/user/org-admin-api/docs/adr/0014-org-invitations.md
- /home/user/org-admin-api/work/tickets/USR-05/ticket.md
