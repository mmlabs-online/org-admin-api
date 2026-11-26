# Consult — identity-access-engineer — USR-36 (2026-11-26)

**Findings**
1. **Ticket vs ADR-0029:** ADR-0029 §3 and ADR-0030 §4 refuse the designated super-admin role at request level (422 `super_admin_role`, "USR-36 implements it"); the ticket Scope (`work/tickets/USR-36/ticket.md:28`) and AC (`:39`) omit it.
2. Self-assignment checked in `_guard_privileged` (`rbac/service.py:1746-1750`), only for admin-app and designated roles — both refused at request level, so a per-user `self_assignment_forbidden` can't happen until USR-25 (option (b), ticket `:28`).
3. `_grant_locked` order (`service.py:1675-1717`): org/expiry 422s → `_guard_privileged` (403) → recipient check → existing-row check (`ConflictError` for an active holder); a self-grant of a held role gets 403, not 409.
4. `expiry_not_allowed` (`:1688`) only for `admin:super-admin` — unreachable in the batch.
5. `db_now()` = Postgres `now()` (transaction start, `rbac/repository.py:593`) — one value per batch.
6. Single POST writes one denied row per refused user (`service.py:1600-1611`); `record_denied` rolls back and commits.
7. G5 (ADR-0008:90-91) covers status changes, not grants; `user_has_privileged_assignment` (`repository.py:802`) covers recipients after commit — no recipient-state check needed (ticket `:30`).
8. Single POST permission `admin:roles:assign` (`assignments_router.py:49-50`).

**Recommendations**
1. `require_permission("admin:roles:assign")`; add the route to `tests/test_route_auth_classes.py` as class 1 (Bearer).
2. Request-level checks in ADR-0008 order (`:103`): lock app → 404 (app, role, role of another app) → body 422s (1..100, unique, aware `expires_at`) → `admin_app_role`, `super_admin_role` (add to AC), `org_id_not_allowed`, `unknown_org`, `expires_in_past` → 403 `admin_wildcard_required` if privileged and caller lacks `admin:*`. Each once, before any savepoint.
3. Split `_grant_locked`: per-request (org, expiry, `require_super` when privileged) and per-user (self check, recipient check, existing-row check). Single POST calls both. Self check **before** existing-row check, or a held self-target shows `unchanged` (leaks state, differs from single POST).
4. `unchanged` only for "already active"; an `IntegrityError` in a savepoint (user deleted meanwhile) → `failed not_found`.
5. Mid-batch denied rows after the savepoint rollback, in the outer transaction, same content: `record_denied(..., commit=False)`; existing callers unchanged.
6. Self in a batch allowed for non-privileged roles (USR-25 (b)); test `failed self_assignment_forbidden` for an `is_admin` role once USR-25 lands.
7. Different `expires_at` on an active holder neither extends nor shortens (`unchanged`, existing expiry returned); an expired duplicate takes the request's `expires_at` (incl. `null`).
8. `batch_id` = server-generated `request_id`, never client-supplied.

**Risks**
- 5xx rolls back mid-batch denied rows — accept, document.
- App lock held across up to 100 savepoints — `SET LOCAL lock_timeout` + `= ANY(:ids)` preload.
- No audit row for a request-level `admin_wildcard_required` would diverge from R4.

**Open questions for Ming (defaults)**
1. Request-level 403 `admin_wildcard_required` audit: one `denied` row targeting the role with `{role_key, user_count}` via `record_denied`.
2. Add `super_admin_role` to Scope/AC (ADR wins) — yes.
3. Wait for USR-25 — no; per-user self check through `_guard_privileged` picks it up automatically.
