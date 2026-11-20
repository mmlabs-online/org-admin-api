# Consult — identity-access-engineer — ADM-036 (2026-11-20)

### Findings
1. `current_deployer` (`app/modules/deployer_credentials/deps.py:25-35`) is ready and takes no `key` param. Every failure goes through `_reject`, which returns one 401 with `WWW-Authenticate: Bearer realm="deployer"` and logs only the reason and key_id (`service.py:29-35`). The check order is missing, parse, lookup, `compare_digest`, revoked, then a guarded touch (`service.py:133-150`).
   - An `mmak_` key or an admin JWT fails at `parse_credential` (`codec.py:25-30`) before any DB read, so the 401 is already uniform.
2. The touch is `UPDATE … WHERE revoked_at IS NULL RETURNING` (`repository.py:73-82`). Its row lock stays held until the request transaction ends. Two effects:
   - Concurrent reports with the same key are serialised.
   - A staff revoke waits on that lock.
   - A 409 or 422 after auth rolls the touch back, so `last_used_at` does not move on refused reports. This was already noted in ADM-035's security consult.
3. Route-decorator `dependencies=[rate_limit(...)]` run before signature dependencies (pattern at `manifest_router.py:18-38`, `rate_limit.py:130-145`). **But FastAPI reads and JSON-decodes a declared Pydantic body before any dependency runs.** So with a typed `body:` param:
   - An unauthenticated request with malformed JSON gets 422 instead of 401 or 429, and the rate limit does not count it.
   - A large body is read in full before the rate limit and auth checks.
   - No body cap exists anywhere today: there is no `max_body` setting in `app/`.
4. Registry state:
   - `DEPLOYER_CREDENTIAL = set()` (`tests/test_route_auth_classes.py:66`).
   - `assert not DEPLOYER_CREDENTIAL` (`:278`).
   - `test_openapi_bearer_scheme` checks only `bearerAuth` and `appCredential` (`:284-313`). A deployer route currently falls into the `else` branch, which passes without checking anything.
   - The CORS loop only replaces `{key}` (`:326`).
5. `AuthenticatedDeployer.actor` already returns `SystemActor("deployer:<key_id>", privileged=False)` (`service.py:45-47`), which matches ADR-0021 §8.
6. CLAUDE.md R3 4(b) still says "only the release deployment report (ADM-036)", with no method or path. The `deps.py:5-6` docstring says "No production route uses it yet".

### Recommendations
- **AC3, dependency order:** `dependencies=[rate_limit("deployer_report")]` on the decorator, then `caller: DeployerDep`, then the path params.
  - Do **not** declare a Pydantic body param. Take `Request`, read it with a 4 KiB cap (oversize: 413 `payload_too_large`), then `ReleaseDeploymentReport.model_validate_json` (failure: 422 with field names only).
  - Publish the schema through `openapi_extra` (requestBody).
  - This gives the order 429, then 401, then 413/422, then 404. Coordinate with ADM-034 so the app release PUT does the same.
- **AC3, rate limit:** add `deployer_report_rate_limit=30` and its window to `app/core/config.py` (next to `:104`), keys to `.env.example`, and a `_LIMITS["deployer_report"]` entry. Use a separate bucket from `app_manifest` and the ADM-034 release write.
- **AC3, unknown app:** resolve the app (404) in the service **after** auth, so unauthenticated callers cannot enumerate apps. Accept disabled apps and the `admin` app.
- **AC3, 401 uniformity tests:** run every case below on the report route and assert identical `error` JSON, `realm="deployer"`, and no details:
  - missing token, malformed token, unknown key_id, wrong secret, revoked key
  - `mmak_` with scope `manifest` and with scope `releases`
  - admin JWT (`admin:*`), cookie-only
  - Also: an `mmdk_` key on the ADM-034 release PUT returns 401 `realm="app"`.
  - Also: on the 31st request without credentials, 429 wins over 401.
- **Keep the touch short:** inside the transaction, authenticate first, then do the release upsert with no external I/O. Do not take the deployer advisory lock in auth.
  - Lock order is deployer row, then (`lock_app` if the DB consultant wants it), then release row. The app path is `lock_app`, then app-credential row, then release row, so there is no cycle.
  - Document that `last_used_at` moves only on committed reports.
- **AC1, audit:** one row per real change, actor `caller.actor`, carrying ids and status only (no URLs). Add `created_via='deployer'` on create. Retries and no-ops write no row. 401s are log-only, as with app credentials.
- **Registry and OpenAPI:**
  - Set `DEPLOYER_CREDENTIAL = {("PUT", f"{V1}/apps/{{key}}/releases/{{environment}}/{{version}}/deployment")}`, and make `:277-281` an exact-equality check.
  - In `test_openapi_bearer_scheme`, assert `securitySchemes["deployerCredential"] == {"type":"http","scheme":"bearer","bearerFormat":"mmdk"}`.
  - Add a `DEPLOYER_CREDENTIAL` branch asserting `security == [{"deployerCredential": []}]`, `"401"` in responses, `"403"` **not** in responses (there is no 403 path), and no `x-permission`.
  - Use `_fill(path)` at `:326`.
  - Add a no-CORS preflight test like `test_manifest_no_cors`.
- **Docs:**
  - Update the R3 4(b) text with the exact method and path, and add the R9 exception (ADR-0021 §12).
  - Update the `deps.py` docstring.
  - Add the D-row if ADM-034 has not added it.

### Risks
- **Leaked `mmdk_` key (Medium):** it can forge deployed/failed status, or fake a rollback (move `deployed_at`) for any app. Mitigations are audit, `last_used_at`, immediate revoke, and the URL grammar on `deploy_run_url`.
- **Shared bucket:** one runner IP handles all prod and staging reports. If `trusted_proxy_cidrs` is misconfigured behind Cloudflare, the bucket becomes effectively global, so anyone could starve reports. CI only warns, but entries are lost.
- **Silent data loss:** the CI warn-only policy means a 401 after rotation goes unnoticed.
- **Admin-app collision (ADR §11):** org-admin-api and admin-pan both report under `admin` with the same `(environment, version)`.

### Open questions for Ming
1. **PUT or PATCH?** Suggested: **PUT**, because it creates (201), is idempotent, and matches the app write.
2. **Body read: raw (recommended) or a Pydantic body param, which accepts 422-before-401 for malformed JSON?** Suggested: raw, with the same approach in ADM-034.
3. **Should a refused `deployed → failed` (409) write a `denied` audit row?** Suggested: no, log only. ADR §8 lists only wrong-app or wrong-scope keys as denied.
4. **Admin-pan collision?** Suggested: a separate app row `admin-pan` with its own `releases` key, decided in ADM-034's plan.
5. **Who applies the CLAUDE.md R3/R9 text, ADM-034 or ADM-036?** Suggested: ADM-034 adds the R9 exception and the 4(a) scope list; ADM-036 adds the 4(b) method and path.
