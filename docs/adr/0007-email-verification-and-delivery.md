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
3. **Token model.** `secrets.token_urlsafe(32)` (256 bit); only SHA-256 is stored in
   `email_tokens`. Single use via one atomic `UPDATE … WHERE used_at IS NULL AND expires_at >
   now() RETURNING`. Issuing a token supersedes the user's older unused tokens of the same purpose.
   TTL: verify 24 h (reset 30 min, USR-04b). Unknown, expired, used and superseded tokens get one
   422 `token_invalid`; a token of an already verified user answers 204 (idempotent).
4. **Send limits.** `email_send_log` keyed by HMAC-SHA256(`IDENTITY_HMAC_KEY`, normalized email),
   no email stored. Per email and purpose: 1 per 60 s, 5 per rolling 24 h; sign-up counts as the
   first send. A global `EMAIL_DAILY_CAP` (default 300) protects the shared Gmail quota. Checks
   run under a transaction-scoped advisory lock per (purpose, email), so concurrent resends
   serialize. When limited, sign-up still succeeds without an email; resend answers 429 with
   `Retry-After` (3600 s for the global cap).
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
- Retention cleanup of `email_send_log` and used tokens is a follow-up.
