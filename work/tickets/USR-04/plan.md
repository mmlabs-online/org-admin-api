Status: READY

# Plan — USR-04: Email verification and password reset via Gmail SMTP

The ticket is split (see `## Split proposal`). **This plan covers slice USR-04 only:** the
`EmailSender` (Gmail SMTP + fake), async in-process sending with retry, the `email_tokens` /
`email_send_log` migration, the sign-up hook, `POST /auth/verify-email`,
`POST /auth/verify-email/resend`, and the runbook. That slice alone unblocks D16 (verified email →
`/auth/token` and join) and the ricotdin integration. Forgot/reset with all-session revocation moves
to the follow-on ticket **USR-04b**.

Estimated size: about 650–750 production lines including the migration (above the ~400 guide).
Splitting further (sender without a caller, or tokens without a sender) would produce a slice that
ships nothing usable, so it stays one slice; the reviewer should expect that size.

Decisions that outlive the ticket go into a new **ADR-0007** (link format, token model, send
durability, send limits, SMTP transport). Owner-confirm items are A1–A10 under `## Assumptions`; none
blocks implementation.

## Spec summary

spec-v1.md §5.8:

> **Sign-up:** open self-registration, protected by Cloudflare Turnstile and a per-IP rate limit (D2).
> Unverified users may log in but must verify their email before joining any app or getting an app token.

> **Email flows:** email verification and password reset by one-time, expiring links, sent through
> Gmail SMTP behind an `EmailSender` interface; resend at most 1/min and 5/day per email (D3).

spec-v1.md §3 (non-goal, R10): "a hosted login UI — not in v1 of §5.8. Each app keeps its own login page".

identity-architecture.md:

> §2 Sign up — `POST /api/v1/auth/signup` (Turnstile + per-IP limit) → user created unverified →
> verification email (USR-04).

> §3 — `email_tokens (id, user_id, purpose verify|reset, token_hash, expires_at, used_at NULL)` (USR-04);
> `email_send_log (id, email_hash, purpose, sent_at)` — resend limits (USR-04)

> §4 — `POST /api/v1/auth/verify-email {token}`; `POST /api/v1/auth/verify-email/resend {account_id}` |
> public / cookie | email verification | USR-04

> §5 S5 — no join and no app token for an unverified email (D2). Login is allowed.
> §5 S6 — verification/reset resend at most 1/min and 5/day per email; tokens single-use, expiring,
> stored hashed. Gmail App Password only in env.

> D3 — Email via Gmail SMTP (dedicated account, App Password, `smtp.gmail.com:587`) behind an
> `EmailSender` interface; async with retry; resend limit 1/min and 5/day per email. Gmail daily limits
> are a known risk.

> D16 — no interim "mark verified" path. Joining an app and getting an app token stay blocked until USR-04 ships.

> D19 — allowed app origins: only `https://mmlabs.online`, `https://*.mmlabs.online` and loopback.

> D20 — cookie `__Host-mm_device`, `Path=/`; identity endpoints stay under `/api/v1/auth/*`.

CLAUDE.md R3 class 2 (cookie: "… resend verification"), class 3 (public: "`/api/v1/auth` signup, login,
verify-email, password forgot/reset (rate-limited, Turnstile where required)"); R5; R6; R11 ("reset and
verification tokens stored only as hashes … never log passwords, tokens or full emails in errors").

Ticket notes (owner, 2026-11-12): "use the same Gmail sender account the owner already set up for the
mmlabs site repo. Its address and App Password go only into this service's deploy env (never into this
repo); the owner provides them at deploy time."

**Conflicts flagged (spec/architecture wins):**
- Ticket "Scope" text implies other paths; §4 paths are used: `/auth/verify-email` and
  `/auth/verify-email/resend` (consult-api-platform-engineer, Findings 1).
- Ticket AC "resend limit … for reset → 429" vs §4 "forgot → always 202": decided in USR-04b (silent
  limit on forgot, see split).
- D3 "dedicated account" vs owner note 2026-11-12 "same Gmail account as the mmlabs site": the later
  owner note is followed (A6).
- CLAUDE.md R11 still says cookie `mm_device`, `Path=/api/v1/auth`; D20 superseded it. Not changed
  here (R7); follow-up F1.

## Acceptance criteria → verification

Ticket ACs that belong to this slice, plus contract items this plan adds. ACs 5 (forgot/reset) and the
"reset" halves of ACs 3/4/8 move to USR-04b.

| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | `EmailSender` interface; Gmail SMTP implementation configured only from env (`.env.example` lists keys without values); a fake for tests | `app/modules/email/sender.py` has `EmailSender` Protocol + `GmailSmtpSender`; `tests/fakes.py::FakeEmailSender`; `.env.example` has `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_LINK_BASE_URL`, `EMAIL_DAILY_CAP`, `VERIFY_EMAIL_RATE_LIMIT`, `VERIFY_EMAIL_RATE_WINDOW_SECONDS` with no values; `test_email_sender.py::test_gmail_sender_starttls_with_default_context_and_login` |
| 2 | Gmail transport is safe: STARTTLS required with certificate + hostname validation, timeouts, `EmailMessage` | `test_email_sender.py::test_gmail_sender_starttls_with_default_context_and_login`, `::test_starttls_not_offered_is_permanent`, `::test_cert_error_is_permanent` |
| 3 | Sending is async (not in the request path) with retry and backoff; failures logged without the token or full address | `test_email_dispatcher.py::test_transient_then_success`, `::test_gives_up_after_max_attempts`, `::test_permanent_not_retried`, `::test_logs_never_contain_token_link_or_address`; `test_email_verification_api.py::test_signup_response_does_not_wait_for_sender` |
| 4 | Quota / auth failures open a circuit breaker; a global daily cap protects the shared Gmail quota | `test_email_dispatcher.py::test_quota_error_pauses_and_drops`, `::test_auth_error_pauses`; `test_email_verification_api.py::test_daily_cap_resend_429_and_signup_still_201` |
| 5 | Verification tokens: random (256 bit), single-use, expiring (24 h), stored only as SHA-256 hash; a new token invalidates older unused ones | `test_email_verification_api.py::test_token_stored_hashed_only`, `::test_verify_used_token_invalid_when_not_verified`, `::test_verify_expired_token_invalid`, `::test_resend_invalidates_previous_token`; `test_email_schema.py` |
| 6 | Sign-up sends the verification email after commit; nothing is sent when sign-up fails | `::test_signup_sends_verification_link_to_app_origin`, `::test_signup_without_app_header_uses_link_base`, `::test_signup_failure_sends_nothing`, `::test_signup_bad_app_header_403_no_user` |
| 7 | Link = `{origin}/verify-email#token=…`; origin = the request `Origin` validated against the `X-MM-App` app's `origins`, else `EMAIL_LINK_BASE_URL`; never from Host / X-Forwarded-Host | `::test_signup_sends_verification_link_to_app_origin`, `::test_link_ignores_host_and_forwarded_host` |
| 8 | `POST /auth/verify-email` sets `email_verified_at`; afterwards `/api/v1/auth/token` no longer returns `email_unverified` (join uses the same flag, USR-03b) | `::test_verify_then_token_works` |
| 9 | Unknown, expired, used, superseded tokens → one uniform 422 `token_invalid`; malformed → 422 `validation_error`; verify is idempotent (204) when the user is already verified | `::test_verify_invalid_tokens_uniform_422` (parametrized), `::test_verify_idempotent_when_already_verified`, `::test_verify_disabled_user_token_invalid`, `::test_verify_malformed_token_422` |
| 10 | Resend limit per email: 1/min and 5/day (sign-up counts as the first send) → 429 `rate_limited` + `Retry-After` | `::test_resend_429_within_a_minute_of_signup`, `::test_resend_429_after_five_a_day`, `test_email_throttle.py` (pure function) |
| 11 | Resend is cookie class (R3): 401 without/invalid cookie or foreign `account_id`, 403 without `X-MM-App` / bad Origin, 409 `email_already_verified` | `::test_resend_requires_cookie`, `::test_resend_foreign_account_401`, `::test_resend_requires_app_header_and_origin`, `::test_resend_already_verified_409` |
| 12 | `verify-email` is per-IP rate-limited | `::test_verify_rate_limited_per_ip` |
| 13 | Staging/production refuse to start without `SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_LINK_BASE_URL` (valid D19 non-loopback origin); errors never echo values | `test_config.py::test_smtp_required_in_production`, `::test_email_link_base_url_rules` |
| 14 | One migration with working `downgrade()`, models in `app/db/models.py`, `alembic check` clean | `make check`; CI `alembic check`; conftest downgrade/upgrade; `test_email_schema.py` |
| 15 | Runbook: Gmail daily limits and what to do when hit; how to switch provider; deploy prerequisites | `docs/deploy.md` section "Email (USR-04)"; reviewer reads it |
| 16 | Consumer contract documented (path convention, fragment handling, error codes) | `docs/consumers.md` section "Email verification"; reviewer reads it |
| 17 | `make check` green | `make check` |

## Files to create / modify

| File | Change | Required because |
|---|---|---|
| `app/modules/email/__init__.py` | new, empty | new module |
| `app/modules/email/sender.py` | new: `OutgoingEmail` (no secrets in `repr`), `EmailDeliveryError(kind, smtp_code)`, `EmailSender` Protocol, `GmailSmtpSender` (stdlib `smtplib` in `asyncio.to_thread`), `DisabledEmailSender`, `build_email_sender(settings)` | AC1–2 |
| `app/modules/email/dispatcher.py` | new: `EmailDispatcher` (bounded `asyncio.Queue`, lazily started worker task, retry/backoff, circuit breaker, masked logging; injectable `sleep` and `clock`) | AC3–4 |
| `app/modules/email/messages.py` | new: `verification_message(to, link) -> (subject, body)` plain text | AC6 |
| `app/modules/auth/models.py` | add `EmailToken`, `EmailSendLog` | AC5, AC10 |
| `app/modules/auth/repository.py` | add `EmailTokenRepository` (issue, consume, find, invalidate) and `EmailSendLogRepository` (lock, window stats, global count, insert) | AC5, AC10 |
| `app/modules/auth/email_throttle.py` | new: pure `resend_retry_after(now, last, count_24h, oldest_24h) -> int \| None` + constants | AC10 (unit-testable) |
| `app/modules/auth/email_verification.py` | new: `EmailVerificationService` (`prepare`, `verify`, `resend`) | AC5–11 |
| `app/modules/auth/hooks.py` | keep `SignupHook` Protocol (same signature); add `VerificationEmailHook`; remove `noop_signup_hook`/`get_signup_hook` from here | AC6 |
| `app/modules/auth/deps.py` | add `get_signup_hook` (moved; builds `VerificationEmailHook`), `get_email_dispatcher`/`EmailDispatcherDep`, `get_optional_link_origin`, `get_link_origin` | AC6–7, AC11 |
| `app/modules/auth/router.py` | signup uses the new hook dependency (+ `BackgroundTasks`); add `POST /verify-email`, `POST /verify-email/resend` | AC6–12 |
| `app/modules/auth/schemas.py` | add `VerifyEmailRequest`, `ResendVerificationRequest` | AC9, AC11 |
| `app/modules/auth/errors.py` | add `TokenInvalidError` (422 `token_invalid`), `EmailAlreadyVerifiedError` (409 `email_already_verified`) | AC9, AC11 |
| `app/core/config.py` | SMTP/email keys, `verify_email_rate_*`, staging/production gate | AC1, AC12–13 |
| `app/core/rate_limit.py` | add `"verify_email"` to `Scope` and `_LIMITS` | AC12 |
| `app/main.py` | `app.state.email_dispatcher = EmailDispatcher(build_email_sender(settings), daily-cap aware logging)` | AC3 |
| `app/db/models.py` | import/export `EmailToken`, `EmailSendLog` | R2 |
| `migrations/versions/20261112_<rev>_create_email_tokens.py` | new revision, `down_revision="83936724f780"` | R2, AC14 |
| `.env.example` | new keys without values (comments: which are secrets) | R5, AC1 |
| `tests/conftest.py` | set test env `SMTP_HOST=smtp.test.invalid`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_LINK_BASE_URL=https://app.mmlabs.online`; fixtures `fake_email`, `email_dispatcher`; override `get_email_dispatcher` in `make_client` and `make_strict_app` | tests never reach a real SMTP host; existing production-mode `Settings` tests stay valid |
| `tests/fakes.py` | add `FakeEmailSender` (records `OutgoingEmail`s; programmable failures) | AC1 |
| `tests/test_auth_signup.py` | import `get_signup_hook` from `app.modules.auth.deps` (moved) | existing hook test |
| `tests/test_email_sender.py`, `tests/test_email_dispatcher.py`, `tests/test_email_throttle.py`, `tests/test_email_schema.py`, `tests/test_email_verification_api.py` | new | see `## Tests to write` |
| `tests/test_config.py` | new cases for the email gate | AC13 |
| `docs/adr/0007-email-verification-and-delivery.md` | new ADR (planner rule 7) | decisions outlive the ticket |
| `docs/consumers.md` | section "Email verification" (paths, fragment handling, errors, ricotdin routes) | AC16 |
| `docs/deploy.md` | section "Email (USR-04)": config keys, deploy prerequisites, Gmail limits runbook, provider switch, App Password rotation; replace "after USR-04 ships" note in super-admin bootstrap with "verify via the link; reset arrives with USR-04b" | AC15 |
| `docs/identity-architecture.md` | §3 only: `email_tokens` gains `created_at`; `email_send_log.email_hash` → `email_hmac` (HMAC-SHA256 with `IDENTITY_HMAC_KEY`) | keep the data model doc true (consult-database-optimizer 2) |

No new runtime dependency: stdlib `smtplib` + `ssl` + `email.message` (see Approach §3 for why not
`aiosmtplib`). `pyproject.toml` / `uv.lock` unchanged.

## Tests to write

| Test (file › name) | Covers |
|---|---|
| `test_email_sender.py › test_gmail_sender_starttls_with_default_context_and_login` | fake `smtplib.SMTP` (monkeypatched): connect with timeout, `ehlo`, `starttls(context=…)` where context has `check_hostname` and `CERT_REQUIRED`, `login(user, password)`, `send_message`; From/To/Subject headers |
| `test_email_sender.py › test_starttls_not_offered_is_permanent` | `SMTPNotSupportedError` → `EmailDeliveryError(kind="permanent")`; never sends in clear |
| `test_email_sender.py › test_cert_error_is_permanent` | `ssl.SSLCertVerificationError` → permanent |
| `test_email_sender.py › test_classification` (parametrized) | 421/451 → transient; 550 `5.4.5` → quota; 535 auth → auth; other 5xx → permanent; timeout / disconnect / `ConnectionError` → transient; exception has no `__cause__`/`__context__` text (raised `from None`) |
| `test_email_sender.py › test_outgoing_email_repr_hides_secrets` | `repr(OutgoingEmail)` has neither address nor body |
| `test_email_dispatcher.py › test_transient_then_success` | fake fails once transient, then sends; 2 attempts; instant sleep |
| `test_email_dispatcher.py › test_gives_up_after_max_attempts` | 3 transient failures → `email_failed` logged, worker keeps running for the next message |
| `test_email_dispatcher.py › test_permanent_not_retried` | 1 attempt |
| `test_email_dispatcher.py › test_quota_error_pauses_and_drops` | quota → `email_quota_exceeded`; next message dropped while paused (`email_dropped reason=paused`); after the pause (fake clock) sends again |
| `test_email_dispatcher.py › test_auth_error_pauses` | auth → `email_auth_failed` + pause |
| `test_email_dispatcher.py › test_queue_full_drops` | maxsize reached → `email_dropped reason=queue_full`, no exception to the caller |
| `test_email_dispatcher.py › test_logs_never_contain_token_link_or_address` | `caplog` over all paths: no token, link, local part or full address |
| `test_email_throttle.py › test_resend_retry_after` (parametrized) | none sent → None; last 10 s ago → 50; 5 in 24 h (last > 60 s) → seconds until oldest + 24 h; boundary values |
| `test_email_schema.py › test_email_tokens_constraints` | purpose CHECK, `octet_length(token_hash)=32`, unique `token_hash`, FK cascade on user delete |
| `test_email_schema.py › test_email_send_log_constraints` | purpose CHECK, `octet_length(email_hmac)=32` |
| `test_email_verification_api.py › test_signup_sends_verification_link_to_app_origin` | signup with `X-MM-App` + registered Origin → one email to the user, link `https://<origin>/verify-email#token=<43 chars>`; one `email_send_log` row |
| `… › test_signup_without_app_header_uses_link_base` | no header → link on `EMAIL_LINK_BASE_URL` |
| `… › test_signup_bad_app_header_403_no_user` | bad `X-MM-App` / unregistered Origin → 403, no user row, nothing sent |
| `… › test_link_ignores_host_and_forwarded_host` | `Host` / `X-Forwarded-Host: evil.example` change nothing |
| `… › test_signup_failure_sends_nothing` | strict client: 409 duplicate and 422 weak password → no email queued |
| `… › test_signup_response_does_not_wait_for_sender` | fake sender blocks on an event; signup returns 201 before it is released |
| `… › test_token_stored_hashed_only` | DB row holds `sha256(token)`, not the token |
| `… › test_verify_then_token_works` | signup → login → `/auth/token` 403 `email_unverified` → verify 204 → `email_verified_at` set → `/auth/token` no longer `email_unverified` (200 when member via `rbac_factories`) |
| `… › test_verify_invalid_tokens_uniform_422` (unknown, expired, used, superseded) | identical body `{"error":{"code":"token_invalid",…}}` |
| `… › test_verify_used_token_invalid_when_not_verified` | consumed token for an unverified user (e.g. forced via DB) → 422 |
| `… › test_verify_idempotent_when_already_verified` | second POST with the same token → 204 |
| `… › test_verify_expired_token_invalid` | `expires_at` in the past → 422 |
| `… › test_verify_disabled_user_token_invalid` | disabled user → 422 `token_invalid`, token not consumed, flag not set |
| `… › test_verify_malformed_token_422` | too short / bad charset / extra field → 422 `validation_error`, token not echoed |
| `… › test_resend_invalidates_previous_token` | after resend (limit window moved via DB) the old token → 422, the new → 204 |
| `… › test_resend_429_within_a_minute_of_signup` | 429 `rate_limited`, `Retry-After` 1..60, nothing queued |
| `… › test_resend_429_after_five_a_day` | 5 rows in the last 24 h (older than 60 s) → 429, `Retry-After` > 60 |
| `… › test_resend_ok_202` | 202 empty body, one email, `Cache-Control: no-store`, rotated cookie forwarded when rotation happens |
| `… › test_resend_requires_cookie` | no cookie → 401 `session_invalid` |
| `… › test_resend_foreign_account_401` | `account_id` of another device → 401 |
| `… › test_resend_requires_app_header_and_origin` | missing `X-MM-App` → 403 `app_header_required`; bad Origin → 403 `origin_not_allowed` |
| `… › test_resend_already_verified_409` | 409 `email_already_verified` |
| `… › test_daily_cap_resend_429_and_signup_still_201` | `EMAIL_DAILY_CAP=1`: second signup 201 but nothing queued (`email_not_sent reason=daily_cap`); resend 429 |
| `… › test_verify_rate_limited_per_ip` | `VERIFY_EMAIL_RATE_LIMIT=1` → second request 429 |
| `test_config.py › test_smtp_required_in_production` (staging, production) | missing username / password / link base → `ValueError`, message names the key, never the value |
| `test_config.py › test_email_link_base_url_rules` | path, port, http, non-mmlabs, loopback-in-production → refused; loopback allowed in local |

## Approach

1. **Config (R5).** Add to `Settings`:
   `smtp_host: str = "smtp.gmail.com"`, `smtp_port: int = 587`, `smtp_username: str | None`,
   `smtp_password: SecretStr | None`, `email_from: str | None` (defaults to `smtp_username`; Gmail
   rewrites/DMARC-fails other From addresses), `email_link_base_url: str | None`,
   `email_daily_cap: int = Field(300, ge=1)`, `verify_email_rate_limit: int = 20`,
   `verify_email_rate_window_seconds: int = 60`.
   Validator `_email_gate`: if `email_link_base_url` is set it must equal `normalize_origin(value)` (D19
   form; no path) and, in staging/production, must not be loopback. In staging/production
   `smtp_username`, `smtp_password` and `email_link_base_url` are required. Messages name the key, never
   the value (`hide_input_in_errors` already on). Like `IDENTITY_HMAC_KEY`, this means `migrate` and the
   API refuse to start in production until the deployment repo provides the keys (deploy prerequisite).

2. **Migration + models (R2).** See `## Data model / migration`. CHECK texts identical in model and
   migration (existing convention, `auth/models.py` docstring).

3. **Sender (`app/modules/email/sender.py`).**
   - `OutgoingEmail(to, subject, body, purpose, user_id)`, frozen dataclass with a custom `__repr__`
     showing only `purpose` and `user_id` (R11).
   - `EmailSender` Protocol: `async def send(self, email: OutgoingEmail) -> None`, raising
     `EmailDeliveryError(kind: Literal["transient","permanent","quota","auth"], smtp_code: int | None)`.
   - `GmailSmtpSender`: builds an `email.message.EmailMessage` (header CR/LF refused by the library),
     then `await asyncio.to_thread(self._send_sync, msg)`. `_send_sync`:
     `smtplib.SMTP(host, port, timeout=15)` → `ehlo()` → `starttls(context=ssl.create_default_context())`
     → `ehlo()` → `login()` → `send_message()`. STARTTLS is always required (no plain fallback;
     `SMTPNotSupportedError` → permanent). Classification: `SMTPAuthenticationError` → `auth`;
     `SMTPResponseException` with code 550 and `5.4.5` in the reply → `quota`; other 4xx → transient,
     other 5xx → permanent; `SMTPRecipientsRefused` → code of the single recipient; `ssl.SSLCertVerificationError`
     → permanent; `TimeoutError`, `SMTPServerDisconnected`, `ConnectionError`/`OSError` → transient.
     Always `raise EmailDeliveryError(...) from None` so the SMTP reply (which echoes the recipient)
     never reaches a traceback or log.
   - `DisabledEmailSender` (local/test when SMTP is unset; unreachable in staging/production because of
     the gate): logs `email_not_sent reason=smtp_unconfigured purpose=… user_id=…`, never the link.
   - Why stdlib `smtplib` and not `aiosmtplib` (consult-security-architect suggested it): it is not in
     `pyproject.toml`; stdlib gives the same STARTTLS/cert/timeout guarantees, volume is a few hundred
     mails/day, and one thread per send is fine. No new dependency, no stack change.

4. **Dispatcher (`app/modules/email/dispatcher.py`).** Decision: **in-process queue after commit**
   (consult-identity-access-engineer 7, consult-security-architect Risks; DB outbox rejected, A3).
   - `asyncio.Queue(maxsize=1000)`; `async def enqueue(email)` (must be `async`: Starlette runs sync
     background functions in a thread, where `asyncio.Queue` is unsafe) starts the worker task lazily
     on the running loop (keeps a strong reference; no lifespan exists today and test clients do not run
     one), then `put_nowait` (full → `email_dropped reason=queue_full`).
   - One worker, sequential. Per message: up to 3 attempts; backoff 2 s, 8 s (±20 % jitter) for
     `transient`; `permanent` → `email_failed`; `quota`/`auth` → pause the dispatcher for 1 h
     (`email_quota_exceeded` / `email_auth_failed`, WARNING); while paused, messages are dropped with
     `email_dropped reason=paused` (users resend; resend limits still apply). Any unexpected exception
     is caught per message so the worker never dies.
   - Logs: event name, `purpose`, `user_id`, `attempt`, `smtp_code` only. Never token, link, address
     (not even masked) or `str(exc)`.
   - `async def join()` for tests (`queue.join()`).
   - Raw token exists only in the `OutgoingEmail` body in memory; a restart loses queued mail and the
     user presses resend (A3).

5. **Tokens and limits (repositories + `email_verification.py`; consult-database-optimizer 1–2).**
   - Token: `secrets.token_urlsafe(32)` (256 bit, 43 chars, URL-safe so no escaping in the fragment);
     stored `sha256(token)` (HMAC not needed for a 256-bit random value; same as device cookies).
   - `EmailTokenRepository.issue(user_id, purpose, token_hash, ttl)`: in one transaction
     `UPDATE … SET used_at=now() WHERE user_id=:u AND purpose=:p AND used_at IS NULL` (supersede older),
     then `INSERT … expires_at = now() + ttl`. TTL verify = 24 h (constant; reset 30 min in USR-04b).
   - `consume(token_hash, purpose)`: `UPDATE email_tokens SET used_at=now() WHERE token_hash=:h AND
     purpose=:p AND used_at IS NULL AND expires_at > now() RETURNING user_id` (race-free single use).
   - `find_user_id(token_hash, purpose)`: any state, for the idempotent path.
   - Send limits (`EmailSendLogRepository`), keyed by `email_hmac = EmailHasher.hmac(normalized email)`
     (`IDENTITY_HMAC_KEY`, reused from `app.state.email_hasher`):
     `lock(hmac, purpose)` = `SELECT pg_advisory_xact_lock(hashtextextended(:purpose || encode(:hmac,'hex'), 0))`;
     `window(hmac, purpose)` = one `SELECT now(), max(sent_at), count(*), min(sent_at) … WHERE sent_at > now() - interval '24 hours'`;
     `count_global_24h()`; `insert(hmac, purpose)`. Order in one transaction: lock → window →
     `resend_retry_after(...)` (1 per 60 s, then 5 per 24 h) → global count vs `EMAIL_DAILY_CAP` →
     insert. The advisory lock is released at commit, so concurrent resends serialize.
   - `EmailVerificationService.prepare(user, link_origin) -> OutgoingEmail` raises `RateLimitedError`
     when limited (retry from the window, or 3600 s for the global cap). Builds the link
     `f"{link_origin}/verify-email#token={token}"` and the message.

6. **Sign-up hook (consult-api-platform-engineer Findings 3, consult-identity-access-engineer 2).**
   - `SignupHook` Protocol keeps its signature `(user_id, session)`; `SignupService` is unchanged.
   - `VerificationEmailHook(link_origin, email_hasher, schedule)`: loads the user, calls
     `prepare`; on `RateLimitedError` logs `email_not_sent reason=limited|daily_cap user_id=…` and
     returns (sign-up still 201); else calls `schedule(email)`.
   - `schedule` is built in `deps.get_signup_hook` as
     `lambda e: background_tasks.add_task(dispatcher.enqueue, e)`. FastAPI runs background tasks
     only after a successful response, and `SessionDep` commits before the response is sent
     (function scope), so the email is queued **only after commit**; on rollback/error nothing is
     queued. The service layer receives a plain callable (no FastAPI import, R1).
   - `get_signup_hook` moves to `deps.py` (it now needs request dependencies; keeping it in
     `hooks.py` would create an import cycle `deps → service → hooks → deps`).

7. **Link origin (option (b); consult-identity-access-engineer 1, consult-security-architect
   "Link building").**
   - `get_optional_link_origin` (signup): no `X-MM-App` → `settings.email_link_base_url`; header
     present → `AppContextService.check(x_mm_app, origin)` (403 on failure, before the user is created)
     and return `normalize_origin(origin)`, which `check` already proved is in
     `allowed_origins(app.origins, app_env)` (loopback ignored in production). Using the request's
     matched origin (not `origins[0]`) avoids sending production users to a staging origin
     (consult-api-platform-engineer Risks) and is never arbitrary: it is an allow-listed value.
   - `get_link_origin` (resend): same, but via the required `AppContextDep`.
   - Never `Host`, `X-Forwarded-Host` or an unchecked header. If there is no base and no header
     (local only), the hook logs `email_not_sent reason=no_link_base`.
   - Path convention `/verify-email` (and `/reset-password` in USR-04b) is fixed in code and documented
     in `docs/consumers.md`; no `apps` column (A2).

8. **Endpoints (R3, R6, R9).** See `## API contract`.
   - `verify-email` (class 3 public, per-IP `verify_email`): body token → hash → `consume`; if no row:
     `find_user_id`; when that user exists, is active and already verified → 204 (double click / mail
     scanner is harmless); else 422 `token_invalid`. With a row: load the user `FOR UPDATE`; disabled →
     raise `token_invalid` (transaction rolls back, token stays unused); set `email_verified_at = now()`
     if null; supersede the user's other verify tokens; log `email_verified user_id=…`; 204.
   - `verify-email/resend` (class 2 cookie): `AppContextDep`, `DeviceDep`, body `{account_id}` →
     account on this device (`SessionRepository.get_active_account` with `created_before`, as in
     `TokenService`), else 401 `session_invalid`; user disabled → 401; verified → 409
     `email_already_verified`; then `prepare` (429 when limited) → schedule via `BackgroundTasks` →
     202. Rotated cookie forwarded like `/token`.
   - Both set `Cache-Control: no-store` (CORS middleware already adds it under `/api/v1/auth/`).
   - `responses=` lists error codes for OpenAPI; empty 202/204 use `response_class=Response`.

9. **main.py:** `app.state.email_dispatcher = EmailDispatcher(build_email_sender(settings))`.
   Startup log `email_sender=gmail|disabled` (no username).

10. **Docs + ADR-0007** (see Files). `docs/deploy.md` "Email (USR-04)":
    - config keys and which are secrets;
    - **deploy prerequisites (owner action, separate PR on `mmlabs-online/deployment`; not in this
      diff):** (a) create a **separate Gmail App Password** named for org-admin-api on the shared
      account (revocable without touching the mmlabs site); (b) add GitHub Actions secrets
      `ORG_ADMIN_SMTP_USERNAME`, `ORG_ADMIN_SMTP_PASSWORD` in the deployment repo, ideally in an
      Environment that only `main` can deploy; (c) `deploy-org-admin-api.yml` step `envfile`: pass them
      through `env:`, fail before `pull` if missing, validate (address regex; App Password
      `^[a-z]{16}$` after removing spaces) so a newline cannot inject lines, write with
      `printf 'SMTP_USERNAME=%s\n'` / `printf 'SMTP_PASSWORD=%s\n'`, update the "No GitHub secrets"
      comment and README; (d) `apps/org-admin-api/compose.yml` `x-app.environment`:
      `EMAIL_LINK_BASE_URL=https://<origin that serves /verify-email>` (not secret; optional
      `EMAIL_FROM`, `EMAIL_DAILY_CAP`); (e) merge that PR **before** the release PR containing USR-04,
      otherwise `migrate` refuses to start and the old API keeps running. A value written by hand on
      the Mac is lost on the next deploy (env file is rewritten every run).
    - runbook: Gmail limits (consumer ~500/day, Workspace ~2,000/day, shared with the mmlabs site),
      symptoms (`email_quota_exceeded`, `email_not_sent reason=daily_cap`, users seeing resend 429),
      what to do (wait for the rolling 24 h window; lower traffic; raise/lower `EMAIL_DAILY_CAP`; move
      to Workspace or another provider), how to switch provider (new `EmailSender` implementation +
      `build_email_sender` branch + env keys; no API change), App Password rotation and revocation
      (`email_auth_failed` after revoke), smoke check after deploy (sign up a test address, look for
      `email_sent`).
    - super-admin bootstrap note: "After USR-04 ships: verify the email via the link"; the password
      reset step waits for USR-04b.

Rules: R1 (router → service → repository; dispatcher/sender are infrastructure called from
router/background), R2 (one migration, downgrade), R3 (classes as above), R5 (env only, `.env.example`),
R6 (envelope, 202/204/401/403/409/422/429), R7 (follow-ups below), R8 (tests per endpoint), R9 (paths
under `/api/v1/auth`), R10 (no hosted UI: links open the app's own page), R11 (hashes, no tokens or
addresses in logs).

## Specialist input used

- **consult-api-platform-engineer** — adopted: §4 paths (Findings 1); 422 `token_invalid` uniform (2);
  `Retry-After` 429; 202/204 with `response_class=Response`; new `Scope` value (only `verify_email` here,
  `forgot`/`reset` in USR-04b); split 04a/04b (9); 409 `email_already_verified` on resend (cookie class,
  no enumeration). Rejected: `AppContextDep` *required* on verify-email (the token is the credential;
  §4/R3 class it as public; CORS already confines browsers to registered origins); `origins[0]` (use the
  matched request origin instead); `email_outbox` + `SKIP LOCKED` worker (A3); env-configurable
  verify/reset paths (fixed convention, A2).
- **consult-database-optimizer** — adopted: `email_tokens` shape, purpose CHECK (not a PG enum),
  `octet_length=32`, partial index, atomic consume, supersede-then-insert (1); `email_hmac` rename,
  advisory-lock + count + insert (2); one revision off `83936724f780` (5). Deferred: retention cleanup
  (4) → follow-up F3, same as sessions/`login_failures`. Rejected: outbox (3, A3).
- **consult-identity-access-engineer** — adopted: option (b) links with fragment and POST consumption
  (1); token model and TTLs (3); idempotent verify when already verified (4); `IDENTITY_HMAC_KEY` for
  limits (6); in-memory send after commit (7); ricotdin notes (into `docs/consumers.md`). Changed:
  `X-MM-App` on signup is **optional**, not required (A1). Reset items (5) → USR-04b.
- **consult-security-architect** — adopted: token generation/storage/consumption, POST-only consume, no
  redirect; link origin only from config or the app's registered origin (never Host); SMTP STARTTLS +
  default context + timeouts + `EmailMessage`; retry 4xx, 5xx permanent, quota circuit breaker,
  global daily cap; logging rules; config keys with production refusal; deploy via GitHub secrets
  with validation and a separate App Password (documented as prerequisite, no edits to the deployment
  repo). Adapted: `aiosmtplib` → stdlib `smtplib` in a thread (no new dependency). Not adopted now:
  `email_hmac` on the token row to reject after an email change (no email-change path exists in v1;
  follow-up F4). Forgot enumeration rules → USR-04b.

## Assumptions

Non-blocking; the owner can confirm or overrule in review.

- **A1 `X-MM-App` on signup is optional** (v1 contract note). Absent → link on `EMAIL_LINK_BASE_URL`;
  present → must be valid (403 otherwise, before any user is created). Additive, non-breaking for
  existing clients; `@mmlabs/auth-client` always sends it. USR-04b uses the same rule for forgot.
- **A2 Link convention:** `{origin}/verify-email#token=…` (USR-04b: `{origin}/reset-password#token=…`),
  fixed paths for every app, no per-app column. The page strips the fragment (`history.replaceState`),
  uses `Referrer-Policy: no-referrer`, and POSTs the token. `EMAIL_LINK_BASE_URL` (production) is the
  origin of an app that implements the convention — first ricotdin's — set by the owner in the
  deployment repo.
- **A3 Send durability = in-memory queue after commit** (owner confirm). The raw token lives only in
  process memory; a restart or crash loses queued mail; the user presses resend (limits allow 1/min).
  A DB outbox would need the token stored encrypted with another env key; not worth it for v1 volume.
- **A4 TTLs:** verify 24 h; reset 30 min (USR-04b). Constants, not env.
- **A5 Global daily cap** default 300 (below consumer Gmail ~500/day, leaving room for the mmlabs site
  on the shared account). When reached: sign-up still succeeds without email; resend returns 429
  `Retry-After: 3600`.
- **A6 Shared Gmail account** (owner note 2026-11-12) supersedes D3's "dedicated account". Shared quota
  and sender reputation with the mmlabs site are an accepted risk; a **separate App Password** for this
  service is recommended. The ADR records the deviation from D3.
- **A7 F2 duplicate sign-up stays 409 `email_taken`** for now (enumeration oracle; security consult
  Findings 2). Changing it (201/202 + "you already have an account" email) is an owner decision and a
  separate ticket (F2 follow-up); it also limits what forgot's always-202 achieves in USR-04b.
- **A8 Reset sets `email_verified_at`** if null (proves inbox control) — decided in USR-04b,
  recommended yes.
- **A9 Email copy:** plain text, Vietnamese first then English, subject
  `Xác nhận email mmlabs / Verify your mmlabs email`, sender name `mmlabs`. Owner may change wording.
- **A10 Local development** without SMTP keys uses `DisabledEmailSender` (logs that nothing was sent,
  never the link). To test end-to-end locally, set the SMTP keys in `.env`.
- Join (USR-03b) is not built yet; AC "afterwards join works" is verified through `/auth/token` (both
  use `users.email_verified_at`).

## Blocking questions

None.

## Split proposal

**This plan covers USR-04** (as described at the top). Follow-on ticket:

**USR-04b — Password forgot/reset with all-session revocation**

- Goal: a user who forgot their password gets a one-time reset link (via the USR-04 `EmailSender`
  and dispatcher) and sets a new password; every signed-in session of that user ends.
- Areas: auth, security, api (no migration: `email_tokens.purpose` and `email_send_log.purpose`
  already allow `reset`).
- Dependencies: USR-04.
- Acceptance criteria:
  - `POST /api/v1/auth/password/forgot {email, turnstile_token}` → always **202** (no enumeration),
    Turnstile always required (403 `turnstile_failed` on failure), per-IP limit scope `forgot`, optional
    `X-MM-App` (A1 rule) for the link origin; link `{origin}/reset-password#token=…`, TTL 30 min.
  - Per-email limit 1/min and 5/day on purpose `reset`, counted per `email_hmac` for **every** email
    (known or unknown) and applied **silently** (still 202, nothing sent); 429 only from the per-IP
    limit. (Spec §4 "always 202" wins over the ticket's "→ 429 beyond" for forgot; flagged.) Same DB
    writes on both branches to limit timing differences; unknown/disabled users get no mail.
  - `POST /api/v1/auth/password/reset {token, new_password: SecretStr}` (public, per-IP scope
    `reset`): look up the token without consuming → USR-01 password policy (422 `weak_password` keeps
    the link usable) → consume atomically → Argon2id hash off the loop → in one transaction: set
    password, revoke **all** non-revoked `account_sessions` of the user on every device (new
    `SessionRepository.revoke_all_accounts_of_user`), supersede other reset tokens, delete the
    `login_failures` row for the email HMAC, set `email_verified_at` if null (A8) → 204, no cookie, no
    login. Unknown/expired/used token or disabled user → 422 `token_invalid`.
  - Tests with the fake sender: forgot same 202 for known/unknown/limited emails, Turnstile 403, per-IP
    429; reset happy path + all sessions revoked (`/auth/token` → 401 for every device), weak password
    leaves token usable, reused/expired token 422, `login_failures` cleared; logs free of token/address.
  - Docs: `docs/consumers.md` reset page; `docs/deploy.md` super-admin bootstrap "then reset the
    password"; note that issued access tokens stay valid ≤ 15 min after a reset.

## Data model / migration

One revision `migrations/versions/20261112_<rev>_create_email_tokens.py`, `down_revision =
"83936724f780"`. New tables only (expand-only; previous image keeps working on the new schema).
CHECK texts must match the models exactly.

```
email_tokens
  id          uuid         NOT NULL  PK (pk_email_tokens)             -- UUIDPkMixin
  user_id     uuid         NOT NULL  FK users.id ON DELETE CASCADE    (fk_email_tokens_user_id_users)
  purpose     varchar(10)  NOT NULL  CHECK (purpose IN ('verify', 'reset'))   ck_email_tokens_purpose_valid
  token_hash  bytea        NOT NULL  UNIQUE (uq_email_tokens_token_hash)
                                     CHECK (octet_length(token_hash) = 32)    ck_email_tokens_token_hash_length
  expires_at  timestamptz  NOT NULL
  used_at     timestamptz  NULL      -- consumed or superseded
  created_at  timestamptz  NOT NULL  DEFAULT now()
  CHECK (expires_at > created_at)                                     ck_email_tokens_expiry_after_creation
  INDEX ix_email_tokens_user_id_purpose_active ON (user_id, purpose) WHERE used_at IS NULL

email_send_log
  id          uuid         NOT NULL  PK (pk_email_send_log)
  email_hmac  bytea        NOT NULL  CHECK (octet_length(email_hmac) = 32)    ck_email_send_log_email_hmac_length
  purpose     varchar(10)  NOT NULL  CHECK (purpose IN ('verify', 'reset'))   ck_email_send_log_purpose_valid
  sent_at     timestamptz  NOT NULL  DEFAULT now()   -- time the send was accepted (queued)
  INDEX ix_email_send_log_email_hmac_purpose_sent_at ON (email_hmac, purpose, sent_at)
  INDEX ix_email_send_log_sent_at ON (sent_at)       -- global daily cap
```

- No FK from `email_send_log` to `users` (unknown emails are counted in USR-04b; no email stored, R11).
- Partial index declared in the model (`postgresql_where`) so `alembic check` shows no drift.
- `downgrade()`: drop the three indexes, then `email_send_log`, then `email_tokens`.
- Retention (delete old log rows / used tokens) is follow-up F3; growth is bounded by sign-ups × ≤ 6
  rows/day.

## API contract

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/signup` (changed) | unchanged body; **optional** headers `X-MM-App` + `Origin` (if `X-MM-App` is sent, both are checked) | unchanged `SignupResponse`; verification email queued after commit | 201, 403 (`turnstile_failed`, `app_header_required`, `origin_not_allowed`), 409, 422, 429 | public (R3 class 3): Turnstile + per-IP |
| POST | `/api/v1/auth/verify-email` | `{"token": str}` (32–128 chars, `[A-Za-z0-9_-]`, extra fields forbidden) | empty | 204 (verified, or already verified), 422 `token_invalid` / `validation_error`, 429 `rate_limited` | public (R3 class 3): per-IP `verify_email` |
| POST | `/api/v1/auth/verify-email/resend` | `{"account_id": uuid}`; cookie `__Host-mm_device`; headers `X-MM-App`, `Origin` | empty | 202, 401 `session_invalid`, 403 `app_header_required` / `origin_not_allowed`, 409 `email_already_verified`, 422 `validation_error`, 429 `rate_limited` (+`Retry-After`) | cookie (R3 class 2) |

All errors in the R6 envelope; responses `Cache-Control: no-store`. No admin endpoint, so no
`admin:*` permission.

Follow-ups (R7, not in this diff):
- **F1** CLAUDE.md R11 still describes cookie `mm_device`, `Path=/api/v1/auth`; update to D20
  (`__Host-mm_device`, `Path=/`). Owner/solo change.
- **F2** Duplicate sign-up 409 is an enumeration oracle (A7); owner decision + ticket.
- **F3** Retention cleanup for `email_send_log` (> 48 h), used/expired `email_tokens` (> 7 d), together
  with the session/`login_failures` cleanup follow-up.
- **F4** When an email-change feature exists, bind tokens to the email (`email_hmac` on the token row).
- **F5** Ticket "Scope" wording vs §4 paths: fix the ticket text to `/auth/verify-email[/resend]`.
- **F6** `@mmlabs/auth-client` gains `verifyEmail`, `resendVerification` (USR-03b) and later
  `forgotPassword`, `resetPassword` (USR-04b).
