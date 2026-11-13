# Review — USR-03b

## Attempt 1 — 2026-11-13
Verdict: PASS

### Checks (run by reviewer)
- make check (`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`), exit 0:
  - `ruff check .`: All checks passed!
  - `ruff format --check .`: 346 files already formatted (impl-log says 345; the working tree is clean and ruff sees the same 148 tracked `.py` files, so this is an environment count, not a code difference)
  - `mypy app`: Success, no issues found in 85 source files
  - `pytest -q`: 1038 passed in 41.19s (same as impl-log)
- alembic check: "No new upgrade operations detected." The diff has no migration and no model change, and `app/db/models.py` is unchanged.

### Acceptance criteria
The plan's AC table (1–20) is the contract. Owner decisions O1–O4 are recorded and not judged here: the approval flow goes to USR-03g, so the ticket's "approval → 202 + staff approve/reject" parts of AC2/AC4 are deferred by O1.
- [x] 1 Accounts list (cookie class): own device only, `created_at, id` order, disabled users hidden, revoked sessions excluded, exact fields with no `user_id`. Evidence: `app/modules/sessions/repository.py:150-167`, `app/modules/auth/router.py:214-242`; tests `test_accounts_lists_own_device`, `test_accounts_fields_exact`, `test_accounts_excludes_other_device`, `test_accounts_excludes_revoked`, `test_accounts_hides_disabled_user`, `test_accounts_empty_device`.
- [x] 2 N1 cutoff with the previous cookie value. Evidence: `app/modules/sessions/repository.py:164-165` and `app/modules/sessions/service.py` `list_accounts` (passes `accounts_created_before`); test `test_accounts_previous_cookie_hides_newer_account` (the newer account is hidden and appears with the new cookie).
- [x] 3 `no-store`, rotation, 401, 403 header/Origin, 429. Evidence: `router.py:229-231`; tests `test_accounts_no_store_and_rotation`, `test_accounts_missing_cookie_401`, `test_accounts_unknown_cookie_401`, `test_accounts_missing_app_header_403`, `test_accounts_bad_origin_403`, `test_accounts_rate_limited_429`, `test_accounts_logs_no_email`.
- [x] 4 `app_mismatch`. Evidence: `app/modules/membership/service.py:48-49`; test `test_join_app_mismatch_403`.
- [x] 5 `admin` is refused before the policy is read. Evidence: `service.py:50-51`; test `test_join_admin_refused_even_if_open`.
- [x] 6 `open` assigns the default role at app scope (`org_id`/`expires_at`/`granted_by` NULL), and `/token` then succeeds. Evidence: `service.py:89`; tests `test_join_open_assigns_default_role`, `test_join_then_token_200`.
- [x] 7 Idempotent: no write and no audit for an existing app-scope member. Evidence: `service.py:70-71`; tests `test_join_idempotent_no_write`, `test_join_existing_other_role_no_write`, `test_join_already_member_of_closed_app`, `test_join_org_scope_role_is_not_membership`.
- [x] 8 An expired row is revived in place. Evidence: `_grant_locked` (`app/modules/rbac/service.py:788-800`); test `test_join_revives_expired_assignment`.
- [x] 9 Unverified email gives 403 `email_unverified` with no write. Evidence: `service.py:66-67`; test `test_join_unverified_403`.
- [x] 10 `closed` → `join_closed`, `approval` → `join_approval_unavailable`. Evidence: `service.py:73-76`; test `test_join_policy_refused_403[closed|approval]`.
- [x] 11 A NULL, missing or `is_admin` default role gives 403 `join_unavailable`, with a warning log for the `is_admin` case. Evidence: `service.py:78-87`; tests `test_join_null_default_role_403`, `test_join_default_role_with_admin_permission_403`.
- [x] 12 Account resolution: other device, unknown, revoked, N1-newer or disabled → 401 and the cookie is kept. Evidence: `service.py:58-65`; tests `test_join_other_device_account_401`, `test_join_unknown_account_401`, `test_join_revoked_account_401`, `test_join_previous_cookie_newer_account_401`, `test_join_disabled_user_401`.
- [x] 13 `lock_app` comes before every read of `roles`, `role_assignments` and `account_sessions` and before every write. Evidence: `service.py:53`; test `test_join_locks_app_before_reads_and_writes` (statement recorder).
- [x] 14 Audit through the interface, once per write, before commit, ids only. Evidence: `app/modules/audit/recorder.py`, `service.py:90-96`; tests `test_join_audit_ids_only`, `test_join_default_recorder_logs_ids_only` (no email and no cookie in the logs).
- [x] 15 Join: `no-store`, rotation, rollback on error, 429, 401, 403, 422. Evidence: `router.py:251-270`; tests `test_join_no_store_and_rotation`, `test_join_error_after_rotation_rolls_back`, `test_join_rate_limited_429`, `test_join_missing_cookie_401`, `test_join_missing_app_header_403`, `test_join_bad_origin_403`, `test_join_body_validation_422` (3 cases), `test_join_disabled_app_403`.
- [x] 16 CORS preflight echoed for a registered origin and not for a foreign one. Evidence: tests `test_preflight_accounts_and_join_from_registered_origin`, `test_accounts_and_join_foreign_origin_not_echoed`.
- [x] 17 USR-07 S1: a NULL default role stays NULL after a register-app rebuild. Evidence: `app/modules/rbac/service.py:183-208,415`; test `test_null_default_role_kept_after_permission_add`; `test_create_app_and_member_role` is still green.
- [x] 18 `join_available` is true only for `open` with a default role. Evidence: `app/modules/tokens/service.py:79-81`; test `test_token_not_member` with 4 parametrized cases.
- [x] 19 Docs. Evidence: `docs/consumers.md` "Accounts and joining an app" (fetch examples, consent rule, sequence, error table); `docs/identity-architecture.md` §2/§3/§4/§6/§7/§8 (D21 owner, D22 proposed); `docs/deploy.md` register-app line.
- [x] 20 `make check` is green and `alembic check` is clean (above).

### Issues
None blocking.

### Scope
- Every app, test and doc file is in the plan's file list.
- The extra files are the orchestrator's work files: `work/backlog.md`, `work/tickets/USR-03b/*`, and the new `work/tickets/USR-03g/ticket.md` and `work/tickets/USR-08/ticket.md`. These come from the owner's 2026-11-13 split and plan O1.
- No unjustified out-of-plan change.
- One deviation from the plan: the membership test uses `AuthzService.effective_roles(user.id, app.key)` instead of `rbac.effective_role_keys(..., None)`. It is the exact call `/token` makes, with the extra prefix filter, so it matches the plan's intent ("the same test `/token` uses").

### Rules R1–R11
No violations found.
- R1: SQL stays in the repositories. The routers only map results.
- R2: no schema change.
- R3: both endpoints are cookie class (`AppContextDep` before `DeviceDep`).
- R4: the `AuditRecorder` placeholder runs inside the service before commit.
- R5: the four new keys are in `config.py` and `.env.example` with no values.
- R6: all errors are `AppError` subclasses (403/401).
- R8: tests cover the happy path and the 401/403/422/429 cases.
- R9: the `{items}` document is recorded as proposed D22 (O2).
- R11: no email or token in logs (tested), `no-store`, rotation, verified email required.

### Nits (do not block)
- `app/modules/auth/router.py:233-241`: the router builds `AccountRead` from ORM attributes. It runs no queries, so this is acceptable under R1. A `model_validate`/service-side mapping would keep the router thinner.
- `app/modules/membership/service.py:89` calls the private `AssignmentService._grant_locked`. There is precedent for this (`AppService` → `catalog._build_for_app`). A public `grant_locked` name would make the cross-service contract explicit.
- Assumption A6: `LogAuditRecorder` logs before commit, so a rolled-back join can leave a log line behind. This is accepted until ADM-004.
- The `test_join_locks_app_before_reads_and_writes` guard also matches `account_sessions` statements made by `DeviceDep`. Those happen before the service runs, so the test only passes because the device lookup reads `device_sessions`. A future change to `DeviceDep` could make the test fail spuriously.

### Follow-ups (out of scope)
- Plan F1–F6: USR-03g approval flow, per-device account cap, shared `ErrorEnvelope`/`operation_id`s, origin exclusivity on admin `PATCH /apps`, CLAUDE.md R11 cookie wording vs D20, re-join deny-list.
- `docs/deploy.md` env table does not list the per-IP rate-limit keys (`TOKEN_*`, `ACCOUNTS_*`, `JOIN_*`).
- Owner to confirm O1–O4 (approval split, D22 R9 exception, unmasked emails in `/accounts`, join 200 vs 201).
- The ticket's AC2/AC4 still name the approval 202 + staff endpoints. Once O1 is confirmed, update the ticket text to point at USR-03g.
- `join_available` stays true for an `open` app whose default role holds an `is_admin` permission. That state cannot be reached through the API, and join refuses it.
