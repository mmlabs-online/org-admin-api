# Security review — USR-04c

## Attempt 1

**Verdict: no blockers.** The diff (`git diff d589d78 HEAD`) does what the ticket asks. I found no path that builds an email link without a validated origin.

### Findings

- 🟢 **No unvalidated link origin is left.**
  - `get_optional_link_origin` is deleted.
  - The only builder is `EmailVerificationService.prepare(user, link_origin)` (`app/modules/auth/email_verification.py:49,67`). It has two callers:
    - Signup reaches it through `get_signup_hook`, which uses `get_link_origin` (`app/modules/auth/deps.py:84-93`).
    - Resend reaches it through `get_link_origin` (`app/modules/auth/router.py:226`).
  - `get_link_origin` depends on `AppContextDep`. That runs `AppContextService.check` (`app/modules/auth/service.py:80-94`), which accepts only an active app, an Origin that is present and not `"null"`, and a normalized Origin in `allowed_origins(app.origins, app_env)`. Loopback origins are dropped in staging/production.
  - `Host` and `X-Forwarded-Host` are never used.
  - `VerificationEmailHook.link_origin` is now a plain `str`, and the "no link base → mail silently skipped" branch is gone (`app/modules/auth/hooks.py:32-42`).
  - grep finds no `email_link_base_url` / `EMAIL_LINK_BASE_URL` left in `app/` or `.env.example`.
- 🟢 **The 403 comes before any side effect.**
  - The route-level `rate_limit("signup")` runs first, so failed probes still count against the per-IP limit.
  - Next, the `get_signup_hook` → `get_link_origin` → `get_app_context` dependency chain raises the 403 while FastAPI resolves dependencies, before the handler body runs.
  - Only after that does `SignupService.signup` run Turnstile, then the password hash, then the insert, then the hook (`service.py:60-69`).
  - `test_signup_bad_app_context_403_no_side_effects` checks that a 403 means no Turnstile call, no user row and no mail. It covers a missing or malformed key, a disabled app, a missing Origin, a `null` Origin and an unregistered Origin.
  - One side effect of the order: a request with no header and an invalid body now gets 403, not 422. The plan says this is intended, and it is fine.
- 🟢 **The SMTP gate for staging/production has not regressed.**
  - `_email_gate` still requires `SMTP_USERNAME` and `SMTP_PASSWORD` in staging and production (`app/core/config.py:187-200`), and the parametrized test still covers both.
  - The test that error messages do not leak secrets is kept.
  - A leftover `EMAIL_LINK_BASE_URL` in the env is ignored because of `extra="ignore"` (`config.py:22`), and `test_stray_email_link_base_url_ignored` proves it.
- 🟢 **CORS:** the preflight already allows `X-MM-App` with credentials (`app/core/cors.py:84`), so browser signups that send the header work. `docs/consumers.md` tells callers to use `credentials: "include"`.
- 🟢 **Allowing admin signup (assumption A2) is acceptable.**
  - Signup grants nothing. An `aud=admin` token still needs the admin path and roles, and `tokens/service.py:58` refuses `admin` on `/token`.
  - The `admin` app's origins pass the same registered-origin check.
  - Refusing `admin` only on signup would split the D12 rule. The remaining cost is operational: the admin UI origin must serve `/verify-email`, and the docs state that rule.
- 💭 **Docs are accurate.**
  - The ADR-0007 §2 amendment, the `consumers.md` contract-change note, the `deploy.md` key removal and gate sentence, and the identity-architecture §4 signup row all match the code.
  - Optional cleanup: ADR-0007 §2 still states the old fallback sentence before the amendment. Marking it as superseded (for example with strikethrough) would make it easier to read. Not required.
  - Cosmetic: `docs/consumers.md` has one over-long line ("…once USR-04b lands). The token is in the fragment…").

### Follow-ups (out of scope)
- Owner: drop the `EMAIL_LINK_BASE_URL=` line from the `mmlabs-online/deployment` compose (`apps/org-admin-api/compose.yml`). It is harmless meanwhile.
- USR-04b (forgot password) must reuse `get_link_origin` / `AppContextDep` in the same way, with the 403 before Turnstile, token issue and mail. This is already noted on the USR-04b ticket.
- Operational: when an origin is registered for an app (for example in the admin UI), remind the operator that every registered origin must serve `/verify-email`, and later `/reset-password`. Otherwise users get a broken link and no security failure shows up.
