# consult-security-architect — USR-04

**Findings**
1. The deploy workflow rewrites the env file from scratch on every run (`deploy-org-admin-api.yml`, "envfile" step), and the Mac is reachable only through Actions. SMTP credentials therefore have to come through the workflow. A value added to the file by hand would be lost on the next deploy.
2. Sign-up still returns 409 `EmailTakenError` for an existing email (`service.py:66`). That is an enumeration oracle, and making forgot always return 202 does not close it (F2 is still open).
3. One AC conflicts with another. "429 beyond the limit" applied to `forgot` contradicts "always 202": an unknown email never reaches its limit, so a 429 would show that the email has an account.
4. The sign-up hook runs inside the transaction and its docstring says "no slow I/O". The plaintext token must never be stored, so the email can only be sent after commit, from memory.
5. There is no hosted UI (R10). The links have to point at an app page, and nothing yet defines which origin that is.

**Recommendations**
- **Tokens (R11, S6):** generate with `secrets.token_urlsafe(32)` (256 bits). Store only `sha256(token)` and look rows up by hash. Consume with one atomic `UPDATE … SET used_at=now() WHERE token_hash=:h AND purpose=:p AND used_at IS NULL AND expires_at>now() RETURNING user_id`. Suggested TTLs: verify 24 h, reset 30–60 min. Issuing a new token marks older unused tokens for the same user and purpose as used. Store the email's HMAC on the token row, and reject the token if the user's email has changed since.
- **Consume by POST only:** the link opens an app page with `#token=…` in the URL fragment, which stays out of server logs and the Referer header. The page then POSTs the token, so email-scanner prefetch cannot use it up. The API never redirects. If a `next` parameter is ever added, check it against `apps.origins`.
- **Link building:** build links only from configuration (`EMAIL_LINK_BASE_URL`, an https origin inside the D19 allowlist) or from an origin registered for the app in `X-MM-App`. Never use the request's Host, X-Forwarded-Host or Origin.
- **Enumeration:** `forgot` always returns 202 and applies limits silently (no send). Count attempts per email HMAC whether or not the account exists. Return 429 only on cookie-authenticated `verify-email/resend`, after checking that `account_id` belongs to this device session (same no-oracle rule as logout). Return 202 the same way for disabled or unknown users and for "already verified".
- **Email bombing:** limit per email (1/min, 5/day), per IP (~20/min on forgot, reset and verify) and Turnstile on forgot. Add a global daily cap set below the Gmail quota (around 400 a day) to protect the sender. `email_send_log.email_hash` should be an HMAC with `IDENTITY_HMAC_KEY`, not a plain SHA-256.
- **Reset:** apply the USR-01 password policy, then in one transaction: hash the password, revoke all of the user's account sessions on every device, invalidate the user's other reset tokens and clear the `login_failures` row for that email. Optionally set `email_verified_at`, since completing a reset proves control of the inbox (owner decision).
- **SMTP:** use `aiosmtplib` with `start_tls=True` and certificate validation on the default context, and confirm the image has CA certificates. Fail if STARTTLS is not offered, and set timeouts. Build messages with `EmailMessage`, which rejects CR/LF in headers. Templates autoescape. The From address must be the Gmail account itself, or the message will fail DMARC.
- **Retry:** retry 4xx responses with backoff (3 attempts). Treat 5xx as permanent. For 550 5.4.5 / 421 4.7.x (quota exceeded), open a circuit breaker for about 24 h and log `email_quota_exceeded`. The runbook should say "wait or switch the `EmailSender` provider via env".
- **Logging (R5/R11):** log event names, user id, purpose and SMTP code only. Never log the token, link, password or full address; mask it (`m***@gmail.com`). Do not log `str(exc)` from SMTP exceptions, because they echo recipients.
- **Config:** add `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD` (SecretStr), `EMAIL_FROM` and `EMAIL_LINK_BASE_URL`. All are required in staging/production by the same kind of model validator as the existing keys.
- **Deploy:** store `ORG_ADMIN_SMTP_USERNAME` and `ORG_ADMIN_SMTP_PASSWORD` as GitHub secrets in the deployment repo. Put them in an Environment that only `main` can deploy to, pass them to the envfile step through `env:` and write them with `printf`. Validate them before writing (address regex; App Password `^[a-z]{16}$` once spaces are removed) so a newline cannot inject extra lines into the env file. Fail before `pull` with a clear message if either is missing. Update the "No GitHub secrets" comment and the README. Create a separate App Password for this service, so it can be revoked without affecting the mmlabs site.

**Risks**
- A Gmail App Password gives full access to the mailbox (IMAP and send), so leaking it is a high-impact event. Anyone with write access to the deployment repo can read the secret through a workflow on the self-hosted runner.
- The mmlabs site and this service share one Gmail quota and one sender reputation, and outages hit both.
- After the commit, sending happens in process memory. A restart loses queued mail, and the only recovery is the user asking to resend. This is acceptable for v1; a DB outbox would fix it.
- Timing on `forgot` still differs slightly between existing and unknown emails (one extra DB write). The risk is low.

**Open questions**
1. Should the link origin come from a single config value or from each app's registered origin, and which app page handles verify and reset?
2. Should a successful reset set `email_verified_at`?
3. Is the deployment repo private, who has write access, and can Environments with required reviewers be used?
4. The duplicate-email behaviour on sign-up (F2) needs an owner decision: keep the 409, or return 201/202 and send "you already have an account" to the existing address?
5. Is the shared Gmail account the "dedicated account" that D3 requires?
