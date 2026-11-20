# Security review — ADM-035 (appsec-engineer)

## Attempt 1 (commit 4beedec)

**Verdict: PASS from security. No blockers.** I found one should-fix (hardening) and a few notes. This was a read-only review: I ran no pytest, no alembic and no `make check`, so the reviewer still owns the green-check gate (R8).

### Checklist (focus items)
- **mmdk_ codec**: ok. `app/modules/deployer_credentials/codec.py:27-30` checks the 128-char cap and the `mmdk_` prefix first, then an anchored regex `^mmdk_([0-9a-f]{16})_([A-Za-z0-9_-]{43})\Z` (`codec.py:16`). Since `\Z` is used, a trailing newline is rejected. The regex has no backtracking risk, and the value is 256-bit `token_urlsafe(32)`. The mmak_ regex (`app/modules/app_credentials/codec.py:12`) refuses mmdk_ in turn.
- **Rejected before any DB read**: ok. `service.py:135-139` returns before `repo.get_by_key_id`. `tests/test_deployer_credentials_auth.py::test_wrong_prefix_no_db_read` confirms this with a DB call that raises.
- **SHA-256 + compare_digest**: ok. `service.py:144` uses the shared `secret_hash` (SHA-256 digest of the secret). The DB enforces `octet_length(secret_hash)=32` in both the model and the migration.
- **Single uniform 401**: ok. Every failure path (`service.py:136,139,143,145,147,149`) goes through `_reject`, which returns `UnauthorizedError("Authentication required")` with no details and `WWW-Authenticate: Bearer realm="deployer"`. A parametrized test checks that the body and header are identical for none, Basic, garbage, mmak_, admin JWT, unknown, wrong secret and revoked.
- **Raw value never logged or audited, returned once with no-store**: ok.
  - `_reject` logs only the reason and the key_id. The key_id is already regex-constrained hex, so log injection is not possible.
  - The audit row on issue contains only `key_id` (`service.py:~103-109`).
  - The model `__repr__` leaves out the hash.
  - The list schema has no secret or hash.
  - `Cache-Control: no-store` is set on POST (`router.py:38`).
  - The commit runs before the response is sent (`app/db/session.py` docstring), so a failed commit cannot return a key that does not exist.
- **At most 2 active, under an advisory lock**: ok.
  - Issue and revoke both take `pg_advisory_xact_lock(hashtextextended('deployer_credentials',0))` (`repository.py:14-22`, `service.py:86,116`). It is held until the commit, so count-then-insert is serialised under READ COMMITTED.
  - The lock key does not collide with the other lock namespaces.
  - A barrier test on real Postgres shows one request gets 201 and the other 409.
- **Revoke semantics**: ok. Revoke returns 404 for an unknown id. Revoking twice is a no-op 204 with no audit row; the first revoke is audited with the DB timestamp. Revoking the last active key is allowed.
- **Revoke takes effect at once**: ok. `authenticate` reads the row with `populate_existing`. `touch_last_used` is a guarded `UPDATE … WHERE revoked_at IS NULL RETURNING`, so a revoke committed between the lookup and the touch gives a 401 (tested). A revoke that is still uncommitted blocks on the row lock, and then the WHERE clause is re-checked.
- **admin:* on all three routes**: ok. The router has the `current_admin` dependency, and each route has `require_permission("admin:*")`. All three are pinned in the `BEARER` map in `tests/test_route_auth_classes.py`.
- **mmdk_ refused by every other auth class**: ok. New tests cover every BEARER and SELF_SCOPED route (401 `realm="admin"`), the manifest route (401 `realm="app"`) and every COOKIE route (401 `session_invalid`, no `WWW-Authenticate`).
- **mmak_ and admin JWT refused by `current_deployer`**: ok (malformed → 401, covered by the probe test).
- **No production route uses `current_deployer`**: ok. `DEPLOYER_CREDENTIAL = set()`, and `test_deployer_credential_allow_list_is_exact` asserts that no route in the app depends on `current_deployer`. Grep finds it only in `deps.py` and the tests.

### Findings
- 🟡 **should-fix (hardening)** at `app/modules/deployer_credentials/service.py:64-65`.
  - **Risk:** `IssuedDeployerCredential` is a frozen dataclass with the default `repr`. The raw `credential` would show up if the object is ever logged, appears in a traceback that prints locals, or shows up in a debugger or Sentry frame dump.
  - **Precedent:** the codebase already hides secrets this way in `app/core/jwt_keys.py:84,87,90` and `app/modules/email/sender.py:31-33`.
  - **Fix:** `from dataclasses import dataclass, field` and then `credential: str = field(repr=False)`. Because this is a subclass with defaultless parent fields, `field(repr=False)` with no default is still valid. Optionally add a one-line test: `assert raw not in repr(issued)`.
  - The same gap exists in `app/modules/app_credentials/service.py:73-74`. That is outside this ticket's scope (see follow-ups).
- 💭 **note: timing** at `service.py:142-145`. An unknown key_id returns without hashing, so response time can reveal whether a key_id exists. The impact is negligible: key_ids are public metadata (list, audit, logs), the secret is 256-bit and compared in constant time, and mmak_ behaves the same way. No change needed. If wanted, hash a dummy secret on the unknown path.
- 💭 **note: limit enforced only in the service**. The DB has no constraint for "at most 2 active", so the limit holds only while every writer goes through `DeployerCredentialService` with the lock. That is true today. Keep it that way in ADM-036: no direct inserts.
- 💭 **note for ADM-036**:
  - `touch_last_used` holds a row lock on the credential until the report transaction commits. Concurrent reports using the same key are serialised, and a staff revoke waits behind an in-flight report. This is acceptable, but keep the report transaction short.
  - ADM-036 must put the per-IP rate-limit dependency before `current_deployer`, as `deps.py:5-6` already notes. The route must also be added to `DEPLOYER_CREDENTIAL`, and the `assert not DEPLOYER_CREDENTIAL` line must be removed.

### Follow-ups (out of scope, R7)
- Apply `field(repr=False)` to `IssuedCredential.credential` in `app/modules/app_credentials/service.py:74` as well.
