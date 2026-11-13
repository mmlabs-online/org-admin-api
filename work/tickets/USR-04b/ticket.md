# USR-04b: Password forgot/reset with all-session revocation

| Field | Value |
|---|---|
| State | DONE |
| Priority | P1 |
| Areas | auth, security, api |
| Attempts | 0 |
| Created | 2026-11-12 |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/22 |

## Goal
A user who forgot their password gets a one-time reset link (via the USR-04 `EmailSender` and dispatcher) and sets a new password; every signed-in session of that user ends.

## Spec reference
§5.8 (Email flows); identity-architecture.md §4, S6; D3, D11; split from USR-04 (`work/tickets/USR-04/plan.md` → "Split proposal"). No migration (`purpose` CHECKs already allow `reset`).

## Acceptance criteria
- [ ] `POST /api/v1/auth/password/forgot {email, turnstile_token}` → always **202** (no enumeration),
  Turnstile always required (403 `turnstile_failed` on failure), per-IP limit scope `forgot`, optional
  `X-MM-App` (A1 rule) for the link origin; link `{origin}/reset-password#token=…`, TTL 30 min.
- [ ] Per-email limit 1/min and 5/day on purpose `reset`, counted per `email_hmac` for **every** email
  (known or unknown) and applied **silently** (still 202, nothing sent); 429 only from the per-IP
  limit. (Spec §4 "always 202" wins over the ticket's "→ 429 beyond" for forgot; flagged.) Same DB
  writes on both branches to limit timing differences; unknown/disabled users get no mail.
- [ ] `POST /api/v1/auth/password/reset {token, new_password: SecretStr}` (public, per-IP scope
  `reset`): look up the token without consuming → USR-01 password policy (422 `weak_password` keeps
  the link usable) → consume atomically → Argon2id hash off the loop → in one transaction: set
  password, revoke **all** non-revoked `account_sessions` of the user on every device (new
  `SessionRepository.revoke_all_accounts_of_user`), supersede other reset tokens, delete the
  `login_failures` row for the email HMAC, set `email_verified_at` if null (A8) → 204, no cookie, no
  login. Unknown/expired/used token or disabled user → 422 `token_invalid`.
- [ ] Tests with the fake sender: forgot same 202 for known/unknown/limited emails, Turnstile 403, per-IP
  429; reset happy path + all sessions revoked (`/auth/token` → 401 for every device), weak password
  leaves token usable, reused/expired token 422, `login_failures` cleared; logs free of token/address.
- [ ] Docs: `docs/consumers.md` reset page; `docs/deploy.md` super-admin bootstrap "then reset the
  password"; note that issued access tokens stay valid ≤ 15 min after a reset.

## Dependencies
USR-04, USR-04c.

## Notes / answers from owner
- 2026-11-13 (USR-04c review nit): put `AppContextDep` on the route itself (signup and forgot), not only inside the email-hook dependency, so the 403 cannot disappear if the hook dependency is overridden.
- 2026-11-12 (USR-04 security review S1/S2): add a per-IP rate-limit scope on `/auth/verify-email/resend`; split or reserve the global `EMAIL_DAILY_CAP` per purpose so sign-up spam cannot block reset mail (e.g. verify ≤ 80 %); optionally serialize the global count with a global advisory lock; runbook: alert on `email_not_sent reason=daily_cap`. Also: wire `EmailDispatcher.aclose()` into an app lifespan; retention cleanup for `email_send_log`/`email_tokens`.
- 2026-11-12: created by the orchestrator from the USR-04 planner's split.
- 2026-11-13 (owner, USR-04c): forgot requires `X-MM-App` + Origin like signup; the reset link origin is the request's validated Origin; there is no `EMAIL_LINK_BASE_URL` fallback.
- 2026-11-13: planner moved dispatcher aclose, retention cleanup and the daily-cap alert runbook to **USR-04d**. Forgot requires X-MM-App + Origin (owner note overrides the AC's 'optional', plan deviation).
