## Attempt 1

# Security review: USR-06 slice USR-06a, attempt 1

**Verdict: PASS (no blockers).** There are two should-fix items (🟡). Neither can be exploited through the 06a endpoints alone, but both get worse once USR-06b lands. I did not run `make check` or pytest, as instructed. I reviewed `git diff origin/main...HEAD` on branch `claude/blissful-wozniak-fx4lm9`.

## Findings

### 🟡 S1. App-scope roles go into every org token, the default role is widened automatically, and the owner role can be the same role as the default role
- **Where:**
  - `/home/user/org-admin-api/app/modules/tokens/service.py:90` builds `roles` as the app-scope roles plus the org's roles, as D10 requires.
  - `/home/user/org-admin-api/app/modules/rbac/service.py:405-426` has the `org_owner_role_id` guards.
  - `/home/user/org-admin-api/docs/consumers.md:140` gives the advice "Keep the default role to app-level permissions plus `<app>:org:create`".
- **Risk:**
  - The token does not say which scope a role came from. Every app-scope role therefore acts like a role in every org the user can get a token for.
  - `RoleCatalogService.build_default_role` / `_build_for_app` (`rbac/service.py:252-290`) runs at seed, at app creation and on every USR-05 manifest push. Each run resets `<app>:member` to every permission of the app with `is_admin = false`, which undoes any tightening staff did.
  - So for calechip, any org-level permission in the manifest (for example `calechip:org:invite` or `calechip:projects:delete`) is held by every member at app scope. A user that staff gave a lesser org-scoped role (possible today through the admin assignment API) would get member-level rights in that org's token. In 06b the same happens to every invitee.
  - `AppService._update` also accepts `org_owner_role_id == default_role_id`. That makes every joiner an "owner" at app scope, which means owner rights in every org they enter.
  - The consumers.md advice is not enforced and is silently undone by the manifest flow.
- **Fix (in scope, small):** in `AppService._update`, after resolving `owner_role_id`, refuse the case where the owner role and the default role are the same:
  ```python
  default_id = data.default_role_id if "default_role_id" in fields else app.default_role_id
  if owner_role_id is not None and owner_role_id == default_id:
      raise UnprocessableError("The org owner role must differ from the default role",
          details=[{"field": "org_owner_role_id", "reason": "same_as_default_role"}])
  ```
  Add a test that covers both directions: setting `default_role_id` to the owner role, and setting `org_owner_role_id` to the default role.
- **Docs:** in `consumers.md` and ADR-0013 Consequences, state that `build_default_role` / the manifest re-widens `<app>:member` to all non-admin permissions. For `org_mode = required` apps, org-level permissions must not be declared as non-admin app permissions, or the default role must be maintained by hand and the manifest's default-role build skipped.
- **Follow-up (owner decision):** a permission/role scope marker (`app` | `org`). Either the token keeps app-scope roles out of org tokens except `org:create`-type ones, or it adds a separate additive `org_roles` claim. Do this before USR-06b ships invitations.

### 🟡 S2. The audit coverage harness lists an audited route as "not audited"
- **Where:** `/home/user/org-admin-api/tests/test_audit_coverage.py:513-514`
- **Risk:** `POST /auth/orgs` is in `IDENTITY_NOT_AUDITED`, whose purpose is to list unaudited routes (ADR-0009). The sweep therefore treats the route as exempt. If a later change drops the audit rows, only `tests/test_auth_orgs.py::test_create_org_audit_rows` would catch it, and the exemption list would misstate R4 compliance.
- **Fix:** add a separate `IDENTITY_MULTI_ROW_AUDITED = {("POST", f"{AUTH}/orgs")}` set (or a small two-row scenario) and assert it is disjoint from `IDENTITY_NOT_AUDITED`. At minimum, rename or comment the set so the route is not counted as unaudited.

### 💭 N1. Slugs are one global namespace shared by staff customer orgs and end-user orgs
- **Where:** `/home/user/org-admin-api/app/modules/organizations/auth_service.py:109`
- **Risk:**
  - Any calechip member with `org:create` can take slugs that staff will need later (customer names, `mmlabs`, `admin`, `support`). They can also pick look-alike names that later show up in admin-pan.
  - The 409 tells the caller whether a slug exists. It only reveals slugs, it is limited to 10 per hour per IP, and failed attempts do not count against the per-user cap. Low impact, but customer slugs of other products can be enumerated.
- **Fix:** follow-up for a reserved-slug denylist. Resolve it together with OQ1 (`organizations.app_key` with `(app_key, slug)` uniqueness).

### 💭 N2. The per-user org cap is only serialized per app
- **Where:** `auth_service.py:80` and `:106`
- **Risk:** `lock_app` (`FOR NO KEY UPDATE`) serializes concurrent creates within one app, so the count-then-insert is safe there. Concurrent creates through two different `org_mode = required` apps could go over `ORG_CREATE_MAX_PER_USER` by one each. calechip is the only such app today.
- **Fix:** none needed now. If a second org app appears, add `SELECT … FOR UPDATE` on the user row before counting.

### 💭 N3. `Pragma: no-cache` missing on the org routes
- **Where:** `/home/user/org-admin-api/app/modules/organizations/auth_router.py:30`
- **Detail:** `/auth/token` sends both `Cache-Control: no-store` and `Pragma: no-cache`. `_finish` sends only `no-store`, which is enough for current clients. Add the `Pragma` header for consistency if desired.

### 💭 N4. End-user free text now reaches the admin UI
- **Detail:** Org `name` and `description` (up to 5000 characters) are now written by end users. They appear in admin-pan and in audit `changes`. React escapes them by default; admin-pan must never render them as HTML or markdown. This is a note for admin-pan, not this repo.

## Verified OK
- **Token org check is org-exact:**
  - `RbacRepository.org_role_keys` (`rbac/repository.py:309-329`) filters `RoleAssignment.org_id == :org` (never the union), `App.key == app_key`, `organizations.status = 'active'` and unexpired by the DB clock. The authz layer also filters by app-key prefix.
  - A role of another app in the same org does not count. An app-scope-only member gets 403. All of these are tested.
- **`<app>:org:create` is checked at app scope only:**
  - `effective_permissions(user.id, app.key)` defaults to `org_id=None`, and `_active_assignments` then means `org_id IS NULL` (`repository.py:277-282`).
  - Tested by `test_create_org_permission_held_only_in_an_org_does_not_count`.
- **No existence oracle for orgs:**
  - Unknown, suspended and non-member orgs all return the same 403 `org_access_denied` with `details={}` from one query. The `not_member` check comes before it, and the `org_mode = none` 422 does not depend on the org.
  - `GET /auth/orgs` lists only the caller's org-exact orgs, so showing suspended status there leaks nothing.
- **Org claim:**
  - `org` is the UUID as a string and is present only when `org_id` is given.
  - A token without `org_id` carries app-scope roles only.
  - The `admin` audience with `org_id` returns 422.
- **`org_owner_role_id` guards:**
  - Same-app composite FK (NO ACTION) plus a CHECK that `org_mode = 'required'`.
  - The service rejects an unknown or other-app role, a role holding admin permissions, `org_mode != required` (including switching `org_mode` to `none` while the owner role is set), and the admin app (G1 plus the guard that the admin app's `org_mode` stays `none`).
  - The guards cover role delete (`:984`), the `is_admin` flip on a held permission (`:798-805`, plus G3), and `replace_permissions` (`:1058`).
  - At grant time, the create path re-checks `role_has_admin_permission`, and `_grant_locked` → `_guard_privileged` runs `require_super` against a non-privileged `SYSTEM_ORG_CREATE` as a second layer.
- **Privilege escalation through org create:** none found beyond S1. The creator only gets the configured, admin-free owner role, inside the new org only. Grants go through `_grant_locked`, which locks the org with `FOR KEY SHARE`.
- **Admin DELETE returns 409:** the pre-check covers expired assignments, `IntegrityError` maps to the same 409 for races, nothing is audited on refusal, and staff grants lock with `FOR KEY SHARE`, which blocks concurrent deletes.
- **Migration:**
  - The orphan preflight raises before the FK is added and never deletes or NULLs rows.
  - FK `RESTRICT` uses the existing `ix_role_assignments_org_id`.
  - `created_by` is `ON DELETE SET NULL` with a partial index.
  - `downgrade()` reverses everything in order, and its docstring states what is lost (`created_by`, `org_owner_role_id`).
  - Constraint names match the models; the CHECK name is consistent through `op.f`.
- **Cookie-class routes:**
  - `rate_limit` runs first, then `AppContextDep` (Origin plus `X-MM-App`), then `DeviceDep`. Body and query validation come after the dependencies.
  - `no-store` is set, the rotated cookie is echoed, and there is no `x-permission`.
  - Both routes are in the `COOKIE` sweep. `account_id` is bound to the device via `get_active_account`, and active plus verified users are required.
- **Limits:** per-IP limits `org_create` (10 per hour) and `orgs_list` (60 per minute), and a per-user lifetime cap. All come from env (R5), and `.env.example` lists the keys without values.
- **Audit and logs:**
  - `organization.created` and `role_assignment.granted` are written in the same transaction with an `EndUser` actor, `app_key` and `org_id`.
  - `changes` is allowlisted and contains no email or token; the test asserts no `@`.
  - Logs contain only UUIDs and the app key.
- **Docs and ADR-0013:** they match the code (app-scope check, org-exact reads, RESTRICT/409, `org` claim, codes, 15-minute TTL = `access_token_ttl_seconds` 900). The gaps are the S1 additions.

## Follow-ups (out of scope)
1. A role/permission scope marker, or an additive `org_roles` claim, so app-scope roles do not grant org rights (S1). Do this before USR-06b.
2. For `org_mode = required` apps, keep `build_default_role` / the manifest from re-widening `<app>:member` with org-level permissions (S1).
3. A reserved-slug denylist, tied to the OQ1 `organizations.app_key` decision (N1).
4. Lock the per-user org cap on the user row if a second org-mode app is added (N2).
5. admin-pan: handle 409 `org_has_assignments`, and render user-supplied org names and descriptions as plain text only (N4).
6. Already in the plan (A9): CLAUDE.md R11 still says `mm_device` / `Path=/api/v1/auth`, but the code and D20 use `__Host-mm_device` / `Path=/`. The owner needs to fix CLAUDE.md.

**Verdict: PASS** with two 🟡 should-fix items (S1: refuse `org_owner_role_id == default_role_id` and add the doc warning; S2: audit coverage classification). There are no 🔴 blockers.
