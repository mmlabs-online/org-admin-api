## Findings (read-only; nothing changed)

Current state:
- Alembic head is `c5e91d3a7b24` (USR-06b). USR-06a `b4d82f6a1c93` added `apps.org_owner_role_id`, a composite same-app FK and `ck_apps_org_owner_requires_org_mode`.
- Model CHECKs are declared in `app/modules/rbac/models.py` with short names, and the migration uses `op.f("ck_apps_...")`. The file states that Alembic does not compare CHECKs, so `alembic check` will not catch a mismatch between model and migration text.
- Org-exact role and permission lookups already exist: `RbacRepository.org_role_keys` and `org_permission_keys` (`app/modules/rbac/repository.py` ~309-357). They are exact-org, unexpired (DB clock), restricted to active orgs, and use DISTINCT/ORDER BY.
- `TokenService.issue` (`app/modules/tokens/service.py` 84-91) already calls `org_role_keys` as an access gate. It then overwrites `roles` with the app+org union (`effective_roles(..., org_id)`). So the org-exact list is computed but is not exposed in the token. That is the S1 gap.
- `build_default_role` (`app/modules/rbac/service.py` ~252) sets `<app>:member` to every `is_admin=false` permission of the app. It has no org-level exclusion. `set_default_role_if_null` (`repository.py` 101) is an unconditional conditional-update. It only checks that the default is NULL, not that it differs from the owner role.
- PATCH validation in `service.py` ~428-434 already rejects owner == default (`same_as_default_role`). Other write paths (`set_default_role_if_null`, seed, CLI register, `_create`) do not.
- The USR-06b guard (`invitations/service.py` `_unavailable_reason`, `rules.roles_overlap`) is a permission-overlap check, with `<app>:org:create` exempted.
- `org:create` is checked at app scope (`ORG_CREATE_ACTION` in `auth_service.py`). `org:invite` is checked org-exact. So "org-level permission" cannot be derived from the key alone (`<app>:org:*`). `org:create` is the exception.

## Recommendations

**1. Token claim (no schema change needed)**
- Add an additive claim `org_roles` (sorted, org-exact) to org tokens only. Reuse the result of `org_role_keys` that the access gate already computes, so there is no extra query.
- Keep `roles` as the union (R9: no breaking change to v1 consumers).
- Consumers must use `org_roles` for org-scoped decisions. This needs a one-line addition to spec §5.8, which needs owner approval (ticket note), plus an ADR and `docs/consumers.md`.
- Optionally reorder `issue` to fetch org roles first, then the union. The union already includes the org roles, so one cheap indexed query, or an in-memory merge of app-scope roles and `org_roles`, avoids a second join. The existing `effective_roles` query is fine for ~tens of rows.
- Index support: `ix_role_assignments_org_id` and the unique (user_id, role_id, org_id) index cover this lookup.

**2. Scope marker (only if the planner wants DB-enforced "default role has no org permissions")**
- Put the marker on `permissions`, not `roles`. A role's scope is derived from its permissions; marking roles invites drift.
- Column: `permissions.scope VARCHAR(10) NOT NULL DEFAULT 'app' CHECK (scope IN ('app','org'))`.
- Add it as `NOT NULL` with a constant server default. On PG 17 this is a metadata-only change with no table rewrite, so it is safe with no backfill step for the default.
- The model needs a matching `server_default` and a CheckConstraint with the same text and a name per the naming convention. Alembic does not compare CHECKs and does compare server defaults, so mismatches show up in `alembic check` only for the default.
- Backfill: set `'org'` only for known org-level permissions, e.g. `key ~ ':org:(invite|...)$'`, and explicitly exclude `:org:create`, which is app-scope. A key-pattern backfill is a heuristic. List the matched rows in the migration docstring and run a preflight count.
- `downgrade()`: drop the check constraint, then the column.
- `build_default_role` for an `org_mode='required'` app: `list_permissions(app.id, is_admin=False, scope='app')`. The `org_mode='none'` path stays unchanged (ticket AC). `_set_role_permissions` already computes removals, so a rebuild would strip org-level permissions from an existing default.
- Cheaper alternative: skip the column and have `build_default_role` exclude `<app>:org:*` except `org:create` by convention in code. This is weaker, because permission keys are free-form by ADR-0003. I recommend the column if USR-05 manifest push will declare permissions. The manifest can then carry `scope`.
- Open-ended cost: the manifest, admin permission create/PATCH schemas and the catalog document gain a field. Check R9 (additive only).

**3. DB CHECK owner != default**
- Do not write it literally as `default_role_id IS DISTINCT FROM org_owner_role_id`: when both are NULL (the common state, every `org_mode='none'` app) that expression is FALSE and the row would be rejected. Use:
  `default_role_id IS NULL OR org_owner_role_id IS NULL OR default_role_id <> org_owner_role_id`, named e.g. `default_role_not_org_owner` (model name → `ck_apps_default_role_not_org_owner`, `op.f()` in the migration).
- Preflight in `upgrade()` (the 06a pattern): `SELECT count(*) FROM apps WHERE default_role_id = org_owner_role_id` and raise `RuntimeError` if non-zero. Do not auto-fix, because choosing which role to null is a policy decision.
- `downgrade()`: `op.drop_constraint(op.f("ck_apps_default_role_not_org_owner"), "apps", type_="check")`.
- Pair it with the service guard. A CHECK failure surfaces as a 500 `IntegrityError`, so the guard must come first.
- `set_default_role_if_null`: add `AND (org_owner_role_id IS NULL OR org_owner_role_id <> :role_id)` to the WHERE clause. It then silently leaves the default NULL when the role equals the owner. Return a bool or raise `ConflictError` so `build_default_role` can report it.
- Name collision: `build_default_role` always uses role key `<app>:member`. If an admin made `<app>:member` the owner role, the rebuild rewrites the owner role's permissions to the default set. For org apps it should refuse (409) when `owner_role_id == member.id`.
- Lock order: write paths already hold `lock_app`, so no new locking.

**4. USR-06b guard**
- If the default role is guaranteed to contain no `scope='org'` permissions, the overlap check is replaced by "default contains no org-scope permission and default != owner". Keep `join_policy`, `org_mode`, `roles_unset` and `default_role_admin` checks. If the marker is not added, keep `roles_overlap` and only relax it once the `org_roles` claim lands. Existing apps whose default was already widened still need the check or a one-off cleanup.

**5. Tests (AC)**
- `org_roles` exact-vs-union with an app-scope-only role, an expired assignment and a suspended org (the claim absent or the 403 gate).
- A schema test `test_rbac_schema.py` for both CHECKs, including both-NULL allowed.
- A migration test in the style of `tests/test_orgs_migration.py`, with preflight failure and downgrade.

## Risks
- A both-NULL mistake in the CHECK rejects every `org_mode='none'` app, so write the NULL-safe form (above).
- Adding a claim changes the spec (§5.8) and the token contract. Consumers that ignore `org_roles` stay on the union (still unsafe for org apps) until `mmlabs-auth` is updated, so list the follow-up.
- Backfilling the scope by key convention may misclassify a permission (e.g. `org:create`). Misclassification toward 'app' only keeps the existing behaviour, so default every row to 'app' and mark org permissions explicitly.
- The CHECK text in the model and migration must match by hand, since `alembic check` will not detect the difference.
- The default rebuild removing permissions from a live default role is a behaviour change for org apps (a loss of access for default holders). Report `removed` in the result and audit it.
- Existing data: production may have apps where default == owner (unlikely, as PATCH forbids it, but the seed/CLI paths were unguarded). The preflight catches this.

## Open questions
1. Is the owner OK to add `org_roles` to spec §5.8, and should `roles` stay a union permanently?
2. Marker column (`permissions.scope`) now, or defer to USR-05 manifest design? It must be in the schema before USR-05 so that the manifest can declare it.
3. Should `org:create` count as 'app'-scope (my assumption) and what is the full list of org-level permission keys in use? Is the full list in the seed?
4. For an existing org app with a widened default: auto-narrow on the next `build_default_role` run, or require an explicit admin action?
5. Should `set_default_role_if_null` raise 409 or silently skip when the role is the owner role?

Files: /home/user/org-admin-api/app/modules/rbac/models.py, /home/user/org-admin-api/app/modules/rbac/repository.py, /home/user/org-admin-api/app/modules/rbac/service.py, /home/user/org-admin-api/app/modules/tokens/service.py, /home/user/org-admin-api/app/modules/invitations/service.py, /home/user/org-admin-api/app/modules/invitations/rules.py, /home/user/org-admin-api/migrations/versions/20261117_b4d82f6a1c93_usr06a_org_foundation.py
