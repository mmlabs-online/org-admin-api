# USR-02d: Permissions and roles staff API (flag-gated)

| Field | Value |
|---|---|
| State | IN_PROGRESS |
| Priority | P1 |
| Areas | api, auth, security |
| Attempts | 0 |
| Created | 2026-11-11 |
| PR | — |

## Goal
Staff can manage an app's permissions and roles, including replacing a role's permission set, behind the `ADMIN_RBAC_API_ENABLED` gate, with the remaining escalation guards.

## Spec reference
§5.3; ADR-0001, ADR-0003; split from USR-02b (`work/tickets/USR-02b/plan.md` → "Split proposal" → slice 02b-2, which holds the contract)

## Acceptance criteria
- [ ] 8 routes: permissions list/create/patch/delete, roles create/patch/delete, `PUT /api/v1/apps/{key}/roles/{role_id}/permissions`; permissions `admin:apps:read` / `admin:roles:manage`, declared per route with a route→permission test
- [ ] Key prefix == app key (422); `<app>:*` forced `is_admin` (`false` → 422); `is_system` and `key` never accepted as input
- [ ] PUT dedupes, lists every unknown/other-app key in 422 details, shares diff logic with the member-role builder; idempotent
- [ ] Guards (409): delete `admin:*` or a permission held by any role; delete a system role, the app's default role, or a role with assignments; PUT leaving `admin:super-admin` without `admin:*`; PUT adding an `is_admin` permission to the default role; PATCH `is_admin=true` on a permission the default role holds
- [ ] Origins restricted per D19 (owner, 2026-11-11): only `https://mmlabs.online`, `https://*.mmlabs.online` and loopback (`http(s)://localhost`, `127.0.0.1`, `[::1]`); any other origin → 422 with no echo; update `app/modules/rbac/origins.py` and its tests (existing apps created by 02b with other origins: none exist outside local/test)
- [ ] Tests per the 02b-2 contract

## Notes / answers from owner
- 2026-11-11: created by the orchestrator from the USR-02b planner's split (slice 02b-2).
- Depends on USR-02b.
- Carry-over from the USR-02b reviews: (1) TOCTOU — role/permission edits must refuse adding an `is_admin` permission to any role that is some app's `default_role_id`, in the same transaction (lock the role row or check in the insert); (2) rename `AppService.list` (shadows builtin `list` for later annotations in the class); (3) wrap `_build_for_app`'s role_permissions insert/delete in a savepoint and map `IntegrityError` → 409; (4) optional: reject origins whose last label is numeric/hex (`https://0x7f.1`).
