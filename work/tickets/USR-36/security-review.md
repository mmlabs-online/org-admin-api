# Security review — USR-36 (Attempt 1, appsec-engineer, 2026-11-27)

Saved by the orchestrator (the appsec agent is read-only at REVIEW; code read only, no pytest — the reviewer ran the suite).

**Verdict: PASS** — no 🔴, no 🟡.

Scope: commit fcb019c vs 97f9974, against ADR-0029, ADR-0030 §4, CLAUDE.md R3/R4/R5/R11.

## Privilege escalation via the batch — closed
Every request-level refusal precedes any write (`app/modules/rbac/service.py`): duplicate ids → 422 `duplicate_user_ids` before the lock (~1844); `SET LOCAL lock_timeout` then the app lock (1873; `admin` app never locked); unknown app/role → 404; admin-app role → 422 `admin_app_role` (1887); designated super-admin role → 422 `super_admin_role` (1891); `_check_grant_request` (org rules, expiry, `_require_privileged_grant` → 403 `admin_wildcard_required`). Per-user `_forbid_self` / `recipient_not_staff_super_admin` remain as defence in depth (unreachable from the batch today). Self id with a non-privileged role allowed, as on the single POST (`test_batch_self_non_privileged_role_created`). The `is_admin` check runs under the app lock.

## The split does not weaken the single POST or other callers
`_grant_locked` (~1697) = `_check_grant_request` + `_grant_user_locked`; guard order unchanged (designated-org check, org mode/unknown org, expiry, `require_super`, self check, recipient check, duplicate). Only change: `find_assignment` (a plain read) now precedes `_forbid_self` — same behaviour. Active duplicate still 409 `assignment_exists` for every caller (membership, invitations, join requests, org create, provisioning). Revoke's `_guard_privileged` (1808) unchanged.

## Audit (R4)
- Granted: `_record_grant` inside each user's savepoint — one `role_assignment.granted` per created/renewed, rolled back with its savepoint; none for `unchanged` / `not_found` (ADR-0029 §6).
- Per-user denied: `record_denied_in_tx` (`audit/service.py:228`) after the savepoint rollback (`service.py:1922`), joins the outer transaction, never commits/rolls back itself; same content as the single POST (`_denied_changes`, `(USER, uid)`, `{role_key}`); system actors write nothing.
- Request-level 403: `_record_batch_wildcard_denied` (1950) reads existing ids before the first `record_denied` rollback, one denied row per existing user; nothing granted yet. `test_batch_admin_wildcard_required_403`, `tests/test_audit_denied.py` (`batch_wildcard`).
- Not forgeable: `batch_id = uuid.UUID(ctx.request_id)` (1852); `app/core/request_id.py` ignores inbound `X-Request-ID`.

## Per-item results — no leak
`_batch_failed` (1498): `exc.code` + fixed `exc.message`, no `details`, email or user-state hint; unknown user = "User not found" (same as single POST).

## Body handling
`read_json_body` (`rbac/router.py:501`): missing/non-numeric Content-Length → 411; > 16 KiB → 413; non-JSON → 415; bounded streaming read (over → 413, short/disconnect → 422 `incomplete`, 10 s timeout); validation without echo. Auth runs first. `extra="forbid"`, 1..100 UUIDs.

## Lock bounds
`SET LOCAL lock_timeout = '5s'` before `lock_app` (`repository.py:829`, constant at 106); SQLSTATE 55P03 → 409 `changed_concurrently`, whole batch rolled back; bounded by the 100-user cap, preloads, sorted order. `test_batch_lock_timeout_409_nothing_written`.

## Findings (all 🟢)
- `service.py:1887`, `:1891`: a non-super caller submitting an admin-app or designated role gets 422 with no denied row (the single POST writes a 403 denied row). Matches ADR-0029 §3/§6, grants nothing, but probing leaves no trail — optional: write a denied row when `not is_super(actor)`, or note it in the ADR.
- `service.py:1950`: request-level denied path makes N separate commits via `record_failure`; partial rows on a mid-way audit failure — same as the single POST.
- `repository.py:829`: `SET LOCAL` via f-string — safe (module constant); optionally assert/whitelist.
- `router.py:501`: transaction may sit idle up to 10 s while the body is read — no locks held.
- `router.py:501`: rbac router imports `app.modules.releases.body` (cross-module, R1 — reviewer's call).

## Follow-ups (out of scope)
- Per-staff rate limit on the batch route.
- OQ79 audit filter by `request_id`; bulk revoke as undo.
- Per-user self-ban denied rows once USR-25 lands (`record_denied_in_tx` ready).
