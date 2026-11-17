## Attempt 1

## Security review: USR-06c, attempt 1

Scope: I read `git diff origin/main...HEAD` on branch `claude/blissful-wozniak-fx4lm9`, along with the plan and ticket. As instructed, I did not run `make check` or pytest.

### Findings

🟡 **S-1: Any `admin:roles:manage` holder can get around the "org→app needs `admin:*`" rule by deleting the permission and creating it again.**
- Where: `/home/user/org-admin-api/app/modules/rbac/service.py:846-848` (`PermissionService._create`) compared with `:893-897` (`update`).
- Risk: On an org app, `PATCH …/permissions/{id} {"scope":"app"}` needs `admin:*`. But `POST …/permissions {"key": K, "scope": "app"}` needs only `admin:roles:manage`, and `tests/test_rbac_permissions_api.py:433` confirms this. Delete also needs only `admin:roles:manage` once no role holds the permission. So a roles:manage holder can:
  1. PUT the org permission K out of the owner and other roles;
  2. DELETE K;
  3. POST K again with `scope: app`;
  4. PUT K into the default role. The PUT guard passes because K is now `app`.

  The result: an org-level permission reaches every member at app scope, and the invitation guard no longer refuses. Each step is audited and the steps are noisy (owners lose K for a while). Consumers that authorize only from `org_roles` are not affected. Consumers still reading `roles` are widened, and that is the residual ADR-0015 accepts until mmlabs-auth ships.
- Fix: apply the same rule on create as on the flip. In `_create`, after `scope` is resolved:
  ```python
  if (
      app.org_mode == OrgMode.REQUIRED.value
      and scope == PermissionScope.APP
      and reserved_scope(app.key, data.key) is None
  ):
      require_super(actor)  # explicit app scope on an org app = eligible for the member role (ADR-0015)
  ```
  Record a denied-audit row the same way `update` does. `SYSTEM_CLI` is privileged, so `register-app --permission` keeps working. Change test line 433 to expect 403 for a leaf actor and 201 with `admin:*`, and add a line to ADR-0015 §2. If the owner prefers to accept this instead, record it in ADR-0015 under Residual risk.

💭 **N-1: The none→required flip keeps every permission `app` (A13).**
- Where: `/home/user/org-admin-api/app/modules/rbac/service.py:464-473`
- Risk: After `org_mode` changes from none to required, the fail-closed default never applies to existing permissions. The PATCH checks only the current default role. The next `build_default_role` then puts all non-admin permissions into `<app>:member`. This is documented (ADR-0015 Consequences, consumers.md), and org-exact consumers are unaffected.
- Optional hardening (follow-up): when `org_mode` changes to `required`, refuse the PATCH unless at least one non-reserved permission is `scope = org`, or reclassify with the migration's backfill rule.

💭 **N-2: The CLI refusal for `reserved_scope` always reports field `org_permissions`.**
- Where: `/home/user/org-admin-api/app/modules/rbac/service.py:510-518`
- This happens even when the offending key came from `--permission`. The CLI maps by reason, so the operator sees the correct message. Only the `details` field is cosmetic-wrong.

💭 **N-3: deploy.md says every refusal comes "before the first write", which is not true for `member_is_org_owner`.**
- Where: `/home/user/org-admin-api/docs/deploy.md` (register-app paragraph)
- `member_is_org_owner` is raised from `_build_for_app` after the permission inserts. The CLI session rolls the whole transaction back, so "nothing was saved" is still true. Suggested wording: "all refusals leave nothing saved".

💭 **N-4: consumers.md tells consumers to "mark org-level permissions `scope = org` first" when changing `org_mode`.**
- Where: `/home/user/org-admin-api/docs/consumers.md` (token section)
- Both actions are staff-side, not consumer-side. Suggested wording: "Staff changing `org_mode` … mark …".

### Verified OK
- **`org_roles` claim** (`app/modules/tokens/service.py:85-93`):
  - It is the same `org_role_keys` result as the 403 `org_access_denied` gate, with no second query. It is org-exact (`RoleAssignment.org_id == org_id`, never NULL), unexpired by the DB clock, only for an `active` org, and prefix-filtered.
  - It is set only inside `if data.org_id is not None`, so it is never present without `org` and never empty.
  - App-scope-only roles cannot appear, because the join requires a non-NULL `org_id`.
  - It is not logged.
  - `roles` is unchanged (still the `effective_roles` union), so R9 holds.
  - Tests cover: app-scope-only role excluded, a role held in both scopes, another org's role excluded, and no `org_roles` without `org`.
- **`permissions.scope` invariants**:
  - DB CHECK `scope IN ('app','org')`; model and migration texts match.
  - Reserved keys are enforced on create, PATCH and CLI. `org:create` is always `app` and `org:invite` always `org`; an explicit contradiction gives 422.
  - When scope is omitted on an org app, it defaults to `org` (fail closed).
  - Explicit null on PATCH gives 422.
  - On an org app, app→org while the default role holds the permission gives 409. org→app requires `admin:*` inside the existing try/except, so a denied-audit row is written (but see S-1).
  - Every guard runs under the app row lock.
- **Default-role build paths**:
  - `_build_for_app` targets `is_admin=false AND scope='app'` for org apps and is unchanged for none apps.
  - It refuses with 409 when member equals owner, before any write.
  - It covers `build_default_role`, `AppService._create` (a new app has no permissions or owner yet) and CLI `register` (`set_default=False`).
  - `_set_role_permissions` has only two callers, build and the role PUT, so there is no other writer to `role_permissions`.
  - `set_default_role_if_null` uses `IS DISTINCT FROM`, which is NULL-safe.
- **App PATCH guard**: 422 `role_has_org_permissions` when `default_role_id` or `org_mode` changes and the resulting default of a required app holds an `org` permission. `same_as_default_role` is still in place.
- **Role PUT guard**: checks the whole target set for the default role of an org app. A no-op PUT that keeps legacy `org` permissions is refused, as intended (A10). The owner and other roles are not affected.
- **owner≠default in three layers**:
  - The CHECK `default_role_id IS NULL OR org_owner_role_id IS NULL OR default_role_id <> org_owner_role_id` is NULL-safe.
  - The preflight `default_role_id = org_owner_role_id` ignores NULLs correctly and aborts without auto-fixing. It runs in the migration transaction, and the CHECK creation re-validates against concurrent writes.
- **Migration backfill**:
  - It runs only for `org_mode='required'` apps and is deterministic.
  - Classification is fail-closed: a permission held by neither role becomes `org`, one held by both becomes `org`, and with a NULL owner the NOT EXISTS is true.
  - It never writes `role_permissions`.
  - `downgrade()` drops both CHECKs and the column, losing `scope` (documented).
- **Invitation guard**: the order is unchanged and still fails closed (org_mode, join_policy, roles_unset, roles_missing, default_role_admin, then default_role_org_permission). It is the same 403 on create and accept.
- **CLI flags**:
  - Prefix and wildcard checks run over both lists, plus `scope_conflict`, `org_mode_required` (based on the resulting mode) and `reserved_scope`.
  - On existing org apps, `scope_mismatch` and the `is_admin` refusal cover both lists.
  - Refusal reasons map to fixed messages and exits.
- **Audit**: `diff.PERMISSION` includes `scope`, so create, update and delete snapshots carry it. The register audit records `org_permissions_added`.
- **Docs**: ADR-0015, ADR-0003, 0005, 0013 and 0014, identity-architecture D7/D9/D10, and spec §5.8 and §5.3 are accurate and carry the owner's approval date. consumers.md says to authorize org data only from `org_roles` mapped through the catalog, to scope data by `org`, to use `roles` only from tokens without `org` for app-level actions, and to reject a token with `org` but no `org_roles`. The mmlabs-auth follow-up is listed.

### Follow-ups (out of scope)
- mmlabs-auth: an `org_roles` accessor; `require_org_permission` that uses only `org_roles` through the catalog; reject tokens with `org` but no `org_roles`; a TS type. It must be released before calechip relies on it.
- Refuse app-scope grants, on org apps, of roles that hold `org` permissions. This closes the residual "staff-granted app-scope role" path for `roles` consumers.
- Reclassify or refuse on an `org_mode` none→required flip (N-1).
- Retire the S1 invitation guard once every org-mode consumer authorizes from `org_roles`.
- Add an `--org-permission` input to the deployment-repo workflow.
- After deploy, run the review query in deploy.md on production and tell calechip that the next build narrows `<app>:member`.

**Verdict: PASS (no blockers).** S-1 is a should-fix: either apply the one-condition `require_super` in `PermissionService._create` in this ticket, or record it as an accepted residual in ADR-0015.

## Should-fix re-check (325ccd0)

**Verdict: PASS.** `uv run pytest tests/test_rbac_permissions_api.py tests/test_rbac_apps_api.py -q`: 128 passed.

- S-1 fixed (`app/modules/rbac/service.py:848-853`): creating a non-reserved permission whose resolved scope is `app` on an `org_mode = required` app calls `require_super`; `PermissionService.create` records the denied-audit row; `SYSTEM_CLI` stays privileged; `<app>:org:create` exempt. `test_create_scope_app_on_org_app_requires_super` (leaf → 403 `admin_wildcard_required`, nothing written; default `org` create → 201). The delete-then-recreate route is closed. ADR-0015 §2 records the rule.
- N-2 fixed (`service.py:564-571`), N-3 and N-4 wording fixed.
- 💭 N-1 (wider): `PATCH /apps/{key}` `org_mode` needs no `admin:*`, so a holder of both `admin:apps:write` and `admin:roles:manage` can clear the owner role → set `org_mode=none` → flip K to `app` → set `required` again → restore owner → PUT K into the default role (~6 audited steps, org creation broken meanwhile). `org_roles` consumers unaffected. Follow-up: require `admin:*` for any `org_mode` change on an existing app, or refuse `required → none` while org assignments exist.
- 💭 Test gap: the new test does not assert the denied-audit row (the code path writes it).
