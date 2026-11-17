
# Attempt 1

## Files touched
Production:
- `app/modules/tokens/service.py`: `sid` claim for `aud=admin` only (plan: mint).
- `app/modules/sessions/repository.py`: `is_account_active` (R1: SQL in the repository).
- `app/modules/admin_auth/service.py`: `AdminSessionEndedError`, `principal(..., session_id=)` check, fail closed.
- `app/modules/admin_auth/deps.py`: parse `sid`, map to the same 401, log reasons `sid` / `session`.
- `app/modules/users/router.py`: `_REVOKE_LIMITS` route docs.

Tests:
- `tests/admin_auth_factories.py`: `admin_session`, `_SIDS`, `claims_for` sid default, `make_staff` creates a session.
- `tests/test_admin_auth.py`: two `admin_session` calls; new missing/malformed/unknown/foreign sid, revoked device, lookup error, logging tests.
- `tests/test_admin_token_sessions.py` (new): six trigger tests, same-user other session, shared device, race, consumer unchanged.
- `tests/test_auth_token.py`: admin claim set plus `sid`.

Docs: `docs/adr/0008-admin-api-authentication.md`, `docs/adr/0005-access-token-signing.md`, `docs/adr/0004-device-sessions-and-login.md`, `docs/consumers.md`, `docs/deploy.md`, `docs/identity-architecture.md`, `docs/spec-v1.md` (§5.10 only).

Not touched: `app/core/access_token.py`, `migrations/`, `.env.example`, `app/core/config.py`.

## Checks (final tree)
`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: exit 0; ruff, format, mypy green; `2338 passed, 1 warning in 228.90s`. No BUG-001 flake occurred. (An earlier run failed lint on import order; fixed with `ruff check --fix`, then the full check was re-run.)
Grep for "including admin" / "do not end an issued admin" in docs and app: no matches.

## Out-of-plan files
None.

## Follow-ups
As listed in plan.md (role revoke re-grant cutoff, `--clear` kill switch, consumer sid, fold session check into user query). Pre-deploy check: admin-pan verifier accepts unknown claims.

## Scope self-check
Only planned files; no commit made.

# Attempt 2 (REWORK)

Fixed the single item from security-review.md "Attempt 1" (docs only).

## Files touched
- `docs/deploy.md` ("Lost or shared device" bullet): the parenthesis now reads "disable the user; or, if you only revoke admin roles, hold that for at least TTL + 60 s; password reset before re-enabling; emergency key rotation for an immediate global cut". Required because the old wording implied a disable must be held for TTL + 60 s.

## Checks
`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: exit 0; `2338 passed, 1 warning in 228.97s`. No BUG-001 flake.

## Out-of-plan files
None.

## Follow-ups
None new.

## Scope self-check
One docs sentence changed; no commit made.
