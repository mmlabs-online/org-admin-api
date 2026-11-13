# Review — USR-04c

## Attempt 1 — 2026-11-13
Verdict: PASS

Diff reviewed: `git diff d589d78 HEAD` (commit 396e6b3; 16 files, +154 / -131).

### Checks (run by reviewer)
- make check (`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, local Postgres accepting connections): ruff `All checks passed!`; format `300 files already formatted`; mypy `Success: no issues found in 77 source files`; pytest `855 passed in 26.10s`. Matches impl-log (855 passed). Format count is 300 here vs 299 in the impl-log; the file is not from this diff (working tree clean) and it does not matter.
- alembic check: n/a (no schema change, no migration in the diff).
- `grep -rn "EMAIL_LINK|email_link_base|get_optional_link_origin" app tests .env.example`: only `tests/test_config.py:229-233` (the stray-env test).

### Acceptance criteria
- [x] Signup requires app context; missing/unknown/disabled app or bad Origin → 403 before any user, Turnstile call or email. Evidence: `app/modules/auth/deps.py:84-93` (`get_signup_hook` depends on `get_link_origin` → `AppContextDep`; resolved during dependency resolution, before `SignupService.signup` runs Turnstile/hash/insert); `tests/test_email_verification_api.py › test_signup_bad_app_context_403_no_side_effects` (8 cases: no headers, Origin without key, `Bad Key!` → `app_header_required`; unknown, disabled `frozen`, no Origin, `Origin: null`, unregistered origin → `origin_not_allowed`; asserts 0 users, `fake_turnstile.calls == []`, `fake_email.calls == 0`, send log 0). Passes.
- [x] Link origin is always the validated request Origin; `EMAIL_LINK_BASE_URL` removed; leftover value ignored. Evidence: `app/modules/auth/hooks.py:32` (`link_origin: str`, None branch removed); `app/core/config.py:75-82,187-200` (field, validator and gate entry removed; unused import dropped); `.env.example` key removed; `tests/conftest.py` env line removed; `test_signup_sends_verification_link_to_app_origin`, `test_link_ignores_host_and_forwarded_host` (both links == `ORIGIN`, never Host/X-Forwarded-Host); `tests/test_config.py › test_stray_email_link_base_url_ignored` (staging + production, `extra="ignore"`). `test_smtp_required_in_production` still covers the SMTP_USERNAME/SMTP_PASSWORD gate. All pass.
- [x] Resend behaviour unchanged. Evidence: no resend code touched; existing resend tests in `tests/test_email_verification_api.py` pass without edits.
- [x] Tests updated (signup without header → 403, no user/no mail; with header → app origin; existing signup tests send the header; config tests). Evidence: `tests/test_auth_signup.py` autouse `stoktok` fixture, every `post(URL, …)` sends `auth_headers()`; `test_signup_failure_sends_nothing`, `test_signup_response_does_not_wait_for_sender` send headers; config tests as above.
- [x] Docs: `docs/consumers.md:63-75` (required header + Origin, 403 codes, `credentials: "include"`, every registered origin serves `/verify-email`, v1 contract-change note); `docs/deploy.md:109-138` (key row removed, start-up gate names SMTP only, compose line can be dropped); `docs/adr/0007-email-verification-and-delivery.md:21-25` (amendment note); `docs/identity-architecture.md:131` (§4 signup row).
- [x] Forgot password (USR-04b) follows the same rule. Evidence: `work/tickets/USR-04b/ticket.md:45` (owner note, 2026-11-13).

### Issues
None.

### Rules R1–R11
No violations found.
- R1: the router only declares dependencies; the check stays in `AppContextService` (via deps); no ORM in the router.
- R3: signup stays class 3 (public, rate limit, Turnstile) plus the Origin/`X-MM-App` check; `rate_limit("signup")` still runs first as a route dependency, so 403 probes still count against the per-IP limit.
- R5: key removed from config and `.env.example`; config errors still name keys, not values (`test_smtp_required_in_production` asserts the marker is not echoed).
- R6: existing error codes (`app_header_required`, `origin_not_allowed`) via the shared envelope.
- R7: every changed file is in the plan's file list (work/ bookkeeping aside); no out-of-plan code.
- R8: green `make check`; 403 and side-effect cases covered.
- R9: contract change inside v1 is documented and justified (no consumers yet; owner decision in ticket).
- R11: POST that starts an email flow now checks Origin and requires `X-MM-App`, like login and resend.

### Nits (do not block)
- `app/modules/auth/router.py:66-74`: the app-context gate on signup is only reached through `get_signup_hook` → `get_link_origin`. Any future override or refactor of the hook dependency (as `tests/test_auth_signup.py › test_on_signup_hook_called_with_user_id` does in tests) silently drops the 403 gate. An explicit `_app: AppContextDep` parameter on the route, or a route-level dependency, would make the gate independent of email wiring. FastAPI caches the dependency, so there is no double query.
- `docs/adr/0007-email-verification-and-delivery.md:19-20` still states the old fallback above the amendment. That is fine for an amendment note; an inline "(superseded, see below)" would help readers.
- `docs/consumers.md:74-75`: one rewrapped line is longer than the surrounding ~100-column wrap.
- Expected behaviour change (plan Approach 1): a request with no header and an invalid body now gets 403, not 422. It is documented in the plan but not pinned by a test.

### Follow-ups (out of scope)
- Owner: drop `EMAIL_LINK_BASE_URL` from `mmlabs-online/deployment` `apps/org-admin-api/compose.yml` (harmless meanwhile; plan A3).
- Consider the explicit route-level `AppContextDep` on signup (nit 1) when USR-04b adds forgot-password with the same rule, so both endpoints declare the gate the same way.
- Plan A2 (`X-MM-App: admin` accepted on signup): owner may still choose to refuse; one-line check if so.
