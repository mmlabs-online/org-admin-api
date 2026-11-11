# USR-02b: Apps registry staff API (flag-gated) and role catalog

| Field | Value |
|---|---|
| State | DONE |
| Priority | P1 |
| Areas | api, auth, security |
| Attempts | 1 |
| Created | 2026-11-11 |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/11 |

## Goal
Staff can manage apps, their permissions and roles, and apps can read their role catalog with an ETag — without exposing privilege-changing endpoints before ADM-002.

## Spec reference
§5.3; ADR-0001; identity-architecture.md §4; split from USR-02 (`work/tickets/USR-02/plan.md` → "Split proposal" → "USR-02b", which holds the full endpoint contract)

## Acceptance criteria
- [ ] New default-off `ADMIN_RBAC_API_ENABLED` (separate from `ADMIN_USERS_API_ENABLED`), refused in staging/production; with any admin flag on, startup also requires `APP_ENV` set explicitly; router mounted only when the flag is on (default app → 404 on every new path)
- [ ] `UnprocessableError` (422) in `app/core/errors.py`; `IntegrityError` races → 409
- [ ] Endpoints exactly as in the USR-02 plan's USR-02b table: apps CRUD by `key` (immutable), permissions and roles by UUID, `PUT …/roles/{role_id}/permissions` replaces the set; `build_default_role` on app create
- [ ] Validation: key formats and app-prefix match (422), `<app>:*` forced `is_admin`, `is_system` never accepted as input, origins normalized and restricted (https; http only for localhost); `admin` app, `admin:*`, `admin:super-admin` not deletable/re-keyable (409)
- [ ] Catalog `GET /api/v1/apps/{key}/roles`: canonical sorted document, strong ETag, `If-None-Match` → 304, 404 for unknown/disabled app; flag-gated until the owner answers Q1
- [ ] Per-route permission declarations (`admin:apps:read|write`, `admin:roles:manage`) with a route→permission test; `# R4` markers on mutations; services take an unused `actor` parameter
- [ ] Tests listed in the USR-02 plan for 02b

## Notes / answers from owner
- 2026-11-11: split by the planner — this ticket is slice 02b-1 (flag + APP_ENV rule + UnprocessableError + USR-02 carry-over fixes + apps CRUD + origins + admin-app locks). Permissions/roles CRUD → **USR-02d**; catalog → **USR-02e**.
- 2026-11-11: created by the orchestrator from the USR-02 planner's split.
- Open owner questions (defaults in place, non-blocking): **Q1** may the catalog be public (add to R3 class 3)? default: flag-gated. **Q2** accept an R9 exception for `{key}` in `/api/v1/apps/{key}/…` and a non-`Page` catalog document? **Q3** restrict origins to `*.mmlabs.online`? default: any https.
- Depends on USR-02.
- Carry-over from the USR-02 security review (🟡, fix in this ticket): (1) `permits()` must reject a `str` as `granted` (TypeError) — otherwise `in` does substring matching; add the edge-case tests listed there; (2) `effective_roles` must drop role keys whose prefix ≠ the app key, as `effective_permissions` does; (3) `build_default_role` must refuse the `admin` app (409). Also enforce key prefix == app key on create (422), and map `IntegrityError` races in `build_default_role` to 409.
