# Review — USR-06b

## Attempt 1 — 2026-11-17
Verdict: PASS

### Checks (run by reviewer)
- make check (`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`): `ruff check` "All checks passed!", `ruff format --check` clean, `mypy app` "Success: no issues found in 115 source files", pytest `1939 passed, 1 warning in 178.68s` (the warning is the existing pty `forkpty()` DeprecationWarning in `test_cli_create_account.py`). Exit 0. Matches impl-log.md (1939 passed).
- alembic (dev DB): `upgrade head` → `check` "No new upgrade operations detected." → `downgrade -1` (c5e91d3a7b24 → b4d82f6a1c93, current `b4d82f6a1c93`) → `upgrade head` → `check` "No new upgrade operations detected." → current `c5e91d3a7b24 (head)`. Single head.

### Acceptance criteria
Ticket ACs (plan AC1–AC14):
- [x] AC4 create by a member holding `<app>:org:invite`, org-exact — evidence: `app/modules/invitations/service.py:159-163` uses `org_role_keys` + new `org_permissions` (`app/modules/rbac/repository.py` `org_permission_keys`, `RoleAssignment.org_id == org_id`, org `active`, unexpired); tests `test_invitations_create.py::test_create_member_without_invite_perm_403`, `::test_invite_perm_at_app_scope_only_does_not_count`, `::test_invite_perm_in_org_a_does_not_allow_org_b`, `::test_create_unknown_org_403_same_as_non_member`, `::test_create_suspended_org_403`; `test_rbac_authz.py` 4 org-permission tests. All pass.
- [x] AC4 accept by the invited verified email; expiring, single-use, stored hashed — evidence: `service.py:275-289` (unverified before lookup, email-HMAC binding, conditional consume `repository.py` `consume`); model stores only `email_hmac` and `token_hash` (`models.py`), CHECK length 32; `expires_at` on DB clock; tests `::test_row_stores_hashes_only`, `test_invitations_accept.py::test_accept_happy_joins_and_grants`, `::test_accept_unverified_403_not_consumed`, `::test_accept_other_verified_account_403_not_consumed_then_right_account_ok`, `::test_accept_email_match_is_case_insensitive`.
- [x] AC6 audit in the same transaction, no email/token in `changes` — evidence: `service.py:200-229` (`invitation_revoked`, `invitation_created`), `service.py:291-330` (`app.joined`, `role_assignment.granted`, `invitation_accepted`); all via the request session; tests `test_invitations_create.py::test_create_audit_row`, `::test_audit_changes_have_no_email_or_token`, `::test_refusals_write_no_audit`, `test_invitations_accept.py::test_accept_audit_rows`, `::test_refusals_write_no_audit`; `test_audit_coverage.py` registers both routes in `IDENTITY_MULTI_ROW_AUDITED`.
- [x] AC7 tests: happy path; other verified / unverified account refused; replayed, expired, revoked (superseded), unknown and other-app tokens get one identical 422 (`test_replayed_expired_superseded_unknown_other_app_identical_422`, compares full bodies, `details == {}`); concurrent accepts → one 200 / one 422 and exactly one org assignment (`test_concurrent_accepts_one_succeeds`, two real connections); inviter cannot exceed own org permissions (`test_role_beyond_inviter_perms_403`, `test_member_inviter_cannot_invite_owner_role`, `test_subset_is_org_exact_app_scope_perms_do_not_count`), `is_admin` role (`test_is_admin_role_403`), another app's role (`test_other_app_role_key_422`), admin-app role (`test_admin_app_context_403`); no account oracle (`test_response_same_for_known_and_unknown_email`, `test_response_same_when_target_capped`, `test_create_does_not_query_users_by_email`).
- [x] Plan AC5 limits — `test_per_inviter_cap_403`, `test_per_org_pending_cap_403`, `test_create_rate_limited_429`, `test_accept_rate_limited_429`, `test_global_cap_counts_invites`, `test_email_throttle.py` (70/40/10 table).
- [x] Plan AC8 accept re-checks — `test_accept_after_inviter_role_revoked_422`, `_role_gains_admin_perm_422`, `_org_suspended_422`, `_inviter_disabled_422`, `_role_gains_perm_the_inviter_lacks_422`.
- [x] Plan AC9 S1 guard — `service.py:115-141`; `test_create_unavailable_*` (3), `test_accept_unavailable_when_default_widened_not_consumed`, `test_accept_closed_app_refused_not_consumed`, `test_invitations_rules.py::test_roles_overlap_*`.
- [x] Plan AC10 role already held — `test_accept_role_already_held_consumes_and_audits`.
- [x] Plan AC12 cookie class — both routes in `COOKIE` of `tests/test_route_auth_classes.py`; `Cache-Control: no-store` set in `router.py:_finish`.
- [x] Plan AC13 migration — `test_invitations_schema.py` (constraints, CHECK text via `pg_get_constraintdef`, partial unique, cascades), `test_invitations_migration.py::test_upgrade_downgrade_roundtrip_drops_invite_send_rows`, plus the reviewer's manual round trip above.
- [x] Plan AC14 `make check` green — see Checks.

### Issues
None.

### Scope
Every changed production file is in the plan's file list. Out-of-plan files are justified in impl-log.md: `tests/invitation_factories.py` (shared setup), `tests/test_password_reset_api.py` (expectation follows the 70/40/10 shares the plan mandates), `docs/deploy.md` (log-reason table named the old 80/50 shares). `work/backlog.md` / `ticket.md` changes are orchestrator state commits.

### Rules R1–R11
No violations found.
- R1: router HTTP only; service raises `AppError` subclasses (`invitations/errors.py`); SQL only in `invitations/repository.py` and `rbac/repository.py`.
- R2: hand-written migration with working, lossy-documented `downgrade()`; model registered in `app/db/models.py`; `alembic check` clean.
- R3: cookie class, no `require_permission`; admin app refused on create via `get_link_origin` (`MailLinkAppContextDep`).
- R4: audit rows in the request transaction; refusals write none.
- R5: TTL, caps and rate limits from `Settings`, keys in `.env.example` without values; per-target limits are constants like the existing verify/reset invariants (plan A7).
- R6: error envelope via existing base classes; codes 200/201/400/401/403/422/429 only.
- R11: only SHA-256(token) and email HMAC stored; token only in the mail link fragment and request body; log lines carry UUIDs, app key and reason only; verified email required before any token lookup.

### Nits (do not block)
- `invitations/repository.py` `supersede_pending` also revokes pending rows that are already expired, and they are audited as `reason: superseded`. This is needed for the partial unique index; a `reason: expired` distinction could be clearer in the audit trail.
- `invitations/rules.py` imports the private `_CONTROL_CHARS` from `audit/service.py` (noted in impl-log); promote to a public name later.
- `InvitationService` reaches into `AssignmentService._grant_locked` (private), as 06a's org create already does.

### Follow-ups (out of scope)
- USR-06c (S1: scope marker / `org_roles` claim, `build_default_role` skipping org-level permissions, `set_default_role_if_null` fix), before USR-05.
- Retention purge of `org_invitations`; list/revoke pending invitations and member management (needs spec line).
- `AuthOrgCreate.name` safe-name validation; CLAUDE.md R3 class list to name "invitations" (plan A14, owner decision).
