# consult-software-architect — USR-02

## Findings

1. **Circular FK.** `apps.default_role_id → roles` and `roles.app_id → apps` reference each other, so the FK needs `use_alter=True` (or `op.create_foreign_key` after both tables exist) and a matching drop order in `downgrade()`.
2. **The unique rule breaks on NULL.** `unique (user_id, role_id, org_id)` with `org_id NULL` does not stop duplicate app-scope assignments (Postgres treats NULLs as distinct by default). We run PG 17, so `UNIQUE NULLS NOT DISTINCT` is available.
3. **Wildcards can leak into the member role.** `admin:*` is stored as an ordinary `permissions` row; unless `<app>:*` is forced to `is_admin = true`, the D9 member-role builder would put it into `<app>:member`.
4. **Two endpoints share one path.** `GET /api/v1/apps/{key}/roles` is both the role catalog (meant to be public or app-credential) and the natural staff "list roles" route. ADM-002 will need to treat this path specially.
5. **Granting roles is a takeover path.** Today `users_router` is mounted only when `admin_users_api_enabled` is set, and that flag is refused in staging/production (`app/core/config.py`). New staff assign/grant endpoints would let anyone give themselves `admin:super-admin`, and those rows would survive into the ADM-002 era. `organizations` is open without a flag, but the owner accepted that and it carries no such escalation risk.
6. **Deploy rules.** Deploys run `alembic upgrade head` as their own step, rollbacks never migrate down, and `tests/conftest.py` builds the test DB from the migrations. Data in migrations is present in every environment, tests included.
7. **Size.** 5 tables, a migration, a seed, ~8 endpoints, the authz service, a CLI and tests ≈ 1,200–1,500 lines — well over the ~400 target.

## Recommendations

- **Module layout (R1, AC1/AC5).** A single `app/modules/rbac/`: models for all 5 tables, one repository, services `AppService`, `CatalogService` (permissions, roles, role permissions, member builder), `AssignmentService`, plus `authz.py`. A separate `apps` module would import `rbac` and vice versa (circular FK). Allowed imports: `rbac` → `users`; `auth` (USR-03 token) → `rbac`; ADM-002 deps → `rbac`. `users` and `core` must **not** import `rbac`.
- **Effective roles/permissions (AC6; reused by USR-03/ADM-002).** `rbac/authz.py`: `effective_roles(session, user_id, app_key, org_id=None)` and `effective_permissions(...)`, expiry filtered with DB `now()` (`expires_at IS NULL OR expires_at > now()`). A pure `permits(granted: set[str], required: str) -> bool` (exact key or `<app>:*`), no ORM — ADM-002's `require_permission` and `mmlabs-auth` both mirror it. `effective_permissions` returns `<app>:*` unexpanded so a wildcard also covers not-yet-catalogued permissions.
- **Invariants (AC1/AC2).** `NULLS NOT DISTINCT` on the assignment unique. `<app>:*` forces `is_admin = true`. Key regex `^<app>:([a-z0-9-]+:[a-z0-9-]+|\*)$`. Same-app checks in the service → 422 (optional DB guard via composite FKs `(role_id, app_id)` / `(permission_id, app_id)` — database-optimizer's call). Blocking deletes: deleting an app's default role → 409; deleting a permission still held by a role → 409.
- **Member role is data, not code (AC3, D9).** `CatalogService.build_default_role(app)` creates/refreshes `<app>:member` = all `is_admin = false` permissions and sets `default_role_id`. Runs only on app creation and on explicit request (service call; later the USR-05 manifest) — **not** automatically when a permission is added, which would undo manual tightening of `role_permissions`. Store role keys namespaced (`stoktok:member`) like permission keys.
- **Seed (AC4).** Static catalog in a separate **data migration** after the schema revision: app `admin` (`closed`, `default_role_id = NULL`), permission `admin:*` (`is_admin`), role `admin:super-admin` (`is_system`), with `INSERT … ON CONFLICT DO NOTHING`; `downgrade()` deletes those rows. No runtime seed on startup (races across replicas, hides writes in the boot path). Fine-grained `admin:*:*` permissions belong to ADM-002.
- **First super-admin (AC4, R5).** CLI `python -m app.cli grant-super-admin --email <email>` (+ `make` target), via `docker compose run --rm api …`; idempotent, calls `AssignmentService.assign` with `granted_by = NULL`; unknown email → non-zero exit with a generic message, full email not logged. No env-var bootstrap (a leftover value would keep re-granting).
- **Gate exposure (R3/R5, AC5).** Split routers: `catalog_router` (`GET /apps/{key}/roles`, always mounted, public read — "not a secret" per the owner) and `admin_router` (all staff CRUD + assignments), mounted only behind a new default-off `admin_rbac_api_enabled` flag with the same staging/production refusal validator. ADM-002 removes the flag and adds the catalog GET to its public allow-list.
- **Org FK (D15).** `organizations` already exists, so add `role_assignments.org_id → organizations ON DELETE CASCADE` **now**; adding it later means checking orphans on a populated table, against "expand now, tighten later". Changes ticket scope → needs owner sign-off.
- **ETag (AC7).** Service returns a canonical, sorted catalog; router sets ETag = sha256 of that canonical JSON, `Cache-Control: no-cache`, 304 on `If-None-Match` match (handle `W/`, comma lists, `*`). No version column.
- **Split the ticket (R7).** **USR-02a:** schema, migrations, seed, CLI, `authz` (`effective_*`, `permits`), member builder, catalog GET + ETag, tests — unblocks USR-03. **USR-02b:** flag-gated staff CRUD for apps, permissions, roles, role permissions, assignments and a user's roles, each noting its permission (`admin:apps:read|write`, `admin:roles:manage`) and an R4 audit placeholder.

## Risks

- Role grants made while endpoints are open would carry into the ADM-002 era — mitigated by the flag plus an ADM-002 pre-release check of `admin` app assignments.
- Tokens cached ~15 min → late revocation (accepted by ADR-0001).
- Auto-rebuilding the member role would silently widen access → rebuild only on explicit request.
- `apps.origins` overlaps the static `cors_origins` until USR-03 switches CORS to the DB; validate now (scheme + host[:port], no path, no wildcard).
- `effective_roles` does not check user status; USR-03 must refuse tokens for disabled users.

## Open questions

1. Org FK now (recommended) or keep deferring to USR-06 per the ticket?
2. Apps by `key` in every path (matches the catalog route), or UUIDs for staff CRUD (R9)?
3. Role permissions via `PUT …/roles/{role_key}/permissions` (replace set) or `PATCH` add/remove (R9 says PATCH for partial updates)?
4. Should a newly added non-admin permission join `<app>:member` automatically, or only on explicit rebuild?
5. Approve the USR-02a / USR-02b split?
