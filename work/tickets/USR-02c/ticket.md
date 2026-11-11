# USR-02c: Role assignments staff API (flag-gated) and bootstrap super-admin CLI

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P1 |
| Areas | api, auth, security |
| Attempts | 0 |
| Created | 2026-11-11 |
| PR | — |

## Goal
Staff can assign, revoke and list a user's roles per app (optionally per org), and the first super-admin can be granted from the command line on the server — never over HTTP.

## Spec reference
§5.3; ADR-0001; identity-architecture.md §4; split from USR-02 (`work/tickets/USR-02/plan.md` → "Split proposal" → "USR-02c", which holds the full contract)

## Acceptance criteria
- [ ] Endpoints behind `ADMIN_RBAC_API_ENABLED`: `GET/POST /api/v1/users/{user_id}/role-assignments`, `DELETE /api/v1/users/{user_id}/role-assignments/{assignment_id}` with the codes and permissions in the USR-02 plan (`admin:users:read`, `admin:roles:assign`)
- [ ] 422 for unknown role, past or naive `expires_at`, `org_id` on an `org_mode = none` app, unknown `org_id`; 409 for an active duplicate; an expired duplicate is replaced in place
- [ ] Last-super-admin guard: revoking the last active, non-expired `admin:super-admin` held by an active user → 409
- [ ] CLI `python -m app.cli grant-super-admin --email <email>` (+ make target, runbook entry in `docs/deploy.md`): app scope, idempotent, exit codes 0/2/3, masked email only in output; no env-var bootstrap, no "mark verified" path (D16)
- [ ] Tests listed in the USR-02 plan for 02c

## Notes / answers from owner
- 2026-11-11: created by the orchestrator from the USR-02 planner's split.
- Depends on USR-02 and USR-02b (shared flag).
