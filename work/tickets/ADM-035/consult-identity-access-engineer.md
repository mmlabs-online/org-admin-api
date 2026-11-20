# Consult — identity-access-engineer — ADM-035 (2026-11-20)

### Findings
1. **The pattern to copy works today.** `app/modules/app_credentials/` already has what ADM-035 needs:
   - The codec has a length cap and a strict regex, and rejects bad input before any database read (`codec.py:21-26`).
   - The secret is hashed with SHA-256 and compared with `hmac.compare_digest` (`service.py:158`).
   - Every failure gets the same 401 through `_reject`, which logs only the reason and `key_id`.
   - The raw value is returned once in `IssuedCredential`, with `no-store` set in the router.
   - The touch re-checks revocation: `touch_last_used ... WHERE revoked_at IS NULL` (`repository.py:73-82`).
2. **The limit of 2 has no lock to borrow.** App credentials enforce "at most 2 active" through `rbac.lock_app` (`service.py:100-123`). Deployer keys belong to no app (ADR-0018 §2), so that lock does not exist here. Without some other lock, two concurrent POSTs can both count 1 and leave 3 active keys.
3. **Revoke needs a different permission from the template.** App-credential revoke requires only `admin:apps:write` (`router.py:58-61`, "revoking only removes access"). The ticket, spec §5.13 (OQ43) and ADR-0018 §2 all say issue, list **and revoke** need `admin:*`. Copying the router as-is would get this wrong. `require_permission("admin:*")` works directly (`admin_auth/deps.py:132-134` rejects `requires_super` alongside `admin:*`).
4. **The route-class test will break.** `test_every_route_is_classified` (`tests/test_route_auth_classes.py:165-178`) only knows PUBLIC, COOKIE, APP_CREDENTIAL, SELF_SCOPED and Bearer. ADM-035 adds no deployer route, so a new `DEPLOYER_CREDENTIAL` set will be empty until ADM-036.
5. **Planned code collides with ADM-034.** ADR-0018 §1 has ADM-034 add a `scope` column to `app_credentials` and edit the same test file and probably the codec.
6. **`mmdk_` is already refused on Bearer routes.** The Bearer guard rejects any non-JWT with `realm="admin"` (test at lines 434-443). The new rejection tests should use the same parametrised pattern.

### Recommendations
- **R1 (AC "3rd active key → 409"):** serialise issue and revoke with a transaction-scoped advisory lock taken first in both (`pg_advisory_xact_lock(<fixed constant>)`, kept in the repository). Then count and insert. Return 409 with `details[0].reason="credential_limit"`, as app credentials do. Do not use a partial unique index: it cannot express "≤ 2".
- **R2 (AC "revoked refused immediately; `last_used_at` updates"):** in `current_deployer`, keep the same order of checks as app credentials:
  1. missing
  2. malformed (prefix and regex, no DB read)
  3. unknown `key_id`
  4. hash compare
  5. `revoked_at`
  6. touch with `WHERE revoked_at IS NULL`, returning False → 401

  The touch's row lock already serialises it against a concurrent revoke. Do not take the global advisory lock on authentication: it would queue every deploy report behind any issue or revoke for no gain.
- **R3 (AC "mmak_/JWT → 401 on current_deployer"):**
  - Every failure is one 401: `unauthorized`, `WWW-Authenticate: Bearer realm="deployer"`, no details (ADR-0018 §3).
  - Use a separate `HTTPBearer(scheme_name="deployerCredential", bearerFormat="mmdk", auto_error=False)`.
  - Unlike `current_app_credential`, it takes no `key` path parameter, since the key works for any app.
  - It returns a frozen `AuthenticatedDeployer(credential_id, key_id)` whose actor is `SystemActor(f"deployer:{key_id}", privileged=False)`.
- **R4 (codec, R7):** give the deployer key its own codec module with `PREFIX="mmdk_"`. Or, if you prefer a shared parametrised helper, make sure the existing `mmak_` behaviour and tests stay byte-identical. A separate module avoids a merge conflict with ADM-034. Either way, test that `mmak_<valid>` is refused by the `mmdk_` parser and the reverse.
- **R5 (AC "mmdk_ refused by every other auth class"):**
  - Add `DEPLOYER_CREDENTIAL: set = set()`.
  - Assert that no route depends on `current_deployer` (mirroring lines 246-250).
  - Run the parametrised `mmdk_`-on-Bearer test over every Bearer and self-scoped route.
  - Add `mmdk_` on `PUT …/manifest` → 401 `realm="app"`.
  - Add one cookie route with a `mmdk_` Bearer header → still treated as unauthenticated.
  - Add the `deployerCredential` scheme check to `test_openapi_bearer_scheme` only when ADM-036 adds the route (FastAPI only publishes schemes that are used). Record this in the plan.
- **R6 (AC "only hash stored, value never in logs or audit"):**
  - Audit `deployer_credential.issued` with `changes={"key_id": …}` only, and `deployer_credential.revoked` with `revoked_at`.
  - Keep `__repr__` free of the hash.
  - Add DB CHECKs: `key_id ~ '^[0-9a-f]{16}$'` and `octet_length(secret_hash)=32`. Keep their text identical between model and migration (Alembic does not compare CHECKs, per the `models.py` note).
  - Add a unique index on `key_id`, a partial index `WHERE revoked_at IS NULL`, and `created_by` FK `ON DELETE SET NULL`.
  - Test: capture logs and audit rows after issue and assert the raw value and secret are absent.
- **R7 (revoke semantics):** use the same `revoke` contract as app credentials:
  - Revoking twice is an audited no-op returning 204.
  - An unknown id → 404.
  - Revoke is allowed even when it leaves 0 active keys. That is the incident-response path, so do not block it.
- **R8 (docs/deploy.md):** document the rotation: issue the 2nd key, update `MMLABS_DEPLOYER_CREDENTIAL`, verify `last_used_at` on the new key, then revoke the old one. Also state that a leaked key means revoke first and issue afterwards.

### Risks
- **A leaked `mmdk_` key affects every app.** ADR-0018 lists what it can do. The mitigations depend on audit plus `last_used_at` being visible in the list response, so include both in the list schema.
- **No lock gives silent drift.** Without R1, the 409 test passes in a single-threaded run while production can drift to 3 keys. Add a concurrency test with two sessions.
- **Merge conflict with ADM-034** on `tests/test_route_auth_classes.py` and the R3 text in `CLAUDE.md` (ADR-0018 §12 says the first implementing ticket applies it).
- **The rate limit is not this ticket's.** The per-IP limit is a route dependency in ADM-036, but `current_deployer` alone has none. Note in the code that a route using it must add the limit first.

### Open questions
1. Does ADM-035 or ADM-034 apply the ADR-0018 §12 R3 class-4 text and the D28 row? Running in priority order makes ADM-035 the likely one, but the R3 text names the ADM-036 route.
2. Should a 403 denial on issue (non-super caller) write a `denied` audit row, as `AppCredentialService.issue` does? Or is the `require_permission("admin:*")` dependency's standard behaviour enough? Pick one for consistency.
3. Should list be `Page[T]` (R9 default, `limit` ≤ 100) even though there are at most a few rows? I recommend yes, to stay inside R9 with no new exception.
