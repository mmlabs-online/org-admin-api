# consult-identity-access-engineer — USR-03f

**Findings**
- **S2.** The lockfile pins FastAPI 0.162.2. In `routing.py:148-153`, `function_stack` closes before `await response(...)`, and response serialization runs inside that stack, so lazy-load risk is unchanged. `Depends(get_session)` is declared in only one place (`app/api/deps.py:9`), so changing `SessionDep` alone moves every endpoint over. No generator dependency depends on `SessionDep`, so `DependencyScopeError` cannot fire. `dependency_overrides` keeps the `Depends` scope, so both `make_client` and `make_strict_client` inherit the change.
- **S2 and commit-before-raise.** The two existing commit-before-raise paths stay correct: the reuse revoke (`sessions/service.py:90`) and the failure counter (`auth/service.py:169`). After such a commit the session auto-begins, and the later rollback does nothing.
- **S1 test mismatch.** The acceptance criterion's test is wrong. `TURNSTILE_AFTER=3` uses `>=`, so at `failures=2` exactly one attempt may run without Turnstile. With any atomic fix, the correct result for two parallel attempts is one 401 `invalid_credentials` and one `turnstile_required`, not both `turnstile_required`.
- **S1 test fixture.** `asyncio.gather` on the shared `db_session` (one connection, savepoint) cannot test this. An `AsyncSession` does not allow concurrent operations, and advisory locks are re-entrant on the same connection, so the test would neither block nor fail.
- **N1.** `revoke_account` (`sessions/repository.py:137`) has no created-at filter.
- **S3.** USR-03b/03c have not shipped, so no app reads `mm_device` yet. Old cookies exist only on test or staging browsers.

**Recommendations**
1. **S2:** use `SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]` and no per-service commits (R1). Test with the strict client by patching `db_session.commit` to raise on login, logout and a 24 h rotation. Assert a 500 envelope, no `Set-Cookie`, and that the row is unchanged.
2. **S1:** add `pg_advisory_xact_lock(:k)` as the first statement of `LoginService.login`, before `failures.get`.
   - Derive `k` as `int.from_bytes(email_hmac[:8], "big", signed=True)`, with a 2-int `(namespace, key)` form or a documented namespace so it cannot collide with future locks.
   - The lock is released by the commit-before-raise, by the rollback on early raises (Turnstile, 429), and by the final commit, which comes before the response once S2 lands.
   - Write the concurrency test with two separate `SessionLocal` sessions or the real app with separate connections, committed data and explicit cleanup. Mark it, since it falls outside the per-test rollback.
3. **S3:** set `DEVICE_COOKIE = "__Host-mm_device"` and `Path=/` in set, clear and `clear_device_cookie_header`. Match the name exactly (case-sensitive).
   - Keep the rule that more than one value gives a 401.
   - Do not read the old `mm_device`: ignore it, never migrate its value.
   - Optionally, on login and logout, also send a `Set-Cookie` that expires the old `mm_device; Path=/api/v1/auth`. This is cheap hygiene, removable in v2.
   - Update ADR-0004, spec D13 and identity-architecture §5. CLAUDE.md R11 is the owner's.
4. **N1:** add `accounts_created_before` to `revoke_account` with a strict `<`. Login rotation and `add_account` share one `now()`, so an account created by the rotating login has `created_at == rotated_at` and must stay hidden.
   - For `{all:true}` with the previous value, keep revoking the whole device. Revoking more is never an escalation, and the only cost is a denial of service within 60 s. Record this in the ADR.

**Risks**
- The advisory lock holds a pool connection during Argon2 and the Turnstile HTTP call. With concurrent logins for one email, connections queue up. Give Turnstile a short timeout. The per-IP 20/min limit bounds the queue.
- **Nameless-cookie bypass** (`=__Host-mm_device=x`): older browsers could still smuggle the cookie. The multi-value 401 limits this to a denial of service when the real cookie is present. Note it in the ADR.
- Raising `pg_advisory_xact_lock` before the Turnstile check makes even `turnstile_required` responses serialize per email. This is acceptable.

**Open questions**
- Has USR-03 been deployed to `api.mmlabs.online` with real browsers holding `mm_device`? If not, skip the legacy-clear header.
- Should the S1 acceptance criterion be corrected to "exactly one 401 and one `turnstile_required`" (or use `failures=3` with no token)?
- Login with the previous value (security review N1, second point) adds an account that the client cannot see. Fix it here by forcing a new rotation, or leave it as a follow-up?
