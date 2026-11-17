## Attempt 1

## Security review: USR-14 (session-bound admin tokens). Attempt 1

**Scope:** I read `git diff origin/main...HEAD`, the code it touches (`current_admin`, `AdminAuthService.principal`, `SessionRepository`, `TokenService.issue`, the disable and reset paths, the test fixtures and the route classification test) and the docs diff. As instructed, I did not run `make check` or pytest.

### Findings

**🟡 should-fix: one sentence in the incident runbook is now out of date**
- **Where:** `/home/user/org-admin-api/docs/deploy.md:258`, in the "Lost or shared device" bullet: "(disable, or revoke admin roles, **held for at least TTL + 60 s**; …)".
- **Risk:** After USR-14, disabling a user revokes every account session (`users/service.py:81`), and re-enabling never un-revokes them. So disable no longer has to be held for TTL + 60 s to kill a stolen admin token. The compromised-staff bullet a few lines below (step 1) now says this. An operator reading the first bullet gets the old rule and may keep a staff member locked out for 12 h for no reason. That is operational confusion, not exposure.
- **Fix:** change the parenthesis to "(disable the user; or, if you only revoke admin roles, hold that for at least TTL + 60 s; password reset before re-enabling; emergency key rotation for an immediate global cut)".

**💭 note: device idle and max-age are not checked when a token is used**
- **Where:** `app/modules/sessions/repository.py:219-230` (`is_account_active`) checks only `revoked_at`.
- **Risk:** A device that hits its idle or max-age limit without being revoked keeps its admin tokens alive until `exp` (at most 12 h). This is a deliberate choice, recorded in the ADR-0008 amendment, and the device lifetimes are much longer than the TTL. Accepted.
- **Fix:** none needed.

**💭 note: a request already in flight still completes after a revoke**
- **Risk:** A request that passes the session check before a concurrent revoke commits still finishes. This is the same accepted risk as ADR-0008 ADM-002b, and it is documented in the amendment's Consequence line.
- **Fix:** none needed.

**💭 note: check admin-pan before deploy**
- **Risk:** admin-pan's handling of the new `sid` claim and of the one-time 401 after deploy has not been verified (the repo is not in this workspace). The ticket notes already flag it. Tokens issued before the deploy fail closed, which is correct.
- **Fix:** before deploy, confirm that admin-pan accepts unknown claims and re-mints once on a 401.

**💭 note: test factory, checked and it does not hide the check**
- **Where:** `tests/admin_auth_factories.py`. `make_staff` auto-creates a session, and `claims_for(User)` adds `sid` automatically, so the ~50 existing `mint(staff)` calls always carry a valid `sid`.
- **Why that is fine:**
  - The trigger, isolation and race tests in `/home/user/org-admin-api/tests/test_admin_token_sessions.py` use real tokens from `/auth/token`, not `mint`.
  - The negative tests in `tests/test_admin_auth.py` (missing, malformed, unknown, foreign `sid`; revoked device; lookup error) all use the `client` fixture, which does not override `current_admin`.
  - The `conftest._override_admin` stub is only active when `admin_permissions` is passed. That predates this ticket and is documented.
  - I could not see a way for a regression that drops or weakens the check to pass these tests. No change needed.

### Focus items checked (no issue found)
- **Every admin route is covered:**
  - The check runs in `current_admin` → `principal`, not in `require_permission`, so `GET /api/v1/me/permissions` is covered.
  - `tests/test_route_auth_classes.py` requires `current_admin` on every route except the PUBLIC, COOKIE and APP_CREDENTIAL ones.
  - The trigger tests assert 401 on both `/organizations` and `/me/permissions`.
  - The manifest route (class 4) and the role catalog (public) correctly do not use it.
  - `TokenService.issue` is the only place that signs tokens (one `.sign(` call in `app/`).
- **`sid` is bound to `sub`:** `is_account_active` filters on `AccountSession.user_id == user_id` (repository.py:219-230), and `test_foreign_sid_401` covers it.
- **Missing or malformed `sid` is rejected:** `deps.py:79-83` catches `KeyError` and `ValueError`. `str()` of any JSON value is a string, so `uuid.UUID` can only raise `ValueError`. Unknown ids return false from `EXISTS`, which gives a 401.
- **No fallback-allow and no caching:** `service.py:48-50` has no try/except, so a lookup error becomes a 500. `test_session_lookup_error_is_not_allowed` covers this.
- **Nothing ever un-revokes a session:** there is no `revoked_at = None` path anywhere in `app/`, and `upsert_account` only matches rows with `revoked_at IS NULL`. Disable followed by re-enable therefore stays dead, and a test covers it.
- **Consumer tokens and the shared verifier are unchanged:**
  - `sid` is added only when `app.key == ADMIN_APP_KEY` (`tokens/service.py:99-100`).
  - `app/core/access_token.py` (`REQUIRED_CLAIMS`) and `tests/jwt_verifier.py` are not in the diff, and there is no migration.
  - `test_consumer_token_has_no_sid_and_survives_logout` covers the consumer side.
- **Race between revoke and mint:** the session state is checked when the token is used, so a token minted with a just-revoked session's id dies on its first call. `test_revoke_between_session_read_and_sign_401` covers this.
- **Uniform 401 and clean logs:**
  - Every new path raises `_unauthorized(token_sent=True)`, giving the same envelope and `WWW-Authenticate`.
  - `_deny` logs only the reason, `jti` and `user_id`. It never logs the token, the email or the `sid`.
  - `test_session_denials_logged` asserts that the token and email do not appear in the log.
- **Layering (R1):** the SQL is in the repository, the service raises an `UnauthorizedError` subclass, and `deps` only maps errors to HTTP.
- **Docs and runbook accuracy:** apart from the 🟡 above, the ADR-0004, 0005 and 0008 amendments, consumers.md, deploy.md, identity-architecture.md and spec §5.10 all correctly say these gaps remain:
  - role revoke followed by a re-grant within TTL + leeway;
  - `set-admin-origins --clear`;
  - consumer tokens, which still last until `exp`.

  The "including admin" and "do not end an issued admin token" wording is gone everywhere.

### Follow-ups (out of scope)
- A per-user admin cutoff, bumped when a privileged role is revoked, to close the revoke-then-re-grant window.
- Make `--clear` an immediate admin kill switch.
- Optionally merge the session check into the user lookup to save one query per admin call.
- A pre-deploy check that admin-pan's verifier accepts unknown claims and re-mints once on a 401.

**Verdict: PASS.** There are no blockers; the one 🟡 is a one-line runbook fix that can go in this rework or as a follow-up.
