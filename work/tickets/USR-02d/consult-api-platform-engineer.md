# consult-api-platform-engineer — USR-02d

## Findings
1. **Reusable:** `RbacRepository` already has `get_app_by_key`, `get_role`, `get_role_by_key`, `add_role` (savepoint), `list_permissions(app_id, is_admin=)` (unpaged), `role_permission_keys`, `add_role_permissions` (`on_conflict_do_nothing`), `remove_role_permissions`, `role_has_admin_permission`. `RoleCatalogService._build_for_app` computes the target/current diff inline; its insert/delete are **not** in a savepoint yet (carry-over 3).
2. **Missing:** repository `add_permission`, `get_permission(app_id, id)`, a paged permission list, `permissions_by_keys(app_id, keys)`, `permission_held_by_any_role`, `role_has_assignments`, `delete_permission`, `delete_role`, and an app row lock.
3. **DB behaviour:** permission delete cascades to `role_permissions` → the "held by a role" 409 must be a service check. Role delete with assignments fails via FK NO ACTION → keep as fallback 409. The `wildcard_is_admin` CHECK backs the `<app>:*` rule.
4. **`AppService.list`:** the only caller is `rbac/router.py:19`; no test calls it. `OrganizationService`/`UserService` have the same `list` name — leave them (R7, follow-up).
5. **Origins:** `normalize_origin` accepts any https host today. Tests that change: the accept list in `tests/test_rbac_origins.py` (`example.com`, `xn--bcher-kva.example`, `:8443`) and the generic message regex. API tests already use `*.mmlabs.online`, `localhost` or bad origins → still pass.
6. **D17 and the route test:** the catalog `GET /apps/{key}/roles` is public but belongs to 02b-3; the test filters by the `/api/v1/apps` prefix, so 02b-3 needs a "public" marker in its map.

## Recommendations
- **Schemas (AC1, AC2, R9):** `PermissionCreate{key: PERMISSION_KEY_RE, description: str|None ≤255, is_admin: bool|None}`; `PermissionUpdate{description (nullable), is_admin (non-null)}` reusing the `AppUpdate` null-guard pattern; `PermissionRead{id,key,description,is_admin}`; `RoleCreate{key: ROLE_KEY_RE, name}`, `RoleUpdate{name}`; `RoleRead{id,key,name,is_system,permissions: list[str] sorted}`; `RolePermissionsReplace{permission_keys: list[str] max_length=200}`, each item `PERMISSION_KEY_RE`. All inputs `extra="forbid"`. Prefix ≠ app key, `*` with `is_admin=false`, unknown keys → `UnprocessableError` with details `[{field|key, reason}]`.
- **Status codes (R6):** unknown app or a `permission_id`/`role_id` of another app (look up by `(app_id, id)`) → 404; duplicate key → pre-check + savepoint `IntegrityError` → 409; every AC4 guard → 409. Disabled apps remain manageable.
- **Paging:** `page_permissions(app_id, *, limit, offset) -> (items, total)` ordered by key, with `PageDep`; keep the builder's unpaged call.
- **Shared diff (AC3, carry-over 3):** service helper `_set_role_permissions(role, app_id, target: dict[UUID,str]) -> (added, removed)` calling one new repository method `apply_role_permission_diff` inside `begin_nested()`; the helper maps `IntegrityError` → 409. `_build_for_app` and PUT both use it. PUT: dedupe → `permissions_by_keys` → 422 listing every missing key → guards → diff → `RoleRead`.
- **Race fix (carry-over 1):** lock the app row with `SELECT … FOR UPDATE` (`get_app_by_key(key, for_update=True)`) in PUT, permission PATCH/DELETE, role DELETE, and `AppService.update` when `default_role_id` is among the fields. `default_role_id` must be a role of the same app (composite FK), so one per-app lock serializes every default-role/`is_admin` race — simpler than ordered role+permission locks.
- **Rename:** `AppService.list` → `list_apps`; one call site; mypy confirms nothing else.
- **Route test (AC1):** extend `test_rbac_routes_declare_permissions` to 13 entries: GET permissions → `admin:apps:read`, the other 7 → `admin:roles:manage`.
- **Origins D19 (AC5):** after the existing parse (already lowercases, rejects userinfo/path/empty labels/trailing dot):
  ```python
  if host not in _LOOPBACK_HOSTS and not (host == "mmlabs.online" or host.endswith(".mmlabs.online")):
      raise ValueError(_MESSAGE)
  ```
  The leading dot rejects `evilmmlabs.online`; `endswith` rejects `mmlabs.online.evil.com`. `http` stays loopback-only. New message (no echo): "origin must be https://mmlabs.online or a subdomain, or http(s) on localhost". Carry-over 4 (`0x7f.1`) is covered by the suffix rule; keep the digits check. Test cases: `evilmmlabs.online`, `mmlabs.online.evil.com`, `http://x.mmlabs.online`, `https://127.0.0.2`, `https://x.mmlabs.online.`. Amend ADR-0003 §9.
- **R4 / ADM-002:** `actor` + `# R4` markers on every mutation; `# ADM-002 guard` where `is_admin`/`<app>:*` is granted.

## Size
Production ~380 lines (schemas ~60, repository ~80, services ~150, router ~80, origins ~8); tests ~400. **Fits ~400**, close to the limit — keep `RoleRead` assembly in the service, no single-role GET.

## Risks
- Changing `_build_for_app` (diff helper + savepoint) can alter `test_rbac_default_role` results — re-run it.
- Without the app lock, carry-over 1 stays open; pre-checks alone aren't enough.
- If the D19 port rule is left implicit, `https://x.mmlabs.online:8443` slips through.
- Staff can still create `admin:`-prefixed permissions/roles while the flag is on (ADM-002 release checklist).

## Open questions
1. D19 ports: allow non-443 ports on `*.mmlabs.online`? Default: **reject**; loopback keeps any port.
2. PATCH a system role's `name`? Default: allowed (only delete is guarded).
3. `is_admin` true → false on a non-wildcard permission with no side effects? Default: yes; member role not auto-rebuilt (D9).
