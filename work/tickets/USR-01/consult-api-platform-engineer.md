# consult-api-platform-engineer — USR-01

**Findings**
- `app/core/errors.py`: `AppError` cannot set response headers, so `429` + `Retry-After` is not expressible yet. R6 lists 201/204/401/403/404/409/422 only — 400 and 429 aren't in it.
- `_validation_error_handler` returns `jsonable_encoder(exc.errors())` as `details`. Pydantic v2 errors include `input`, so a `Field(min_length=…)` failure on `password` would echo the plaintext password in the 422 body — violates R5/R11.
- `pyproject.toml` has no `email-validator`, so `EmailStr` can't be used without adding a dependency.
- Reference module precedent: `PATCH /organizations/{id}` already changes `status` (active|suspended); `Page[T]` + `page_params` (≤100) are reusable as-is.
- Behind Cloudflare Tunnel, `request.client.host` is the tunnel/proxy, not the end user — a per-IP limiter on it would throttle everyone together.

**Recommendations**
1. **Signup request (AC3):** `SignupRequest {email: str (≤254), password: str (max_length 128 only), turnstile_token: str (1..2048)}`, `extra="forbid"`. Do length/common-password checks in the service, not Pydantic, raising `WeakPasswordError` (422, `weak_password`, `details: {"reasons": ["too_short"|"too_common"]}`) — never echo the value (R11).
2. **Validation leak (R5/R11, AC2):** strip `input` (and `ctx`) from each error in the 422 handler before returning. Small core change; the planner should list it explicitly and have appsec review it.
3. **Email normalization (AC1/AC3):** in the service, not the router: strip, NFC, lowercase the whole address; store normalized; citext still guarantees case-insensitive uniqueness. Validate with a strict regex (or add `email-validator` — a deliberate dependency choice). Bad email → 422 `invalid_email`. Never put the email in error messages (R11).
4. **201 body (AC3, "reveal no more than needed"):** `SignupResponse {id, email, email_verified: false, created_at}`. No `status`, no hash, no `display_name`. `Location: /api/v1/users/{id}`? No — that's a staff resource; omit.
5. **Error codes (AC3, R6):** `409 email_taken` · `422 weak_password | invalid_email | validation_error` · `403 turnstile_failed` (prefer 403 over 400: the request is well-formed, the check failed — pick one, not both) · `429 rate_limited` with `Retry-After: <seconds>` (integer) and `details: {"retry_after": n}`. Add `RateLimitedError(AppError)` carrying a `headers` attribute; make `_app_error_handler` pass headers through. Amend R6 to list 403-turnstile/429. Order of checks: rate limit → Turnstile → policy validation → uniqueness, so 409 probing is throttled (the limiter counts every attempt).
6. **409 vs enumeration (D11):** keep 409 as the ticket says — D11 covers login, and signup UX needs "email already registered". Mitigation is Turnstile on every request + per-IP limit. Stronger alternative once USR-04 lands: always `202` and email the existing owner — would change the 201 body; ask the owner now because changing it later inside v1 is a breaking change (R9).
7. **Staff endpoints (AC6, R3, R9):**
   - `GET /api/v1/users?email=&status=&limit=&offset=` → `Page[UserRead]` → `admin:users:read`. `email` filter = case-insensitive prefix (or exact) match on normalized value — state which. Order `created_at desc, id`.
   - `GET /api/v1/users/{id}` → `admin:users:read`; 404 `not_found`.
   - `PATCH /api/v1/users/{id} {status: "active"|"disabled"}` → `admin:users:update` (or `admin:users:disable`). Use PATCH, not action endpoints: matches organizations and R9, idempotent; reject explicit `null`/unknown fields with 422.
   - `UserRead {id, email, status, email_verified_at, display_name, created_at, updated_at}` — explicit allowlist, no `password_hash`; add a test that asserts the JSON keys exactly (AC2/AC7).
8. **Router placement (D13):** `auth_router` prefix `/auth` (signup) and `users_router` prefix `/users` in the identity module, both included in `api_router`.

**Risks**
- Per-IP limit is useless/harmful without the trusted-proxy rule: read `CF-Connecting-IP` only when the request comes from a configured trusted proxy address (R5).
- In-process memory limiter resets on restart and isn't shared across replicas — acceptable for one container, document it.
- A disabled user can sign up again? No — 409 `email_taken`; correct, but reveals existence.
- Turnstile fake must not be enabled in production; fail closed if the secret key is missing outside local/test.

**Open questions**
1. 403 vs 400 for Turnstile failure — I recommend 403 only; owner or planner to decide.
2. Keep 409 on duplicate email, or switch to 202 + email-to-owner once USR-04 lands (point 6)?
3. Permission key naming: `admin:users:update` vs a more specific `admin:users:disable` — must match the ADR-0001 catalog.
4. `email` list filter: exact or prefix match?
5. Is adding `email-validator` acceptable under "do not change the stack" (it's a dependency, not a stack change)?
