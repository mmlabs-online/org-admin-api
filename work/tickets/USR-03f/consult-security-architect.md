# consult-security-architect — USR-03f

**Findings**
- With the `__Host-` prefix, a sibling `*.mmlabs.online` host can no longer set the device cookie. The prefix forbids `Domain` and requires `Secure` and `Path=/`, and the sibling has no way to write a host-only cookie for `api.mmlabs.online`. This closes S3.
- `Path` was never a security boundary within one origin. Script running on `/docs` could already send requests to `/api/v1/auth/*`. So `Path=/` adds no new CSRF exposure. Admin routes only honour Bearer tokens, and the routes that are still unauthenticated before ADM-002 are exposed whether or not the cookie is sent. The `HttpOnly` flag keeps the cookie away from Swagger's JavaScript.
- Origin check: `origins.py:42-43` already refuses `api.mmlabs.online` as an app origin. Scripts on `/docs` therefore fail the Origin/`X-MM-App` check.
- One tossing path is left: an XSS on `/docs` (same host, CDN-loaded Swagger assets) can set a non-HttpOnly `__Host-mm_device` when the victim has no cookie yet. That is login CSRF / fixation. It existed before this change; the owner accepted public `/docs` on 2026-11-11.
- `SameSite=Lax` is enough. Every app is same-site, so `Strict` would only add protection against top-level cross-site GETs, and no cookie GET returns anything an attacker can read.

**Recommendations**
- **S3, cookie handling** (`cookies.py`):
  - Rename `DEVICE_COOKIE` to `__Host-mm_device`.
  - Set `path="/"`, `secure=True`, no `domain`.
  - Clearing must use the same name, `Path=/` and `Secure`, or the browser ignores the clearing `Set-Cookie`.
  - Match the name exactly and case-sensitively. A tossed `mm_device` or `__HOST-mm_device` is ignored and does not count toward the duplicate-401 rule.
  - Keep the duplicate check.
  - Update the `APIKeyCookie` name in `router.py`.
- **S3, tests:**
  - The `Set-Cookie` attributes are exact.
  - A request with a legacy/tossed `mm_device` plus a valid `__Host-` cookie gets 200.
  - An admin or health route sent only the cookie gains nothing.
- **S1:** do the reserve step under `SELECT … FOR UPDATE` after an upsert of the failure row:
  - Decide block/Turnstile from the value before the increment.
  - Increment and commit the reservation before Argon2, and delete the row on success.
  - Do not use a blocking `pg_advisory_xact_lock` held across Argon2. Waiting requests each hold a pool connection, so a burst for one email can exhaust the pool and take the whole service down. If an advisory lock is chosen anyway, use `pg_try_advisory_xact_lock` and answer 429 when it is busy.
  - The concurrency test needs two real sessions with committed data and explicit cleanup, not the rollback-per-test connection.
- **S2:** make the change repo-wide with `Depends(get_session, scope="function")`. It fails closed: a failed commit gives a 500 from the error handler and no merged `Set-Cookie`. Update the strict client fixture to match. Commit-before-raise stays as it is.
- **N1:** the logout `{account_id}` path applies `accounts_created_before`.
- **Docs to amend** (the plan must not touch CLAUDE.md R11/R3; the owner edits those):
  - spec §5.8: the lines on cookie name and `Path=/api/v1/auth`.
  - identity-architecture.md: the diagram (line 18), §3 intro (line 124), §5 S1 (lines 152-153), and a new D20 that amends D5/D13 for the cookie only (endpoints stay under `/api/v1/auth`).
  - ADR-0004: §1, and replace the "clear tossed cookie" follow-up.

**Risks**
- Existing `mm_device` cookies stop working, so every device has to sign in again and the old rows sit orphaned until cleanup. This is acceptable before launch.
- Public `/docs` on the cookie origin remains the main way left to set or toss the cookie.
- Local dev: `Secure` cookies over `http://localhost` work in Chrome and Firefox but not reliably in Safari. Tests should assert raw headers rather than rely on the httpx cookie jar.

**Open questions**
- Should we send a one-off clearing `Set-Cookie` for the legacy host-only `mm_device; Path=/api/v1/auth` on login? It is optional cleanup and not needed for security.
- Should we add a CSP to `/docs` or take it out of production now that the cookie reaches that path? This is outside this ticket's scope, so it goes on the follow-up list.
- Is `SameSite=Strict` wanted as defence in depth? It would be a spec change.
