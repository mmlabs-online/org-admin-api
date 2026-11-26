# Consult — api-platform-engineer — USR-36 (2026-11-26)

**Findings**
- Single POST grant logic `_grant_locked` (`app/modules/rbac/service.py:1664-1731`) mixes request-level checks (org_id / org_mode 1675-1686, expires_at 1687-1695, `_guard_privileged` 1696) with per-user work (staff check 1698, find, upsert 1706-1731).
- Already-active holder → `ConflictError("Role is already assigned")` (1709); racing insert → same (1730). A batch needs a typed outcome: split an upsert returning created/renewed/unchanged; single POST raises 409 on `unchanged`.
- `_guard_privileged` (1733-1750) → `require_super` (403 `admin_wildcard_required`): request-level for a batch. `SelfAssignmentForbiddenError` is per user.
- `DENIED_ERRORS` audited via `record_denied` (1600-1611), which rolls back the whole transaction and commits alone — cannot run mid-batch.
- Single POST 404 = user not found (1587) → per-user `failed/not_found`.
- Schema `AssignmentCreate` (schemas.py:297-305, `extra=forbid`); router assignments_router.py:45-67; `requires_super="conditional"` with `super_when=_PRIVILEGED`.
- **Ticket vs ADR conflict:** ADR-0029 §3 (amended by USR-38) lists request-level 422 `super_admin_role`; the ticket Scope/AC omit it. Without it, per-user `recipient_not_staff_super_admin` would be the only guard.
- USR-37 adds `details[].reason` to the single POST's 409s (`assignment_exists`, concurrent change) and corrects documented 403/404/422; USR-36 should not depend on it.

**Recommendations**
1. `AssignmentBatchCreate`: `extra=forbid`, `user_ids: list[UUID]` 1–100, `org_id?`, `expires_at?: AwareDatetime`; duplicates → 422 `details[{field:"user_ids", reason:"duplicate_user_ids"}]` (no silent dedupe); too many → `too_many_users` reason.
2. `BatchItemStatus` StrEnum (created, unchanged, failed); `AssignmentBatchItem{user_id, status, assignment_id?, expires_at?, error?: {code, message, details?}}`; `AssignmentBatchResult{batch_id, results}`; `status_code=200` (R9 exception); add to `tests/test_route_auth_classes.py`.
3. Per-item `error.code` = the single-POST envelope code (`not_found`, `self_assignment_forbidden`, …) with `details[0].reason` inside — same vocabulary, independent of USR-37.
4. Hoist request-level checks into `_check_grant_request(app, role, org_id, expires_at, actor)` (org rules, `expires_in_past`, `require_super`), used by the single POST too; batch-only 422 `admin_app_role` and `super_admin_role` (`details=[{field:"role_id", reason}]`).
5. `_grant_locked` = thin wrapper over the per-user upsert, 409 on `unchanged` (409 text unchanged — USR-37's job).
6. Mid-batch denied audit: non-committing `record_denied_in_tx` (flush-only) written after releasing the user's savepoint, identical content; split into its own ticket if large.
7. Idempotency via natural key (`unchanged` on retry); no `Idempotency-Key` (ADR-0029); return existing `assignment_id`, `expires_at`.
8. OpenAPI: describe the transaction model (single transaction, one `lock_app`, savepoint per user, sorted ids, 5xx rolls back all) and every request-level code.

**Risks**
- Results in request order while processing sorted — map by `user_id`.
- Denied-row vs savepoint ordering is the riskiest part.
- Ticket vs ADR on `super_admin_role` — ADR wins, or the batch could bulk-grant the designated role.
- `db_now()` per user — fetch once.
- `lock_app` held across 100 users — `SET LOCAL lock_timeout`.

**Open questions for Ming (defaults)**
1. Request-level 422 `super_admin_role` in the AC — yes (ADR wins).
2. Duplicate `user_ids` → 422 `duplicate_user_ids`.
3. Self-assignment denied rows per user via a non-rolling-back audit path (ADR §6) — yes.
4. Per-item reasons: current single-POST names now, align with USR-37 later — yes.
5. Split the audit refactor if the diff exceeds size M — yes.
