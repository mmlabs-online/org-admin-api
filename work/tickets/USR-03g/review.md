# Review — USR-03g

## Attempt 1 — 2026-11-17
Verdict: PASS

### Checks (run by reviewer)
- make check (`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`): ruff "All checks passed!", ruff format "290 files already formatted", mypy "Success: no issues found in 133 source files", pytest "2180 passed, 1 warning in 192.97s". Exit 0, no flake. This matches impl-log.md.
- alembic: `upgrade head`, then `downgrade -1` (a9d4c27e6b13 -> f3a8d51c9e27), then `upgrade head`, then `alembic check` gave "No new upgrade operations detected.". `alembic heads` gives `a9d4c27e6b13 (head)` (a single head).

### Acceptance criteria
- [x] Migration: `join_requests` with CHECK `(status='pending') = (decided_at IS NULL)`, a status CHECK, partial unique `(app_id, user_id) WHERE status='pending'`, two indexes, FKs (CASCADE/CASCADE/SET NULL) and a working `downgrade()`. The model is registered. Evidence: `migrations/versions/20261117_a9d4c27e6b13_usr03g_join_requests.py`, `app/modules/join_requests/models.py` (CHECK/where texts are byte-identical to the migration), `app/db/models.py`, `tests/test_join_requests_migration.py` › `test_downgrade_drops_table`, `test_constraints`, the manual round trip above, and a clean `alembic check`.
- [x] Join on an `approval` app → 202 pending, idempotent; `/token` `join_available` reflects it. Evidence: `app/modules/membership/service.py` `_request` / `join_available`, `app/modules/auth/router.py` join handler, and these tests: `test_join_approval_202_pending` (also covers no-store and the rotated cookie), `test_join_approval_replay_same_request`, `test_join_approval_member_200`, `test_token_join_available_approval`, `test_auth_token.py::test_token_not_member` (approval cases), `test_auth_orgs.py::test_create_org_not_member_approval_join_available`.
- [x] Approve takes `lock_app` and re-checks app status/policy, active user, verified email and a usable default role. It grants the default role at app scope with `granted_by` = staff. Reject records `decided_by`/`decided_at`. Non-pending → 409, unknown or cross-app → 404. Evidence: `app/modules/join_requests/service.py` `approve`/`reject`/`_pending`, and these tests: `test_approve_grants_default_role`, `test_approve_already_member_no_grant`, `test_approve_uses_current_default_role`, `test_approve_refusals_409` (7 cases), `test_not_pending_409`, `test_second_approve_409`, `test_unknown_and_cross_app_404`, `test_reject_records_decision`, `test_reject_allowed_when_app_disabled_or_policy_changed`, and `test_approve_lock_order` (app FOR NO KEY UPDATE < user FOR SHARE < request FOR UPDATE < INSERT).
- [x] Audit via `AuditLogger` in the same transaction: one row per create/approve/reject, and none on replay or failure. I confirmed that `_grant_locked` never audits (`app/modules/rbac/service.py:1390`), so approve writes exactly one row. Evidence: `test_join_approval_audit_once`, `test_audit_coverage.py` (approve/reject in `AUDITED`, `len == 20`, success/failure/no-email tests), and the no-audit assertions in the 409 tests.
- [x] Cooldown from env (R5): `join_reject_cooldown_seconds` (`ge=0`) is in `app/core/config.py` and `.env.example`. Evidence: `test_join_rejected_cooldown_403`, `test_join_after_cooldown_new_request`.
- [x] R3 classification and permission map. Evidence: `tests/test_route_auth_classes.py` (3 BEARER entries) is green, and `test_staff_routes_401_403` covers all 3 routes.
- [x] `make check` green (above).

### Scope
Every changed code, test and doc file is in the plan's file list. The other files changed are `work/` (orchestrator and specialist files and the backlog row), which is expected. No out-of-plan source changes.

### Issues
None.

### Rules R1–R11
No violations found.
- R1: the router only calls the service. The service raises only `NotFoundError`/`ConflictError`. The repository is SQLAlchemy-only.
- R2: the migration is hand-written, the downgrade works and there is no drift.
- R3: class 1, with `current_admin` and one `require_permission` per route.
- R4: the audit row is written in the same transaction after the last guard.
- R5: the cooldown comes from env.
- R6: error envelope; 409 `conflict` with `details[0].reason`.
- R9: `/apps/{key}/join-requests` under the D18 key exception, `Page[T]` via `PageDep`, UUID ids, timestamptz.
- R11: log lines carry only ids and the app key. No email goes into logs or audit `changes`, and the email is re-checked at approve.

### Nits (do not block)
- `app/modules/join_requests/repository.py` `get_user` duplicates a plain `session.get(User, …)` that `UserRepository` could serve. Harmless.
- `JoinService._request` checks `usable_default_role` before the pending lookup. If staff make the default role unusable after a request is queued, a re-POST returns 403 `join_unavailable` instead of the existing 202 `request_id`. This is consistent with "don't queue unapprovable requests", but worth noting in the docs if it confuses clients.
- `docs/consumers.md`: the "`join_approval_unavailable` is gone" note sits at the top of the Joining section. It would read better next to the error table.
- `test_unknown_and_cross_app_404` does not assert that no audit row was written for the cross-app case. The coverage harness covers the unknown-id case.

### Follow-ups (out of scope)
- As in plan.md: a `cancelled` status on user disable, retention purge of decided requests, expiry of stale pending requests, a per-app pending cap, a deny-list, a staff-only reject reason, `details.join_pending`, `role_has_org_permission` defense in depth in `usable_default_role`, `get_for_share` on the open-join user read, and a per-account join rate limit.

## Attempt 2 — 2026-11-17
Verdict: PASS

Scope: narrow rework of security-review.md "Attempt 1" S1 and N4 only. Attempt 1 already PASSed. Reviewed delta: `git diff HEAD~1 -- app tests docs` (commit 1cdfe45).

### Checks (run by reviewer)
- `service postgresql start`, then `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff, format check and mypy are clean; pytest gives **2180 passed, 1 warning** (pty forkpty DeprecationWarning, already known) in 198.54 s. The known flake `test_purge_batches_share_cutoff` passed on the first run, so no rerun was needed. This matches impl-log Attempt 2 (2180 passed).
- alembic check: not applicable (no schema change in this delta).

### Acceptance criteria (rework items)
- [x] S1: approve and reject now send `Cache-Control: no-store`. Evidence: `app/modules/join_requests/router.py` approve handler (new `response: Response` param, header set before return, around :55-62) and reject handler (around :74-80). This matches the existing list route (:31/:39). `Response` was already imported (:6). Tests: `tests/test_join_requests_api.py` `test_approve_grants_default_role` (:118) and `test_reject_records_decision` (:278) assert `resp.headers["cache-control"] == "no-store"`. Both pass.
- [x] N4: the sentence at `docs/consumers.md:353` now reads "...with a default role, unless a rejection is inside the cooldown." This is consistent with the paragraph above it.

### Issues
None.

### Rules R1–R11
No violations found. The header is set in the router, which is the HTTP-only layer (R1). There is no schema change (R2). Auth class and permissions are unchanged (R3). No secrets are involved (R5/R11). Scope is limited to the S1/N4 fixes. The only other changes are ticket.md and backlog.md state bookkeeping (R7).

### Nits (do not block)
- impl-log Attempt 2 says "Not committed", but the changes are in commit 1cdfe45 (made by the orchestrator). This is wording only.
- On an error path (for example a 404/409 raised by the service), the header is not set. This is fine because error bodies do not carry the email.

### Follow-ups (out of scope)
- Unchanged from Attempt 1 and security-review Attempt 1 (N5 index on `decided_by`, pending caps/expiry, retention purge that keeps the newest rejection).
