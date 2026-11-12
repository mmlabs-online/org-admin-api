# Security review — USR-03

## Attempt 1

**Verdict: no 🔴 blockers. 3 🟡 should-fix, 8 🟢 notes.** `make check` is green: ruff, mypy (62 files) and pytest (619 passed).

The core controls are in place and correct:
- **Cookie:** 256-bit value, only SHA-256 stored, `HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth`, no `Domain`, and `__repr__` hides the hashes.
- **Rotation:** the CAS uses the database clock, and the losing request re-reads and is accepted as a previous value within grace, never as reuse.
- **Reuse:** reuse after grace revokes the device and commits before the 401. On logout, `AppContextDep` (the Origin check) runs before `DeviceDep`.
- **Fixation:** a device with no active account is replaced at login. A previous value only reaches account sessions created before `rotated_at`. Two `mm_device` cookies give a 401 with no revocation.
- **Login timing and enumeration:** dummy Argon2 for unknown emails. Unknown emails, wrong passwords and disabled users get the same 401. Unknown emails are counted like known ones. The HMAC key is required in staging/production.
- **Origin check:** `normalize_origin` only accepts `https://*.mmlabs.online` or exactly `localhost` / `127.0.0.1` / `[::1]`, so `_is_loopback` covers every loopback form that can be stored. Loopback origins are rejected in staging/production.
- **No secrets leak:** the 422 handler strips `input`/`ctx`, and the log lines contain only ids.
- **Migration:** new tables only, and the downgrade is correct.

### Findings

🟡 **S1 — Per-email backoff and Turnstile can be raced (check, then act later)**
- **Where:** `app/modules/auth/service.py:145-169`, `app/modules/auth/repository.py:24-41`.
- **Problem:** `failures.get()` reads the counter without a lock. The Turnstile and block decisions are made on that snapshot, but the counter only goes up after Argon2. Requests for one email sent at the same time all see the same `failures` value.
- **Risk:** at `failures < 3` an attacker can send a burst of N parallel guesses with no Turnstile. N is limited only by per-IP 20/min × number of IPs, and Argon2 requests queue rather than fail. The same works after each block ends. This weakens D11, which says Turnstile after 3 and backoff after 5.
- **Fix:** do the per-email check and the increment as one atomic step.
  - Preferred: `SELECT pg_advisory_xact_lock(hashtextextended(:email_hmac_hex, 0))` before `failures.get()`. This makes logins for one email take turns; the lock is released at commit, including the commit before raise. It cost only one Argon2 per email at a time.
  - Alternative: upsert-and-reserve the attempt first (`failures+1 RETURNING`). Decide Turnstile and block on the value before the reservation, and delete the row on success.
- **Test:** add one using `asyncio.gather` that checks two parallel attempts at `failures=2` both get `turnstile_required`.

🟡 **S2 — The commit happens after the response (and its `Set-Cookie`) is sent**
- **Where:** `app/db/session.py` `get_session` (existing code); it affects `app/modules/auth/router.py:105,136,138`.
- **Cause:** FastAPI 0.162.2 runs default-scope `yield` dependencies after `await response(...)` (`fastapi/routing.py` ~152-155). So `session.commit()` runs after the 200/204 and the new or cleared cookie have reached the client.
- **Problem 1 (logout):** if that commit fails (connection drop, serialization error), the client sees 204 and a cleared cookie, but the session stays active on the server. Logout is not guaranteed.
- **Problem 2 (login / 24 h rotation), failed commit:** the client gets a new cookie that was never saved, and it no longer has the old value. The session is lost.
- **Problem 3 (login / 24 h rotation), normal race:** a client that uses the new cookie right away (for example `/token` straight after login in USR-03c) can arrive before the commit. It gets `session_invalid` and a cookie-clearing `Set-Cookie`, which logs the user out.
- **Fix:** commit before the response for these endpoints. Either use `SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]` (the repo-wide change, which fits R1/R6 better), or commit explicitly at the end of `LoginService.login`, `LogoutService.logout` and the rotating `resolve`.
- **Test:** with the strict client, patch `commit` to raise on success and assert that no `Set-Cookie` is sent.

🟡 **S3 — Cookie tossing from a sibling `*.mmlabs.online` subdomain (owner decision; must be fixed before USR-03c/03b)**
- **Where:** `app/modules/auth/cookies.py:26-35` (the cookie name is `mm_device`, not a prefixed name).
- **Attack:** any sibling app on `mmlabs.online` that has an XSS or is compromised can set `mm_device=<attacker device>; Domain=mmlabs.online; Path=/api/v1/auth`. That attacker device already has the attacker's account signed in.
- **Effect on a victim with no host-only cookie:**
  - This slice: their next login only rotates the attacker's device, so no takeover yet.
  - Once USR-03c/03b ship: `/token` and `/accounts` silently act as the attacker's account. This is classic login CSRF.
- **Effect on a victim who does have the host-only cookie:**
  - Tossing a cookie with a more specific path (`Path=/api/v1/auth/logout`, or later `/token`) makes every such request send two cookies, so the victim gets a 401 every time. That is a lasting denial of service, including being unable to log out. The clearing `Set-Cookie` only removes the host-only cookie (A7).
- **Fix:** name the cookie `__Host-mm_device`. The browser then refuses any `Domain=` or non-`/` path version. This needs `Path=/`, which conflicts with spec/R11 `Path=/api/v1/auth` and must be flagged to the owner. Sending the cookie to admin Bearer endpoints is harmless because they ignore it. If the owner keeps the current path, record the risk in ADR-0004 and make the existing "clear tossed cookie" follow-up a dependency of USR-03c.

🟢 **N1 — Previous-value handling**
- `LogoutService.logout` (`service.py`, logout) ignores `accounts_created_before`. Someone holding the previous value within the 60 s grace can revoke account sessions created after the rotation, or the whole device with `{all:true}`. The impact is only a denial of service inside 60 s, but USR-03b and USR-03c must apply the filter for reads and token minting.
- Login with a previous value inside the grace window (`sessions/service.py:86-87` reached from `start_for_login`) adds an account session dated after `rotated_at`, and it is not visible through that same previous value. This is a correctness issue, not a security one.

🟢 **N2 — Reuse limits (by design, D6)**
- A client that loses one rotated response because of a network drop, then comes back after 60 s, revokes its own device.
- Only one previous value is kept, so a cookie stolen two rotations ago gives a plain 401 with no revocation.
- Both are already documented. A log line at `sessions/service.py:92` would help tell these cases apart.

🟢 **N3 — Timing**
- `verify_dummy` (`passwords.py:83-87`) uses the current Argon2 parameters. A real user whose hash used older parameters takes a different time, which is a small enumeration signal. It goes away once rehash-on-login (follow-up) lands.
- The first unknown-email request per process also pays a one-off hash.
- Signup's 409 `email_taken` already reveals which emails exist (existing code), so the login timing work protects only login.

🟢 **N4 — Lock held during Argon2.** `get_by_email_for_share` holds a `FOR SHARE` row lock during Argon2 (`service.py` around 157-161). This is acceptable, but status changes for that user are blocked for the length of the login.

🟢 **N5 — `login_failures` growth.** Every failed login for a new email inserts a row. Per-IP 20/min is per address, so rotating IPv6 addresses grow the table without bound. Cleanup is already a follow-up; consider limiting by IPv6 /64 in `client_ip` as well (existing limiter).

🟢 **N6 — Key strength.** `IDENTITY_HMAC_KEY` is checked for length (32 characters), not randomness. `deploy.md` gives a correct generation command, so this is acceptable.

🟢 **N7 — Commit before raise is correct.** In both places (reuse revocation in `sessions/service.py:89-93`, failure counter in `auth/service.py:165-170`) nothing else is pending in the transaction except reads and the `FOR SHARE` lock. On login, `commit_on_reuse=False` keeps the revocation inside the successful login transaction. The control test proves the commit is needed.

🟢 **N8 — Migration safety.** Additive only, no locks on existing tables, and the CHECK texts match the models. `alembic check` runs in CI.

### Follow-ups
- Owner decides on the `__Host-mm_device` prefix with `Path=/` vs the spec's `Path=/api/v1/auth` (S3). Gate USR-03c/03b on it, or on the "clear tossed cookie" follow-up.
- Repo-wide: make `get_session` commit before the response (`scope="function"`) so every mutating endpoint commits before replying (S2).
- Make the per-email check atomic, with a concurrency test (S1, if not fixed in this attempt).
- USR-03b/03c: apply `accounts_created_before` to `/accounts`, `/token` and the logout `{account_id}` path (N1).
- Already in the plan: rehash on login (N3); cleanup jobs for `login_failures` and sessions (N5); a global failure-rate trigger that makes Turnstile mandatory; rejecting `Sec-Fetch-Site: cross-site`; exclusive origins for `admin`.
- Consider limiting per-IP by IPv6 /64 in the rate limiter (N5).
