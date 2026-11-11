# ADR-0003: RBAC keys, permission matching and catalog integrity

Status: Accepted (USR-02 plan, slice USR-02a, 2026-11-11)
Date: 2026-11-11

## Context
ADR-0001 §3–§4 defines namespaced permissions, app-scoped roles and the five RBAC tables;
identity-architecture.md §3 and D9 add `join_policy`, `org_mode`, `origins`, `default_role_id` and
`permissions.is_admin`. Several details were left open and must be identical everywhere a permission
is checked: this service (USR-03 token `roles`, ADM-002 `require_permission`) and the `mmlabs-auth`
library apps use (USR-03b).

## Decision
1. **Key grammar** (enforced by database CHECKs; the USR-02b endpoints add 422s and the
   "prefix == owning app" check, which a CHECK cannot express):
   - app key: `^[a-z][a-z0-9-]{1,31}$` (no `:`; immutable once created);
   - permission key: `<app>:<resource>:<action>` or `<app>:*`, each segment `[a-z][a-z0-9-]{0,47}`.
     Rejected: bare `*`, uppercase, `_`, extra segments, and `<app>:<resource>:*`. The ADM-002 ticket
     example `admin:apps:*` is therefore not allowed; use `admin:apps:read` / `admin:apps:write`;
   - role key: namespaced `<app>:<name>` (e.g. `stoktok:member`, `admin:super-admin`).
2. **Wildcard.** `<app>:*` is a stored permission row and must have `is_admin = true` (CHECK).
   It is returned unexpanded by `effective_permissions` and covers every `<app>:…` key, including keys
   registered later.
3. **One matcher**, `permits(granted, required)`: true when `required` is in `granted` or
   `<app>:*` of the same app is. No global `*`, no `<app>:<resource>:*`, no deny rules. A required key
   without an `<app>:` prefix never matches. `mmlabs-auth` copies it exactly.
4. **Same-app integrity in the database.** `roles` and `permissions` have `UNIQUE (id, app_id)`;
   `role_permissions` carries `app_id` with composite FKs to both, and
   `apps (default_role_id, id) → roles (id, app_id)`, so a role can neither hold another app's
   permission nor be another app's default role. `effective_permissions` also drops keys whose prefix
   is not the requested app (second check).
5. **Assignments.** `UNIQUE NULLS NOT DISTINCT (user_id, role_id, org_id)`: `org_id NULL` = app scope
   and is unique per user and role. Expiry is compared with the database `now()`.
6. **`effective_*` is RBAC only.** It does not check user status, email verification, or app
   status/join policy; callers check those and return their own errors.
7. **Default role is data, rebuilt only when asked.** `build_default_role(app)` makes `<app>:member`
   hold exactly the app's non-`is_admin` permissions, sets `default_role_id` only when it is NULL, is
   idempotent and returns the diff. It never runs automatically on permission changes, so a staff
   tightening is not silently undone.
8. **The `admin` app has no member role.** It is `closed`; `default_role_id` stays NULL and roles
   come only from grants. Seed: `admin`, `admin:*` (`is_admin`), `admin:super-admin` (`is_system`).

## Consequences
- USR-03 uses `effective_roles` for the token `roles` claim; ADM-002 uses `permits`.
- Widening the grammar later (e.g. allowing `_`) is additive; narrowing it is not.
- A new permission reaches `<app>:member` only after an explicit rebuild (USR-02b app creation,
  USR-05 manifest) or a staff edit.
- The `org_id → organizations` FK is added in USR-06.
