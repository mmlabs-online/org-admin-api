# Consult — database-optimizer — USR-36 (2026-11-26)

**Findings**
- `lock_app` (`rbac/repository.py:131-143`) `FOR NO KEY UPDATE` on the app row; every assignment writer takes it first — one lock per batch serializes against single assigns.
- `_grant_locked` (`rbac/service.py:1664-1731`): shared rules 1675-1695 (`org_id_not_allowed`, `unknown_org`, `expiry_not_allowed`, `expires_in_past`); per-user: `_guard_privileged` (1696), designated recipient check (1698), duplicates (1706-1731; active duplicate → `ConflictError` 1709 — batch needs `unchanged`, split the upsert). `require_super` (1746) is request-level for a batch; self-assignment (1748, 1750) per user.
- `add_assignment` (`rbac/repository.py:824-829`) already uses `begin_nested`; `find_assignment` (789) uses `IS NOT DISTINCT FROM`; verify `models.py:226` unique covers NULL `org_id` (NULLS NOT DISTINCT).
- `AuditLogger.record_failure` (`audit/service.py:156-196`) rollback + commit — mid-batch it would wipe earlier users and release the lock. `record_in_savepoint` (224) is the nearest pattern but records SUCCESS without `org_id`.
- `DENIED_ERRORS` (`core/errors.py:102`) includes `SelfAssignmentForbiddenError` — the only per-user denied path.
- Precedent `SET LOCAL lock_timeout` (`usage/meter_repository.py:79`).

**Recommendations**
1. No batches table, no migration; `batch_id = ctx.request_id` (ADR-0029 §5); audit addition in `audit/service.py`, not `core`.
2. Split `_grant_locked`: `_check_request_inputs(app, role, org_id, expires_at)` once under `lock_app`; `_upsert_locked(...)` → `CREATED | RENEWED | UNCHANGED`; `_grant_locked` wrapper keeps behaviour (UNCHANGED → `ConflictError`); other callers untouched.
3. 404 / admin-app checks before locking; `lock_app` once; request-level checks (`admin_app_role`, `super_admin_role`, `require_super`, shared inputs); `db_now()` once per batch.
4. Preload: existing users (`id = ANY(:ids)`), existing assignments (`role_id=:r AND org_id IS NOT DISTINCT FROM :o AND user_id = ANY(:ids)`), `is_active_staff_super_admin` batched; cache `role_has_admin_permission` once.
5. Loop over sorted ids, one `begin_nested()` per user around upsert + audit; `AppError` → `failed` (only that user rolled back); anything else → 5xx, whole transaction rolls back; `add_assignment`'s own savepoint is enough nesting.
6. No `ON CONFLICT`: preload + app lock make inserts race-free; `IntegrityError` → per-user `failed`/`conflict`; request uniqueness is a Pydantic 422.
7. `AuditLogger.record_denied_in_batch(...)`: same DENIED outcome, `changes={"reason", "role_key"}`, target and `org_id` as the single POST; add + flush in the outer transaction after the user's savepoint rolled back; `record_denied` unchanged; keep the `AdminPrincipal` guard.
8. `SET LOCAL lock_timeout = '5s'` before `lock_app`.
9. Query count ≈ 8 fixed + 2–3 per created user.

**Risks**
- If the unique constraint doesn't cover NULL `org_id`, the app lock is the only guard — verify `models.py:226`.
- App lock held ~100–300 ms for 100 users (estimate) — capped by the timeout.
- Identity-map staleness — preload with `populate_existing`.
- Two denied-audit paths — document the contract.

**Open questions for Ming (defaults)**
1. Lock timeout → 409, nothing written (vs 503).
2. Renewed user writes `granted` and the result is `created` — yes.
3. `lock_timeout` 5s.
