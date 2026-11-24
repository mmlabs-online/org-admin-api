# Review — USR-31

## Attempt 1 — 2026-11-24
Verdict: PASS

Reviewed commit 0c8ebf1 (branch `claude/festive-volta-rikup7`) against `origin/main`.

### Checks (run by reviewer)
- `uv run pytest -q` (FULL suite, local Postgres) — exit 0, `4066 passed, 2 warnings in 667.96s`. This matches impl-log. Warnings: an SAWarning from the new test's scalar subquery (tests/test_app_org_invitations.py:153) and a pty DeprecationWarning in test_cli_create_account.
- `uv run mypy app` — `Success: no issues found in 209 source files`.
- `uv run ruff check .` — 13 errors, all in `.github/scripts/risk_tier.py`. `uv run ruff format --check .` — 1 file, `.github/scripts/risk_tier.py`. This is the known kit failure: `git diff origin/main...HEAD -- .github/scripts/risk_tier.py` is empty, so the file is unchanged. Ruff check and ruff format on the 9 changed `.py` files both pass ("All checks passed!", "9 files already formatted").
- `uv run alembic check` — exit 0, "No new upgrade operations detected." There is no migration in the diff.

### Acceptance criteria
- [x] AC1: pending, unexpired invitations of `{key}` only. Evidence: repository filter at app/modules/invitations/repository.py:181-206 (org, app, not accepted, not revoked, `expires_at > func.now()`, explicit columns, composite Role join, `(created_at, id)` order). Tests (all pass): `test_lists_pending_invitations_of_key_only` (tests/test_app_org_invitations.py:104; excludes accepted, revoked, expired, another app in the same org, and the same app in another org; `total == 2`), `test_expiry_uses_db_clock` (:131), `test_paging_stable_by_created_at_then_id` (:141), `test_limit_over_100_is_422` (:165), `test_empty_list_for_related_org_without_invitations` (:172).
- [x] AC2: no email, token or hash in the response, logs or audit rows. Evidence: the schema has 4 fields (app/modules/organizations/schemas.py:81-85), and `test_item_has_exactly_four_fields` (:182) checks them. `test_no_secret_in_response_logs_or_audit` (:191) uses the invitee email, HMAC, raw token, token hash, inviter email and inviter id as sentinels. Its log capture is non-vacuous: it asserts an `app.modules.app_credentials` record that carries the key id (:247-250) and the org id in the logged text (:251). Impl-log records that the test failed with a temporary `logger.info(email_hmac.hex())`.
- [x] AC3: 403 mismatch/scope and 401 for an admin JWT or deployer key. Evidence: `test_other_app_credential_403_mismatch_audited` (:312) asserts a denied row with action `organization.invitations_read`. `test_manifest_releases_usage_keys_403_scope` (:328) gets 3 denied rows. The registry entry is at tests/test_route_auth_classes.py:67,76, with `lock is False` at :325, so the parametrized 401, no-CORS and allow-list tests cover this route.
- [x] AC4: identical 404 bodies and 422 for `org_mode=none`. Evidence: `test_unknown_unrelated_suspended_org_same_404` (:261) compares the bodies byte for byte, and the suspended org holds a pending invitation. `test_org_with_only_expired_key_roles_is_404` (:283). `test_org_mode_none_422` (:303). The guard runs before the query (app/modules/organizations/app_service.py:125 before :128).
- [x] AC5: the contract is in docs/consumers.md:252 ("Pending invitations (USR-31)"). It covers the route, query, fields, errors, shared limits, "not a seat guarantee" and the snapshot note.
- [x] Plan AC6 (audit, no-store, shared buckets). Evidence:
  - Success audit row with counts only: app/modules/organizations/app_service.py:131-139.
  - `test_success_writes_one_audit_row_counts_only` (:354) asserts exactly one row with `changes == {"returned":1,"total":1,"limit":5,"offset":0}` and no `members_read` row.
  - `test_404_writes_one_denied_row` (:370) asserts exactly one row.
  - tests/test_audit_coverage.py `test_org_invitations_read_route_is_audited` asserts `== ["organization.invitations_read"]`.
  - `test_cache_control_no_store` (:380).
  - Shared IP and key buckets: (:388) and (:401). Router evidence at app/modules/organizations/app_router.py:74,94.
- [x] Plan AC7: tests/test_app_org_members.py is unchanged and passes. The guard extraction (app_service.py:24-47) has the same logic as before, with the action passed as a parameter.

On the implementer's rollback note: the `client` fixture's session override does not commit per request, so in the no-secret test the success row is undone when `record_failure` (app/modules/audit/service.py:185) rolls back for the later 404. This happens only in the test harness. In production, `get_session` commits each request (app/db/session.py:32). The one-row-per-call guarantee is still proven, because each audit test makes a single call and unpacks exactly one row (`(row,) =`) for success (:362), denied 404 (:375) and mismatch (:323), and the scope test checks 3 rows for 3 calls (:342). The success row's content is pinned exactly (:366), so it cannot hold a sentinel. The weaker `assert rows` at :253 is acceptable.

### Issues
None.

### Rules (CLAUDE.md)
No violations found.
- R1: the router is HTTP only. The service uses repositories. The repository is SQLAlchemy only.
- R3: class 4 (a). The registry and the CLAUDE.md R3 text are updated as Ming approved.
- R4: success and denied rows are written in the request transaction.
- R5 and R11: no config change, and no secrets in logs or the response.
- R6: only existing codes are used.
- R7: every changed file is in the plan's file list.
- R8: tests cover the happy path, 404, 422, 401/403 and 429.
- R9: plural noun and `Page[T]`.

The only High path is `app/modules/app_credentials/deps.py` (+10 lines, a new dependency declaration modelled on `OrgMembersCredentialDep`), so the PR stays High: Ming merges it.

### Blockers from review agents
None. The orchestrator copies in anything from security-review.md if appsec runs.

### Nits (do not block)
- tests/test_app_org_invitations.py:153: wrap the subquery in `.scalar_subquery()` to silence the SAWarning.
- tests/test_app_org_invitations.py:253: the test could use `make_strict_client` to keep both the success and denied rows and assert `len(rows) == 2`. The coverage is already adequate.

### Follow-ups (out of scope)
- A `role` filter if simap asks for it, and the admin-pan help text for the new route (both from the plan).

### Ming verification after merge
- From simap's backend, call `GET /api/v1/apps/simap/orgs/{org_id}/invitations` once with the existing `MMLABS_ORG_MEMBERS_CREDENTIAL`.

### Review agents (attempt 1)
- appsec-engineer: `Verdict: PASS` — no 🔴, no 🟡; 3 💭 notes (see `security-review.md`). No blockers from review agents.
