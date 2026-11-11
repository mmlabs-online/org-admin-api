Status: READY

# Plan — USR-02d: Permissions and roles staff API (flag-gated)

> One slice (contract: `work/tickets/USR-02b/plan.md` → Split proposal → **02b-2**). Estimated ~400 production lines (schemas ~70, repository ~90, services ~170, router ~85, origins ~10), ~450 test lines. Close to the planner's 400-line guide, but every part shares one lock and one diff helper, so splitting it would ship a half-closed race. No single-role GET (as contracted; roles are read through `RoleRead` responses and the USR-02e catalog).

## Spec summary
- spec §5.3: "Apps are registered; each app declares namespaced permissions (`<app>:<resource>:<action>`) and its own roles … Every admin `/api/v1` endpoint declares the `admin:*` permission it needs."
- spec §5.3 Membership: "a default role `<app>:member` holding all its permissions except those flagged `is_admin` … Policy and role contents are data. … app origins are limited to `mmlabs.online`, its subdomains and localhost (D19)."
- identity-architecture.md **D18**: "apps are addressed by their immutable `key` in `/api/v1/apps/{key}/…`; … Permissions, roles and assignments keep UUID ids." **D19**: "only `https://mmlabs.online`, `https://*.mmlabs.online` and loopback (`http(s)://localhost`, `127.0.0.1`, `[::1]`). Any other origin → 422." **D17** (catalog public) is USR-02e, not this ticket.
- ADR-0003 §1 ("prefix == owning app key is enforced in the service (422 `unprocessable`) by the permission/role endpoints (USR-02d)"), §2 (`<app>:*` must be `is_admin`), §7 (default role is data, rebuilt only when asked), §9 ("A default role never holds an `is_admin` permission").
- 02b-2 contract (USR-02b plan): "path ids of another app → 404 … `<app>:*`: `is_admin` absent → forced true; `is_admin=false` → 422; PATCH `is_admin=false` on `*` → 422. `is_system`, `key` never accepted (`extra="forbid"`). PUT: dedupe; every unknown/other-app key listed in 422 `details`; empty list allowed; idempotent; diff applied through a `_set_role_permissions` shared with the builder. 409 guards: DELETE permission `admin:*` or held by any role; DELETE role `is_system`, the app's default role, or with assignments; PUT on `admin:super-admin` without `admin:*`; PUT adding an `is_admin` permission to the app's current default role; PATCH permission `is_admin=true` while the default role holds it. `IntegrityError` → 409."
- Ticket carry-overs: (1) TOCTOU on default roles, same transaction; (2) rename `AppService.list`; (3) savepoint + `IntegrityError` → 409 in `_build_for_app`; (4) numeric/hex last label.
- CLAUDE.md R1, R3 class 1 (declared `admin:…` permission until ADM-002), R4 (marker), R6, R7, R9 (+D18), R11 (no echo of secrets).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | 8 routes exactly as in **API contract**, in the existing `rbac/router.py` (mounted only with `ADMIN_RBAC_API_ENABLED`); GET permissions → `admin:apps:read`, the other 7 → `admin:roles:manage`; mutations have `# R4` markers and an unused `actor: uuid.UUID \| None = None` (ticket AC1) | `tests/test_rbac_apps_api.py › test_rbac_routes_declare_permissions` (exact 13-route map); `test_rbac_permissions_api.py › test_permissions_api_not_mounted_by_default`; `grep -n "# R4" app/modules/rbac/service.py` shows the 7 new mutations |
| 2 | Key prefix ≠ app key → 422 `unprocessable` (`{"field": "key", "reason": "prefix_mismatch"}`); bad format → 422 `validation_error`; `<app>:*` with `is_admin` absent/null → stored `true`; `is_admin=false` on create or PATCH of `<app>:*` → 422 `unprocessable` (`reason: wildcard_must_be_admin`); `key`, `is_system`, `id`, `app_id` in any body → 422 (`extra="forbid"`) (ticket AC2) | `test_create_permission_prefix_mismatch_422`, `test_create_permission_validation_422`, `test_create_wildcard_forced_admin`, `test_create_wildcard_not_admin_422`, `test_patch_wildcard_not_admin_422`, `test_create_role_prefix_mismatch_422`, `test_create_role_validation_422`, `test_patch_role_rejects_key_and_is_system` |
| 3 | PUT dedupes, lists **every** unknown/other-app key in 422 `details` (`[{"field": "permission_keys", "reason": "unknown_permission", "key": k}]`, sorted by key), allows `[]`, is idempotent (same response, no writes on repeat), and uses the same `_set_role_permissions` helper as `_build_for_app` (ticket AC3) | `test_put_role_permissions_replaces_set`, `test_put_role_permissions_dedupes_and_is_idempotent`, `test_put_role_permissions_empty_list`, `test_put_role_permissions_unknown_keys_422` (two unknown + one other-app key, all listed, nothing written), `test_put_role_permissions_format_422`; `grep -n "_set_role_permissions" app/modules/rbac/service.py` shows the builder and PUT |
| 4 | 409 `conflict` guards: DELETE `admin:*`; DELETE a permission held by any role; DELETE a system role, the app's default role, or a role with assignments; PUT on `admin:super-admin` without `admin:*`; PUT with any `is_admin` permission on the app's default role; PATCH `is_admin=true` on a permission the default role holds. Rows unchanged after each 409 (ticket AC4) | `test_delete_admin_wildcard_409`, `test_delete_permission_held_by_role_409`, `test_delete_system_role_409`, `test_delete_default_role_409`, `test_delete_role_with_assignments_409`, `test_put_super_admin_without_wildcard_409`, `test_put_admin_permission_on_default_role_409`, `test_patch_permission_admin_while_default_holds_409` |
| 5 | Carry-over (1) TOCTOU: every RBAC mutation (7 new routes, `AppService.update`, `AppService.delete`, `build_default_role`) first takes `SELECT … FROM apps … FOR NO KEY UPDATE` on the app row and decides every guard after it; the lock blocks a second locker but not FK `KEY SHARE` checks | `tests/test_rbac_locking.py › test_lock_app_blocks_lockers_not_key_share` (real second connection on the committed `admin` row), `test_mutations_lock_app_first` (parametrized, SQL capture: the request's first statement is the lock), `test_build_default_role_locks_app`; sequential race sides: `test_default_then_put_admin_permission_409`, `test_put_admin_permission_then_set_default_422` |
| 6 | Carry-over (3): `_build_for_app` and PUT apply the diff in one savepoint; `IntegrityError` → 409, session still usable | `tests/test_rbac_default_role.py › test_build_permission_fk_race_is_409`; `test_rbac_roles_api.py › test_put_role_permissions_integrity_race_is_409`; existing `test_rbac_default_role.py` tests still green |
| 7 | Carry-over (2): `AppService.list` renamed `list_apps`; only call site `rbac/router.py` updated | `grep -n "def list\b" app/modules/rbac/service.py` → nothing; `make check` (mypy) |
| 8 | Origins per D19 (+ carry-over 4): `https` only on `mmlabs.online` or a host ending in `.mmlabs.online`, default port only, not `api.mmlabs.online`; loopback (`localhost`, `127.0.0.1`, `[::1]`) on `http` or `https`, any port; everything else → 422 `validation_error` with one generic message that never echoes the value (ticket AC5) | `tests/test_rbac_origins.py › test_normalize_origin_accepts`, `test_normalize_origin_rejects` (exact generic message), `test_normalize_origins_dedupes_and_limits`; `test_rbac_apps_api.py › test_patch_app_non_mmlabs_origin_422` (body does not contain the host); existing `test_create_app_origins_normalized`, `test_patch_app_bad_origin_422` still green |
| 9 | Every endpoint has tests for happy path, 404 (unknown app, id of another app, unknown id), validation/conflict (R8) | the test table below |
| 10 | `make check` green; no migration (no schema change) | `make check`; `uv run alembic check` → no new operations |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/rbac/origins.py` | D19 host rule, mmlabs default port only, refuse `api.mmlabs.online`, new generic message, module docstring | AC 8 |
| `app/modules/rbac/schemas.py` | `PermissionCreate`, `PermissionUpdate`, `PermissionRead`, `RoleCreate`, `RoleUpdate`, `RoleRead`, `RolePermissionsReplace` | AC 1–3 |
| `app/modules/rbac/repository.py` | `lock_app`; `page_permissions`; `get_permission`; `get_permission_by_key`; `permissions_by_keys`; `add_permission` (savepoint); `delete_permission`, `delete_role` (savepoint); `permission_held_by_any_role`; `role_holds_permission`; `role_has_assignments`; `apply_role_permission_diff` (savepoint) | R1, AC 2–6 |
| `app/modules/rbac/service.py` | `_set_role_permissions` helper used by `_build_for_app` and PUT; `build_default_role` locks; `AppService.list` → `list_apps`, `update`/`delete` lock; new `PermissionService`, `RoleService` | AC 1–7, R1, R4 |
| `app/modules/rbac/router.py` | 8 routes (HTTP only), `list_apps` call renamed; module docstring mentions permissions/roles | AC 1, 7, R1, R3 |
| `app/core/errors.py` | `UnprocessableError` docstring: details may carry a catalog `key` that already passed format validation (PUT unknown keys) | AC 3, keeps the documented contract true |
| `docs/adr/0003-rbac-keys-and-matching.md` | amend header line; §1 wildcard forcing and 422s; §7 shared diff helper, a PUT on `<app>:member` is overwritten by the next rebuild; §9 origins per D19 and the invariant now enforced on every path under the per-app lock; new §10 guard catalogue | planner rule 7 (decision outlives the ticket; identity-access R6) |
| `tests/test_rbac_origins.py` | new accept/reject lists and message | AC 8 |
| `tests/test_rbac_apps_api.py` | 13-route map; `test_patch_app_non_mmlabs_origin_422` | AC 1, 8 |
| `tests/test_rbac_default_role.py` | `test_build_permission_fk_race_is_409` | AC 6 |
| `tests/test_rbac_permissions_api.py` (new) | permission endpoint tests | AC 1, 2, 4, 9 |
| `tests/test_rbac_roles_api.py` (new) | role + PUT endpoint tests, sequential race sides | AC 2–6, 9 |
| `tests/test_rbac_locking.py` (new) | lock behaviour and lock-first ordering | AC 5 |

No migration; no change to `app/main.py`, `app/api/router.py`, `app/db/models.py`, `app/modules/rbac/models.py`, `tests/conftest.py`, `tests/rbac_factories.py` (if a factory helper is truly needed, add it there and record why in `impl-log.md`). Each new test file defines its own `rbac_client` fixture (same 3 lines as `test_rbac_apps_api.py`).

## Tests to write
Notes: API tests use `rbac_client` = `make_client(admin_rbac_api_enabled=True)`; rows come from `tests/rbac_factories.py`. A `stoktok` app made with `make_app` has **no** member role; tests that need a default role either create the app through `POST /api/v1/apps` (builds `stoktok:member` as default) or set `default_role_id` with `PATCH /apps/stoktok`. Use `admin` only for `admin:*` / `admin:super-admin` cases (seeded by the migration). Every 409/422 test re-reads the rows to show nothing changed.

| Test (file › name) | Covers |
|---|---|
| `test_rbac_origins.py › test_normalize_origin_accepts` | `https://mmlabs.online`; `https://Stoktok.MMLabs.online/` → `https://stoktok.mmlabs.online`; `https://stoktok.mmlabs.online:443` → no port; `https://a.b.mmlabs.online`; `https://xn--abc.mmlabs.online`; `http://localhost:3000`, `http://127.0.0.1:5173`, `http://[::1]:8080`, `http://localhost:80` → `http://localhost`, `https://localhost:3000`, `https://127.0.0.1:8443` |
| `› test_normalize_origin_rejects` | keep the 23 existing rejects, plus: `https://example.com`, `https://app.example.com:8443`, `https://evilmmlabs.online`, `https://mmlabs.online.attacker.com`, `https://mmlabs.online.`, `https://x.mmlabs.online:8443`, `https://mmlabs.online:80`, `http://mmlabs.online`, `http://x.mmlabs.online`, `https://api.mmlabs.online`, `https://API.mmlabs.online:443`, `https://0x7f.1`, `https://a.123`, `https://127.0.0.2`, `https://xn--mmlabs-online.com`; each raises with the exact new generic message |
| `› test_normalize_origins_dedupes_and_limits` | as today, with `mmlabs.online` hosts: `["https://A.mmlabs.online", "https://a.mmlabs.online/"]` → one; 21 distinct subdomains → `ValueError`; a > 255-char entry → `ValueError`; `[]` → `[]` |
| `test_rbac_apps_api.py › test_rbac_routes_declare_permissions` | exact 13-entry map (5 existing + 8 new) |
| `› test_patch_app_non_mmlabs_origin_422` | PATCH `origins: ["https://example.com"]` → 422 `validation_error`, `"example.com" not in resp.text`, row unchanged |
| `test_rbac_default_role.py › test_build_permission_fk_race_is_409` | `_setup`; monkeypatch `RbacRepository.list_permissions` to return a transient `Permission(id=uuid4(), app_id=app.id, key="stoktok:x:y")` (not in DB) → `ConflictError`; then an exact `count(App)` query on the session works |
| `test_rbac_permissions_api.py › test_permissions_api_not_mounted_by_default` | default `client`: GET/POST `/apps/stoktok/permissions`, PATCH/DELETE `…/permissions/{uuid}` → 404 |
| `› test_list_permissions_paginated_and_sorted` | 3 permissions → sorted by key, `total`, `limit=1&offset=1` → second; `limit=101` → 422; only this app's rows |
| `› test_list_permissions_unknown_app_404` | `/apps/nope/permissions` → 404 |
| `› test_permission_read_has_exact_keys` | `{id, key, description, is_admin}` on list, create, patch |
| `› test_create_permission` | 201; `description` null when absent; `is_admin` false by default; `is_admin: true` stored |
| `› test_create_wildcard_forced_admin` | `{key: "stoktok:*"}` and `{key: "stoktok:*", is_admin: null}` → 201 `is_admin: true` |
| `› test_create_wildcard_not_admin_422` | `{key: "stoktok:*", is_admin: false}` → 422 `unprocessable`, details `[{"field": "is_admin", "reason": "wildcard_must_be_admin"}]`; nothing stored |
| `› test_create_permission_prefix_mismatch_422` | on `stoktok`: `calechip:x:read`, `admin:x:read` → 422 `unprocessable` `[{"field": "key", "reason": "prefix_mismatch"}]` |
| `› test_create_permission_validation_422` | parametrized → `validation_error`: key `Stoktok:x:y`, `stoktok:x`, `stoktok:x:*`, `*`, missing key; description 501 chars, description with `\x00`/`\n`; extras `is_system`, `id`, `app_id` |
| `› test_create_permission_duplicate_409` / `› test_create_permission_unknown_app_404` | same key twice → 409; `/apps/nope/permissions` → 404 |
| `› test_create_permission_integrity_race_is_409` | monkeypatch `get_permission_by_key` → `None`; POST existing key → 409; session usable |
| `› test_patch_permission` | `description` set, then `null` clears; `is_admin` false → true → false (no default holds it); `{}` → 200 unchanged |
| `› test_patch_permission_null_is_admin_422` / `› test_patch_permission_rejects_key` | `{is_admin: null}` → `validation_error`; `{key: …}`, `{is_system: …}` → 422 |
| `› test_patch_wildcard_not_admin_422` | `admin:*` (seed) and a created `stoktok:*` → PATCH `is_admin: false` → 422 `unprocessable`; still true |
| `› test_patch_permission_admin_while_default_holds_409` | app via POST `/apps`; permission created, then `PUT` onto `stoktok:member`; PATCH `is_admin: true` → 409; still false |
| `› test_patch_permission_404` | unknown app; unknown id; id of a `calechip` permission under `/apps/stoktok/…` → 404; non-UUID id → 422 |
| `› test_delete_permission` | 204, row gone, list no longer has it |
| `› test_delete_admin_wildcard_409` | DELETE seeded `admin:*` → 409, still there |
| `› test_delete_permission_held_by_role_409` | permission granted to a non-default role → 409; still there and still granted |
| `› test_delete_permission_404` | unknown app / unknown id / other app's id → 404 |
| `test_rbac_roles_api.py › test_role_read_has_exact_keys` | `{id, key, name, is_system, permissions}` on create, patch, PUT |
| `› test_create_role` | 201, `is_system: false`, `permissions: []` |
| `› test_create_role_prefix_mismatch_422` | `calechip:viewer` on `stoktok` → `unprocessable` `prefix_mismatch` |
| `› test_create_role_validation_422` | key `stoktok`, `stoktok:a:b`, `Stoktok:x`, missing name, `name: ""`, name 256 chars, name with `\x07`; extras `is_system`, `id` → `validation_error` |
| `› test_create_role_duplicate_409` / `› test_create_role_unknown_app_404` | duplicate key (e.g. `stoktok:member` after POST `/apps`) → 409; unknown app → 404 |
| `› test_create_role_integrity_race_is_409` | monkeypatch `get_role_by_key` → `None`; existing key → 409; session usable |
| `› test_patch_role_name` | name changed; system role (`stoktok:member`) name editable; `{}` → 200 unchanged |
| `› test_patch_role_rejects_key_and_is_system` | `{key}`, `{is_system: true}`, `{name: null}` → 422 |
| `› test_patch_role_404` | unknown app / unknown id / other app's role id → 404 |
| `› test_delete_role` | 204, role and its `role_permissions` gone |
| `› test_delete_system_role_409` | `admin:super-admin` and a member role made non-default → 409 |
| `› test_delete_default_role_409` | a non-system role set as `default_role_id` → 409 |
| `› test_delete_role_with_assignments_409` | `make_user` + `assign` → 409 |
| `› test_delete_role_404` | unknown app / unknown id / other app's id → 404 |
| `› test_put_role_permissions_replaces_set` | role holds `{a, b}`; PUT `[b, c]` → `permissions == [b, c]` sorted; DB matches |
| `› test_put_role_permissions_dedupes_and_is_idempotent` | PUT `[c, a, a]` → `[a, c]`; repeating gives the same body and no INSERT/DELETE on `role_permissions` (SQL capture or row `ctid`s unchanged — implementer's choice, record it) |
| `› test_put_role_permissions_empty_list` | PUT `[]` → `permissions: []` |
| `› test_put_role_permissions_unknown_keys_422` | PUT `[stoktok:a:x, stoktok:zz:y, stoktok:yy:y, calechip:x:read]` (last exists in `calechip`) → 422 `unprocessable`, details = the 3 missing keys sorted, each `{"field": "permission_keys", "reason": "unknown_permission", "key": …}`; role unchanged |
| `› test_put_role_permissions_format_422` | `["*"]`, `["stoktok:x:*"]`, 201 items, `{"permission_keys": "x"}`, missing field → `validation_error` |
| `› test_put_role_permissions_404` | unknown app / unknown role / other app's role → 404 |
| `› test_put_super_admin_without_wildcard_409` | `admin:super-admin` PUT `[]` → 409; PUT with an extra admin permission plus `admin:*` → 200 |
| `› test_put_admin_permission_on_default_role_409` | app via POST `/apps`; `stoktok:*` or an `is_admin` permission onto `stoktok:member` → 409; non-admin permission → 200 |
| `› test_put_system_member_role_allowed` | `stoktok:member` PUT with non-admin keys → 200 (D9: content is data) |
| `› test_default_then_put_admin_permission_409` | race side (a), sequential: PATCH `/apps/stoktok` `default_role_id` = role R (no admin perms) → then PUT an `is_admin` permission onto R → 409 |
| `› test_put_admin_permission_then_set_default_422` | reverse order: PUT `is_admin` permission onto R → then PATCH default to R → 422 (02b-1 rule still holds under the lock) |
| `› test_put_role_permissions_integrity_race_is_409` | monkeypatch `permissions_by_keys` to return a transient `Permission` (random id, not in DB) → 409; session usable |
| `test_rbac_locking.py › test_lock_app_blocks_lockers_not_key_share` | `RbacRepository(db_session).lock_app("admin")` (seeded, committed row); a second connection from a fresh engine: `SELECT 1 FROM apps WHERE key = 'admin' FOR NO KEY UPDATE NOWAIT` → `DBAPIError` with SQLSTATE `55P03`; then (new transaction) `FOR KEY SHARE NOWAIT` → succeeds; second connection rolled back and closed before the fixture rollback |
| `› test_mutations_lock_app_first` | parametrized over the 7 mutating new routes + PATCH and DELETE `/apps/{key}`: SQL capture (`before_cursor_execute` listener on `db_session`'s connection, removed after); the first statement issued by the request selects from `apps` and contains `FOR NO KEY UPDATE` |
| `› test_build_default_role_locks_app` | same capture around `RoleCatalogService.build_default_role("stoktok")` |

## Approach
1. **Lock (AC 5, carry-over 1, R1).** `RbacRepository.lock_app(key) -> App | None`:
   ```
   select(App).where(App.key == key)
       .with_for_update(key_share=True)            # renders FOR NO KEY UPDATE
       .execution_options(populate_existing=True)  # refresh an already-loaded App
   ```
   Every RBAC mutation calls it **first** (before any other read) and takes every guard decision after it: the 7 new mutating service methods, `AppService.update` and `AppService.delete` (replace `self.get(key)` with a private `_locked(key)` that raises `NotFoundError`), and `RoleCatalogService.build_default_role` (replaces `get_app_by_key`). `_build_for_app` does not lock itself; its docstring states that the caller holds the app lock or owns the uncommitted new row (`AppService.create`). Why one per-app lock: roles, permissions, `role_permissions` and `apps.default_role_id` are bound to one app by the composite FKs, so it serializes all three race sides (identity-access F1) and the delete/PUT races (F3); the lock order is always app → rows, so no deadlocks; `NO KEY UPDATE` does not conflict with the `KEY SHARE` locks that FK checks take (02c assignment inserts). Put a comment on `lock_app`: "Every RBAC writer must call this first (ADR-0003 §9/§10)."
2. **Shared diff (AC 3, 6, carry-over 3).** Repository `apply_role_permission_diff(role_id, app_id, add, remove)` runs the existing `add_role_permissions` + `remove_role_permissions` inside `async with self.session.begin_nested()`. Service module-level helper:
   ```
   async def _set_role_permissions(repo, role, target: dict[UUID, str]) -> tuple[list[str], list[str]]:
       current = await repo.role_permission_keys(role.id)
       to_add, to_remove = target.keys() - current.keys(), current.keys() - target.keys()
       if to_add or to_remove:
           try:
               await repo.apply_role_permission_diff(role.id, role.app_id, to_add, to_remove)
           except IntegrityError as exc:
               raise ConflictError("Role permissions changed concurrently") from exc
       return sorted(target[p] for p in to_add), sorted(current[p] for p in to_remove)
   ```
   `_build_for_app` uses it (its result fields stay the same). No write when the diff is empty (idempotent PUT).
3. **Schemas (AC 1–3, R9).** All input models `extra="forbid"`. Constant `_NO_CONTROL = r"^[^\x00-\x1f\x7f]*$"` (control characters, incl. NUL that Postgres `text` refuses with a 500).
   - `PermissionCreate {key: str = Field(pattern=PERMISSION_KEY_RE, max_length=160); description: str | None = Field(None, max_length=500, pattern=_NO_CONTROL); is_admin: bool | None = None}`
   - `PermissionUpdate {description: str | None (same constraints, null allowed); is_admin: bool | None}` + `model_validator` refusing explicit null for `is_admin` (same pattern as `AppUpdate`).
   - `PermissionRead {id, key, description, is_admin}` (`from_attributes`).
   - `RoleCreate {key: str = Field(pattern=ROLE_KEY_RE, max_length=160); name: str = Field(min_length=1, max_length=255, pattern=_NO_CONTROL)}`; `RoleUpdate {name: str | None}` same constraints, explicit null refused.
   - `RoleRead {id, key, name, is_system, permissions: list[str]}` (sorted keys; built in the service).
   - `RolePermissionsReplace {permission_keys: list[Annotated[str, Field(pattern=PERMISSION_KEY_RE, max_length=160)]] = Field(max_length=200)}`.
4. **`PermissionService`** (every mutation: `app = await self._locked(key)` first; `# R4` marker; `actor=None` param):
   - `list_permissions(key, limit, offset)`: `get_app_by_key` (no lock) → 404 → `page_permissions` ordered by key.
   - `create`: prefix check (`data.key.split(":", 1)[0] != app.key` → `UnprocessableError`, `prefix_mismatch`); wildcard (`data.key == f"{app.key}:*"`): `is_admin is False` → 422 `wildcard_must_be_admin`, else `True`; others `bool(data.is_admin)`. Duplicate pre-check `get_permission_by_key` → 409; `add_permission` in a savepoint, `IntegrityError` → 409. `# ADM-002 guard: creating an is_admin or <app>:* permission, or any admin-app permission, requires admin:*`.
   - `update`: `get_permission(app.id, id)` → 404. `is_admin is False` on a wildcard key → 422. `is_admin is True` and currently false and `app.default_role_id` is not None and `role_holds_permission(app.default_role_id, perm.id)` → 409. Apply changed fields only; no change → no write; `repo.save()` + `IntegrityError` → 409; refresh. `# ADM-002 guard` on `is_admin` changes (true → false makes a permission eligible for the default role).
   - `delete`: 404; `app.key == ADMIN_APP_KEY and perm.key == "admin:*"` → 409; `permission_held_by_any_role` → 409; `delete_permission` (savepoint), `IntegrityError` → 409.
5. **`RoleService`** (same lock-first rule, markers, `actor`):
   - `create`: prefix → 422; dup pre-check → 409; `add_role(Role(..., is_system=False))` with `IntegrityError` → 409; returns `RoleRead` with `permissions=[]`. `# ADM-002 guard` for admin-app roles.
   - `update` (name): 404; system roles allowed; no change → no write; save/refresh; returns `RoleRead`.
   - `delete`: 404; `is_system` → 409; `role.id == app.default_role_id` → 409; `role_has_assignments` → 409; `delete_role` (savepoint), `IntegrityError` → 409 (FK `NO ACTION` on `role_assignments.role_id` and `fk_apps_default_role_same_app` as second line).
   - `replace_permissions`: 404 role; `keys = list(dict.fromkeys(data.permission_keys))`; `found = {p.key: p for p in await repo.permissions_by_keys(app.id, keys)}`; missing (sorted) → one `UnprocessableError("Unknown permissions for this app", details=[{"field": "permission_keys", "reason": "unknown_permission", "key": k} ...])`; guards (409): `role.id == app.default_role_id and any(p.is_admin)`; `app.key == ADMIN_APP_KEY and role.key == "admin:super-admin" and "admin:*" not in found`; then `_set_role_permissions(repo, role, {p.id: p.key for p in found.values()})`; return `RoleRead` with `sorted(found)`. `# ADM-002 guard: granting <app>:* or any is_admin permission, and any admin-app role edit, requires admin:*`.
   - `RoleRead` assembly: `RoleRead(id=..., key=..., name=..., is_system=..., permissions=sorted((await repo.role_permission_keys(role.id)).values()))` for create/update.
6. **`AppService` (carry-overs 1, 2).** Rename `list` → `list_apps` (router call site). `update`/`delete` start with the lock (step 1); the existing `default_role_id` 422 checks now run under it. No other behaviour change.
7. **Router (R1, R3, R9/D18).** In `rbac/router.py` (prefix `/apps`): `GET/POST /{key}/permissions`, `PATCH/DELETE /{key}/permissions/{permission_id}`, `POST /{key}/roles`, `PATCH/DELETE /{key}/roles/{role_id}`, `PUT /{key}/roles/{role_id}/permissions`; ids typed `uuid.UUID`; `openapi_extra=permission(...)`; 201 on POST, 204 on DELETE; only schema building and service calls. Do **not** add `GET /{key}/roles` (USR-02e).
8. **Origins (AC 8, D19).** After the existing parse and checks in `normalize_origin`:
   ```
   mmlabs = host == "mmlabs.online" or host.endswith(".mmlabs.online")
   if host in _LOOPBACK_HOSTS:
       pass  # http or https, any port
   elif scheme != "https" or not mmlabs or host == _API_HOST or port not in (None, "443"):
       raise ValueError(_MESSAGE)
   ```
   (`_API_HOST = "api.mmlabs.online"`; place the port check before the default-port drop, or equivalently reject a non-`None` port after it.) New `_MESSAGE = "origin must be https://mmlabs.online or one of its subdomains, or http(s) on localhost"`. Keep the IP-literal check (it still stops `https://127.0.0.2`); the suffix rule covers carry-over 4 (`0x7f.1`, `a.123` end in a non-`online` label). Update the module docstring (D19).
9. **ADR-0003** amendments (header "Amended 2026-11-11 (USR-02d): §1, §7, §9, new §10"): §1 — `<app>:*` without `is_admin` is stored `true`, `false` → 422; prefix check live. §7 — PUT and the builder share `_set_role_permissions` (one savepoint, races → 409); a manual PUT on `<app>:member` is overwritten by the next explicit rebuild. §9 — origins per D19 (replace "`https` for any LDH host"); the invariant is enforced on every path (app PATCH, role PUT, permission PATCH) under the per-app lock. New §10 — "Writers lock the app row (`FOR NO KEY UPDATE`) first", and the guard catalogue: 409 list from AC 4; 422 for prefix/wildcard/unknown keys; system rows (`admin:*` not deletable, `admin:super-admin` keeps `admin:*`, `is_system` roles PUT/name-editable but not deletable); unknown-key 422 details carry keys (format-valid, not secrets).
10. **Rules.** R1 (ORM only in repository; services raise `AppError`s; router HTTP only); R2 (no schema change → no migration, `alembic check` clean); R3 class 1 (declared permissions; flag gate until ADM-002); R4 markers; R5 nothing new; R6 envelope, 201/204/404/409/422; R7 (follow-ups below); R8 tests per endpoint; R9 + D18 (`{key}` paths, UUID ids, `Page[T]` for the list, PATCH); R11 (no logging, origin errors never echo; only format-valid catalog keys appear in details).

## Specialist input used
**api-platform-engineer** (`consult-api-platform-engineer.md`)
- Adopted: schemas and status codes (Rec. schemas / status codes), 404 for other-app ids via `(app_id, id)` lookups, `page_permissions` + `PageDep`, shared diff helper in a savepoint, `list` → `list_apps`, 13-route map, D19 suffix rule with leading dot, size estimate (one slice), no single-role GET.
- Adopted with change: the lock — `FOR NO KEY UPDATE` (identity-access R1) instead of `FOR UPDATE`, and taken on every `AppService.update`, not only when `default_role_id` changes (simpler, no extra path).
- Open Q1 (ports): adopted — reject non-default ports on mmlabs hosts. Q2 (PATCH system role name): adopted — allowed. Q3 (`is_admin` true → false): adopted — allowed, member role not rebuilt (D9).

**identity-access-engineer** (`consult-identity-access-engineer.md`)
- R1 per-app `FOR NO KEY UPDATE` lock taken first, incl. `AppService.update` and the builder: adopted (Approach 1). Trigger / SERIALIZABLE / advisory lock: rejected for the same reasons as the consult.
- R2 guards after the lock: adopted. R3 shared diff, empty list allowed: adopted. R4 member role PUT-editable, not deletable; system role names editable: adopted.
- R5 tests: adopted (sequential race sides + SQL-capture lock-first test), plus a real two-connection lock test on the committed `admin` row.
- R6 ADR-0003 amendments: adopted (Approach 9).
- Risk "unknown keys in details vs no-echo": adopted — only format-valid keys, ≤ 200, documented in `errors.py` and ADR §10.
- Q1 force every `admin`-app permission `is_admin`: rejected for this ticket (not in the 02b-2 contract; no effect while `admin` has no default role) → ADM-002 follow-up. Q2 reserve `<app>:member`: rejected (it already exists for every non-`admin` app, so a create → 409 dup; `admin:member` is inert) → follow-up. Q3 `admin:super-admin` PUT beyond `admin:*`: as contracted (allowed).

**security-architect** (`consult-security-architect.md`)
- AC5 origins: adopted — label-safe suffix rule, https only, default port only on mmlabs hosts, loopback any scheme/port, `xn--` labels under the suffix accepted, numeric/hex last label covered, exact reject/accept test lists, generic message.
- Q1 refuse `api.mmlabs.online`: adopted (Assumption 2). Q2 non-default ports: adopted. Q3 `admin:*` only on `admin:super-admin`: rejected here (not in the contract; ADM-002 adds "granting `is_admin` needs `admin:*`") → follow-up. Q4 refuse loopback on the `admin` app now: rejected — it would block local development of the admin web UI against a local API; the environment-based drop belongs to USR-03 (follow-up already listed in USR-02b).
- Lock-first for every role/permission mutation and app PATCH: adopted.
- `# ADM-002 guard` marker sites: adopted (Approach 4, 5; `admin` origins marker already exists).
- Input hygiene: adopted with change — role `name` keeps the column/`AppCreate` limit 255 (not 100), description ≤ 500; control characters refused in both.
- USR-02e catalog notes (own schema, caps, same 404): not this ticket → forwarded as follow-ups.
- Risk "rebuild overwrites a manual PUT on member": documented in ADR §7, not restricted.

## Assumptions
1. **Mmlabs hosts use the default port only** (`https://x.mmlabs.online:8443` → 422); loopback keeps any port. Reversible (widening is additive), consults agree.
2. **`https://api.mmlabs.online` is refused as an app origin** (the API's own host; same-origin to itself, so it never needs CORS). Reversible.
3. **Loopback stays allowed on every app, `admin` included** (D19 as written); USR-03 decides the per-environment echo.
4. Existing `apps.origins` rows that break D19 are not re-validated (ticket: none exist outside local/test).
5. `is_admin: null` on permission create = absent; on PATCH it is a `validation_error` (non-null contract).
6. PUT with an `is_admin` permission on the default role is 409 even if the role somehow already holds it (the target set must hold none).
7. Disabled apps and the `admin` app stay manageable through these endpoints (escalation control is ADM-002, marked `# ADM-002 guard`).
8. Role `name` ≤ 255 and permission `description` ≤ 500, both without control characters — stricter now is reversible; `description` has no DB limit today.
9. A PUT on a role does not touch `roles.updated_at`.

## Blocking questions
None.

## Split proposal
None (one slice; see the size note at the top). Follow-ups (not in this diff, R7):
- **ADM-002:** every `# ADM-002 guard` site needs `admin:*` (admin-app writes, granting `<app>:*`/`is_admin`, `is_admin` true → false, `admin` origins); consider `admin:*` only on `admin:super-admin`, all `admin`-app permissions `is_admin`, reserving the `<app>:member` key; release checklist: review `admin:`-prefixed rows created while the flag was on.
- **USR-02e:** the route-permission test filters by `/api/v1/apps`, so the public catalog needs a "public" (no `x-permission`) entry in the map; own catalog schema (never `RoleRead`), size caps.
- **USR-02c / USR-05 / CLI:** every new RBAC writer must call `lock_app` first; consider a DB trigger for "default role never holds `is_admin`" for paths that bypass the service.
- **USR-03:** loopback not echoed in staging/production; re-validate stored origins at read time.
- `OrganizationService.list` / `UserService.list` share the shadowing name (rename later).

## Data model / migration
None. No table, column, constraint or index change; `alembic check` must stay clean. Existing DB backstops used: `uq_permissions_app_id_key`, `uq_roles_app_id_key` (dup → 409), `wildcard_is_admin` CHECK, composite FKs (cross-app), `role_assignments.role_id` and `fk_apps_default_role_same_app` (`NO ACTION`, delete races → 409), `role_permissions` `ON DELETE CASCADE` (hence the service-side "held by a role" check).

## API contract
All R3 class 1 (Bearer, `aud=admin` once ADM-002 lands); unauthenticated until then and mounted only when `ADMIN_RBAC_API_ENABLED=true` (refused in staging/production). `{key}` = immutable app key (D18); ids are UUIDs (non-UUID → 422). An id belonging to another app → 404. Errors use the R6 envelope; `validation_error` for schema failures, `unprocessable` for data refusals. Every mutation locks the app row first.

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/apps/{key}/permissions` | `limit` (1–100, default 20), `offset` (≥ 0) | `Page[PermissionRead {id, key, description, is_admin}]` ordered by `key` | 200, 404, 422 | `admin:apps:read` |
| POST | `/api/v1/apps/{key}/permissions` | `PermissionCreate {key, description? (≤ 500, no control chars), is_admin?}` | 201 `PermissionRead`; `<app>:*` stored `is_admin=true` | 201, 404, 409 (dup, race), 422 (format/extra → `validation_error`; prefix, wildcard `is_admin=false` → `unprocessable`) | `admin:roles:manage` |
| PATCH | `/api/v1/apps/{key}/permissions/{permission_id}` | `PermissionUpdate {description? (nullable), is_admin? (non-null)}` | `PermissionRead` | 200, 404, 409 (`is_admin=true` while the default role holds it; race), 422 (null `is_admin`/extra → `validation_error`; `is_admin=false` on `<app>:*` → `unprocessable`) | `admin:roles:manage` |
| DELETE | `/api/v1/apps/{key}/permissions/{permission_id}` | — | — | 204, 404, 409 (`admin:*`; held by any role; race) | `admin:roles:manage` |
| POST | `/api/v1/apps/{key}/roles` | `RoleCreate {key, name (1–255, no control chars)}` | 201 `RoleRead {id, key, name, is_system, permissions: [sorted keys]}` (`is_system=false`, `permissions=[]`) | 201, 404, 409 (dup, race), 422 (format/extra; prefix → `unprocessable`) | `admin:roles:manage` |
| PATCH | `/api/v1/apps/{key}/roles/{role_id}` | `RoleUpdate {name? (non-null)}` | `RoleRead` | 200, 404, 422 | `admin:roles:manage` |
| DELETE | `/api/v1/apps/{key}/roles/{role_id}` | — | — | 204, 404, 409 (`is_system`; app's default role; has assignments; race) | `admin:roles:manage` |
| PUT | `/api/v1/apps/{key}/roles/{role_id}/permissions` | `RolePermissionsReplace {permission_keys: list[str] (≤ 200, each `PERMISSION_KEY_RE`)}`; deduped; `[]` allowed | `RoleRead` (the new set) | 200, 404, 409 (`admin:super-admin` without `admin:*`; `is_admin` permission on the default role; race), 422 (format → `validation_error`; unknown/other-app keys → `unprocessable` with every key in `details`) | `admin:roles:manage` |

Unchanged routes, changed behaviour: `PATCH /api/v1/apps/{key}` and `POST /api/v1/apps` accept only D19 origins (other origins → 422 `validation_error`, no echo); `PATCH`/`DELETE /api/v1/apps/{key}` take the app lock first.
