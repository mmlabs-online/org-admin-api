# consult-security-architect — USR-04c

**Findings**
- Ordering is already correct. The route-level `rate_limit("signup")` runs first. Then `get_signup_hook` → `get_optional_link_origin` → `AppContextService.check` raises 403 while dependencies resolve. All of that happens before `SignupService.signup`, which calls Turnstile, hashes, inserts and runs the hook. So a 403 means no Turnstile call, no write and no mail. Failed 403 probes still count against the per-IP limit, which is good.
- Origin binding: a script can forge `Origin`, but the check only accepts origins registered for an active app's `X-MM-App`, with loopback filtered in staging/production. The worst a forger can do is choose which legitimate mmlabs app hosts the victim's link. The token stays in the fragment and verification is a POST, so this risk is the same as before the change.
- CSRF: requiring the custom header forces a CORS preflight, so a cross-site browser can no longer make a "signup-as-victim" request. This is a net gain. Signup sets no cookie, so it does not work like a login-CSRF.
- Enumeration is unchanged. The 409 remains, and the header adds no real barrier for scripts. Rate limiting and Turnstile still cover it (see USR-04 N6).
- `Settings` uses `extra="ignore"`, so a leftover `EMAIL_LINK_BASE_URL` in the deployment env is harmless.

**Recommendations**
- AC1/AC2: switch signup to `get_link_origin` (non-optional `str`) and delete `get_optional_link_origin`. Make `VerificationEmailHook.link_origin: str` and remove the `None` → "no mail" branch, so mail can never be skipped silently.
- AC2: in `_email_gate`, keep the staging/production requirement for `SMTP_USERNAME`/`SMTP_PASSWORD` and drop only the link key. Add a test that a stray `EMAIL_LINK_BASE_URL` env value loads and is ignored.
- AC4: add one test where an unregistered `Origin` with a valid key returns 403, with no user row and no Turnstile call. Use the fake verifier's call count to check this.
- Deployment repo: drop the compose line. No rush, since the leftover value is ignored.

**Risks**
- Every registered origin of every app must serve `/verify-email` (D19). If one does not, users from that origin get a broken link. Document this in `docs/consumers.md`.
- Breaking v1 contract change. There are no consumers yet, so the impact is low.

**Open questions**
- Should signup with `X-MM-App: admin` be refused (403)? Admin has no end-user signup page, and D17 already treats `admin` specially.
