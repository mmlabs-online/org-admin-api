# ADR-0029: Bulk role assignment

Status: Accepted (2026-11-26)
Date: 2026-11-26

Decision-log row D41 (`docs/identity-architecture.md`). Spec §5.15 Part 3, OQ70–OQ73 (owner, 2026-11-26).

## Context
admin-pan (PAN-033) assigns one role to many users. A client loop of single
`POST /api/v1/users/{user_id}/role-assignments` calls takes the app lock once per user, gives no
per-user outcome in one place and pushes assignment rules into the browser. The owner asked for
one server-side endpoint that reuses the single-assignment logic, at most 100 users, refuses
`admin`-app roles, and treats an already-held role as `unchanged`.

## Decision
1. **Route.** `POST /api/v1/apps/{key}/roles/{role_id}/assignment-batches` — the role write path
   (`roles`, not the read-only `role-definitions`), no custom-method suffix; a batch is a resource
   identified by `batch_id`. R3 class 1 (Bearer), `admin:roles:assign`. Body
   `{user_ids (1..100, unique), org_id?, expires_at?}`.
2. **Response.** Always **200** `{batch_id, results: [{user_id, status, assignment_id?, expires_at?,
   error?}]}`, one result per user in request order, even if every user failed. This is an R9
   exception (`CLAUDE.md` R9; 200 instead of 201 for a POST that creates rows, R6).
3. **Request-level vs per-user errors.** Request-level, nothing written: 401; 403
   `permission_denied`; 403 `admin_wildcard_required` (role holds an `is_admin` permission, caller
   lacks `admin:*`); 404 unknown app, unknown role or a role of another app; 422 bad body,
   `admin_app_role` (any `admin`-app role), `super_admin_role` (the app's designated super-admin
   role, ADR-0030; USR-36 implements it), and the shared-input rules `org_id_not_allowed`,
   `unknown_org`, `expires_in_past`. Per user: `created` (new, or an expired duplicate re-activated
   with its old id), `unchanged` (active holder, even with a different `expires_at`; nothing
   changed, existing id and expiry returned), `failed` (`not_found`, `self_assignment_forbidden` (the caller's own privileged role, ADR-0008, USR-25)
   and any other per-user rule of the single POST). Target state as the single POST (disabled
   users, unverified email, disabled apps accepted).
4. **One transaction, one app lock, one savepoint per user.** The batch takes `lock_app` once and
   holds it until commit; each user runs in its own savepoint, so a per-user failure undoes only
   that user. User ids are processed in sorted order (deterministic row-lock order); lock order is
   app → org → rows as in ADR-0003 §10, so no deadlock with single assigns, which wait for at most
   one batch. An unexpected error rolls back the whole batch.
5. **`batch_id` = `request_id`.** The server-generated request id already on every audit row; no
   schema change.
6. **Audit identical to the single POST.** One `role_assignment.granted` row per created assignment
   in the same transaction; `denied` rows exactly where the single POST writes them; nothing for
   `unchanged`, `not_found` or 422s. The grant rules are reused from `AssignmentService`, not
   copied. Open for the plan: `record_denied` / `record_failure` roll back and commit alone, so
   they cannot be called mid-batch as they are.

## Consequences
- Precedent for future bulk endpoints: a plural-noun `…-batches` sub-resource, 200 with per-item
  results, request-level refusals for anything that does not depend on the item, one lock and one
  transaction with per-item savepoints, `batch_id` = `request_id`.
- Filtering the audit log by `batch_id` needs a `request_id` filter and index (spec §8 OQ79).
- Bulk revoke is later (spec §5.7) and would follow this ADR.
