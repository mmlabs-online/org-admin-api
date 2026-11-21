## Attempt 1 — 2026-11-21
Verdict: REWORK

### Checks (run by reviewer)
- `uv run ruff check` / `ruff format --check` on the 19 changed `.py` files — All checks passed / 19 files already formatted (the `make check` lint failure on the untouched kit file `.github/scripts/risk_tier.py` is pre-existing).
- `uv run mypy app` — Success: no issues found in 203 source files.
- `uv run pytest -q` (full suite) — 3857 passed, 1 warning in 784.99s. Matches impl-log.
- `alembic check` — not needed (no migration or model change).
- Reviewer mutation check (scratchpad plugin, repo untouched): with `RbacRepository.lock_app` replaced by a plain SELECT plus a 0.2 s sleep, `test_two_owners_remove_each_other_one_wins` fails with `assert [204, 204] == [204, 403]`. With the real lock it passes. So the race test does catch a missing lock.
- Order check: `uv run pytest tests/test_org_member_remove_concurrency.py tests/test_org_member_remove.py` gives **3 failed, 34 passed** (see Issue 1).

### Acceptance criteria
- [x] AC1 204 + no-store; only this app/org touched; all org roles removed; co-owner; delegate — `tests/test_org_member_remove.py:79-136`.
- [x] AC2a–2i — tests at `tests/test_org_member_remove.py:139-398` (names as in the plan); service check order `app/modules/organizations/member_service.py:99-150` matches plan §4.
- [x] AC2g 409 `last_org_owner` — `test_last_owner_409` (:285) and `test_expiring_or_inactive_owner_does_not_count` (:293). The "deputy" caller (`_super`, :66-71) holds every org permission of the owner role plus remove-member but not the owner role, so it passes the rank rule. The target is the only counting owner, so the request reaches `leaves_no_owner` (member_service.py:146-150). Using a non-owner caller is the only way to reach 409, because an owner caller is always counted as an "other owner". The deviation is sound, and the 409 path is genuinely proven.
- [x] AC3 "at least one owner remains" — `tests/test_org_member_remove_concurrency.py:59-84` uses committed data and separate connections, and asserts exactly 1 owner row. `[204, 403 org_access_denied]` is the correct outcome: after the lock wait, the loser's caller no longer holds any org role (member_service.py:111-112). The plan's `[204, 409]` could not happen. The mutation check above shows the assertion depends on `lock_app`, so this is genuinely proven.
- [x] AC4 / 4b audit and invitation revoke — `test_success_writes_one_audit_row_without_email` (:403), `test_refusals_write_no_audit_row` (:424), `test_pending_invitations_to_target_revoked` (:436), and the `IDENTITY_AUDITED` entry in `tests/test_audit_coverage.py`. These tests are order-dependent (Issue 1).
- [x] AC5 token after removal — `test_token_after_removal` (:463); ADR-0026 and consumers.md document the lag.
- [x] AC6 — `tests/test_route_auth_classes.py` has the COOKIE entry; `CLAUDE.md:55` contains "org member removal — D35".
- [x] AC7 — `test_rate_limit_per_ip_429` (:500), `test_rate_limit_per_user_429` (:511).
- [x] AC8 — `tests/test_rbac_scopes.py::test_reserved_scope_remove_member_is_org`.
- [x] AC9 — `tests/test_org_member_rules.py` (6 tests).
- [x] Full test suite green (ruff on changed files, mypy, pytest).

### Issues
#### Issue 1 — 🔴 blocker — test isolation (order-dependent tests)
- **Where**: `tests/test_org_member_remove.py:59-64` (`_rows`), used at :410, :435 and in `test_pending_invitations_to_target_revoked` (:436); the cause is `tests/test_org_member_remove_concurrency.py:59-84`.
- **Expected**: Tests pass in any order. The repo already handles this case: the concurrency tests commit rows to the append-only `audit_log`, so audit assertions filter by actor (`tests/test_invitations_accept.py:83-85`, docstring "the concurrency test commits rows; audit_log is append-only").
- **Actual**: The race test commits an `organization.member_removed` row that its cleanup fixture cannot delete. `_rows` selects by action only. Whenever the concurrency file runs first in a session, three tests fail: `test_success_writes_one_audit_row_without_email` (`ValueError: too many values to unpack`), `test_refusals_write_no_audit_row` (`assert [<AuditLog>] == []`) and `test_pending_invitations_to_target_revoked` (`assert 2 == 1`). You can reproduce it with `uv run pytest tests/test_org_member_remove_concurrency.py tests/test_org_member_remove.py`. The default full run passes only because `.` sorts before `_` in the file names.
- **Fix instruction**: In `_rows`, also filter by the test's own org: add `AuditLog.org_id == <org_id>` (pass `renv.org.id`) or by actor, as `tests/test_invitations_accept.py:83` does. Every test builds a fresh org, so the committed race row can no longer match. Add a one-line docstring with the reason. Then re-run the reproduction command above and the full suite.

### Rules (CLAUDE.md)
No violations found. R1 holds: the router has no ORM access, the service raises `AppError` subclasses, and the queries are in the repository. R3 holds: the route is cookie class and in the registry. R4 holds: the audit row is written in the same transaction. R5 holds: the four keys are in `.env.example` without values. R6 holds: module-local codes use the envelope. R7 holds: the one out-of-plan file, `tests/test_cli_register_app.py`, is justified in impl-log. R11 holds: logs carry ids only, and `test_denied_log_has_no_email` covers this.

### Blockers from review agents
None. The orchestrator adds any from security-review.md.

### Nits (do not block)
- `member_service.py:139-141`: one `role_has_admin_permission` query per role. This is fine for the handful of roles involved; a single query could replace it later.
- `member_service.py:22` imports the private `_org_not_supported` from `auth_service`. The plan does this on purpose; consider making it public later.

### Follow-ups (out of scope)
- Those already listed in impl-log: de-duplicate `_user` (5 copies), self-service "leave org", cookie-side member list, guard staff revoke of the last org owner, webhook on removal.

## Attempt 2 — 2026-11-21
Verdict: PASS

Reviewed rework commit a2e17de (on top of fdfe1b9) against origin/main.

### Scope of the rework
`git diff --stat 79e936e a2e17de`: only `tests/test_org_member_remove.py` (+15/-6) and `work/tickets/USR-30/impl-log.md`. No app, migration or other test change. The fix: `_rows(db, org_id)` now filters `AuditLog.action.in_(ACTIONS), AuditLog.org_id == org_id`; all 4 call sites pass `renv.org.id`. Each test builds its own org, so the concurrency test's committed (append-only) audit rows no longer leak into these assertions.

### Attempt-1 blocker: resolved
- Concurrency first: `uv run pytest -p no:randomly tests/test_org_member_remove_concurrency.py tests/test_org_member_remove.py` → exit=0, `37 passed in 11.15s` (collection order confirmed: concurrency file ran first).
- Reverse order: → exit=0, `37 passed in 11.02s`.

### Checks (`make check` components)
- `ruff check .` → fails only in `.github/scripts/risk_tier.py` (13 errors, known kit file, out of scope); `ruff check --exclude .github .` → `All checks passed!`
- `ruff format --check .` → `1 file would be reformatted` = `.github/scripts/risk_tier.py` only (kit, out of scope).
- `mypy app` → `Success: no issues found in 203 source files`
- `pytest -q` (full suite) → `3857 passed, 1 warning in 724.54s`

### Carried from attempt 1
All other attempt-1 findings (acceptance criteria evidence, scope, R1–R11, security review) stand unchanged; the rework touched no production code.

### Nits
None new.

### Follow-ups
- Kit: `.github/scripts/risk_tier.py` fails repo ruff lint/format; fix upstream in spaceM project-kit (not here).

### Review agents (attempt 2)
- appsec-engineer: attempt 1 `Verdict: PASS` (no 🔴/🟡, 4 💭) stands; the attempt-2 rework (a2e17de) changed only `tests/test_org_member_remove.py` and `impl-log.md`. No blockers from review agents.
