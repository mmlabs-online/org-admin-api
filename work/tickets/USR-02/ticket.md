# USR-02: Apps registry and per-app RBAC (join policy, org mode, origins, default role)

| Field | Value |
|---|---|
| State | IN_PROGRESS |
| Priority | P1 |
| Areas | architecture, db, api, auth, security |
| Attempts | 0 |
| Created | 2026-11-11 |
| PR | — |

## Goal
One authorization model for every mmlabs app: registered apps with join policy, org mode and allowed origins; namespaced permissions (some flagged `is_admin`); app-scoped roles including a default `<app>:member` role; role assignments per user (app scope or one org).

## Scope
Tables `apps` (+ `join_policy`, `default_role_id`, `org_mode`, `origins`), `permissions` (+ `is_admin`), `roles`, `role_permissions`, `role_assignments`; staff endpoints; `effective_roles/permissions` service; read-only role catalog; seed data. Out of scope: join endpoint (USR-03b), org tables (USR-06 — `role_assignments.org_id` is created nullable now, FK added in USR-06), app self-registration (USR-05).

## Spec reference
§5.3 (incl. Membership), §5.5 (merged); ADR-0001 §1–§4, §6; identity-architecture.md §3; decisions D8, D9, D10

## Acceptance criteria
- [ ] Tables + migration per identity-architecture.md §3; `join_policy` ∈ open|approval|closed (default open), `org_mode` ∈ none|required (default none), `origins` text[], `permissions.is_admin` default false; `role_assignments.org_id` nullable, NULL = app scope (never 0); unique constraints as in ADR-0001; downgrade works
- [ ] A role holds only permissions of its own app (422 otherwise); permission keys match `<app>:<resource>:<action>` or `<app>:*`; `default_role_id` must be a role of the same app
- [ ] Default role rule as data: a service/seed helper builds `<app>:member` = all the app's permissions with `is_admin = false`; changing it later is a data change only
- [ ] Seed: app `admin` (`join_policy = closed`, role `admin:super-admin` with `admin:*`), and a config/CLI way to grant super-admin to a first user without editing the DB by hand
- [ ] Staff endpoints: CRUD apps (incl. join_policy, org_mode, origins); permissions and roles of an app; set role permissions; assign/revoke a role (app scope or org); list a user's roles per app — permissions noted for ADM-002 (R3)
- [ ] `effective_roles(user_id, app_key, org_id=None)` → app-scope roles + roles in that org; expired assignments ignored; wildcard resolution in `effective_permissions`
- [ ] `GET /api/v1/apps/{key}/roles` returns role → permissions with `ETag`; `If-None-Match` → 304
- [ ] Tests: cross-app permission rejected, duplicate keys 409, member-role builder excludes `is_admin`, app vs org scope, expired assignment ignored, wildcard, ETag/304, 404s

## Dependencies
USR-01.

## Notes / answers from owner
- 2026-11-11: split by the planner into three slices — this ticket is the RBAC core (USR-02a in plan.md: schema, seed, authz, member-role builder; no HTTP routes). Staff API + catalog → **USR-02b**; assignments + bootstrap CLI → **USR-02c**.
- 2026-11-11: merges former ADM-003 and ADM-005. Join policy defaults per D9 (open; admin closed).
- `GET /apps/{key}/roles` auth (public read vs app credential): decide in the plan or in USR-05; not a secret by design.
