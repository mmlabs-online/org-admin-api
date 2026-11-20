# Consult — api-platform-engineer — ADM-034 (2026-11-20)

ADM-034 api consult (read-only). Nothing was run. I did not open the releases module's models, service or repository, the migration, the full body of `docs/spec-v1.md` §5.13, or `app/modules/bug_reports/service.py`. Line cites below come only from the files I did read.

## Findings
- **Manifest pattern.** `app/modules/rbac/manifest_router.py:15-38` is the template. It uses `rate_limit("app_manifest")` as a route dependency, `AppCredentialDep`, and a `responses` block listing 401/403/409/422/429.
- **Auth runs before the body is checked.** `app/modules/app_credentials/deps.py:20-30` runs `AppCredentialService.authenticate`. A missing, malformed, unknown, wrong-secret or revoked key gives one 401, and `app/modules/app_credentials/service.py:144-181` fixes the check order.
- **Audit action is hard-coded.** The denied-audit row at `service.py:168-175` always uses `APP_MANIFEST_SYNCED`. ADM-034 must pass the action in, as the ticket says.
- **Scope has no check yet.** `authenticate` has no scope parameter. `AppCredentialDep` is a fixed `Depends`, so you need a scope factory such as `app_credential_for(scope)`. A bare `Annotated[..., Depends(...)]` cannot take an argument.
- **Check order today.**
  1. Disabled app, 403 `app_disabled` (`service.py:162`).
  2. App mismatch (`service.py:166`).
  3. `lock_app`, then `touch_last_used` (`service.py:178-180`).
  - Scope must be checked after the secret and revoked checks. Otherwise a wrong key could probe scopes.
- **Issuance has no body and a per-app limit.** `app/modules/app_credentials/router.py:33-38` takes no body. `service.py:115-123` refuses `admin_app` regardless of scope and applies `MAX_ACTIVE` per app. `AppCredentialRead` (`schemas.py:7-19`) has no `scope`. Both need the scope change.
- **Rate limit.** `app/core/rate_limit.py` has `Scope`, `_LIMITS` and settings entries (`app/core/config.py:104-105`). A new `app_release` scope needs 3 edits, mirroring `app_manifest`.
- **Body-size cap cannot be a dependency.** FastAPI reads and parses the body before it resolves dependencies. A size check in a dependency would run after the whole 256 KiB+ body is already in memory. `app/core/errors.py:135-143` already has `LengthRequiredError` (411) and `PayloadTooLargeError` (413), and bug reports use them (`app/modules/bug_reports/auth_router.py:77-78`). The practical shape is a route taking `Request`, checking `Content-Length` (411 if missing, 413 if over) and then parsing JSON with `Release.model_validate_json`. Ordering must still be rate limit, auth, size, validation.
- **422 shape.** `app/core/errors.py:207-217` emits `[{type, loc, msg}]` and never echoes input. This satisfies ADR-0021 §7 for plain FastAPI/Pydantic validators. Custom validators must not put the value in `msg`. Pydantic includes the value in some messages, for example `pattern` mismatches do not but `ValueError(f"...{v}")` would.
- **Releases schemas.** `app/modules/releases/schemas.py` has read models only. `ReleaseChange.title` already carries "Untrusted plain text … render as text" (lines 40-47). `pr_url` has no such note. There is no `ReleasePut` model yet.
- **Route-class registry.** `tests/test_route_auth_classes.py:63-66` has `APP_CREDENTIAL = {("PUT", MANIFEST)}`, a set with no scope. Line 271 asserts equality with the manifest route and line 274 asserts `with_dep == APP_CREDENTIAL`. These assertions and the no-CORS and 401 tests at lines 432-463 must be generalised to cover the release route. A route-to-scope mapping is needed, as the ticket says.

## Recommendations
1. **PUT contract.** Add `ReleasePut` with `extra="forbid"` and these fields: `git_sha` (40 hex), `deployment_pr` (nullable), `has_migrations: bool`, `images: list[{name, digest}]`, `changes: list[{pr_url, title, ticket_id?, risk?}]` (≤ 200). Add field validators for OQ45:
   - digest regex `sha256:[0-9a-f]{64}`
   - GitHub URL allow-lists
   - control characters stripped from titles
   - titles ≤ 200 characters
   - ticket id and risk formats

   The path validators for `environment` and version live in the service. A bad version returns 422 with `loc ["path","version"]`, not 404. (Criteria: 422 per rule.)
2. **Status codes.** The responses block must declare 201 and 200. Use `response_model=ReleaseDetail`. Set 201 on a successful insert by setting `response.status_code` in the route. Declare `responses={201:…, 401, 403, 404, 409 (release_not_pending), 411, 413, 422, 429}`. Document the no-op and enrichment 200s, and that `status` is never client-settable. Rejecting `status` through `extra=forbid` is the answer to "never changes status". (Criteria: PUT outcomes.)
3. **Race on first insert.** Use `INSERT ... ON CONFLICT (app_id, environment, version) DO NOTHING RETURNING`, then re-select with `FOR UPDATE` and apply the pending, deployed or failed rule. Never let `IntegrityError` reach a 500 handler. (Criterion: race test.)
4. **Issuance.** Add an optional body `AppCredentialIssue {scope: AppCredentialScope = manifest}` with `extra=forbid`. An empty POST body must keep working, so the body has to be optional (`Body(default=None)`). Add `scope` to `AppCredentialRead` and a `StrEnum`. Count the limit per `(app, scope)`, and refuse `admin_app` only when `scope == manifest`. Keep `credential_limit` as the 409 reason. (Criteria: existing keys unchanged; scope listing.)
5. **Authenticate order.** Order the checks as: 401 conditions, then disabled, then app mismatch, then scope, then lock and touch. A wrong-scope key on another app's path reports mismatch first, which leaks less. Audit actions should be `APP_MANIFEST_SYNCED` for the manifest scope and the release action for the releases scope, both with `outcome=DENIED` and `reason=app_credential_scope` or `app_credential_mismatch`. Add `AppCredentialScopeError` (403, `app_credential_scope`) next to `AppCredentialMismatchError` in `app/modules/app_credentials/errors.py`. (Criterion: manifest and releases keys cannot cross over.)
6. **OpenAPI.** Mark `pr_url` and `title` as "untrusted, display as text only, never follow server-side". Declare the security scheme on this route, and say plainly "no CORS, server-to-server only". Document the `Retry-After` header on 429.
7. **Config.** Add `app_release_rate_limit=30`, `app_release_rate_window_seconds=60` and `release_put_max_bytes=262144` to `app/core/config.py`.
8. **Tests.** Parametrise the route-class registry by scope, for example `APP_CREDENTIAL = {("PUT", MANIFEST): "manifest", ("PUT", RELEASE): "releases"}`. Add tests for the no-CORS preflight, an admin JWT giving 401, and an `mmdk_` key giving 401 on the release route.

## Risks
- **Early body read.** The body is fully buffered before the size check unless the route reads the raw stream with a limit. A missing `Content-Length` with chunked transfer must be handled (411, as the bug-report upload does). The cap is only real if it is checked before parsing.
- **Echoed input.** A custom validator that interpolates the bad value into its message would put a URL or title into a 422 body. Use fixed messages.
- **Release replace race.** Two concurrent PUTs against a pending record should be serialised by the row lock. Without it, a pending record could flip to deployed between the check and the write and then be overwritten.
- **Rate-limit sharing.** The per-IP bucket is shared by all apps on GitHub's runner IPs when the limit is per IP only. A 429 is a CI warning, so this is tolerable, but the 30/min default is worth confirming.

## `admin` app collision
Today `admin` has no manifest, no entitlements and no app credentials (`service.py:115-116`). Two products, org-admin-api and admin-pan, both map to `admin` and could write the same `(app, environment, version)`.
- Recommended default: register a second app row `admin-pan` for admin-pan (not an "admin" key variant). Keep `admin` for org-admin-api releases. Allow `releases` keys for the `admin` app only. A key for `admin-pan` is then an ordinary app credential, with no collision.
- Cost: the new row appears in the role catalog and the app list. Check the registry rules in `app/modules/rbac/service.py:585` (an `admin` registration is refused) and the catalog 404 rule for `admin`.

## Open questions for Ming
1. Admin-pan's own app row `admin-pan`, or a shared `admin` row? Suggest: its own row.
2. Should `releases` keys for `admin` require `requires_super="always"` at issuance as today? Suggest: yes, keep it.
3. Body cap 256 KiB and 30/min as the final numbers? Suggest: 256 KiB, 30/min per IP.
4. Is `changes` ≤ 200 the final cap, or should it match the 256 KiB size? Suggest: keep 200, since 200 × ~0.5 KiB sits well under the cap.
5. Should a malformed version return 422 or 404? Suggest: 422.
