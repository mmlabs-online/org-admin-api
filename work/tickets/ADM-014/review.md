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

## Attempt 2 — 2026-11-16
Verdict: PASS

### Checks (run by reviewer)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: green. ruff clean, `225 files already formatted`, mypy `Success: no issues found in 102 source files`, pytest `1736 passed, 1 warning in 120.95s`. The warning is the existing `forkpty()` DeprecationWarning. I did not reproduce the `test_purge_batches_share_cutoff` flake that impl-log reports for its first run. That test is unrelated to this docs-only diff.
- alembic check: n/a (no migrations changed).
- Scoped grep `grep -rnE '(^|[^-[:alnum:].])admin\.mmlabs\.online' docs README.md CLAUDE.md Makefile`: 6 hits, all "not used / must not exist / superseded / clean-up" statements: `docs/deploy.md:209,559,560,566` and `docs/adr/0008-admin-api-authentication.md:56,59`. This matches impl-log attempt 2.
- `grep -n 'so CORS answers the preflight' docs/consumers.md`: no hits (rc=1).
- `sh -n` on the 21-line admin-token runbook block (the "Log in, get a token" `sh` fence, from `(` to `)`): rc=0.
- `bash -n` on the extracted `set-admin-origins` bash block: rc=0.
- `git diff --name-only origin/main...HEAD`: `Makefile`, `docs/adr/0008-…`, `docs/consumers.md`, `docs/deploy.md`, plus `work/` files only. Attempt 2 (`git diff 1e65a16 HEAD`) touches only `docs/consumers.md`, `docs/deploy.md`, `impl-log.md`, `ticket.md` and `backlog.md` (state rows).

### Acceptance criteria
- [x] AC1: deploy.md uses `https://admin-portal.mmlabs.online` throughout, and `admin.mmlabs.online` must have no DNS record or tunnel route. Evidence: `docs/deploy.md:434,447,456,458,507,510,519-520` (unchanged since attempt 1) and Host policy `:553-567`. Attempt 2 adds three things: a concrete `dig +short` check, a catch-all `service: http_status:404` clause, and an unconditional dry-run clean-up with a 60 s wait and the 15 min token note.
- [x] AC2: ADR-0008 dated note and superseded marker at `docs/adr/0008-admin-api-authentication.md:55-60`. `Makefile:29` help line updated. The scoped grep is clean apart from the "must not exist" notes.
- [x] AC3: consumers.md uses `omit` for signup, forgot, verify-email and reset, and `include` for login and cookie-class calls. Attempt 1 Issue 1 is fixed:
  - `docs/consumers.md:42-43` now reads "Only login creates the cookie; every cookie-class call may rotate it (R11), and logout or a 401 clears it." This matches `app/modules/auth/router.py` (rotation via `set_device_cookie`) and `deps.py` (a 401 clears the cookie).
  - `docs/consumers.md:119-120` now reads "sign-up neither reads nor sets the cookie; only login creates it". Both lines are exactly as instructed.
- [x] AC4: no code change (no `app/`, `tests/` or `migrations/` in the diff). The runbook blocks pass `sh -n` / `bash -n`.

### Issues
None.

### Rules R1–R11
No violations found. Docs only.
- R3: auth classes unchanged.
- R5/R11: no secrets added. The new deploy.md text keeps the existing replace-semantics, 60 s CORS cache and 15 min TTL facts consistent with the `--clear` paragraph (`docs/deploy.md:548-551`).
- R7: every changed file is in the plan. The deploy.md attempt-2 edits apply the attempt-1 nits and the appsec notes inside the planned Host policy paragraph.

### Nits (do not block)
- `docs/consumers.md:42` is now 136 characters, well past the ~100-column wrap of the surrounding paragraph (`:119` is 103). Reflow it when you next touch the file.
- `docs/deploy.md:563-564`: "Reusing it needs an owner decision." is followed by "One-off clean-up: run" on a new source line with no blank line. It renders fine, but a blank line would make the clean-up its own paragraph.

### Follow-ups (out of scope)
- Unchanged from attempt 1: plan F-1 to F-5; CLAUDE.md R11 cookie name/path drift (`__Host-mm_device`, `Path=/`); test constants that still use `https://admin.mmlabs.online`; optional `set-admin-origins` refusal of the old host (code change, needs an owner decision).
- `tests/test_cli_audit_purge.py::test_purge_batches_share_cutoff` may be timing-sensitive under load (impl-log attempt 2). It passed in the reviewer run. Track it if it recurs.
