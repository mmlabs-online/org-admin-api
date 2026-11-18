# Review: USR-14

## Attempt 1 — 2026-11-17
Verdict: PASS

### Checks (run by reviewer)
- make check (ruff, format, mypy, pytest): `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` exit 0. ruff "All checks passed!", format clean, mypy "Success: no issues found in 146 source files", pytest "2338 passed, 1 warning in 244.24s". BUG-001 flake did not occur. This matches impl-log (2338 passed).
- alembic: `alembic upgrade head` ok; `alembic check` reports "No new upgrade operations detected." No file under `migrations/` changed, and no model changed.
- Scope: `git diff origin/main...HEAD --stat` touches only the plan's files, plus workflow files (ticket, backlog, consult notes, plan, impl-log). `app/core/access_token.py`, `tests/jwt_verifier.py`, `migrations/`, `.env.example` and `app/core/config.py` are unchanged.

### Acceptance criteria
- [x] Design chosen and recorded in an ADR amendment. Evidence: the `sid`-only design is in `docs/adr/0008-admin-api-authentication.md` ("Amendment: session-bound admin tokens (USR-14)") and in `docs/adr/0005-access-token-signing.md` ("Amendment: `sid` for admin tokens"). That amendment covers the claims contract, rewrites risk 4, strikes the follow-up and records why `tokens_valid_after` was rejected.
- [x] For `aud=admin`, the old token gets 401 on the next admin request after each trigger. Evidence: `tests/test_admin_token_sessions.py` has `test_logout_account_ends_admin_token`, `test_logout_all_ends_admin_token`, `test_password_reset_ends_admin_token`, `test_staff_revoke_one_ends_admin_token`, `test_staff_revoke_all_ends_admin_token` and `test_disable_then_reenable_ends_admin_token`. All pass. Each test uses a real `/auth/token` token, and asserts the exact envelope and `WWW-Authenticate` on both `/organizations` and `/me/permissions`. The code that does this:
  - `app/modules/admin_auth/service.py`: `principal` calls `is_account_active`.
  - `app/modules/admin_auth/deps.py`: parses `sid` and maps the failure to the same 401.
  - `app/modules/sessions/repository.py`: `is_account_active` checks that the account session and its device are both not revoked and belong to `user_id`.
- [x] Consumer tokens unchanged, docs updated. Evidence:
  - `test_consumer_token_has_no_sid_and_survives_logout` passes.
  - `test_auth_token.py::test_token_happy_path` (the exact consumer claim set) is unmodified and passes.
  - `sid` is added only when `app.key == ADMIN_APP_KEY` (`app/modules/tokens/service.py`).
  - Docs: consumers.md, the deploy.md runbook, ADR-0004/0005/0008, identity-architecture.md and spec §5.10 are updated.
- [x] Additive claim only, no migration. Evidence: `REQUIRED_CLAIMS` is untouched, and `test_token_admin_audience_issued` asserts that the admin claim set is the consumer set plus `sid`, with `sid == account.id`. No column was added, so no migration is needed, and `alembic check` is clean.
- [x] Tests for each trigger, plus a token issued after the cutoff still works. Evidence: every trigger test ends with `_fresh_works` (new device, real mint, 200). Negative and edge tests also pass:
  - isolation: `test_other_session_of_same_user_survives`, `test_shared_device_other_user_survives`;
  - race: `test_revoke_between_session_read_and_sign_401`;
  - `test_admin_auth.py`: missing, malformed (3 values), unknown and foreign `sid`; revoked device; lookup error not allowed; denials logged without the token or email.

### Issues
None.

### Rules R1–R11
No violations found.
- R1: the SQL is in the repository, the service raises an `UnauthorizedError` subclass, and deps maps it to HTTP.
- R3: the check is in `current_admin`, so it also covers the self-scoped `/me/permissions`.
- R6: the same 401 envelope, with no details.
- R11: logs carry the reason, `jti` and `user_id` only.
- Fail closed: no try/except around the lookup (tested).
- R2: not triggered.
- R4: no new mutation.

### Nits (do not block)
- `docs/deploy.md:258` ("Lost or shared device") still says "disable, or revoke admin roles, held for at least TTL + 60 s". Since USR-14, the hold applies only to a role revoke. Disable is now immediate and survives re-enable, as the rewritten "Compromised staff account" bullet says.
- `app/modules/users/router.py` `_REVOKE_LIMITS`: the string is split awkwardly ("It does not stop a " / "new sign-in with the " / "password."). The rendered text is fine, but the literals could be reflowed.
- `tests/test_admin_token_sessions.py::_token_of` imports `mint` inside the function. It could be a module-level import next to `bearer` and `make_staff`.

### Follow-ups (out of scope)
- As listed in plan.md: a per-user cutoff for role revoke followed by re-grant within TTL; `--clear` as an immediate admin kill switch; consumer-side session introspection; folding the session check into the user query.
- Pre-deploy: the owner confirms that the admin-pan verifier accepts the extra `sid` claim (not in this workspace). The release note should say that admin tokens issued before the deploy get one 401 and must be re-minted.

## Attempt 2 — 2026-11-17
Verdict: PASS

Scope: docs-only rework for the single 🟡 in security-review.md "## Attempt 1" (runbook wording). Reviewed the attempt-2 delta only (`git diff HEAD~1 -- docs`). Attempt 1 code was already PASSed above.

### Checks (run by reviewer)
- `service postgresql start` then `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: exit 0. ruff "All checks passed!"; ruff format "313 files already formatted"; mypy "Success: no issues found in 146 source files"; pytest "2338 passed, 1 warning in 222.69s" on the first run (no BUG-001 flake, no rerun needed).
- alembic check: not applicable (no migration changes in this attempt).

### Acceptance criteria
- [x] security-review 🟡 fixed: `docs/deploy.md:258-260` now reads "(disable the user; or, if you only revoke admin roles, hold that for at least TTL + 60 s; password reset before re-enabling; emergency key rotation for an immediate global cut)", which is word-for-word the fix the security review asked for.
- [x] Accurate against code: disabling revokes every account session (`app/modules/users/service.py:79-81`, `revoke_all_accounts_of_user`), and admin requests check session revocation, so disable needs no TTL hold. Only a role revoke that leaves sessions alive needs the TTL + 60 s hold before a re-grant. This matches the "Compromised staff account" bullet (`docs/deploy.md:265-269`), so the runbook is now self-consistent.
- [x] Scope: the diff touches only `docs/deploy.md` (one sentence) plus workflow files (`work/backlog.md`, `work/tickets/USR-14/impl-log.md`, `ticket.md`). No code changed.

### Issues
None.

### Rules R1–R11
No violations found.

### Nits (do not block)
- None new.

### Follow-ups (out of scope)
- Unchanged from attempt 1.
