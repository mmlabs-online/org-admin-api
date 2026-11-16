## Attempt 1 — 2026-11-16
Verdict: REWORK

### Checks (run by reviewer)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` (ruff, format, mypy, pytest): green, `1736 passed, 1 warning in 115.30s`. The warning is the existing `forkpty()` DeprecationWarning in `tests/test_cli_create_account.py`. This matches impl-log.
- alembic check: n/a (no migrations changed).
- Scoped grep `grep -rnE '(^|[^-[:alnum:].])admin\.mmlabs\.online' docs README.md CLAUDE.md Makefile`: 6 hits. All are "not used / must not exist / superseded" statements: `docs/deploy.md:209,559,562,564` and `docs/adr/0008-admin-api-authentication.md:56,59`.
- `grep -n 'so CORS answers the preflight' docs/consumers.md`: no hits (rc=1).
- `sh -n` on the admin-token runbook block (21 lines, extracted from the "Log in, get a token" `sh` fence): rc=0.
- `bash -n` on the extracted `set-admin-origins` bash block(s): rc=0.
- `git diff --name-only origin/main...HEAD`: `Makefile`, `docs/adr/0008-…`, `docs/consumers.md`, `docs/deploy.md`, plus `work/` ticket files only. No `app/`, `migrations/` or `tests/` change.

### Acceptance criteria
- [x] AC1 deploy.md host policy, runbook and examples use `https://admin-portal.mmlabs.online`, and `admin.mmlabs.online` must have no DNS or tunnel route. Evidence: `docs/deploy.md:434` (step 4), `:447` (trap logout Origin), `:456`/`:458` (login/token Origin), `:507`, `:510`, `:519-520`, and Host policy `:553-564`. The runbook diff changes hostname text only.
- [x] AC2 ADR-0008 amended with a dated note, and the old text is marked superseded (`docs/adr/0008-admin-api-authentication.md:55-60`). The Makefile help line is updated (`Makefile:29`). The scoped grep is clean apart from the "must not exist" notes.
- [ ] AC3 consumers.md: forgot, verify-email and reset (and signup) use `credentials: "omit"`. The guide no longer recommends `include` for them (`docs/consumers.md:43-44,118,137,168,192`). However, the rewritten credentials rule adds a sentence that contradicts the code. See Issue 1.
- [x] AC4 No code change, and the runbook blocks pass `sh -n` / `bash -n` (see Checks).

### Issues
#### Issue 1 — 🔴 blocker — doc correctness (this sentence is new in the diff)
- **Where**: `docs/consumers.md:42` ("Only login sets the cookie."). Related: `docs/consumers.md:118-119` ("sign-up sets no cookie; only login does").
- **Expected**: The integration guide matches the code. Every cookie-class route can send `Set-Cookie`:
  - `/token`, `/accounts`, `/apps/{key}/join`, `/logout` and `/verify-email/resend` call `set_device_cookie` on rotation (`app/modules/auth/router.py` token ~l.199, accounts ~l.229, logout ~l.170, resend ~l.325, join).
  - `/logout {all:true}` clears the cookie.
  - A 401 from `get_device` clears it.
  - R11 rotation ("at most every ~24 h") runs on these calls, not only on login.
- **Actual**: Line 42 says only login sets the cookie. This contradicts the sentence just before it (`omit` makes the browser drop `Set-Cookie` on cookie-class calls) and the code. Appsec flagged the same line (security-review.md, 🟡). A ticket whose deliverable is correct consumer guidance should not ship a new false statement.
- **Fix instruction**:
  - In `docs/consumers.md:42`, replace "Only login sets the cookie." with "Only login creates the cookie; every cookie-class call may rotate it (R11), and logout or a 401 clears it."
  - In `docs/consumers.md:118-119`, change "sign-up sets no cookie; only login does" to "sign-up neither reads nor sets the cookie; only login creates it".
  - Change nothing else. Re-run the scoped grep and `make check`.

### Rules R1–R11
No violations found. Docs only: R3 auth classes are unchanged, and the consumers.md split matches the router (login and `DeviceDep` routes read the cookie; signup, verify-email, forgot and reset do not). R5/R11: no secrets added, and the runbook credential handling is unchanged. R7: all changed files are in the plan.

### Nits (do not block)
- `docs/deploy.md:559-561` "no tunnel ingress or catch-all route": cloudflared requires a final catch-all rule. Wording such as "no ingress rule for it; the catch-all must be `service: http_status:404`" would be followable (appsec note).
- `docs/deploy.md:562-564` one-off clean-up: run the dry run unconditionally, add "wait 60 s" after the real run, and mention that issued admin tokens stay valid up to 15 min (appsec note).
- `docs/consumers.md:118` and `:168-169`: reflowed lines are now longer than the surrounding wrap width.

### Follow-ups (out of scope)
- Plan F-1 to F-5 (production `apps[admin].origins` state, wildcard DNS / catch-all, `@mmlabs/auth-client` credentials audit, identity-architecture.md diagram, deployment repo `TURNSTILE_ALLOWED_HOSTNAMES`).
- CLAUDE.md R11 still says `mm_device` / `Path=/api/v1/auth`, but the code uses `__Host-mm_device` / `Path=/` (already tracked from ADM-012).
- Test constants still use `https://admin.mmlabs.online` as the sample admin origin (`tests/test_admin_guards.py`, `test_auth_app_context.py`, `test_auth_join.py`, `test_cli_set_admin_origins.py`, `test_cli_register_app.py`).
- Optional hardening: `set-admin-origins` could refuse `https://admin.mmlabs.online` in staging/production (code change, needs an owner decision).
