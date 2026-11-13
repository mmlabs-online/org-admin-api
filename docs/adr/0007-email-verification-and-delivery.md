# ADR-0007: Email verification links and delivery

Status: Accepted (USR-04 plan, 2026-11-12)
Date: 2026-11-12

## Context
D2 requires a verified email before joining an app or getting an app token; D16 rules out any
interim "mark verified" path. D3 picks Gmail SMTP behind an `EmailSender` interface, async with
retry, resend limited to 1/min and 5/day per email. There is no hosted login UI (R10), so a link
must open a page of one of the apps. The owner asked (2026-11-12) to reuse the Gmail account the
mmlabs site already sends from, which changes D3's "dedicated account".

## Decision
1. **Link format.** `{origin}/verify-email#token=<token>` (USR-04b: `/reset-password`). The path
   is the same for every app (no per-app column). The token is in the fragment and is consumed
   by `POST /api/v1/auth/verify-email {token}` (public, per-IP limit); `GET` never consumes it, so
   mail scanners cannot use it up and there is no redirect.
2. **Link origin.** Sign-up and resend use the request's `Origin` once it is proven to be a
   registered origin of the `X-MM-App` app (the CSRF check). Sign-up without `X-MM-App` uses
   `EMAIL_LINK_BASE_URL` (a D19 origin from env). Never `Host` or `X-Forwarded-Host`.
   *Amended by USR-04c (2026-11-13):* sign-up **requires** the app context (`X-MM-App` + a
   registered `Origin`, 403 otherwise, before any account is created), like login and resend.
   `EMAIL_LINK_BASE_URL` is removed: many apps share this service, so one fallback origin would
   send users of other apps to the wrong app. Every registered origin of an app must serve the
   link pages (`/verify-email`, later `/reset-password`).
   *Amended by USR-04b (2026-11-13):* `POST /auth/password/forgot` also **requires** the app
   context (owner decision); the reset link origin is the validated `Origin`. The 403 comes before
   Turnstile and before any write.
3. **Token model.** `secrets.token_urlsafe(32)` (256 bit); only SHA-256 is stored in
   `email_tokens`. Single use via one atomic `UPDATE … WHERE used_at IS NULL AND expires_at >
   now() RETURNING`. Issuing a token supersedes the user's older unused tokens of the same purpose.
   TTL: verify 24 h (reset 30 min, USR-04b). Unknown, expired, used and superseded tokens get one
   422 `token_invalid`; a token of an already verified user answers 204 (idempotent).
   *Amended by USR-04b (2026-11-13):* reset link `{origin}/reset-password#token=…`, TTL 30 min.
   `POST /auth/password/reset` looks the token up without consuming it, checks the password policy
   (a weak password keeps the link usable), hashes, then in one transaction locks the user row,
   consumes the token, sets the password, revokes **all account sessions** of the user (not the
   device sessions, which may carry other users' accounts), supersedes the user's other reset and
   verify tokens, clears the login backoff and marks the email verified. A disabled user gets 422
   `token_invalid` and the token is not consumed.
4. **Send limits.** `email_send_log` keyed by HMAC-SHA256(`IDENTITY_HMAC_KEY`, normalized email),
   no email stored. Per email and purpose: 1 per 60 s, 5 per rolling 24 h; sign-up counts as the
   first send. A global `EMAIL_DAILY_CAP` (default 300) protects the shared Gmail quota. Checks
   run under a transaction-scoped advisory lock per (purpose, email), so concurrent resends
   serialize. When limited, sign-up still succeeds without an email; resend answers 429 with
   `Retry-After` (3600 s for the global cap).
   *Amended by USR-04b (2026-11-13):* forgot always answers 202; its per-email limit (purpose
   `reset`) applies to every email, known or not, and is silent. An accepted forgot writes one
   `email_send_log` row for every email; only active users get a token and a mail. The global cap
   now counts real sends (tokens issued in the last 24 h, so unknown-email forgot never counts)
   per purpose: verification may use at most 80 % of `EMAIL_DAILY_CAP` (at least 1), reset uses
   the rest, so sign-up spam cannot block reset mail. The count runs under one global
   transaction-scoped advisory lock. A capped forgot answers 202 and logs
   `email_not_sent reason=daily_cap purpose=reset`. Per-IP limits: forgot 5/60 s, reset 10/60 s,
   resend 5/600 s.
5. **Delivery.** An in-process `asyncio.Queue` (max 1000) with one worker, fed by a FastAPI
   background task, i.e. only after the request's transaction committed. Up to 3 attempts with
   2 s / 8 s backoff (±20 %) for transient errors (4xx, timeouts, disconnects); permanent 5xx are
   not retried; Gmail quota (`550 5.4.5`) or authentication failure pause sending for 1 h and
   drop mail meanwhile. The raw token exists only in that queued message: a restart loses queued
   mail and the user presses resend. A database outbox was rejected: it would need the token
   stored encrypted under another key, not worth it at v1 volume.
6. **SMTP transport.** stdlib `smtplib` in a worker thread (no new dependency): STARTTLS always
   required with `ssl.create_default_context()` (certificate and hostname checked), 15 s timeout,
   `EmailMessage` (refuses header injection). Errors become `EmailDeliveryError(kind, smtp_code)`
   raised outside the original handler, so the SMTP reply (which echoes the recipient) never
   reaches a traceback or log. Logs carry event, purpose, user id, attempt and SMTP code only.
7. **Account.** The shared Gmail account of the mmlabs site, with a separate App Password for this
   service (revocable on its own). Credentials only in the deploy environment; staging/production
   refuse to start without them.

## Consequences
- Every app that triggers sign-up or resend with `X-MM-App` must serve `/verify-email`.
- Quota and sender reputation are shared with the mmlabs site (accepted risk, deviation from D3).
- Mail queued at a restart is lost; resend limits allow a retry after a minute.
- Changing provider is a new `EmailSender` plus env keys; no API or schema change.
- Retention cleanup of `email_send_log` and used tokens is a follow-up (USR-04d).
- Every app that offers "forgot password" must serve `/reset-password` (USR-04b).
- Accepted risks (USR-04b): forgot still differs slightly in timing for known users (the token
  insert and the enqueue); access tokens issued before a reset stay valid ≤ 15 min; data an
  attacker created under a pre-registered email (e.g. memberships) stays after the owner resets
  the password.
