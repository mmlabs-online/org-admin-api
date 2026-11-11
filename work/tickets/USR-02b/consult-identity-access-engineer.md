# consult-identity-access-engineer — USR-02b

## Findings
1. **`permits()`** (`app/modules/rbac/authz.py:11`) is typed `Collection[str]`, which a `str` satisfies; with a string, `required in granted` is substring matching → `permits("a:x:read-all", "a:x:read")` is True. ADR-0003 §3 tells `mmlabs-auth` to copy it exactly, so the bug would spread to every app.
2. **`effective_roles`** returns repository rows unfiltered; `effective_permissions` drops keys whose prefix ≠ the app key, but nothing does that for roles — a `stoktok` role keyed `admin:x` would end up in the token `roles` claim (USR-03).
3. **`build_default_role`:** (a) doesn't refuse `admin`, though ADR-0003 §8 says the admin app has no member role; (b) two concurrent builds both pass `get_role_by_key`, and the second hits `uq_roles_app_id_key` or the `role_permissions` PK with an unmapped `IntegrityError`; (c) `if app.default_role_id is None` reads a previously loaded value and writes it back — a concurrent `PATCH default_role_id` can be overwritten (lost update).
4. **Escalation paths through data (beyond the carry-overs):** `PATCH /apps/admin` with `join_policy=open` + `default_role_id=<admin:super-admin>` would hand super-admin to anyone who joins; `PATCH status=disabled` on `admin` locks out all staff once USR-03/ADM-002 check app status; on any app, a default role holding `is_admin` permissions turns joining into an admin grant.

## Recommendations
- **permits (carry-over 1, ADR-0003 §3):** signature `granted: AbstractSet[str] | Sequence[str]`, first line `if isinstance(granted, str): raise TypeError(...)`. Add `test_permits` cases `({"adm:*"}, "admin:x:y")` → False, `({"admin:*"}, "adm:x:y")` → False, `({"a:*"}, "a:")` → False, `({"a:*"}, ":x:y")` → False, `({"A:*"}, "a:x:y")` → False, and `str` granted → TypeError.
- **effective_roles (carry-over 2):** the same `startswith(f"{app_key}:")` filter as `effective_permissions`; test `test_effective_roles_drop_foreign_prefix` (absent for both `stoktok` and `admin`).
- **build_default_role (carry-over 3; AC "IntegrityError races → 409"):** a module constant `ADMIN_APP_KEY = "admin"` checked right after the lookup → `ConflictError`. Insert the role in `session.begin_nested()` (the `users/repository.add` pattern), mapping `IntegrityError` → `ConflictError`. Make `role_permissions` inserts idempotent with `pg_insert(...).on_conflict_do_nothing()`. Set the default with a conditional `UPDATE apps SET default_role_id=:r WHERE id=:a AND default_role_id IS NULL` in the repository. Split into a public method (looks up the app) and an internal `_build_for_app(app)` so app creation doesn't re-query.
- **App create + build in one transaction:** yes, same request session. Insert the app in a savepoint (dup → 409), flush, call `_build_for_app`; if the build fails the app rolls back too, so no app is left without `<app>:member`. On create the member role is empty (expected).
- **Prefix == app key (AC validation, 422):** in the service (the app key comes from the path): `key.split(":", 1)[0] != app.key` → `UnprocessableError(details={"key": ...})` on POST permission and POST role. DB-level `app_key` column + composite FK as a follow-up (R7: schema change).
- **Immutability:** `extra="forbid"` on every PATCH schema; no PATCH accepts `key` (apps, permissions, roles) or `is_system`. Test `{"key": …}` → 422 for each.
- **`<app>:*` rule:** POST with `is_admin` absent → forced True; `is_admin=false` → 422. PATCH `is_admin=false` on a `*` key → 422.
- **System rows (409):** DELETE app `admin`; DELETE permission `admin:*`; DELETE any `is_system` role (covers `admin:super-admin` and every `<app>:member`); DELETE an app's default role; PUT on `admin:super-admin` whose set lacks `admin:*`; for `admin`: PATCH changing `join_policy` away from `closed`, setting a non-NULL `default_role_id`, or setting `status` to anything but `active`.
- **Default role holding admin permissions:** refuse PATCH `default_role_id` to a role that holds any `is_admin` permission (422); refuse a PUT adding an `is_admin` permission to a role that is its app's default role (409).
- **ADR-0003 update:** §1 prefix enforced in the service (422) and app/permission/role keys immutable; §3 `str` guard + edge-case table; §4 `effective_roles` also drops foreign prefixes; §7 conditional `default_role_id` set, races → 409; §8 builder refuses `admin` + the protected admin fields; new: the system-row list, "super-admin always holds `admin:*`", "a default role never holds `is_admin` permissions".

## Risks
- Until ADM-002, every endpoint is unauthenticated — the flag must stay refused in staging/production; a test should pin it.
- Prefix check and protected rows live in the service only: direct SQL, or a USR-05 manifest sync that bypasses the service, gets around them. USR-05 must call the same service.
- The `http://localhost` origins exception becomes credentialed CORS (R11) once USR-03 uses `apps.origins` in production.
- A data-only rule like "join_policy closed" is not a security boundary without the PATCH guards.

## Open questions
1. Q1: if the catalog goes public, should `GET /apps/admin/roles` return 404 so admin role names aren't listed?
2. Refuse `http` localhost origins once `APP_ENV=production`?
3. Default-role "no `is_admin` permissions" guards in 02b, or as actor-based guards in ADM-002 (requiring `admin:*`)? Recommend 02b: cheap and actor-independent.
