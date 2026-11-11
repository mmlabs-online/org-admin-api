Status: READY

# Plan — USR-02a: RBAC core (apps registry schema, seed, effective roles/permissions, member-role builder)

> USR-02 is split (see **Split proposal**). This plan covers **slice USR-02a only**: data model and migration with seed, the authorization read path (`effective_roles`, `effective_permissions`, `permits`) and the default-role builder. **USR-02a adds no HTTP endpoint, no CLI and no config flag, and has no way to grant a role.** Staff endpoints, the role catalog and assignments come in USR-02b and USR-02c, and their contracts are below.

## Spec summary
- spec §5.3: "One model for every mmlabs app, the admin API included (app `admin`). Apps are registered; each app declares namespaced permissions (`<app>:<resource>:<action>`) and its own roles; users get roles per app, optionally limited to one organization. Every admin `/api/v1` endpoint declares the `admin:*` permission it needs. A bootstrap super-admin (`admin:*`) exists."
- spec §5.3 Membership: "each app has a `join_policy` (`open` | `approval` | `closed`; default `open`, `admin` = `closed`), a default role `<app>:member` holding all its permissions except those flagged `is_admin`, an `org_mode` (`none` | `required`; calechip = `required`) and its allowed browser `origins`. Joining assigns the default role at app scope (`org_id NULL`). Policy and role contents are data."
- spec §5.5: "The list of mmlabs products is the `apps` table of §5.3 (key, name, status)."
- ADR-0001 §3: "Permissions are namespaced per app: `<app>:<resource>:<action>` … Roles belong to one app (`stoktok:member`). Wildcard `<app>:*` is allowed for that app's super-admin role. No deny rules in v1."
- ADR-0001 §4: the five tables; "unique (user_id, role_id, org_id) — org_id NULL = app-wide"; "A role and its permissions must belong to the same app. A user can use an app if they hold at least one non-expired role in it."
- identity-architecture.md §3: `apps (… join_policy open|approval|closed default 'open' (admin = 'closed'), default_role_id → roles NULL, org_mode none|required default 'none', origins text[] …)`, `permissions (… is_admin bool default false) unique (app_id, key)`, `role_assignments (… org_id → organizations NULL …) org_id NULL = app scope; never 0`; "Default role content is data: `<app>:member` = all permissions of the app with `is_admin = false` (D9)."
- D7: token `roles` = "app-scope roles + roles in the selected org" (USR-03 consumes `effective_roles`). D9: "Default join_policy `open`, except `admin` = `closed`. Default role `<app>:member` = all app permissions except `is_admin` ones … Policy and role content are data." D10: "No org = `org_id NULL`."
- Ticket scope note: "`role_assignments.org_id` is created nullable now, FK added in USR-06."

## Acceptance criteria → verification
Ticket ACs covered by this slice (the rest are in the Split proposal).

| # | Criterion (USR-02a) | How the reviewer verifies it |
|---|---|---|
| 1 | Tables `apps`, `permissions`, `roles`, `role_permissions`, `role_assignments` + one migration per identity-architecture.md §3: `join_policy` ∈ open\|approval\|closed default `open`; `org_mode` ∈ none\|required default `none`; `origins text[] NOT NULL DEFAULT '{}'`; `permissions.is_admin` default false; `role_assignments.org_id` nullable, NULL = app scope (no FK yet, per ticket); `unique (app_id, key)` on permissions and roles; `unique NULLS NOT DISTINCT (user_id, role_id, org_id)` on assignments; `downgrade()` works; `alembic check` clean (ticket AC1, R2) | `tests/test_rbac_schema.py`: `test_app_defaults`, `test_app_check_constraints`, `test_duplicate_permission_key_rejected`, `test_duplicate_role_key_rejected`, `test_duplicate_app_scope_assignment_rejected`, `test_org_scoped_assignments_unique_per_org`. Commands: `uv run alembic upgrade head && uv run alembic check && uv run alembic downgrade -1 && uv run alembic upgrade head && uv run alembic check` (all succeed against a local DB) |
| 2 | Database rules: a role holds only permissions of its own app; `default_role_id` must be a role of the same app; keys match `<app>:<resource>:<action>` or `<app>:*` (permissions) and `<app>:<name>` (roles); `<app>:*` must be `is_admin` (ticket AC2: database check now; the 422 comes with the USR-02b endpoints) | `test_role_permission_cross_app_rejected`, `test_default_role_must_belong_to_same_app`, `test_permission_key_format` (parametrized valid and invalid), `test_role_key_format`, `test_wildcard_permission_must_be_admin` |
| 3 | Default role as data: `RoleCatalogService.build_default_role(app_key)` creates or updates `<app>:member` to hold exactly the app's permissions with `is_admin = false`, sets `apps.default_role_id` when it is NULL, is idempotent, returns what was added and removed, and only runs when called (ticket AC3) | `tests/test_rbac_default_role.py`: `test_build_creates_member_with_non_admin_permissions`, `test_build_is_idempotent`, `test_build_reflects_data_changes`, `test_build_keeps_existing_default_role`, `test_build_unknown_app_raises_not_found`, `test_new_permission_not_added_until_rebuild` |
| 4 | Seed (migration): app `admin` (`join_policy = closed`, `org_mode = none`, `origins = {}`, `default_role_id` NULL, status `active`), permission `admin:*` (`is_admin = true`), role `admin:super-admin` (`is_system = true`) holding exactly `admin:*`; no `admin:member` (ticket AC4, seed part) | `test_seed_admin_app` |
| 5 | `AuthzService.effective_roles(user_id, app_key, org_id=None)` → sorted, de-duplicated role keys: app-scope roles, plus roles in that org when `org_id` is given; expired assignments ignored (compared with the database `now()`); other apps' roles never included; unknown app → `[]`. `effective_permissions(...)` → the union of those roles' permission keys, `<app>:*` returned as is (not expanded); keys without the `<app>:` prefix are dropped. `permits(granted, required)` → exact key or a matching `<app>:*` (ticket AC6) | `tests/test_rbac_authz.py`: `test_effective_roles_app_scope`, `test_effective_roles_with_org`, `test_effective_roles_without_org_excludes_org_roles`, `test_effective_roles_ignores_expired`, `test_effective_roles_other_app_excluded`, `test_effective_roles_unknown_app_is_empty`, `test_effective_roles_deduplicated_and_sorted`, `test_effective_permissions_union_and_wildcard`, `test_effective_permissions_super_admin_seed`, `test_effective_permissions_drop_foreign_prefix`, `test_permits` (parametrized) |
| 6 | Deletes: deleting an app removes its permissions, roles and role permissions (even when it has a default role); a role that has assignments cannot be deleted; deleting a user removes their assignments and sets `granted_by` to NULL where they granted roles | `test_delete_app_cascades_catalog`, `test_role_with_assignments_cannot_be_deleted`, `test_delete_user_cascades_and_nulls_granted_by` |
| 7 | Every new model imported in `app/db/models.py`; `make check` green (R2, R8) | `make check`; `alembic check` (row 1) |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/rbac/__init__.py` (new) | empty | new domain module (one module for all five tables because `apps` and `roles` reference each other — software-architect) |
| `app/modules/rbac/models.py` (new) | `App`, `Permission`, `Role`, `RolePermission`, `RoleAssignment`, StrEnums `AppStatus`, `JoinPolicy`, `OrgMode`, regex constants used in CHECKs | AC 1, 2, 6 |
| `app/modules/rbac/repository.py` (new) | `RbacRepository`: app/role/permission lookups, role-permission add/remove, the two effective-access queries | R1 (SQLAlchemy only here) |
| `app/modules/rbac/authz.py` (new) | `AuthzService` (`effective_roles`, `effective_permissions`) + pure `permits()` | AC 5; reused by USR-03 (token roles) and ADM-002 (`require_permission`) |
| `app/modules/rbac/service.py` (new) | `RoleCatalogService.build_default_role`, `DefaultRoleResult` dataclass | AC 3 |
| `app/db/models.py` | import and export the five models | R2 |
| `migrations/versions/20261111_<rev>_create_rbac.py` (new) | schema for 5 tables + deferred `apps.default_role_id` FK + seed rows; downgrade | AC 1, 2, 4, R2 |
| `docs/adr/0003-rbac-keys-and-matching.md` (new) | records key grammar, namespaced role keys, `<app>:*` matching, same-app composite FKs, `NULLS NOT DISTINCT`, "effective_* is RBAC only, no status checks", "member role rebuilt only when asked", "admin app has no member role" | planner rule 7: these outlive the ticket; `mmlabs-auth` (USR-03b) must copy `permits` exactly |
| `tests/rbac_factories.py` (new) | async helpers `make_app`, `make_permission`, `make_role`, `grant`, `assign`, `make_user` that insert rows through `db_session` + `flush()` | shared by the three test files |
| `tests/test_rbac_schema.py` (new) | constraints, defaults, seed, deletes | AC 1, 2, 4, 6 |
| `tests/test_rbac_authz.py` (new) | effective roles/permissions, `permits` | AC 5 |
| `tests/test_rbac_default_role.py` (new) | member-role builder | AC 3 |

No other files. In particular: **no change** to `app/main.py`, `app/api/router.py`, `app/core/config.py`, `app/core/errors.py`, `.env.example` (nothing is exposed in this slice).

## Tests to write
Test-writing notes:
- Each test runs inside one rolled-back transaction (`tests/conftest.py`). Every test that expects an `IntegrityError` wraps the failing statement in `async with db_session.begin_nested():` inside `pytest.raises(IntegrityError)`, so the session can still be used afterwards.
- The test database already holds the seed rows (the migrations build it). Tests use app keys other than `admin` (e.g. `stoktok`, `calechip`), except the seed tests.
- `now()` stays fixed during a transaction. Use `expires_at = 2000-01-01Z` for "expired" and `2999-01-01Z` for "future".

| Test (file › name) | Covers |
|---|---|
| `test_rbac_schema.py › test_seed_admin_app` | `admin` app fields as in AC 4; `admin:*` `is_admin`; `admin:super-admin` `is_system` with exactly `{admin:*}` in `role_permissions`; no role `admin:member`; `default_role_id IS NULL` |
| `› test_app_defaults` | insert `App(key="stoktok", name="Stoktok")` with only the required fields → `join_policy="open"`, `org_mode="none"`, `origins == []`, `status="active"`, `default_role_id is None` |
| `› test_app_check_constraints` | parametrized → IntegrityError: `join_policy="x"`, `org_mode="x"`, `status="x"`, `key` ∈ {`"Bad"`, `"a"`, `"st:ok"`, `"1app"`, 33 chars} |
| `› test_permission_key_format` | valid: `stoktok:portfolio:read`, `stoktok:org:create`, `stoktok:*` (with `is_admin=True`). Invalid → IntegrityError: `*`, `stoktok`, `stoktok:a`, `stoktok:*:*`, `stoktok:a:*`, `stoktok:a:b:c`, `Stoktok:a:b`, `stoktok:a_b:c`, `stoktok::read` |
| `› test_wildcard_permission_must_be_admin` | `stoktok:*` with `is_admin=False` → IntegrityError |
| `› test_role_key_format` | valid `stoktok:member`, `stoktok:super-admin`; invalid → IntegrityError: `member`, `stoktok:*`, `stoktok:a:b`, `STOKTOK:member` |
| `› test_duplicate_permission_key_rejected` | same `(app_id, key)` twice → IntegrityError |
| `› test_duplicate_role_key_rejected` | same `(app_id, key)` twice → IntegrityError |
| `› test_role_permission_cross_app_rejected` | role of `stoktok` + permission of `calechip`: row with `app_id=stoktok` → IntegrityError; row with `app_id=calechip` → IntegrityError; same-app row OK |
| `› test_default_role_must_belong_to_same_app` | `stoktok.default_role_id = <calechip role id>` + flush → IntegrityError; a `stoktok` role → OK; NULL → OK |
| `› test_duplicate_app_scope_assignment_rejected` | two `RoleAssignment(user, role, org_id=None)` → IntegrityError (NULLS NOT DISTINCT) |
| `› test_org_scoped_assignments_unique_per_org` | same user+role: app scope + org A + org B all OK (`org_id` = random UUIDs, no FK); org A twice → IntegrityError |
| `› test_delete_app_cascades_catalog` | app with 2 permissions, a role holding them, `default_role_id` set → `DELETE FROM apps` (through `session.delete` + flush) succeeds; roles/permissions/role_permissions for that app are gone |
| `› test_role_with_assignments_cannot_be_deleted` | delete a role that has an assignment → IntegrityError |
| `› test_delete_user_cascades_and_nulls_granted_by` | user U granted a role by G; G also holds a role. Delete G → G's assignment gone; U's assignment kept with `granted_by IS NULL` |
| `test_rbac_authz.py › test_effective_roles_app_scope` | app-scope assignments → their role keys |
| `› test_effective_roles_with_org` | app-scope role R1 + org-A role R2 + org-B role R3; `org_id=A` → `[R1, R2]` |
| `› test_effective_roles_without_org_excludes_org_roles` | same data, `org_id=None` → `[R1]` |
| `› test_effective_roles_ignores_expired` | expired (2000) ignored; future (2999) and NULL included |
| `› test_effective_roles_other_app_excluded` | roles in `calechip` not returned for `stoktok` |
| `› test_effective_roles_unknown_app_is_empty` | `app_key="nope"` → `[]` |
| `› test_effective_roles_deduplicated_and_sorted` | the same role at app scope and in org A, `org_id=A` → appears once; output sorted |
| `› test_effective_permissions_union_and_wildcard` | two roles with overlapping permissions → sorted union; a role holding `stoktok:*` → `stoktok:*` appears as is, not expanded |
| `› test_effective_permissions_super_admin_seed` | user assigned the seeded `admin:super-admin` → `effective_roles(..., "admin") == ["admin:super-admin"]`, `effective_permissions == ["admin:*"]` |
| `› test_effective_permissions_drop_foreign_prefix` | a `stoktok` permission row keyed `admin:*` (`is_admin=True`; the CHECK allows it, the app-prefix check comes in USR-02b), held by a `stoktok` role → not in `effective_permissions(user, "stoktok")`, and not in `effective_permissions(user, "admin")` |
| `› test_permits` | parametrized (no DB): `({"a:x:read"}, "a:x:read") → True`; `({"a:*"}, "a:x:read") → True`; `({"a:*"}, "a:new:thing") → True`; `({"a:*"}, "b:x:read") → False`; `({"*"}, "a:x:read") → False`; `({"a:x:read"}, "a:x:write") → False`; `({"a:*"}, "a:*") → True`; `({"a:*"}, "nocolon") → False`; `(set(), "a:x:read") → False` |
| `test_rbac_default_role.py › test_build_creates_member_with_non_admin_permissions` | app with `p1`, `p2` (non-admin), `roles:manage` (`is_admin`), `*` (admin) → `stoktok:member` created, `is_system=True`, holds exactly `{p1, p2}`; `apps.default_role_id` = its id; result `created=True`, `added == [p1, p2]` (sorted), `removed == []` |
| `› test_build_is_idempotent` | second call → `created=False`, `added == removed == []`; row count in `role_permissions` unchanged |
| `› test_build_reflects_data_changes` | after a first build: add permission `p3`, set `p1.is_admin = True` → rebuild returns `added == [p3]`, `removed == [p1]`; the role now holds `{p2, p3}` |
| `› test_build_keeps_existing_default_role` | `default_role_id` already points at another `stoktok` role → after build it is unchanged; `stoktok:member` is still created |
| `› test_build_unknown_app_raises_not_found` | `build_default_role("nope")` → `NotFoundError` |
| `› test_new_permission_not_added_until_rebuild` | after a build, insert a non-admin permission → the member role does not hold it (no automatic rebuild) |

## Approach
1. **Models** (`app/modules/rbac/models.py`; R2, R9). Use `UUIDPkMixin`; `TimestampMixin` on `App`, `Permission`, `Role`. Do not add `relationship()` (explicit queries in the repository avoid circular mapper configuration). Constants:
   - `APP_KEY_RE = r"^[a-z][a-z0-9-]{1,31}$"`
   - `PERMISSION_KEY_RE = r"^[a-z][a-z0-9-]{1,31}:([a-z][a-z0-9-]{0,47}:[a-z][a-z0-9-]{0,47}|\*)$"`
   - `ROLE_KEY_RE = r"^[a-z][a-z0-9-]{1,31}:[a-z][a-z0-9-]{0,47}$"`

   Write CHECKs as `key ~ '<re>'`. The CHECK SQL text in the model and in the migration must be identical, because Alembic does not compare CHECKs or server defaults (database-optimizer).
   - `App` (`apps`): `key String(32)` not null, `UniqueConstraint("key", name="uq_apps_key")`, CHECK `key_format`; `name String(255)` not null; `status String(20)` not null, default/server_default `'active'`, CHECK `status_valid` `IN ('active','disabled')`; `join_policy String(20)` not null, server_default `'open'`, CHECK `join_policy_valid` `IN ('open','approval','closed')`; `org_mode String(20)` not null, server_default `'none'`, CHECK `org_mode_valid` `IN ('none','required')`; `origins: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default=text("'{}'::text[]"))`; `default_role_id Uuid` null; `ForeignKeyConstraint(["default_role_id", "id"], ["roles.id", "roles.app_id"], name="fk_apps_default_role_same_app", use_alter=True)`. No ON DELETE clause, so the default NO ACTION applies: it is checked at the end of the statement, so deleting an app cascades through its roles without tripping this FK. Do **not** use RESTRICT (checked immediately) or SET NULL (would also null `apps.id`).
   - `Permission` (`permissions`): `app_id → apps.id ON DELETE CASCADE` not null; `key String(160)`; `description Text` null; `is_admin Boolean` not null, server_default `false`; `UniqueConstraint("app_id","key", name="uq_permissions_app_id_key")`; `UniqueConstraint("id","app_id", name="uq_permissions_id_app_id")`; CHECK `key_format`; CHECK `wildcard_is_admin`: `is_admin OR key NOT LIKE '%:*'`.
   - `Role` (`roles`): `app_id → apps.id ON DELETE CASCADE`; `key String(160)`; `name String(255)` not null; `is_system Boolean` not null, server_default `false`; `uq_roles_app_id_key`, `uq_roles_id_app_id`; CHECK `key_format`.
   - `RolePermission` (`role_permissions`): `role_id`, `permission_id`, `app_id` (all not null); PK `(role_id, permission_id)`; `ForeignKeyConstraint(["role_id","app_id"], ["roles.id","roles.app_id"], ondelete="CASCADE", name="fk_role_permissions_role_same_app")`; `ForeignKeyConstraint(["permission_id","app_id"], ["permissions.id","permissions.app_id"], ondelete="CASCADE", name="fk_role_permissions_permission_same_app")`; `Index` on `permission_id`. `app_id` is an extra column that is not in ADR-0001: it lets the database itself refuse a permission from another app (AC 2).
   - `RoleAssignment` (`role_assignments`): `user_id → users.id ON DELETE CASCADE` not null; `role_id → roles.id` (no ON DELETE, so NO ACTION: a role with assignments cannot be deleted; USR-02b turns this into a 409); `org_id Uuid` null, **no FK** (ticket: added in USR-06); `granted_by → users.id ON DELETE SET NULL` null; `expires_at DateTime(timezone=True)` null; `created_at` timestamptz not null, server_default `now()` (no `updated_at`, per §3); `UniqueConstraint("user_id","role_id","org_id", name="uq_role_assignments_user_role_org", postgresql_nulls_not_distinct=True)`; indexes on `role_id` and `org_id`. The unique index starts with `user_id`, so it also serves the effective-access query.
   - Explicit names are kept as written for `uq`/`fk` (verified: the naming convention only rewrites names when the template uses `constraint_name`, which applies to `ck`). Single-column FKs keep the convention names.
2. **Migration** (R2). Run `make migration m="create rbac"`, then edit the file by hand. `down_revision = "7b847bdc5230"`. Order: create `apps` without the default-role FK → `permissions` → `roles` → `role_permissions` → `role_assignments` → `op.create_foreign_key("fk_apps_default_role_same_app", "apps", "roles", ["default_role_id","id"], ["id","app_id"])` → seed. Seed uses `op.bulk_insert` with fixed UUID literals (module constants):
   - app `00000000-0000-4000-8000-0000000000a1` (`admin`, name "mmlabs Admin", `join_policy='closed'`)
   - permission `…00b1` (`admin:*`, `is_admin=true`, description "All admin permissions")
   - role `…00c1` (`admin:super-admin`, name "Super admin", `is_system=true`)
   - `role_permissions (…00c1, …00b1, app …00a1)`

   `downgrade()`: drop `fk_apps_default_role_same_app` first, then drop `role_assignments`, `role_permissions`, `roles`, `permissions`, `apps` (the seed rows go with the tables). Expand-only: new tables, nothing existing is touched (deploy.md migration rules). Run `alembic check` after editing (`postgresql_nulls_not_distinct` is compared by Alembic 1.20).
3. **Repository** (`RbacRepository(session)`; R1). Methods:
   - `get_app_by_key(key) -> App | None`
   - `get_role_by_key(app_id, key) -> Role | None`
   - `add_role(role) -> Role` (add + flush + refresh)
   - `list_permissions(app_id, *, is_admin: bool | None = None) -> list[Permission]` (ordered by key)
   - `role_permission_keys(role_id) -> dict[uuid.UUID, str]` (permission_id → key)
   - `add_role_permissions(role_id, app_id, permission_ids)` and `remove_role_permissions(role_id, permission_ids)`
   - `effective_role_keys(user_id, app_key, org_id) -> list[str]`: `SELECT DISTINCT r.key FROM role_assignments ra JOIN roles r ON r.id = ra.role_id JOIN apps a ON a.id = r.app_id WHERE ra.user_id = :u AND a.key = :app AND <scope> AND (ra.expires_at IS NULL OR ra.expires_at > now()) ORDER BY r.key`. `<scope>` is `ra.org_id IS NULL` when `org_id` is None, otherwise `(ra.org_id IS NULL OR ra.org_id = :org)`. Use `func.now()`, never the Python clock.
   - `effective_permission_keys(...)`: the same joins plus `JOIN role_permissions rp ON rp.role_id = r.id JOIN permissions p ON p.id = rp.permission_id AND p.app_id = a.id`, `DISTINCT p.key ORDER BY p.key`.
4. **Authz** (`app/modules/rbac/authz.py`). `AuthzService(session)`:
   - `effective_roles(user_id, app_key, org_id=None) -> list[str]` passes through to the repository.
   - `effective_permissions(...) -> list[str]`: the repository result filtered to keys starting with `f"{app_key}:"` (defense against a permission registered under another app's prefix, security-architect finding 4).

   Docstring: "RBAC only. Does not check user status, email verification, or app status/join policy; callers (USR-03 token, ADM-002) check those and return their own errors." Module-level `def permits(granted: Collection[str], required: str) -> bool`: `app, sep, rest = required.partition(":")`; `if not sep or not app or not rest: return False`; return `required in granted or f"{app}:*" in granted`. No global `*`, no `<app>:<resource>:*` (ADR-0003). No ORM import in `permits` (ADM-002 and `mmlabs-auth` copy it).
5. **Member builder** (`app/modules/rbac/service.py`). `@dataclass(frozen=True) DefaultRoleResult(role_key: str, created: bool, added: list[str], removed: list[str])`. `RoleCatalogService(session).build_default_role(app_key)`:
   1. Look up the app; if missing, raise `NotFoundError("App not found")`.
   2. `key = f"{app.key}:member"`; get the role, or create `Role(app_id, key, name="Member", is_system=True)`.
   3. `target = {p.id: p.key for p in list_permissions(app.id, is_admin=False)}`; `current = role_permission_keys(role.id)`.
   4. Add `target - current` and remove `current - target`.
   5. If `app.default_role_id is None`, set it to `role.id`. Flush.
   6. Return the sorted keys of what was added and removed.

   Docstring: runs only when called (seed, USR-02b app creation, USR-05 manifest); never automatically on permission changes, because that would silently widen or undo manual tightening (D9 "data, not code"). `<app>:*` is never included, since the CHECK forces it to `is_admin`. Add the comment `# R4: audit entry once ADM-004 lands`. The seed does **not** call the builder for `admin` (closed app, no member role — A5).
6. **ADR-0003**: short (Context / Decision / Consequences), covering the points in the files table. Mark the ADM-002 ticket example `admin:apps:*` as not allowed by the grammar (USR-02b/ADM-002 use `admin:apps:read|write`).
7. **Tests** as listed; `make check` green.

Rules: **R1** (SQL only in `repository.py`; services raise `NotFoundError`; no router). **R2** (one hand-edited migration with a working downgrade, models registered, `alembic check` clean). **R3** (no endpoint added; nothing to classify). **R4** (no admin action exposed; marker comment in the builder). **R5** (no config). **R6** (no HTTP surface; `NotFoundError` only). **R7** (only the listed files; extras go to Follow-ups). **R8** (tests above; 401/403 not applicable). **R9** (UUID PKs, timestamptz). **R10** (no billing or OIDC). **R11** (no credentials touched; no logging added).

Size estimate: about 470 lines of app code, migration and ADR, plus about 400 lines of tests, so about 870 in total. This is above the ~400 guide, but it is one cohesive slice with no HTTP surface. Splitting the schema from the authz code would leave constraints that only tests use and would add a ticket hop before USR-03. The orchestrator may still split authz plus the builder into its own slice if it wants.

## Specialist input used
**software-architect**
- Adopted: a single `app/modules/rbac/` module (because of the circular FK); `effective_*` filters expiry with the database `now()`; pure `permits()`; `<app>:*` returned unexpanded; `<app>:*` forced to `is_admin`; namespaced role keys; the member role rebuilt only when asked; seed `admin` closed with `default_role_id` NULL and `admin:*`/`admin:super-admin` (`is_system`); no seeding at runtime.
- Modified: a separate data migration → seed inside the single schema migration (one revision; the downgrade drops the tables, and with them the seed rows; keeps the slice to one migration).
- Modified split: catalog GET moved out of slice a (USR-03 does not need it; it is the first HTTP surface and its auth class is an owner question). CLI moved to USR-02c (it is the first grant path and belongs with assignments). → 3 slices.
- Rejected for now: add the `org_id → organizations` FK now. The ticket explicitly says USR-06. No org-scoped row can be written in staging or production before USR-06 (USR-02c is flag-gated and refused there; the CLI grants app scope only), so adding the FK later meets no orphans. Conflict with identity-architecture.md §3 recorded in FU3.

**database-optimizer**
- Adopted: explicit constraint names; CHECKs for key format, `join_policy`, `org_mode`; `origins ARRAY(Text) NOT NULL '{}'::text[]` written identically in the model and the migration; `UNIQUE (id, app_id)` + composite FKs for `role_permissions` and `default_role_id` with `use_alter`; `NULLS NOT DISTINCT`; FK indexes; seed with fixed UUIDs; upgrade/check/downgrade/upgrade loop; leave `NAMING_CONVENTION` unchanged.
- Modified: `default_role_id` FK with NO ACTION instead of RESTRICT (RESTRICT is checked immediately and would block deleting an app through the cascade; NO ACTION still blocks deleting a default role directly).
- Modified: `ON CONFLICT DO NOTHING` not needed (the rows are inserted in the same revision that creates the tables).
- Not adopted: `cardinality(origins)` CHECK. Origins validation belongs to the write path (USR-02b); keeps this slice minimal.
- Open Q1 (org FK): follow the ticket, see above. Q2 (`role_permissions.app_id`): adopted (security-architect agrees). Q3 (`<app>:*` as a real row): adopted. Q4 (app delete): DB cascades the catalog, but assignments block it; the 409 and the disable-instead policy are in USR-02b.

**identity-access-engineer**
- Adopted: key grammar (app `^[a-z][a-z0-9-]{1,31}$`, segment ≤ 48 chars; reject uppercase, extra segments, bare `*`, `<app>:<resource>:*`); namespaced role keys; one matcher `permits` with no global `*`; `effective_roles` with `org_id=None` returns app scope only; RBAC only (no status filter); the builder explicit and idempotent, returning the diff; admin app has no member role; ADM-002's `admin:apps:*` example flagged.
- Modified: segment pattern `[a-z][a-z0-9-]` without `_`, to match the existing slug style and the ticket examples (`super-admin`). Adding `_` later would widen the grammar without breaking anything.
- Deferred to USR-02b/02c: 422 rules for `expires_at` and `org_mode`, the last-super-admin guard, the `actor` parameter, escalation guards, CLI semantics.

**security-architect**
- Adopted: same-app integrity in the database (composite FKs) as well as in the service; `NULLS NOT DISTINCT`; `effective_permissions` resolved through `app_id` joins, never through string prefixes, plus a prefix filter as a second check; no grant path and no HTTP route in this slice, so the escalation risk (finding 1) does not arise in USR-02a.
- Deferred with contract (USR-02b/02c): separate default-off `ADMIN_RBAC_API_ENABLED`, refused in staging/production; requiring `APP_ENV` to be set explicitly when any admin flag is on; origins canonicalisation; deletion rules for system rows; CLI-only bootstrap with masked output.
- Owner questions (catalog in R3, origins suffix) → Split proposal (USR-02b), non-blocking for USR-02a.

**api-platform-engineer**
- Adopted (for USR-02b/02c contracts): apps addressed by immutable `key`, children by UUID; status rules (path id → 404, body id → 422, duplicate → 409, `IntegrityError` → `ConflictError`); `PUT` replacing a role's permission set; catalog document shape and ETag/`If-None-Match` handling; permission declarations through `openapi_extra` + a route-map test; `UnprocessableError` in `app/core/errors.py`.
- Modified: the catalog is mounted behind the RBAC flag in USR-02b, not public, until the owner adds it to R3's public list (keeps the v1 contract undecided).
- No part of it is in USR-02a (no endpoints).

## Assumptions
- **A1 — Split.** USR-02 is too large (~1,200–1,500 lines per all consults). This plan covers USR-02a; USR-02b and USR-02c are specified below for the orchestrator to create.
- **A2 — No `org_id` FK yet.** Follows the ticket scope ("FK added in USR-06"). The `organizations` table exists and USR-06 keeps the same table name (D15), so adding the FK then is a single additive step. Nothing writes org-scoped assignments in staging or production before then.
- **A3 — Key grammar** as in Approach §1. App keys contain no `:` and are immutable once created (enforced in USR-02b). Role keys are namespaced `<app>:<name>` (ADR-0001 example `stoktok:member`; the ticket's `<app>:member`, `admin:super-admin`). The `<app>` prefix matching the owning app cannot be a CHECK (it needs another table). It is enforced in the USR-02b service, and `effective_permissions` filters it as a second check.
- **A4 — App status** `active | disabled` (like users; USR-03 "app disabled → 403").
- **A5 — Admin app has no member role.** D9's "default role `<app>:member`" is read as applying to apps that can be joined. `admin` is `closed` (D9), so `default_role_id` stays NULL and roles come only from grants. Reversible: building `admin:member` later is a data change.
- **A6 — `<app>:*` is a stored permission row** with `is_admin = true` (CHECK). `permits` treats it as covering any `<app>:…` key, including keys not registered yet (so `admin:*` keeps working as ADM-002 adds permissions).
- **A7 — `effective_*` does not check status.** Disabled users or apps and unverified email are checked by USR-03/ADM-002, so each can return its own error.
- **A8 — The default role builder sets `default_role_id` only when it is NULL**, so a staff choice is not overwritten. It sets the member role's `is_system = true` (system-managed; deletion protection is in USR-02b).
- **A9 — Delete semantics.** In the database: deleting an app cascades to permissions, roles and role permissions; a role with assignments cannot be deleted; deleting a user cascades to their assignments. Whether staff may delete an app at all (vs `status = disabled`) is a USR-02b service rule.
- **A10 — `role_assignments` has no `updated_at`** (§3 lists `created_at` only).

## Blocking questions
None. The owner-level questions affect only the USR-02b/02c HTTP contract. They are listed there as non-blocking for USR-02a, and each has a reversible default (routes stay flag-gated and refused in staging/production until the owner answers).

## Split proposal
Order: **USR-02a → USR-02b → USR-02c**. USR-03 depends only on USR-02a. ADM-002 depends on 02a, 02b and 02c. USR-05 depends on 02b. ADM-006 depends on 02a. (Backlog dependency edits are the orchestrator's job.) USR-02b and USR-02c may be merged into one ticket if the orchestrator prefers; together they are about 1,000 lines.

### USR-02a — RBAC core (this plan)
Covers: ticket AC1, AC2 (database), AC3, AC4 (seed), AC6, and the matching part of AC8.

### USR-02b — Apps registry staff API (flag-gated) + role catalog
Covers: ticket AC2 (422 responses), AC5 (apps, permissions, roles, role permissions), AC7, and its part of AC8. Areas: api, auth, security.
- **Exposure:** new `admin_rbac_api_enabled: bool = False` (`ADMIN_RBAC_API_ENABLED=` in `.env.example`), separate from `ADMIN_USERS_API_ENABLED`. A `model_validator` refuses it when `app_env` ∈ {staging, production}. Also: when either admin flag is true, refuse to start unless `"app_env" in self.model_fields_set` (closes the USR-01b security-review follow-up 1). The router is included in `create_app` only when the flag is on (not in `app/api/router.py`). Test: the default app returns 404 for every new path.
- **Errors:** `UnprocessableError(AppError)` 422, code `unprocessable`, `details` listing the offending keys/fields, in `app/core/errors.py`. `IntegrityError` → `ConflictError` on racing inserts.
- **Validation:** app key `APP_KEY_RE`, immutable (`extra="forbid"` on PATCH). Permission key `PERMISSION_KEY_RE` and prefix == app key, else 422. `<app>:*` forced to `is_admin = true` (`is_admin=false` → 422). Role key `ROLE_KEY_RE` and prefix == app key, else 422. `is_system` is never accepted in input. Origins: each `scheme://host[:port]`, lowercase, IDNA → punycode, default port dropped, no path (a lone `/` stripped)/query/fragment/userinfo/`*`/`null`; `https` only, `http` only for `localhost`/`127.0.0.1`/`[::1]`; deduplicated; ≤ 20 entries, each ≤ 255 chars; stored and returned in normalized form.
- **Endpoints** (all Bearer class per R3, declared via `openapi_extra=permission(...)`, enforced by ADM-002; a test enumerates the route → permission map):

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/apps` | `limit`, `offset` | `Page[AppRead]` (order `key`) | 200, 422 | `admin:apps:read` |
| POST | `/api/v1/apps` | `{key, name, join_policy?, org_mode?, origins?}` | 201 `AppRead`; runs `build_default_role` | 201, 409 dup key, 422 | `admin:apps:write` |
| GET | `/api/v1/apps/{key}` | — | `AppRead` `{id,key,name,status,join_policy,org_mode,origins,default_role_id,created_at,updated_at}` | 200, 404 | `admin:apps:read` |
| PATCH | `/api/v1/apps/{key}` | any of `{name,status,join_policy,org_mode,origins,default_role_id}`; `null` allowed only for `default_role_id`; `extra="forbid"` | `AppRead` | 200, 404, 422 (bad value, role of another app or unknown role) | `admin:apps:write` |
| DELETE | `/api/v1/apps/{key}` | — | — | 204, 404, 409 (`admin`, or the app has assignments) | `admin:apps:write` |
| GET | `/api/v1/apps/{key}/permissions` | `limit`, `offset` | `Page[PermissionRead]` `{id,key,description,is_admin}` | 200, 404 | `admin:apps:read` |
| POST | `/api/v1/apps/{key}/permissions` | `{key, description?, is_admin?}` | 201 `PermissionRead` | 201, 404, 409, 422 | `admin:roles:manage` |
| PATCH | `/api/v1/apps/{key}/permissions/{permission_id}` | `{description?, is_admin?}` | `PermissionRead` | 200, 404, 422 (unset `is_admin` on `*`) | `admin:roles:manage` |
| DELETE | `/api/v1/apps/{key}/permissions/{permission_id}` | — | — | 204, 404, 409 (held by a role; `admin:*`) | `admin:roles:manage` |
| POST | `/api/v1/apps/{key}/roles` | `{key, name}` | 201 `RoleRead` `{id,key,name,is_system,permissions:[keys]}` | 201, 404, 409, 422 | `admin:roles:manage` |
| PATCH | `/api/v1/apps/{key}/roles/{role_id}` | `{name}` | `RoleRead` | 200, 404, 422 | `admin:roles:manage` |
| DELETE | `/api/v1/apps/{key}/roles/{role_id}` | — | — | 204, 404, 409 (`is_system`, is the app's default role, has assignments) | `admin:roles:manage` |
| PUT | `/api/v1/apps/{key}/roles/{role_id}/permissions` | `{permission_keys: [..]}` replaces the whole set | `RoleRead` | 200, 404, 422 `details` = unknown or other-app keys, 409 (removing `admin:*` from `admin:super-admin`) | `admin:roles:manage` |
| GET | `/api/v1/apps/{key}/roles` | `If-None-Match` | `{app, roles:[{id,key,name,is_system,permissions:[keys]}]}` sorted by key; `ETag` strong `"<first 32 hex of sha256(canonical JSON)>"`; `Cache-Control: no-cache` | 200, 304 (weak comparison, lists, `W/`, `*`; empty body, same ETag), 404 (unknown or disabled app) | catalog: **flag-gated in 02b**; public mount = owner decision (Q1) |

- **Rules:** services get an unused `actor: uuid.UUID | None = None` parameter (ADM-002 escalation guards). `# R4` markers on every mutation. The `admin` app, `admin:*` and `admin:super-admin` cannot be deleted or re-keyed. The catalog is built by a service (R1); ETag/304 is handled in the router. Rebuild of the member role happens on app creation only; staff tighten it with `PUT`.
- **Tests:** happy path, 404, 409 duplicates, 422 cross-app and prefix mismatch, default role of another app → 422, origins normalization and rejection, ETag stable/changes after a permission change, 304 variants, flag off → 404, flag refused in staging/production, route → permission map.
- **Owner questions (non-blocking, defaults in place):**
  - Q1: may `GET /api/v1/apps/{key}/roles` be added to R3 class 3 (public)? (Ticket: "not a secret by design".) Default: flag-gated until answered or until USR-05.
  - Q2: accept an R9 exception for `{key}` (immutable) in `/api/v1/apps/{key}/…` paths, as already fixed by the spec/ADR catalog path, plus a non-`Page` catalog document?
  - Q3: should origins also be limited to `*.mmlabs.online` (§7 same-site)? Default: any https.

### USR-02c — Role assignments staff API (flag-gated) + bootstrap super-admin CLI
Covers: ticket AC4 (CLI), AC5 (assign/revoke/list), and its part of AC8. Areas: api, auth, security.
- Same `ADMIN_RBAC_API_ENABLED` gate.
- **Endpoints:**

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/users/{user_id}/role-assignments` | `app` (key, optional), `limit`, `offset` | `Page[AssignmentRead]` `{id, app, role_id, role_key, org_id, granted_by, expires_at, active, created_at}` (order `created_at desc, id`) | 200, 404 user, 422 | `admin:users:read` |
| POST | `/api/v1/users/{user_id}/role-assignments` | `{role_id, org_id?, expires_at?}` | 201 `AssignmentRead` | 201, 404 user, 409 active duplicate, 422 (unknown role, past or naive `expires_at`, `org_id` on an `org_mode = none` app, unknown `org_id` checked against `organizations`) | `admin:roles:assign` |
| DELETE | `/api/v1/users/{user_id}/role-assignments/{assignment_id}` | — | — | 204, 404, 409 (last active super-admin) | `admin:roles:assign` |

- An expired duplicate is replaced in place (new `expires_at`, `granted_by`) → 201.
- Last-super-admin guard: revoking the last active, non-expired `admin:super-admin` assignment held by an active user → 409.
- **CLI:** `python -m app.cli grant-super-admin --email <email>` + `make grant-super-admin email=...`; documented in `docs/deploy.md` (`docker compose -p org-admin-api run --rm org-admin-api python -m app.cli …`). App scope, `granted_by` NULL, `expires_at` NULL. Idempotent (exit 0 "granted" / "already held"); unknown email → exit 2, disabled user → exit 3, with a generic message. Output shows the user id and a masked email only (R11). No env-var bootstrap and no "mark verified" path (D16).
- **Tests:** assign/revoke/list, duplicate 409, expired-replace, the 422 cases, last-super-admin 409, flag off → 404, route → permission map, CLI exit codes and idempotency, no full email in the CLI output.

## Data model / migration
One revision `create rbac` (down_revision `7b847bdc5230`). All ids `uuid`, all times `timestamptz`.

**apps**
| Column | Type | Null | Default |
|---|---|---|---|
| id | uuid PK | no | Python `uuid4` |
| key | varchar(32) | no | — |
| name | varchar(255) | no | — |
| status | varchar(20) | no | `'active'` |
| join_policy | varchar(20) | no | `'open'` |
| org_mode | varchar(20) | no | `'none'` |
| origins | text[] | no | `'{}'::text[]` |
| default_role_id | uuid | yes | — |
| created_at, updated_at | timestamptz | no | `now()` |

Constraints:
- `pk_apps`; `uq_apps_key`
- `ck_apps_key_format` (`key ~ '^[a-z][a-z0-9-]{1,31}$'`)
- `ck_apps_status_valid`, `ck_apps_join_policy_valid`, `ck_apps_org_mode_valid`
- `fk_apps_default_role_same_app (default_role_id, id) → roles (id, app_id)`: NO ACTION, MATCH SIMPLE (NULL skips the check), created after `roles`

**permissions**
| Column | Type | Null | Default |
|---|---|---|---|
| id | uuid PK | no | |
| app_id | uuid → apps.id ON DELETE CASCADE | no | |
| key | varchar(160) | no | |
| description | text | yes | |
| is_admin | boolean | no | `false` |
| created_at, updated_at | timestamptz | no | `now()` |

Constraints: `uq_permissions_app_id_key (app_id, key)`, `uq_permissions_id_app_id (id, app_id)`, `ck_permissions_key_format`, `ck_permissions_wildcard_is_admin (is_admin OR key NOT LIKE '%:*')`.

**roles**: `id`, `app_id → apps.id ON DELETE CASCADE`, `key varchar(160)`, `name varchar(255)`, `is_system boolean NOT NULL DEFAULT false`, timestamps. Constraints: `uq_roles_app_id_key`, `uq_roles_id_app_id`, `ck_roles_key_format`.

**role_permissions**: `role_id`, `permission_id`, `app_id` (uuid, not null).
- `pk_role_permissions (role_id, permission_id)`
- `fk_role_permissions_role_same_app (role_id, app_id) → roles (id, app_id) ON DELETE CASCADE`
- `fk_role_permissions_permission_same_app (permission_id, app_id) → permissions (id, app_id) ON DELETE CASCADE`
- `ix_role_permissions_permission_id`

**role_assignments**: `id`; `user_id → users.id ON DELETE CASCADE` not null; `role_id → roles.id` (NO ACTION) not null; `org_id uuid` null (no FK until USR-06); `granted_by → users.id ON DELETE SET NULL` null; `expires_at timestamptz` null; `created_at timestamptz NOT NULL DEFAULT now()`.
- `uq_role_assignments_user_role_org UNIQUE NULLS NOT DISTINCT (user_id, role_id, org_id)`
- `ix_role_assignments_role_id`, `ix_role_assignments_org_id`

**Seed** (same revision): app `admin` (closed); permission `admin:*` (is_admin); role `admin:super-admin` (is_system); the link between them. Fixed UUIDs as in Approach §2.

**Downgrade:** drop `fk_apps_default_role_same_app` → drop `role_assignments`, `role_permissions`, `roles`, `permissions`, `apps`. Expand-only, so a rollback to the previous image keeps working on the new schema (deploy.md).

## API contract
None in USR-02a (no endpoint added or changed). The USR-02b and USR-02c contracts are in the Split proposal.

## Follow-ups (not in this diff)
- **FU1 — USR-02b / USR-02c:** create the tickets from the Split proposal; update the backlog dependencies (USR-03 ← USR-02a; ADM-002 ← USR-02a/b/c; USR-05 ← USR-02b).
- **FU2 — ADM-002 ticket:** its example `admin:apps:*` does not fit the grammar (ADR-0001 §3 / ADR-0003); use `admin:apps:read|write`. Seed the fine-grained `admin:` permissions there. Before the release, review `role_assignments` for the `admin` app and `apps.origins` written while the flag was on.
- **FU3 — Owner/spec:** identity-architecture.md §3 and ADR-0001 §4 show `org_id → organizations`; the ticket defers the FK to USR-06. Confirm USR-06 adds it (with an orphan check).
- **FU4 — USR-03:** use `AuthzService.effective_roles` for the token `roles` claim. Check user status, email verification and app status/`join_policy` separately (A7). Production has only the `admin` app until USR-02b endpoints are usable there (after ADM-002), so registering stoktok/calechip in production waits for ADM-002 (or a later data migration).
- **FU5 — ADM-004:** audit builder runs and every RBAC mutation (`# R4` markers).
