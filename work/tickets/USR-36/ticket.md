# USR-36: Bulk-assign one role to up to 100 users (`assignment-batches`)

| Field | Value |
|---|---|
| State | PLANNING |
| Priority | P2 |
| Size | M |
| Areas | architecture, api, auth, security, db |
| Gate | plan-approval |
| Depends on | — |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-26 |
| Claimed by | claude@2026-11-26T08:06Z |
| PR | — |

## Goal
Staff grant one role to many users in a single server-side request with per-user results, applying exactly the single-assignment rules. Requested by admin-pan for PAN-033 (bulk assign).

## Scope reference
- `docs/spec-v1.md` §5.15 Part 3 (owner, 2026-11-25; OQ70–OQ73, OQ78 answered 2026-11-26).
- ADR-0029 (bulk role assignment), decision-log D41, `CLAUDE.md` R9 / R6 exception.
- Owner: "The API owns all business rules; reuse the existing single-assignment logic, do not duplicate it."

## Scope
- `POST /api/v1/apps/{key}/roles/{role_id}/assignment-batches` (R3 class 1 Bearer, `admin:roles:assign`). Body `{user_ids: [uuid] (1..100, unique), org_id?, expires_at?}`.
- Response always 200: `{batch_id, results: [{user_id, status: created | unchanged | failed, assignment_id?, expires_at?, error?: {code, message}}]}`, one result per user, request order. `created`/`unchanged` carry `assignment_id` and `expires_at`.
- Request-level errors (nothing written): 401; 403 `permission_denied`; 403 `admin_wildcard_required`; 404 unknown app / role / role of another app; 422 bad body, `admin_app_role` (any `admin`-app role), and shared-input rules `org_id_not_allowed`, `unknown_org`, `expires_in_past`.
- Per-user: `created` (new, or expired duplicate re-activated with its old id); `unchanged` (already active, even with a different `expires_at`; not touched); `failed` (`not_found`, and `self_assignment_forbidden` once USR-25 lands; any other per-user rule of the single POST).
- Target state as the single POST: disabled users, unverified email, disabled apps accepted.
- One transaction, one `lock_app` for the batch, one savepoint per user, user ids processed sorted; unexpected error rolls back all (5xx).
- Audit identical to the single POST per user; `batch_id` = `request_id`.

## Out of scope
- Bulk revoke (§5.7); tightening target-state rules (OQ78, separate ticket for both endpoints); audit filter by `batch_id` (OQ79); multi-role or multi-app batches.

## Acceptance criteria
- [ ] Happy path and mixed created / unchanged / failed results, in request order, with `batch_id` equal to the audit rows' `request_id`; idempotent retry → all `unchanged` with ids and expiry; expired duplicate → `created` with its old id; all-failed → 200.
- [ ] Request-level refusals as listed (404s, 422 body and `admin_app_role` incl. `admin:super-admin`, request-level 422 `org_id_not_allowed` / `unknown_org` / `expires_in_past`, 403 `permission_denied` and `admin_wildcard_required`, 401), each writing nothing.
- [ ] The single POST keeps its current behaviour and the batch reuses its grant logic (no duplicated rule code); existing single-assign tests stay green.
- [ ] One `role_assignment.granted` row per created assignment, none for `unchanged`; `denied` rows exactly when the single POST writes them, without rolling back the batch's other rows.
- [ ] A concurrent single assign on the same app neither deadlocks nor is lost (concurrency test).
- [ ] Route registered in `tests/test_route_auth_classes.py`; OpenAPI documents the transaction/lock behaviour and all codes.

## Technical notes
- Files: `app/modules/rbac/service.py` (split `_grant_locked` into a per-request check and a per-user upsert returning created/renewed/unchanged, per architect consult), rbac router/schemas, `app/modules/audit/service.py` if needed, route registry test.
- **Constraint:** `AuditLogger.record_denied` / `record_failure` roll back the whole transaction and commit alone; the plan must solve mid-batch `denied` rows without changing their content.
- Load existing users and assignments with `= ANY(:ids)` up front to keep the lock short; consider `SET LOCAL lock_timeout`.
- Soft order: let USR-25 land first so the batch inherits the wider self-assignment ban (not a hard dependency).
- Size: M; if PLAN finds the audit change pushes it past M, propose a split (e.g. audit refactor first).

## Test plan
- API tests per criterion against the Postgres test DB, including a concurrency test with a parallel single assign.

## Notes / answers from Ming

## Handoff
