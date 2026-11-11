Status: READY

# Plan — USR-01: Users, passwords (Argon2id) and sign-up with Turnstile — slice 1 of 2 (see Split proposal)

## Spec summary
Spec §5.8 (owner, 2026-11-11):
> - **Users:** email (unique, case-insensitive), password, status (`active` / `disabled`), email verified flag, timestamps. Staff are users too; what they may do in the admin API is RBAC (§5.3).
> - **Sign-up:** open self-registration, protected by Cloudflare Turnstile and a per-IP rate limit (D2). Unverified users may log in but must verify their email before joining any app or getting an app token.
> - **Passwords:** hashed with Argon2id; never logged or returned; minimum length and a breached/common-password check per the plan.
> - **Endpoints:** all under `/api/v1/auth/*` (cookie `Path=/api/v1/auth`); JWKS at `/.well-known/jwks.json` (D13).

identity-architecture.md §3:
> `users (id uuid, email citext unique, password_hash, status active|disabled, email_verified_at NULL, display_name NULL, created_at, updated_at)`

identity-architecture.md §4: `POST /api/v1/auth/signup {email, password, turnstile_token}` — "public, Turnstile, per-IP limit — create unverified user; send verification email — USR-01 (+USR-04 email)".

identity-architecture.md §5 S3: "per-IP ~20 req/min on login/signup/forgot; Turnstile required … (and always on signup / forgot)."
D2: "Open self-registration, protected by Cloudflare Turnstile + per-IP rate limit."
D11: "identical error for wrong email or password" (scoped to login — see Assumptions A1).
CLAUDE.md R11: "Argon2id for passwords; … never log passwords, tokens or full emails in errors."

Ticket scope: "`users` table and the Identity module skeleton; password hashing/policy; `POST /api/v1/auth/signup`; Turnstile verification behind an interface; per-IP limiter; staff user endpoints. Out of scope: sessions/login (USR-03), sending the verification email (USR-04 — signup only records the user as unverified and calls an `on_signup` hook)."

**This plan covers slice 1 (USR-01): ticket AC 1–5 and the matching part of AC 7. The staff endpoints (AC 6) move to slice 2 (proposed USR-01b) — see Split proposal.**

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | `users` table + migration as in identity-architecture.md §3 (email citext unique, `email_verified_at` NULL, status `active\|disabled`); downgrade works | `tests/conftest.py` runs `downgrade base` → `upgrade head` every session (green `make check`); `uv run alembic check` → "No new upgrade operations detected"; manual: `alembic downgrade -1 && alembic upgrade head` on a local DB; `test_users_model.py › test_status_check_rejects_unknown_value`, `› test_email_roundtrips_as_str` |
| 2 | Argon2id hashing (parameters in config); minimum length and common-password rejection; hash never returned or logged (R11) | `test_passwords.py › test_hash_is_argon2id_with_configured_params`, `› test_verify_and_needs_rehash`, `› test_policy_rejects_too_short`, `› test_policy_rejects_common_password_case_insensitive`, `› test_policy_accepts_strong_password`, `› test_policy_counts_code_points_after_nfkc`; `test_auth_signup.py › test_signup_response_has_exact_keys` (no hash); `User.__repr__` excludes the hash (`test_users_model.py › test_repr_has_no_password_hash`); grep diff for `logger`/`print` of password/hash |
| 3 | `POST /api/v1/auth/signup {email, password, turnstile_token}` → 201 (user unverified) · 409 duplicate email (any case) · 422 weak password / bad email · 403 invalid Turnstile · 429 over the per-IP limit; responses never reveal more than needed | `test_auth_signup.py` tests listed below (201, 409 case-insensitive, 422 ×3, 403, 429 + `Retry-After`, no password/email echoed in any 4xx body) |
| 4 | Turnstile verification behind an interface with a test fake; secret key from env (`.env.example` lists the key, no value) | `test_turnstile.py` (Cloudflare impl against `httpx.MockTransport`: success, failure, hostname mismatch, timeout/5xx → fail closed, missing secret → fail closed, Cloudflare test secret in production → fail closed); `.env.example` contains `TURNSTILE_SECRET_KEY=` with no value; signup tests use `FakeTurnstileVerifier` via `dependency_overrides` |
| 5 | Per-IP rate limiter (shared component, reused by login/forgot later) with configurable limits | `test_rate_limit.py` (window counting, reset after window, `retry_after`, key eviction cap, client-IP resolution incl. spoofed `X-Forwarded-For`/`CF-Connecting-IP` from untrusted peer ignored, IPv6 /64 bucketing); `test_auth_signup.py › test_signup_rate_limited_returns_429_with_retry_after`; limiter lives in `app/core/rate_limit.py` with a `rate_limit("<scope>")` dependency factory |
| 6 | *(moved to slice 2 / USR-01b)* Staff endpoints list/get/disable/enable | Not in this slice — see Split proposal |
| 7 | Tests for every bullet above, including case-insensitive duplicate and Argon2id verification | All tests in "Tests to write" exist and pass in `make check` |
| 8 | 422 responses never echo submitted values (needed for AC 2/3 "never returned", R5/R11) | `test_errors.py › test_validation_error_details_have_no_input_or_ctx`; `test_auth_signup.py › test_password_never_echoed_in_error_bodies` |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `pyproject.toml` | Add runtime deps `argon2-cffi>=23.1`, `email-validator>=2.2`; move `httpx>=0.27` from dev group to `dependencies` (keep it usable by tests) | Argon2id (AC2), `EmailStr` (AC3), Turnstile siteverify at runtime (AC4) |
| `uv.lock` | Regenerated by `uv add` / `uv lock` | Docker build uses `uv sync --frozen` |
| `.env.example` | Add keys (no secret values): `TURNSTILE_SECRET_KEY=`, `TURNSTILE_ALLOWED_HOSTNAMES=`, `TRUSTED_PROXY_CIDRS=`, `SIGNUP_RATE_LIMIT=`, `SIGNUP_RATE_WINDOW_SECONDS=`, `PASSWORD_MIN_LENGTH=`, `ARGON2_TIME_COST=`, `ARGON2_MEMORY_KIB=`, `ARGON2_PARALLELISM=`, `ARGON2_MAX_CONCURRENCY=` | R5; AC4 |
| `app/core/config.py` | New settings (see Approach step 2) + `model_validator` enforcing floors in `staging`/`production` | R5; AC2, AC4, AC5 |
| `app/core/errors.py` | (a) `AppError` gets optional `headers: dict[str, str] \| None` (ctor kwarg), `_app_error_handler` passes them to `JSONResponse`; (b) new `RateLimitedError(AppError)` 429 `rate_limited`; (c) `_validation_error_handler` keeps only `type`, `loc`, `msg` per error (drops `input`, `ctx`, `url`) | AC3 (429 + `Retry-After`), AC8 / R11 (no echo of password/email) |
| `app/core/client_ip.py` (new) | `client_ip(request) -> str`: peer address, or `CF-Connecting-IP` only when the peer is inside `trusted_proxy_cidrs`; `ip_key(ip) -> str` (IPv4 as-is, IPv6 → /64 network) | AC5 — behind Cloudflare Tunnel the peer is cloudflared, not the user |
| `app/core/rate_limit.py` (new) | `RateLimiter` Protocol; `InMemoryRateLimiter` (fixed window per `(scope, key)`, injectable clock, expired-key eviction + max-keys cap); `RateLimit` config dataclass; dependency factory `rate_limit(scope) -> Depends` raising `RateLimitedError` with `Retry-After` | AC5 (shared component for login/forgot) |
| `app/main.py` | `create_app(settings: Settings \| None = None)`; set `app.state.settings`, `app.state.rate_limiter`, `app.state.turnstile_verifier`, `app.state.password_hasher`; include the new `auth` router via `api_router` | Testable per-app config/state; AC3–AC5 |
| `app/api/router.py` | Include `auth_router` | AC3 |
| `app/db/models.py` | Import `User` | R2 |
| `app/modules/users/__init__.py` (new) | Package | Identity module family (Assumption A6) |
| `app/modules/users/models.py` (new) | `UserStatus` StrEnum, `User` model (see Data model), `__repr__` without hash | AC1 |
| `app/modules/users/repository.py` (new) | `UserRepository.add(user)` (insert inside `session.begin_nested()`, flush, refresh) | AC1, AC3; R1 |
| `app/modules/auth/__init__.py` (new) | Package | — |
| `app/modules/auth/passwords.py` (new) | `normalize_password()` (NFKC), `PasswordPolicy` (min length in code points, common-list check), `PasswordHasher` (Argon2id via argon2-cffi, params from settings, async `hash`/`verify` run in a worker thread behind an anyio `CapacityLimiter`, sync `needs_rehash`), common-list loader (`functools.cache`) | AC2 |
| `app/modules/auth/data/common-passwords.txt.gz` (new, data) | Bundled common-password list (see Approach step 5) | AC2 |
| `app/modules/auth/turnstile.py` (new) | `TurnstileVerifier` Protocol `async verify(token, remote_ip) -> bool`; `CloudflareTurnstileVerifier` (httpx, 5 s timeout, optional `transport` ctor arg for tests, fail closed) | AC4 |
| `app/modules/auth/hooks.py` (new) | `SignupHook` Protocol `async __call__(user_id: UUID, session: AsyncSession) -> None`; `noop_signup_hook`; dependency `get_signup_hook` | Ticket scope ("calls an `on_signup` hook", USR-04) |
| `app/modules/auth/errors.py` (new) | `EmailTakenError(ConflictError)` code `email_taken`; `WeakPasswordError(AppError)` 422 `weak_password`; `TurnstileFailedError(PermissionDeniedError)` code `turnstile_failed` | AC3; R1 (services raise `AppError` subclasses) |
| `app/modules/auth/schemas.py` (new) | `SignupRequest`, `SignupResponse` (see API contract) | AC3 |
| `app/modules/auth/service.py` (new) | `SignupService.signup(data, remote_ip) -> User` (order in Approach step 8) | AC3; R1 |
| `app/modules/auth/deps.py` (new) | `get_password_hasher`, `get_password_policy`, `get_turnstile_verifier` (read `request.app.state`), `ClientIpDep` | Dependency overrides in tests; R1 |
| `app/modules/auth/router.py` (new) | `APIRouter(prefix="/auth", tags=["auth"])`, `POST /signup` with `dependencies=[Depends(rate_limit("signup"))]` | AC3 |
| `migrations/versions/20261111_<rev>_create_users.py` (new) | Autogenerated, hand-reviewed; `CREATE EXTENSION IF NOT EXISTS citext` + table; downgrade drops table then extension | AC1; R2 |
| `tests/conftest.py` | Set test env before imports (`ARGON2_MEMORY_KIB=1024`, `ARGON2_TIME_COST=1`, `ARGON2_PARALLELISM=1`); add `make_client(settings_overrides)` factory fixture (same DB-session override; `client` uses it); default `client` overrides `get_turnstile_verifier` with `FakeTurnstileVerifier` | Fast Argon2 in tests; per-test settings (rate limit); no network |
| `tests/fakes.py` (new) | `FakeTurnstileVerifier` (accepts token `"pass"`, records calls) | AC4 "test fake" |
| `tests/test_errors.py` (new) | Validation-detail stripping, headers passthrough | AC8, AC3 |
| `tests/test_passwords.py` (new) | Hasher + policy | AC2 |
| `tests/test_turnstile.py` (new) | Cloudflare verifier against `httpx.MockTransport` | AC4 |
| `tests/test_rate_limit.py` (new) | Limiter + client IP | AC5 |
| `tests/test_users_model.py` (new) | Constraint, citext round-trip, repr | AC1 |
| `tests/test_auth_signup.py` (new) | Endpoint behaviour | AC3, AC7 |
| `THIRD_PARTY_NOTICES.md` | Add SecLists (MIT) attribution for the bundled password list | Licence of bundled data |
| `docs/adr/0002-identity-abuse-controls-runtime.md` (new) | ADR: in-process rate limiter (single uvicorn process is a v1 constraint), client IP = `CF-Connecting-IP` only from `TRUSTED_PROXY_CIDRS`, Turnstile fail-closed, Argon2 in worker threads with a concurrency cap. ≤ 40 lines | Rule 7 — these outlive the ticket (USR-03 login/forgot reuse them) |

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `test_errors.py › test_validation_error_details_have_no_input_or_ctx` (POST invalid org body; each detail has only `type`,`loc`,`msg`) | AC8, R11 |
| `test_errors.py › test_app_error_headers_are_passed_through` (route raising `RateLimitedError(retry_after=7)` on a throwaway app) | AC3 429 |
| `test_users_model.py › test_status_check_rejects_unknown_value` (insert `status='banned'` → `IntegrityError` on `ck_users_status_valid`) | AC1 |
| `test_users_model.py › test_email_unique_is_case_insensitive` (insert `a@x.io` and `A@X.IO` via ORM → `IntegrityError` on `uq_users_email`) | AC1, AC7 |
| `test_users_model.py › test_email_roundtrips_as_str` (asyncpg + citext) | AC1 |
| `test_users_model.py › test_repr_has_no_password_hash` | AC2, R11 |
| `test_passwords.py › test_hash_is_argon2id_with_configured_params` (prefix `$argon2id$v=19$m=1024,t=1,p=1$`) | AC2, AC7 |
| `test_passwords.py › test_verify_and_needs_rehash` (verify true/false; `needs_rehash` true after params change) | AC2, AC7 |
| `test_passwords.py › test_policy_rejects_too_short` / `› test_policy_accepts_strong_password` | AC2 |
| `test_passwords.py › test_policy_rejects_common_password_case_insensitive` (an entry from the bundled list, upper-cased) | AC2 |
| `test_passwords.py › test_policy_counts_code_points_after_nfkc` (e.g. full-width chars normalise; 12 emoji/accents counted as code points) | AC2 |
| `test_turnstile.py › test_success`, `› test_failure_response`, `› test_hostname_not_allowed`, `› test_network_error_fails_closed`, `› test_5xx_fails_closed`, `› test_missing_secret_fails_closed_without_network_call`, `› test_cloudflare_test_secret_rejected_in_production`, `› test_sends_secret_response_remoteip` | AC4 |
| `test_rate_limit.py › test_allows_up_to_limit_then_blocks`, `› test_window_resets`, `› test_retry_after_is_seconds_left`, `› test_scopes_and_keys_are_independent`, `› test_evicts_expired_and_caps_keys` | AC5 |
| `test_rate_limit.py › test_client_ip_ignores_headers_from_untrusted_peer` (`CF-Connecting-IP`, `X-Forwarded-For` ignored), `› test_client_ip_uses_cf_header_from_trusted_peer`, `› test_invalid_cf_header_falls_back_to_peer`, `› test_ipv6_keyed_by_64` | AC5, S3 |
| `test_auth_signup.py › test_signup_creates_unverified_active_user` (201; DB row has `email_verified_at IS NULL`, `status='active'`, hash verifies with the password) | AC3, AC2, AC7 |
| `test_auth_signup.py › test_signup_response_has_exact_keys` (`{"id","email","email_verified","created_at"}`, `email_verified is False`, email lower-cased) | AC2, AC3 |
| `test_auth_signup.py › test_duplicate_email_any_case_returns_409` (`Bob@Example.com` then `bob@example.COM` → 409 `email_taken`; body has no email, no id) | AC3, AC7 |
| `test_auth_signup.py › test_weak_password_too_short_returns_422` / `› test_common_password_returns_422` (code `weak_password`, `details.reasons`) | AC3 |
| `test_auth_signup.py › test_invalid_email_returns_422` (code `validation_error`) | AC3 |
| `test_auth_signup.py › test_unknown_field_and_oversized_password_return_422` | AC3 (extra=forbid, max 128) |
| `test_auth_signup.py › test_invalid_turnstile_returns_403_and_creates_no_user` | AC3, AC4 |
| `test_auth_signup.py › test_signup_rate_limited_returns_429_with_retry_after` (app with `signup_rate_limit=2`; 3rd request — even a malformed one — → 429, `Retry-After` integer header, `details.retry_after`) | AC3, AC5 |
| `test_auth_signup.py › test_password_never_echoed_in_error_bodies` (distinctive password in 422 too-long, 422 bad email, 403, 409 cases; `password not in resp.text`, full email not in `resp.text` for 409/422) | AC3, AC8, R11 |
| `test_auth_signup.py › test_on_signup_hook_called_with_user_id` (override `get_signup_hook` with a recorder) | ticket scope (hook) |
| `test_auth_signup.py › test_turnstile_receives_client_ip` (fake records `remote_ip` == test client peer) | AC4, AC5 |

## Approach
1. **Dependencies.** `uv add argon2-cffi email-validator httpx` (moves httpx to runtime); `uv lock`. Dev group keeps everything else. (Not a stack change: libraries inside the existing stack — Assumption A9.)
2. **Settings (`app/core/config.py`, R5).** Add:
   - `turnstile_secret_key: SecretStr | None = None`, `turnstile_allowed_hostnames: list[str] = []` (empty = don't check hostname; set in prod).
   - `trusted_proxy_cidrs: list[str] = []` (validated with `ipaddress.ip_network`).
   - `signup_rate_limit: int = 20`, `signup_rate_window_seconds: int = 60` (S3 "~20 req/min").
   - `password_min_length: int = 12` (validator: ≥ 8), `password_max_length` is a constant 128 (schema).
   - `argon2_time_cost: int = 3`, `argon2_memory_kib: int = 65536`, `argon2_parallelism: int = 4`, `argon2_max_concurrency: int = 2`.
   - `model_validator(mode="after")`: when `app_env in {"staging","production"}` refuse `argon2_memory_kib < 19456` or `argon2_time_cost < 2` (OWASP floor). Do **not** refuse to start on a missing Turnstile secret (Assumption A5).
3. **Errors (`app/core/errors.py`, R6).** `AppError.__init__(message, details=None, headers=None)`; handler passes `headers=exc.headers`. `RateLimitedError(retry_after: int)` → 429, code `rate_limited`, message "Too many requests", `details={"retry_after": n}`, `headers={"Retry-After": str(n)}`. Validation handler: `[{"type": e["type"], "loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]`. (R6's code list lacks 429; the owner-written AC requires it — follow-up F1 amends R6.)
4. **Client IP + limiter (`app/core/client_ip.py`, `app/core/rate_limit.py`).** `client_ip`: peer = `request.client.host` (fallback `"unknown"`); if peer ∈ any trusted CIDR and `CF-Connecting-IP` parses as an IP → that IP; never read `X-Forwarded-For`. `ip_key`: IPv6 → `str(ip_network(f"{ip}/64", strict=False))`. `InMemoryRateLimiter.hit(scope, key, limit, window) -> int | None` (None = allowed, else seconds until reset, min 1); fixed window keyed `(scope, key)`; on each hit purge expired entries when size > `max_keys` (default 10 000), and if still over, drop oldest windows. No lock needed (single event loop, no awaits inside `hit`). `rate_limit(scope)` returns a dependency that reads limits for that scope from `app.state.settings` (mapping `{"signup": (signup_rate_limit, signup_rate_window_seconds)}`; USR-03/04 add `login`/`forgot`), computes `ip_key(client_ip(request))`, and raises `RateLimitedError`. Limiter instance is `app.state.rate_limiter` (fresh per `create_app`, so tests are isolated).
5. **Passwords (`app/modules/auth/passwords.py`, R11).** `normalize_password(pw) = unicodedata.normalize("NFKC", pw)` — no trimming. Policy: `reasons = []`; `len(normalized) < min` → `"too_short"`; `normalized.lower() in common_set` → `"too_common"`; non-empty → `WeakPasswordError("Password does not meet the policy", details={"reasons": reasons})`. Common list: fetch SecLists `Passwords/Common-Credentials/10-million-password-list-top-100000.txt` (MIT), keep lines with length ≥ 8, lowercase, dedupe, gzip into `app/modules/auth/data/common-passwords.txt.gz`; load once into a `frozenset` (`functools.cache`, `importlib.resources`/`Path(__file__)`). If the list cannot be downloaded in the implementer's environment, stop and record it in impl-log (do not hand-write a list). `PasswordHasher` wraps `argon2.PasswordHasher(time_cost, memory_cost, parallelism, type=Type.ID)`; `async hash(pw)` / `async verify(hash, pw) -> bool` (catch `VerifyMismatchError`/`InvalidHashError` → False) run via `anyio.to_thread.run_sync(..., limiter=self._limiter)` where the `CapacityLimiter(argon2_max_concurrency)` is created lazily on first use; `needs_rehash(hash)` sync. Both hash and verify always take the NFKC-normalised password (USR-03 must use the same function).
6. **Turnstile (`app/modules/auth/turnstile.py`).** `CloudflareTurnstileVerifier(settings, transport=None)`: if secret missing, or `app_env == "production"` and secret starts with a Cloudflare test-key prefix (`1x0000000000000000000000000000000`, `2x…`, `3x…`) → log error "turnstile secret not configured", return False (fail closed, no network call). Else POST form `{secret, response: token, remoteip}` to `https://challenges.cloudflare.com/turnstile/v0/siteverify` with `httpx.AsyncClient(timeout=5.0, transport=transport)`; any `httpx.HTTPError`, non-200, or non-JSON → log warning (status / exception class only), return False. Return `body.get("success") is True and (not allowed_hostnames or body.get("hostname") in allowed_hostnames)`; on failure log Cloudflare `error-codes` only — never the token or secret.
7. **Users model + migration (R2).** Model per Data model. Generate with `make migration m="create users"`, then hand-edit: first statement `op.execute("CREATE EXTENSION IF NOT EXISTS citext")`; ensure the email column is `postgresql.CITEXT()`; downgrade `op.drop_table("users")` then `op.execute("DROP EXTENSION IF EXISTS citext")` with a comment that a later migration using citext must move/remove that line. Run `alembic check` → no drift.
8. **Signup service (`app/modules/auth/service.py`, R1).** Order (cheapest first; rate limit already ran as a route dependency, before body validation errors are raised):
   1. `email = str(data.email).strip().lower()` (EmailStr already validated with email-validator, no DNS).
   2. `policy.check(password)` → `WeakPasswordError` (422).
   3. `await verifier.verify(token, remote_ip)` → False ⇒ `TurnstileFailedError("Verification failed")` (403).
   4. `password_hash = await hasher.hash(password)` — always before touching the DB, so 201 and 409 take similar time (security consult #6).
   5. `repo.add(User(email=email, password_hash=…))` inside `session.begin_nested()`; catch `IntegrityError` → `EmailTakenError("Email is already registered")` (409, no email/id in message or details). No pre-check SELECT (no race, no timing oracle).
   6. `await hook(user.id, session)` — same transaction; default no-op. Docstring: hooks only record/enqueue, no slow I/O (USR-04 must also apply its per-email send limits to the first verification mail — follow-up F6).
9. **Router (`app/modules/auth/router.py`, R1, R3).** `POST /signup` → `SignupResponse`, 201; public by design (R3 class 3); `dependencies=[Depends(rate_limit("signup"))]`; calls the service with injected deps; returns `SignupResponse(id, email, email_verified=user.email_verified_at is not None, created_at)`. No `Location` header (the user resource is a staff endpoint).
10. **App wiring.** `create_app(settings=None)`: `settings = settings or get_settings()`; set `app.state.*` (step 4–6); `docs_url` unchanged. `app = create_app()` stays at module level.
11. **Tests + conftest.** As listed; `make check` green.
12. **Docs.** ADR-0002 (short) and THIRD_PARTY_NOTICES entry.

Rules: R1 (router → service → repository; service raises `AppError` subclasses only), R2 (hand-reviewed migration, model registered, `alembic check`), R3 (signup is a public identity endpoint by design), R4 (n/a — signup is not an admin action; staff disable/enable audit comes with slice 2/ADM-004), R5 (all knobs in `Settings`, `.env.example` keys without values, secret as `SecretStr`, no secrets logged), R6 (envelope everywhere; 429 is an AC-required addition → F1), R7 (staff endpoints, R6 amendment, CORS alignment → follow-ups), R8 (tests above), R9 (`/api/v1/auth/signup` per D13; UUID; timestamptz), R10 (no social login/OIDC), R11 (Argon2id, no hash/password/email in responses/logs/errors).

## Specialist input used
**software-architect**
- Adopted: flat sibling modules `users/` + `auth/` instead of nested `identity/` — keeps the reference-module shape; "Identity module" = this family (A6).
- Adopted: `RateLimiter` Protocol + in-memory impl in `app/core/rate_limit.py`, `rate_limit(scope)` dependency, limits from config — exact for one uvicorn process.
- Adopted: `TurnstileVerifier` Protocol, httpx impl, fake via `dependency_overrides`, fail closed.
- Adopted: `AppError.headers` + `RateLimitedError`; R6 amendment as follow-up F1.
- Adopted: `SignupHook` Protocol, no-op default, same transaction.
- Adopted: split into 01a/01b — estimate confirmed > 400 lines (see Split proposal).
- Rejected: 400 for Turnstile failure — 400 is not in R6 and the request is well-formed; 403 `turnstile_failed` (A3).
- Rejected: `CLIENT_IP_HEADER` trusted solely because "port 8000 is tunnel-only" — header trusted only from `TRUSTED_PROXY_CIDRS` (defence if the port is ever published; dev compose does publish 8000).

**database-optimizer**
- Adopted: `CITEXT` unique column (`uq_users_email`), no functional index, no normalized duplicate column.
- Adopted: hand-written `CREATE EXTENSION IF NOT EXISTS citext`; downgrade drops table then extension (symmetric for conftest's `downgrade base`).
- Adopted: `password_hash` as `Text`, status `String(20)` + `ck_users_status_valid`, `email_verified_at` nullable timestamptz, `display_name String(100)` nullable, `TimestampMixin`.
- Adopted: `IntegrityError` → 409 (handles concurrent signups); citext round-trip test.
- Adopted: no limiter table; in-memory now, PG UNLOGGED design noted for later (F4).
- Rejected (for this slice): staff list indexes / email "contains" filter — belong to slice 2; no index needed there either per the consult.
- Rejected: `char_length(email) <= 254` check — email-validator already enforces it; not required by the AC.

**api-platform-engineer**
- Adopted: `SignupRequest` with `extra="forbid"`, `password` max 128, `turnstile_token` 1..2048; policy checks in the service → `422 weak_password` with `details.reasons`.
- Adopted: strip `input`/`ctx` from 422 details globally.
- Adopted: 201 body `{id, email, email_verified, created_at}`, no `Location`.
- Adopted: `409 email_taken`, `403 turnstile_failed`, `429 rate_limited` + integer `Retry-After` + `details.retry_after`.
- Adopted: rate limit counts every attempt (dependency runs before body validation errors are raised), so 409 probing is throttled.
- Modified: check order — policy before Turnstile (a Turnstile token is single-use; checking the cheap local policy first avoids forcing a new challenge for a too-short password and leaks nothing).
- Adopted: keep 409 as the ticket says (A1); 202-alternative recorded as F2.
- Adopted: `email-validator` as a dependency (A9). Normalisation: strip + lowercase whole address (NFC is done by email-validator).
- Deferred to slice 2: staff endpoint shapes (PATCH `{status}`, `Page[UserRead]`, exact-keys test) and permission naming — will use ADM-002's `admin:users:read|write`.

**identity-access-engineer**
- Adopted: Argon2id t=3, m=64 MiB, p=4 defaults from config, OWASP floor enforced in staging/production, `verify` + `needs_rehash` now for USR-03, hashing in a worker thread behind a concurrency limiter (default 2, not 4 — small Intel Mac; ≈128 MiB peak).
- Adopted: NFKC, no trimming, length in code points, max 128 in the schema, no composition rules.
- Adopted (modified): bundled SecLists top-100k filtered to length ≥ 8, compared lowercased; no HIBP call in v1. `BreachedPasswordChecker` interface not added — YAGNI; F5.
- Rejected: "password == email / local part" rule — not in the AC (R7); F5.
- Adopted: `CF-Connecting-IP` only from trusted CIDRs, never `X-Forwarded-For`, IPv6 by /64.
- Adopted: siteverify with `remoteip`, 5 s timeout, `success` + hostname allowlist, fail closed, `SecretStr`.
- Rejected: `idempotency_key` retry and `action == "signup"` check — no retry is done, and our clients don't set an action yet (USR-03b); F7.
- Rejected: refusing to start when the secret is missing — fail closed per request instead (A5).
- Answered OQ1 min length: 12 default, configurable (A4). OQ2: siteverify unreachable → 403 `turnstile_failed` (fail closed, stays in R6 codes).

**security-architect**
- Adopted: strip `input`/`ctx`; `SecretStr` for the password; test that the password never appears in any 4xx body.
- Adopted: trusted-proxy client IP; limiter TTL eviction + key cap; single-process limitation documented in ADR-0002; tests for spoofed `X-Forwarded-For`.
- Adopted: hash before DB, duplicates via `IntegrityError`, generic 409 body.
- Adopted: no logging of bodies, tokens, hashes; `User.__repr__` excludes the hash.
- Adopted: hook receives the user id only; USR-04 email-bombing note → F6.
- Adopted (in slice 2): build staff endpoints but mount them only behind a config flag defaulting off — recorded in the Split proposal, so this slice adds no PII exposure.
- Modified: rate-limit before schema (yes, via dependency) → policy → Turnstile → hash → insert (policy is local and cheap; bots still never reach Argon2 without a valid Turnstile).
- Rejected: min length 10 — 12 chosen (A4).
- Not blocking: 409 vs 202 (A1, F2); unverified-account purge → F8.

## Assumptions
- **A1 — 409 on duplicate email is kept.** The ticket AC (written by the owner) explicitly says "409 duplicate email (any case)"; D11's identical-error rule is scoped to login (spec §5.8 "the same error for unknown email and wrong password"). Mitigations: Turnstile on every signup, per-IP limit, hash-before-insert (no timing difference), generic 409 body. Switching to a non-enumerating 202 later would change the v1 contract (R9) — raised to the owner as F2 to confirm **before the first release that includes signup**; not blocking because the owner already decided it in the AC.
- **A2 — Staff endpoints (AC 6) are not in this slice;** they go to USR-01b with the gated-mount design (Split proposal). So the question "may `/api/v1/users` be public before ADM-002" does not block this plan; it is surfaced for USR-01b.
- **A3 — Invalid Turnstile → 403 `turnstile_failed` only** (not 400): in R6's list, request is well-formed; siteverify unreachable/5xx/timeout also → 403 (fail closed; logged).
- **A4 — Password policy:** min 12 code points after NFKC (env `PASSWORD_MIN_LENGTH`, floor 8), max 128 (schema), no composition rules, common-list check case-insensitive. NIST SP 800-63B-4 asks 15 for single-factor passwords; 12 is a UX compromise the owner can raise via env at any time without code change (affects new passwords only) — F3.
- **A5 — Missing Turnstile secret does not crash startup;** signup fails closed (403) and logs an error. Rationale: a missing signup secret must not take down the existing admin API on deploy; safety is identical since no signup succeeds. The fake verifier is never selectable by config (tests use `dependency_overrides` only). Cloudflare test secrets are treated as missing in production.
- **A6 — Layout:** `app/modules/users/` (model, repository; staff router in slice 2) and `app/modules/auth/` (signup, passwords, turnstile, hooks). Dependency direction auth → users only. D15's "Identity module" is this family of modules; no nested package.
- **A7 — In-memory limiter is correct for v1:** the Dockerfile runs one uvicorn process, one container. Resets on restart are accepted. Must be revisited before adding workers/replicas (ADR-0002, F4).
- **A8 — Client IP:** `CF-Connecting-IP` is used only when the direct peer is in `TRUSTED_PROXY_CIDRS`; default empty (= use the peer). **Until the deploy sets `TRUSTED_PROXY_CIDRS` to the Docker network of cloudflared, all production signups share one bucket (20/min)** — safe but degraded; F9 is a release prerequisite.
- **A9 — `argon2-cffi`, `email-validator` and moving `httpx` to runtime are libraries inside the agreed stack**, not a stack change.
- **A10 — Email normalisation:** `EmailStr` (email-validator, no DNS check) then strip + lowercase the whole address before storing; dots and `+tags` are kept. citext guarantees case-insensitive uniqueness regardless.
- **A11 — `display_name` column ships now** (nullable, in §3) to avoid a second migration; not exposed or settable in this slice.
- **A12 — 422 details shape change** (dropping `input`/`ctx`/`url`) also applies to existing organizations endpoints; the `details` content was never part of a documented contract, so this is not a breaking change under R9.
- **A13 — Prod DB privilege for `CREATE EXTENSION citext`:** citext is a trusted extension (PG ≥ 13); works if role `org_admin` owns database `org_admin`. If not, the owner pre-creates it once as superuser (`IF NOT EXISTS` makes the migration tolerant) — F10, release prerequisite check.

## Blocking questions
None.

## Split proposal
Estimated hand-written, non-test code for the full ticket ≈ 600 lines (core errors/config/client-IP/limiter ≈ 140, users model/repo ≈ 60, auth passwords/turnstile/hooks/schemas/service/router/deps ≈ 230, staff endpoints schemas/repo/service/router/gating ≈ 170) — over the ~400 guide and two spec features (§5.8 Sign-up/Passwords and §5.8 Admin).

- **Slice 1 — USR-01 (this plan):** `users` table + migration, Argon2id hasher + policy, `POST /api/v1/auth/signup`, Turnstile interface + Cloudflare impl + fake, per-IP limiter + client IP, 429 support, 422 input stripping, `on_signup` hook. Ticket AC 1–5, 7 (for these), 8. ≈ 430 lines.
- **Slice 2 — USR-01b (new ticket, to be created by the orchestrator via `/ticket`):** staff endpoints `GET /api/v1/users` (`Page[UserRead]`, filters `email` = case-insensitive contains with LIKE escaping, `status`; order `created_at desc, id`), `GET /api/v1/users/{id}`, `PATCH /api/v1/users/{id} {status: active|disabled}` (extra=forbid, explicit null → 422); `UserRead {id, email, status, email_verified_at, display_name, created_at, updated_at}` with an exact-keys test; permissions `admin:users:read` (list/get) and `admin:users:write` (PATCH), matching ADM-002's catalog, declared per route (`openapi_extra={"x-permission": …}`). **Exposure:** recommended design is to build and test everything but mount the users router only when `ADMIN_USERS_API_ENABLED=true` (default `false`; tests enable it), until ADM-002 lands. That is the owner's call because spec §6.1's accepted risk ("anyone who can reach the hostname can call the admin endpoints") was made before user PII and a disable action existed — USR-01b's planner should either get the owner's answer or apply the default-off flag. Ticket AC 6 and its tests. Depends on USR-01.

## Data model / migration
Table `users` (model `app/modules/users/models.py::User(UUIDPkMixin, TimestampMixin, Base)`):

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | `uuid` | no | app-side `uuid4` | `pk_users` |
| `email` | `citext` (`sqlalchemy.dialects.postgresql.CITEXT`) | no | — | `uq_users_email` (unique, case-insensitive); stored stripped + lowercased |
| `password_hash` | `text` | no | — | Argon2id PHC string; never selected into any schema |
| `status` | `varchar(20)` | no | `'active'` (server + app default) | `ck_users_status_valid`: `status IN ('active', 'disabled')`; `UserStatus` StrEnum |
| `email_verified_at` | `timestamptz` | yes | NULL | NULL = unverified |
| `display_name` | `varchar(100)` | yes | NULL | not exposed in this slice |
| `created_at` | `timestamptz` | no | `now()` | `TimestampMixin` |
| `updated_at` | `timestamptz` | no | `now()`, `onupdate` | `TimestampMixin` |

No other indexes (the unique index serves email lookups; user count is small).

Migration `migrations/versions/20261111_<rev>_create_users.py`, `down_revision = "b1c724bc5edb"`:
- `upgrade()`: `op.execute("CREATE EXTENSION IF NOT EXISTS citext")`; `op.create_table("users", …)` with `op.f()` constraint names as autogenerated.
- `downgrade()`: `op.drop_table("users")`; `op.execute("DROP EXTENSION IF EXISTS citext")` (comment: remove this line once another table uses citext).
- Purely additive → safe for the "previous image on new schema" rollback model (docs/deploy.md).
- Verify: `alembic check` reports no drift (CITEXT reflects as CITEXT); conftest exercises downgrade/upgrade.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/signup` | `SignupRequest` (`extra="forbid"`): `email: EmailStr`, `password: SecretStr` (`max_length=128`), `turnstile_token: str` (`min_length=1`, `max_length=2048`) | 201 `SignupResponse {id: uuid, email: str, email_verified: false, created_at: timestamptz}` | 201 created (unverified, active) · 403 `turnstile_failed` · 409 `email_taken` (no email/id in body) · 422 `weak_password` (`details: {"reasons": ["too_short" \| "too_common", …]}`) · 422 `validation_error` (bad email, unknown field, too-long password, missing field; details = `[{type, loc, msg}]` only) · 429 `rate_limited` (`Retry-After: <int seconds>` header, `details: {"retry_after": n}`) | **Public by design** (R3 class 3); per-IP rate limit scope `signup` (default 20 / 60 s); Turnstile required |

Evaluation order: per-IP limit (counts every attempt) → body validation → password policy → Turnstile → Argon2id hash → insert (`IntegrityError` → 409) → `on_signup(user_id)` hook → commit.

Staff `/api/v1/users` endpoints: none in this slice (USR-01b).

## Follow-ups (not in this diff, R7)
- **F1** Amend CLAUDE.md R6 to list 429 (`rate_limited`, with `Retry-After`) and note 403 `turnstile_failed`.
- **F2** Owner to confirm before the first release with signup: keep 409 on duplicate email (A1), or switch to a non-enumerating 202 + "you already have an account" email once USR-04 exists (would be a v1 contract change if done later).
- **F3** Owner may raise `PASSWORD_MIN_LENGTH` to 15 (NIST 800-63B-4 single-factor) — env only.
- **F4** Shared (Postgres UNLOGGED or Redis) limiter before running >1 worker/replica; USR-03 per-email backoff needs persistence anyway.
- **F5** Context-specific password checks (password ≠ email/local part) and optional HIBP k-anonymity check.
- **F6** USR-04: apply the 1/min and 5/day per-email send limits to the first verification mail sent from `on_signup`, not only to resends.
- **F7** Turnstile `action` check (`"signup"`) once `@mmlabs/auth-client` (USR-03b) sets it; idempotency-key retry if needed.
- **F8** Purge of never-verified accounts after N days (bot clutter) — owner decision.
- **F9** Deployment repo: set `TRUSTED_PROXY_CIDRS` (cloudflared's Docker network), `TURNSTILE_SECRET_KEY`, `TURNSTILE_ALLOWED_HOSTNAMES` for `api.mmlabs.online` before releasing USR-01.
- **F10** Verify role `org_admin` owns database `org_admin` (citext `CREATE EXTENSION`), or pre-create citext as superuser.
- **F11** `CORS_ORIGINS` must include every app origin that calls signup until USR-03 brings `apps.origins`.
- **F12** Create ticket USR-01b (staff user endpoints) per the Split proposal.
