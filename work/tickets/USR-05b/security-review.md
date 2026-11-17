## Attempt 1

Security review: USR-05b (`PUT /api/v1/apps/{key}/manifest`, app credential, R3 class 4 / ADR-0016)

I read the diff `origin/main...HEAD` (commit 0436f25), the ticket, the plan, and the helpers it reuses: `rbac/service.py` (`_create`, `_build_for_app`, `replace_permissions`), `audit/service.py` (`record_failure`), `core/rate_limit.py`, `core/client_ip.py`, `app_credentials/codec.py`, `admin_auth/route_classes.py`, `db/session.py`, and ADR-0015. As instructed, I did not run `make check` or pytest.

## Findings

### 🔴 Blockers
None.

### 🟡 Should-fix

1. **Lock-order inversion between a push and a revoke, and a revocation race.**
   - Where: `app/modules/app_credentials/service.py:258` (`touch_last_used` in `authenticate`), `app/modules/rbac/manifest.py:388` (`lock_app`), and `revoke` in the same service file (≈ line 191 onward).
   - Deadlock: a push takes the `app_credentials` row lock first (the `UPDATE ... last_used_at` in the dependency) and only then takes `apps FOR NO KEY UPDATE` in `sync`. `revoke` does the opposite: it locks the `apps` row first, then updates the credential row. A push racing a revoke of the same app can deadlock. Postgres aborts one of them, which surfaces as an unhandled 500.
   - Revocation race: `get_with_app` reads the credential without a lock. If a revoke commits between that read and the `UPDATE`, the `UPDATE` still matches (`WHERE id = :id` only). The push then succeeds with a credential that was already revoked. The window is small, but ADR-0016 says "Revocation is immediate".
   - Fix:
     - In `AppCredentialRepository.touch_last_used`, add `.where(AppCredential.revoked_at.is_(None)).returning(AppCredential.id)` and return whether a row was updated.
     - In `authenticate`, after the app-match check, call `await self.rbac.lock_app(app.key)` first, then `touch_last_used`. If it returns False, `raise _reject("revoked", key_id)`. Re-locking the same app in `sync` within the same transaction is harmless.
     - Pin it with a service-level test: revoke the row between `get_with_app` and the touch, and expect a 401.

### 💭 Notes

2. **Unthrottled JSON parsing before the rate limit.** `app/modules/rbac/manifest_router.py:599`. FastAPI reads and `json.loads` the body before it runs route dependencies. An unauthenticated, unthrottled client can therefore make the server parse a JSON body of any size. This is already true of every JSON route, and the global body-size limit is already a follow-up. It is worth calling out here because this route is per-IP throttled "before parsing" only for the credential, not for the body.

3. **No trace of a refused authenticated push.** `app/modules/app_credentials/service.py:258` and `manifest.py:386/419/457`. A 409 or 422 after a successful authentication writes no audit row and no log line, and `last_used_at` is rolled back. Someone probing with a leaked credential leaves nothing in the DB. Suggestion: `logger.info("app_manifest_refused key_id=%s status=%s", caller.key_id, ...)` in the router or service. Never log the raw value.

4. **Timing difference on unknown `key_id`.** `service.py:236–240`. An unknown `key_id` returns before any hash work. This is a tiny timing signal for whether a `key_id` exists. `key_id` is public by design (16 hex, logged and audited) and the secret is 256-bit, so no action is needed.

5. **The manifest can undo staff tightening of `<app>:member`.** `manifest.py:510–511`. Any push that creates a permission, including an inert `is_admin` dummy, rebuilds the member role and re-adds non-admin permissions that staff removed. Owner item 5 accepted this and `auth-integration.md` documents it. Be aware that a leaked credential can do it at will. The same app-namespace boundary applies.

6. **Org permissions on roles granted at app scope (ADR-0015 residual risk S1).** `manifest.py:498–500`. On org apps, extra roles may gain `scope = org` permissions. If such a role was granted at app scope, the permission reaches every org through the `roles` union. ADR-0015 already lists this as a residual risk with a follow-up ("refuse app-scope grants of roles that hold org permissions"). The manifest adds an automated path to it, so link the follow-up.

7. **Stored `scope = org` on an `org_mode = none` app.** If staff set this, declaring that key gives either 409 `scope_mismatch` or 422 `org_mode_required`, so the app can never push it. This fails closed (functional, not security). Documenting it would help.

8. **Push snippet hardening (optional).** `docs/auth-integration.md`: consider `--proto '=https' --max-time 30`, and in the workflow example `permissions: {}` / `contents: read` with `environment:` and required reviewers. The `printf` builtin and the `-H @file` approach correctly keep the secret off argv.

9. **Temp files on interrupt.** `docs/deploy.md` issue snippet: `$resp` and `$cred` (both 0600) are left behind if the operator presses Ctrl-C between steps. A `trap 'rm -f -- "$resp"' EXIT` inside the subshell would close this. The fail-loud `[ -s "$cred" ]` guard and the orphan-revoke line from AC9 are both present and correct.

## Focus items checked
- **Credential parsing.** `parse_credential` checks the 128-character cap before the anchored regex (`\Z`). The comparison is `hmac.compare_digest` on SHA-256 digests (`service.py:239`), with a spy test.
- **One uniform 401.** All five reasons (missing, malformed, unknown, wrong secret, revoked) give `Bearer realm="app"` with no details, and the test matrix asserts identical bodies. Logs carry `key_id` only, and only after the format check, so there is no log injection.
- **Order of checks.** The rate limit is a route-level dependency, so it runs before the credential dependency. Credential errors come before body validation (tested: 3 requests give 401, 401, 429; a schema-invalid body with no credential gives 401). The rate limit uses `CF-Connecting-IP` only from trusted proxies.
- **Credential only on this route.**
  - `APP_CREDENTIAL` is pinned exactly, including a dependant-tree check.
  - There is no `current_admin`, so `BearerRouteMatcher` returns False and there is no admin CORS. The route is outside `/api/v1/auth`, so there is no identity CORS either. A test sends a preflight from an admin origin and from an app origin.
  - An admin JWT on this route is rejected as malformed (401), and `mmak_` on Bearer routes gives 401; both are tested.
  - No other route path collides with this one.
- **Cross-app requests.**
  - A credential used on another app's path gets 403 `app_credential_mismatch`, decided after the credential is resolved, so it does not reveal whether the path app exists.
  - The denied row is written by `record_failure`, which commits it alone, with actor `app:A/<key_id>` and `app_key` = A. The unvalidated path key is never written.
  - A disabled app gets 403 and is re-checked under the lock.
- **Privilege escalation.**
  - **Namespace:** keys must match the app key exactly as the first segment (`split(":",1)[0]`, not `startswith`).
  - **Wildcard:** `<app>:*` is refused. `PERMISSION_KEY_RE` only allows `*` as the whole remainder.
  - **`is_admin`:**
    - `is_admin` permissions are inert. `admin_create_allowed` skips `require_super` only for apps other than `admin`.
    - The `admin` app still needs `require_super` (defence in depth), plus the 409 `admin_app` and the refusal to issue credentials for it.
    - `is_admin` permissions are never in roles or `member`, enforced by the declared flag, and stored and declared flags must match (409).
    - Scope flips are 409.
  - **ADR-0015 guard:** the org-app `scope = app` guard is pre-checked (`requires_staff`) and is still enforced inside `_create`.
  - **Member override:** only narrows (intersection with `is_admin = false`, and on org apps `scope = app`).
  - **Protected roles:** system, default and org-owner roles are refused, and `<app>:member` is reserved. `member_is_org_owner` is pre-checked and checked again in `_build_for_app`.
  - **Parity with role PUT:** this matches the `replace_permissions` guards, because the manifest can never add `is_admin` permissions and never touches the default or owner role.
- **Validation before writes.** All 422s (shape, then mode) and 409s happen before the first write, under `lock_app`. Any later error rolls back the request transaction.
- **DoS limits.** Payload caps are 200 permissions, 50 roles, 200 permissions per role and 200 in `member`, with description ≤ 500 and name ≤ 255 plus a character-category filter. A push costs at most a few hundred queries, behind authentication and the per-IP limit.
- **Secrets in logs and audit.** The `caplog` test covers the 401 matrix and a 200 (no secret logged). Audit `changes` hold key lists only. The actor label is at most 53 characters, under the 64 CHECK.
- **Audit rows.** One row per changing push, none for a no-op, in the same transaction.
- **Docs.**
  - `auth-integration.md`: deploy job only, never on `pull_request`/`pull_request_target`, secret sent through a 0600 header file, `-f`.
  - `deploy.md`: the runbook has no unchecked pipeline, has the `[ -s ]` guard and the orphan-revoke instruction.
  - `identity-architecture.md` gains the S8 audit row and the §7 risk line.

## Follow-ups (out of scope)
- A global request body-size limit, so the body read happens after the rate limit (already listed).
- Log or count refused authenticated pushes (note 3).
- ADR-0015: refuse app-scope grants of roles that hold org permissions; the manifest now feeds this path (note 6).
- A per-credential rate-limit bucket (already listed).

**Verdict: PASS with should-fix.** There are no blockers. Finding 1 (lock order and revocation re-check) should land in this ticket or as an immediate follow-up.
