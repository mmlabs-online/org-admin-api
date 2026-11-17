## Consult: software-architect, USR-06c (org-admin-api)

I only read files. Sources: ticket USR-06c; USR-06 security-review S1; USR-06b plan.md and security-review.md (S-1/S-2, N-2); ADR-0013; ADR-0014; spec §5.3/§5.8; identity-architecture D7/D9/D10; consumers.md §Organizations and token claims; `app/modules/tokens/service.py`; `app/modules/rbac/{service,repository,models,schemas}.py`; `app/modules/invitations/service.py`.

### Findings

1. **Where the token gets its roles.** `TokenService.issue` (`/home/user/org-admin-api/app/modules/tokens/service.py:84-91`) already computes the org-exact role list `authz.org_role_keys(user, app, org)` for the access check, then throws it away. It puts `effective_roles(user, app, org)` (app-scope ∪ org) into `roles`.
   - So an org-exact list costs no extra query: it is the value on line 88.
   - `GET /auth/orgs` already returns the same org-exact list per org.
2. **The token carries roles, not permissions.** Consumers map roles to permissions through the role catalog (D17). That means a scope marker has to sit on **permissions**, because "org-level" is a property of a permission. Roles mix scopes: `<app>:member` holds `org:create` (app-level) and, today, everything else non-admin.
   - A role-level marker therefore cannot classify `<app>:member` correctly.
   - A permission-level marker cannot show up in a roles-only token unless the catalog changes and every consumer learns new logic.
3. **Option (b), changing what `roles` contains for org tokens, breaks the v1 contract.** D10 says "token `roles` = app-scope + selected-org roles". consumers.md:377-379 tells calechip that `roles` mixes scopes. Dropping app-scope roles from org tokens would change the meaning of an existing claim inside v1 (R9).
4. **Spec conflict beyond the one the ticket names.** AC2 (default role excludes org-level permissions) contradicts spec §5.3 line 48: "a default role `<app>:member` holding all its permissions except those flagged `is_admin`". So §5.3 needs an amendment too, not only §5.8. The spec wins (CLAUDE.md), so the planner must take both amendments to the owner.
5. **One choke point for the default-role build.** Every build goes through `RoleCatalogService._build_for_app` (`rbac/service.py:262`). Its callers are `build_default_role`, `AppService._create` (:339) and the USR-07 CLI `register` (:585, `set_default=False`). USR-05 will reuse it. One change there covers AC2.
6. **The owner≠default gap that remains.** `AppService._update` (:428-435) refuses equality only when both ids are set. `set_default_role_if_null` (`rbac/repository.py:101-108`) has no owner check. Sequence: staff set `org_owner_role_id = <app>:member` while `default_role_id` is NULL, then `build_default_role` sets default = owner.
   - `register` uses `set_default=False`, and `_create` runs on a brand-new app with no owner, so the only live path is `build_default_role`. A DB CHECK closes every path at once.
7. **USR-06b guard and N-2.** `_unavailable_reason` (`invitations/service.py:133-150`) infers "org-level" from the owner role's contents. That is why there is the N-2 precondition (the owner role must be a superset of all org-level permissions). Once an `org_roles` claim exists, the token no longer depends on what the default role holds, as long as consumers use the new claim.

### Recommendation: (a), plus a fixed default-role build rule for org apps. Not (b). No marker column in 06c.

**R-A. Token: add an `org_roles` claim** (AC1, AC5, R9, R10)
- **Content:** present **only when `org` is present**, so it follows the same invariant as `org`. Value: the sorted `org_role_keys(user, app, org_id)` already computed on line 88. Non-org tokens omit it; do not send an empty list.
- **`roles` stays exactly as today** (union). This is purely additive, so nothing breaks in v1.
- **Consumer rule for consumers.md:**
  - For org-scoped data and actions, authorize **only** from `org_roles`, mapped through the catalog, and scope by `org`.
  - For app-level actions (create or pick an org), use a non-org token's `roles`.
  - Never authorize org data from the `roles` of an org token.
- **Decision record:** a new ADR-0015 ("org-scoped roles claim"). Amend ADR-0013 §6, identity-architecture D7 and D10, and consumers.md:374-379.
- **Owner approval:** spec §5.8 needs a line such as "`org_roles` (if scoped: roles held in that org only)". Ask this as the single blocking owner question (see Q1).
- **Tests:**
  - A role held only at app scope (staff-granted `<app>:editor` with `org_id` NULL) is in `roles` and **not** in `org_roles`.
  - A role held both at app scope and in the org is in both.
  - A non-org token has neither `org` nor `org_roles`.
  - A role held in org B does not appear in an org-A token.
- **Why not (b) or (c):** see Findings 2 and 3. (b) either breaks `roles` or needs catalog and consumer changes larger than (a). (c) adds the cost of (b) and buys nothing that (a) plus R-B does not.

**R-B. `build_default_role` for `org_mode = required` apps: a fail-closed rule, no schema change** (AC2)
- In `_build_for_app`:
  - If `app.org_mode == required`, the target is the non-admin permissions **intersected with `{f"{app.key}:org:create"}`**.
  - Otherwise the target is unchanged: all non-admin permissions (AC2: `none` apps keep today's behaviour).
- **Definition for the ADR:** in an org app, every permission except the reserved `<app>:org:create` is org-level and reaches users only through org roles.
- **Staff can still widen by hand** (D9 "role content is data"). But build, seed, `register` and USR-05 never re-widen. The rebuild now removes hand-added permissions where it used to undo tightening; document this.
- **Spec §5.3 amendment** (same owner question): "…except those flagged `is_admin`; for `org_mode = required` apps, only `<app>:org:create` (org-level permissions come from org roles)".
- **Alternative I considered: a permission marker column** (e.g. `permissions.app_level boolean NOT NULL DEFAULT false`, used only for org apps; `org:create` forced true).
  - It lets an org app give joiners further app-level permissions through the build.
  - It costs a migration, admin permission schema changes, guards on flips and catalog questions.
  - It overlaps the USR-05 manifest design.
  - Recommendation: defer it to USR-05, where the manifest is the natural place to declare it. If it is ever added, it must default to **org-level** (fail closed), never the reverse, so that flipping an app from `none` to `required` does not fail open.
- **Tests:**
  - An org app with `org:create`, `org:invite` and `projects:delete` gets a built member role of exactly `{org:create}`.
  - A `none` app is unchanged.
  - Rebuilding after a hand-widening removes the extra permissions.
  - The `register` path on an org app behaves the same.

**R-C. owner ≠ default on every write path** (AC3, R2)
- **DB:** add `ck_apps_default_role_not_org_owner CHECK (default_role_id <> org_owner_role_id)`. A NULL on either side makes the CHECK pass, so no NULL handling is needed. Do not use `IS DISTINCT FROM` here: it would reject the case where both are NULL.
- **Migration:**
  - Preflight that aborts on any violating row; never auto-fix (ADR-0013 §4 precedent).
  - A `downgrade()` that drops the constraint.
  - A schema test using `pg_get_constraintdef`, because Alembic does not compare CHECKs.
  - Model and migration CHECK text must match.
- **Repository:** in `set_default_role_if_null`, add `.where(App.org_owner_role_id.is_distinct_from(role_id))`.
  - `IS DISTINCT FROM` is correct here: a NULL owner must not block the update.
  - The update then becomes a no-op and the default stays NULL. That fails closed (join unavailable).
  - Return or log whether the default was set, so the build result and staff can see it.
- **Keep the service 422 `same_as_default_role`** for a friendly error. Map an `IntegrityError` on the new CHECK to the same 422 as a backstop.
- **Tests:** owner = `<app>:member` with default NULL, then build → default stays NULL. Both PATCH directions are still refused. A direct insert/update violating the constraint raises (schema test).

**R-D. USR-06b guard: keep it unchanged in code, change only its rationale** (AC4)
- After R-B, a built default role is `{org:create}`, so the overlap check passes naturally for a correctly configured app. AC6 ("invitations still work") holds, and the manifest re-widening problem goes away.
- It still protects consumers that read `roles`, until `mmlabs-auth` and calechip move to `org_roles`.
- Update ADR-0014 §11:
  - The guard is now defense in depth, not the fix.
  - Its removal criterion: every `org_mode = required` consumer authorizes org data from `org_roles` (a `mmlabs-auth` release plus calechip confirmed).
  - The N-2 precondition (owner role is a superset) now matters only for consumers still on `roles`.
- Do not tighten the guard to "default ⊆ {org:create}". That would turn off invitations for apps whose staff hand-add app-level permissions to the default role, for no gain to `org_roles` consumers.
- Test: a correctly configured app (default built by R-B, owner holds `org:invite`) can invite and accept, end to end.

**R-E. Follow-ups** (AC5, R7)
- `mmlabs-auth`: an `org_roles` accessor and an org-permission helper that resolves through the catalog and ignores `roles` for org decisions.
- calechip migration note.
- The permission marker, inside USR-05.
- Retiring the guard.
- Pre-existing behaviour, not in scope: a user holding only org roles and no app-scope role gets `not_member` at `/auth/token` (line 74).

### Risks
- **The spec gate blocks the loop.** Both §5.8 (new claim) and §5.3 (default-role content) need owner approval. Without it, R-A and R-B cannot ship and the ticket goes to BLOCKED.
- **Old consumers stay exposed.** Any consumer that keeps authorizing from the org token's `roles` keeps the S1 exposure. Mitigation: R-D guard, R-B narrowing, consumers.md and the `mmlabs-auth` follow-up.
- **Operational narrowing.** The next build, `register` or manifest run on calechip removes app-scope permissions from `<app>:member` for every joined user. Anything calechip gates on those permissions through non-org tokens stops working. Release and deploy notes must say so, and Q2 must be answered first.
- **Migration abort.** The CHECK migration aborts if production already has default = owner. That is deliberate, but the deploy step needs a runbook line.
- **Token size** grows by one short list. Negligible.

### Open questions
- **Q1 (blocking, owner):** approve both amendments.
  - Spec §5.8 claims line: "`org_roles` (if scoped): roles held in that org only".
  - Spec §5.3: for `org_mode = required` apps, the built `<app>:member` holds only `<app>:org:create`.
  - Fallback if §5.8 is refused: ship R-B, R-C and R-D only. AC1 stays unmet, and S1 remains mitigated only by the guard.
- **Q2:** Does any deployed consumer (calechip) read org tokens, or rely on app-scope permissions in `<app>:member` beyond `org:create`? This decides whether R-B needs a migration or a communication step before the first rebuild.
- **Q3:** Should an org app ever give joiners app-level permissions besides `org:create`? If yes, the `app_level` marker (fail-closed default) moves from USR-05 into 06c.
- **Q4:** Should hand-adding org-level permissions to an org app's default role be refused on the role-permission write paths? My recommendation is no (D9 says policy is data, and `org_roles` makes it harmless). The planner should record this as an assumption.

### Relevant files
- /home/user/org-admin-api/app/modules/tokens/service.py (lines 84-107)
- /home/user/org-admin-api/app/modules/rbac/service.py (`_build_for_app` 262-289, `_update` 428-435, `register` 581-586)
- /home/user/org-admin-api/app/modules/rbac/repository.py (`set_default_role_if_null` 101-108)
- /home/user/org-admin-api/app/modules/rbac/models.py (`App.__table_args__`, where the new CHECK goes)
- /home/user/org-admin-api/app/modules/invitations/service.py (`_guard` / `_unavailable_reason` 124-150)
- /home/user/org-admin-api/docs/spec-v1.md (§5.3 line 48, §5.8 line 67)
- /home/user/org-admin-api/docs/adr/0013-org-scoped-identity.md, /home/user/org-admin-api/docs/adr/0014-org-invitations.md (§11 and Consequences)
- /home/user/org-admin-api/docs/consumers.md (lines 132-191, 374-379)
- /home/user/org-admin-api/docs/identity-architecture.md (D7 line 259, D9 line 261, D10 line 262)
