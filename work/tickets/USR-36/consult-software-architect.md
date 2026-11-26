# Consult — software-architect — USR-36 (2026-11-26)

**Findings**
- `_grant_locked` (`rbac/service.py:1664-1731`): request-wide — designated + `org_id` (1676), `org_mode` / `unknown_org` with an org KEY SHARE lock (1680-1686), `expiry_not_allowed` / `expires_in_past` (1687-1695), `require_super` in `_guard_privileged` (1746); per user — self-assignment (1747-1750), `recipient_not_staff_super_admin` (1698), duplicate/renew/insert upsert (1706-1731).
- Active duplicate → `ConflictError` (1709); the batch needs `unchanged` as a value.
- With request-level refusals (`admin_app_role`, `super_admin_role`, `admin_wildcard_required`) no per-user `DENIED_ERRORS` path remains until USR-25.
- `record_failure` rolls back + commits alone (audit/service.py:185-187); `record_in_savepoint` (224-249) is the pattern to copy.
- Single POST writes a `denied` row for `admin_wildcard_required` (service.py:1600-1611, target USER); the ticket says request-level errors write "nothing" — conflict (Q1).
- Locks: every assign path `lock_app` first (repository.py:131); `set_status` locks admin app → user `FOR UPDATE` (users/service.py:91-96); the batch never locks `admin`; inserts take FK KEY SHARE on users in sorted order — no cycle.
- `db_now()` = transaction start, constant per batch.

**Recommendations**
1. Split inside `AssignmentService` (no new module): `_check_grant_request(app, role, org_id, expires_at, actor)` (request checks in today's order, designated-org before org lookup, `require_super` first); `_grant_user_locked(...) -> (ra, GrantOutcome.CREATED|RENEWED|UNCHANGED)` (self, recipient, upsert); `_grant_locked` = both, `UNCHANGED` → `ConflictError` (with USR-37's `assignment_exists`); `bootstrap_super_admin` may use `UNCHANGED` (optional, R7); split `_guard_privileged` into role part and self part (`revoke` keeps the combined one).
2. Flow: resolve role → `lock_app` once → re-read role → 404s → `admin_app_role`, `super_admin_role` → `_check_grant_request` once → existing users via one `= ANY(:ids)` → sorted `user_id`: `begin_nested()` per user, `_grant_user_locked` + `_record_grant` → results in request order; `role_has_admin_permission` once.
3. Per-user errors: `AppError` (incl. `ConflictError` from an `IntegrityError` in the savepoint) → `failed {code, message}` with USR-37 reasons; missing user → `not_found`; anything else → 5xx, whole rollback.
4. `AuditLogger.record_denied_in_tx` for the later USR-25 case (same `_build` content, inside the savepoint, committed with the batch); `record_failure` unchanged; keeps size M.
5. Do not preload existing assignments (cascade deletes without the app lock can make it stale): per-user `find_assignment` under the lock (indexed, ≤ 100). `SET LOCAL lock_timeout` (5 s) after `lock_app`; timeout → 503 or 409 (Q3).
6. `batch_id` = `ctx.request_id` cast to UUID; assert not None.
7. Concurrency test: batch + single assign on the same user and role → exactly one `created` or one 409, no deadlock, one `granted` row.

**Risks**
- 100-user batch holds the app lock ~500 round trips (~100–300 ms), blocking the app's RBAC, join, invite writers — acceptable at ≤ 100; note in OpenAPI.
- Denied rows written in the transaction lost on 5xx (differs from single POST).
- The split touches the single POST path (High); USR-37 lands first — rebase onto its reasons.
- Pre-existing race: `set_status`'s G5 check doesn't take the target app lock — follow-up.

**Open questions for Ming (defaults)**
1. Request-level 403 `admin_wildcard_required`: one `denied` row per requested user (same content as single POST) in a separate commit, nothing else.
2. Expired duplicate re-activated updates `granted_by` and `expires_at` — yes, identical to single POST.
3. Lock timeout → 409 `conflict` reason `changed_concurrently`, nothing written.
4. Wait for USR-25 — no; build `record_denied_in_tx` now, test with a stubbed per-user denial.
