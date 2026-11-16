# Review — USR-08

## Stages A+B — Attempt 1 — 2026-11-16
Verdict: PASS

Scope of this review: commits `4ad6e5c` (stage A) and `9b723d9` (stage B) on `claude/blissful-wozniak-fx4lm9` in `/home/user/mmlabs-auth`, judged against plan.md Stage A/B and the ACs that those stages own (2, 6, 7, 8, 9, 10, plus the CI part of 1 and G). Stages C, D and E are not reviewed here.

### Checks (run by reviewer)
The stage gate replaces `make check` here. All commands were run from a clean tree at `9b723d9`:
- `pnpm -C ts install --frozen-lockfile`: "Lockfile is up to date, resolution step is skipped"
- `pnpm -C ts lint`: "Checked 26 files … No fixes applied."
- `pnpm -C ts typecheck`: exit 0
- `pnpm -C ts test`: "Test Files 10 passed (10)", "Tests 109 passed (109)"
- `pnpm -C ts build`: exit 0. I removed `dist/` afterwards; it is git-ignored.
- `uv sync --locked --directory python`: "Resolved 34 packages … Checked 33 packages"
- `ruff check .`: "All checks passed!"
- `ruff format --check .`: "2 files already formatted"
- `mypy`: "Success: no issues found in 2 source files"
- `pytest`: "2 passed"

These results match impl-log.md (Stage B checks) exactly. `alembic check` does not apply: there is no org-admin-api code change.

Contract freshness: `contract/SOURCE` = `74e9b62…`. It is an ancestor of org-admin-api HEAD, and `git diff 74e9b62 HEAD -- app` is empty, so the contract is current.

### Acceptance criteria (Stage A/B portions)
- [x] **AC1 (CI part).** `ts/` and `python/` are in the separate repo. `.github/workflows/ts.yml` and `python.yml` are present. Release and versioning are deferred to E1.
- [x] **AC2.** TS tests cover account choice per origin, token refresh and error mapping. Evidence: `ts/test/accounts.test.ts` (11 tests), `token-cache.test.ts` (12 tests), and `errors.test.ts`, whose `it.each(contract.codes)` runs 25 cases from `contract/error-codes.json` (errors.test.ts:30). All are green.
- [x] **AC6.** Tokens are cached in memory, refreshed before expiry, fetched single-flight, and never persisted.
  - Implementation: `ts/src/token-cache.ts:13-17` (skew `min(60 s, 20 %)`), `:51-63` (single-flight with `finally` identity check), `:73-79` (generation stamp), `:88-100` (clear bumps the generation and drops in-flight entries). There are no timers.
  - Tests: `token-cache.test.ts:65` (10 concurrent calls → 1 fetch), `:75` (a failure is not cached), `:115` (an in-flight result is discarded after logout or a switch), `:153` (`getTimerCount() === 0`).
  - Storage hygiene: `storage-hygiene.test.ts:37-58` spies on `Storage.prototype.setItem` and asserts `calls.length > 0`, so the spy provably catches writes. It also requires every write to be `mm_account_id` with a UUID value, with `sessionStorage`/`localStorage` empty after logout, `indexedDB.open` never called, and `document.cookie` empty. `:60-82` checks the client's own keys, `globalThis` string values, `location` and `localStorage`. The token lives only in `#private` fields (`client.ts:43-49`, `token-cache.ts:26-30`). The test is meaningful.
- [x] **AC7.** `listAccounts()` exists; `mm_account_id` is stored per origin with UUID validation on read and a memory fallback (`accounts.ts:30-62`). Logout covers one account or all (`client.ts:195-211`, which clears synchronously before the POST). Tests: `accounts.test.ts:19-143`.
- [x] **AC8.** There are typed errors plus a generic fallback (`errors.ts:112-145`). `retryAfter` is parsed from seconds, an HTTP-date, `details`, then 5 (`retry-after.ts:7-21`). Messages are fixed (`mmlabs-auth: <code>`). Only allow-listed details are kept: `joinAvailable`, `reasons` and `issues{type,loc}`. `toJSON` spreads only those safe fields.
  - Test `errors.test.ts:141` puts the sentinel token, email and password into both the server message and the details, then checks `JSON.stringify`, `String` and `message` for every contract code plus an unknown one.
  - `contract/error-codes.json` matches every auth-flow code in org-admin-api `app/core/errors.py`, `app/modules/*/errors.py` and the `_HTTP_CODES` map.
- [x] **AC9.** `credentials` and `X-MM-App` are set per transport class:
  - `authFetch` sends `include`, `X-MM-App` and `no-store`, and sets `Content-Type` only when there is a body. It asserts the `/api/v1/auth/` prefix (`transport.ts:72-83`).
  - `publicFetch` (verify-email and reset) sends `omit` with no `X-MM-App` (`transport.ts:84-92`). That matches org-admin-api `auth/router.py:272-292` and `:371-399`, which mark both endpoints as public class 3.
  - Bearer calls force `omit`, delete `X-MM-App` and set `Authorization` (`bearer.ts:19-25`).
  - Tests: `transport.test.ts:49-63` (8-row table), `:65`; `bearer.test.ts:22`, `:36` (this one also covers a `Request` input).
- [x] **AC10.** `resendVerification` maps 202 to `"sent"` and 409 `email_already_verified` to `"already_verified"` (`client.ts:213-225`; `endpoints.test.ts:91`). The fresh-token-after-verify flow is tested at `token-cache.test.ts:141`.
- [x] **G.** Both suites are green after stage B, and the impl-log carries the outputs for both stages.

Focus checks requested by the orchestrator:
- **Single-flight and generation guard.** Correct. `clearAll` bumps `#epoch` and `clearAccount` bumps the per-account generation. Both delete in-flight entries. A late result is returned to its own awaiters but not cached (`token-cache.ts:83-89`). The `finally` block deletes only its own promise (`:68`), so it cannot evict a newer request.
- **429 local block.** `blockedUntil[audience]` is checked before the cache and before `forceRefresh` (`token-cache.ts:52-57`). The remaining seconds are reported. There is no automatic retry. Tests: `rate-limit.test.ts:12-58`, including the HTTP-date case and `forceRefresh` during a block.
- **401 refresh-once limit.** On a 401 the client makes one forced refresh and one retry, and returns the second response whatever its status. This is gated by `lastForcedAt` at 30 s; the client has a single audience, so per-client equals per-audience. A streamed body is never retried (`bearer.ts:27-36`). Tests: `bearer.test.ts:47-99`.
- **BroadcastChannel payload.** Messages are only `{type}` or `{type:"logout", accountId}`, and are validated strictly with an exact key count and a UUID check (`broadcast.ts:5-21`). Tests: `broadcast.test.ts:44-61`, `:80`.
- **Endpoint shapes against org-admin-api.** Every request and response field matches `auth/schemas.py`, `tokens/schemas.py` and `membership/schemas.py`. `LogoutRequest` is XOR, `JoinResponse.status` is `Literal["member"]`, `org_id` is not sent (A10), and status codes match `auth/router.py` (201/200/204/202).
- **CI hardening.**
  - `permissions: contents: read` at the top level, and no `pull_request_target`.
  - `persist-credentials: false` on checkout.
  - All four actions are pinned by full SHA with a version comment.
  - Installs are frozen/locked, and `ts/.npmrc` sets `ignore-scripts=true`.
  - `pnpm audit --prod` and `pip-audit` run on runtime dependencies.
  - Dependabot covers npm, uv and actions.
  - `ts/package.json` has no `dependencies` and no lifecycle scripts, and keeps `"private": true`.

### Deviation 1 decision (skew: refresh at 840 s vs the plan's 720 s test row): ACCEPTED
The plan's normative formula (plan.md, Approach › Tokens) is `skew = min(60 s, 0.2 × expires_in)`. For `expires_in = 900` that gives a skew of 60 s, so the token is refreshed at 840 s; 720 s would mean a 25 %/20 % skew with no 60 s cap. Every source agrees on the 60 s side:
- org-admin-api `docs/consumers.md:41`: "Call `/auth/token` again about 60 s before …"
- consult-software-architect.md:36: "Refresh at `receivedAt + expires_in - 60 s`"
- consult-security-architect.md:23 (T4): "Refresh when `exp − now ≤ 60 s`, or ≤ 20 % of TTL for short TTLs"
- consult-identity-access-engineer.md:48: `min(60 s, expires_in/4)`

The 720 s in the test row is an arithmetic slip in the plan. The implementation (`token-cache.ts:13-17`) and test (`token-cache.test.ts:48-63`, with 900 → 840 s and 100 → 80 s, both boundaries checked at −1 ms) follow the formula and the consumer contract.

The other impl-log deviations are acceptable as well:
- `TypeError` for programmer errors, with fixed messages.
- `unexpected_response` keeps the real status.
- The `issues` field on `ValidationError`.
- The `getToken` overloads.
- Stricter `baseUrl` checks.
- `account_changed` carries only `{type}`.
- `join()` accepts only `"member"`, as the server's `JoinResponse` does today.

None of them widens scope or weakens a control.

### Issues
None blocking.

### Rules R1–R11
No violations found. R1, R2, R4 and R9 do not apply, because there is no org-admin-api code. The libraries comply with the others:
- **R3:** each endpoint uses its class transport.
- **R5:** no secrets, only sentinels and a dummy token in `contract/`.
- **R6:** errors are parsed from the envelope.
- **R7:** every file is on the Stage A/B lists. `python/.python-version` is justified in the impl-log.
- **R10:** no OIDC or social login.
- **R11:** tokens stay in memory, no `console.*` (Biome `noConsole` plus a test), and errors contain no token, password or email.

### Nits (do not block)
- `ts/src/client.ts:73-74`: the `storage`-event fallback only emits `account-changed`; it does not clear cached tokens. Without `BroadcastChannel`, a logout in another tab leaves that account's token in this tab's memory. It can be reached only through `getToken({accountId})`, because `getAccountId()` already returns null. Suggestion: call `this.#tokens.clearAll()` there too. Also, with both mechanisms present, a single change in another tab emits `account-changed` twice.
- `ts/src/token-cache.ts:52-57`: during a 429 block, a still-fresh cached token for a different key (explicit `accountId`) is rejected rather than served. Checking the cache before the block, for non-forced calls, would be friendlier.
- `ts/src/bearer.ts:31-35`: concurrent 401s inside the 30 s window. Only the first request retries; the others get the 401, even though a fresh token is now cached. A non-forced `getToken(false)` retry when the token has changed would fix that without extra `/token` load. As written, it follows the plan.
- `ts/src/transport.ts:77,85`: consider `redirect: "error"` on the auth and public calls. The API never redirects.
- `ts/src/client.ts:167,214`: the explicit `accountId` passed to `getToken` and `resendVerification` is not UUID-checked, unlike `selectAccount`, `join` and `logout`. The server answers 422 anyway.
- `ts/src/client.ts:262`: `token_type` and `audience` in the `/token` response are not checked against `Bearer` and `appKey`.
- `.github/workflows/python.yml:46`: `uvx pip-audit` is unpinned. Pin it, e.g. `uvx pip-audit@<ver>`, so that CI tooling is reproducible.

### Follow-ups (out of scope)
- org-admin-api `app/core/cors.py:39`: `IDENTITY_POLICY.expose_headers` is only `Retry-After`. Because `X-Request-ID` is not exposed, `AuthError.requestId` is always undefined for cross-origin browser auth calls. Consider exposing it (server ticket).
- `fetchWithToken` sends the Bearer token to any URL the caller passes. An optional `allowedOrigins` option for the app's own backend would limit a misuse leak.
- These are already in the impl-log: map a 429 without an envelope to `RateLimitedError`; `join()` must accept `"pending"` when USR-03g lands; wrap backend `TypeError` in `fetchWithToken`.

## Final — Attempt 1 — 2026-11-16
Verdict: PASS

Scope: the whole ticket. mmlabs-auth `main..HEAD` on `claude/blissful-wozniak-fx4lm9` = `4ad6e5c` (A), `9b723d9` (B), `1f6dcf7` (B hardening), `7079675` (C), `cf911ac` (D), `3c84715` (E1). A and B were already reviewed above, so this pass focuses on B hardening, C, D and E1. org-admin-api `origin/main...HEAD` = `docs/consumers.md` (E2, +29 lines) plus `work/` bookkeeping only. Both working trees were clean before and after the review.

### Checks (run by the reviewer)
mmlabs-auth, from a clean tree at `3c84715`:
- `pnpm -C ts install --frozen-lockfile`: "Lockfile is up to date, resolution step is skipped"
- `pnpm -C ts lint`: "Checked 30 files in 23ms. No fixes applied."
- `pnpm -C ts typecheck`: exit 0
- `pnpm -C ts test`: "Test Files 12 passed (12)", "Tests 146 passed (146)"
- `pnpm -C ts build`: exit 0. I removed `ts/dist/` afterwards.
- `pnpm -C ts audit --prod`: "No known vulnerabilities found"
- `uv sync --locked --directory python`: "Resolved 34 packages … Checked 33 packages"
- `ruff check .`: "All checks passed!"
- `ruff format --check .`: "22 files already formatted"
- `mypy`: "Success: no issues found in 21 source files"
- `pytest`, with the org-admin-api checkout present: "148 passed". With `ORG_ADMIN_API_DIR=/nonexistent` (the CI case): "143 passed, 5 skipped", so the drift tests skip cleanly.
- `uvx zizmor@1.16.3 --offline .github/workflows`: "No findings to report. Good job! (4 suppressed)", covering ts.yml, python.yml and release.yml.

org-admin-api: `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` (local Postgres) exited 0:
- ruff: "All checks passed!"
- format: "222 files already formatted"
- mypy: "Success: no issues found in 101 source files"
- pytest: "1716 passed, 1 warning"

All of these results match impl-log.md (Stage D and Stage E) exactly. `alembic check` does not apply: no org-admin-api code or migration changed.

### Acceptance criteria → evidence
| AC | Evidence | Result |
|---|---|---|
| Separate repo, with CI and versioning there | `/home/user/mmlabs-auth` has `ts/`, `python/`, `contract/`. CI: `.github/workflows/ts.yml`, `python.yml`. Versioning: `.github/workflows/release.yml:6-10` (tags `ts-v*` / `py-v*`), tag-vs-manifest checks at `release.yml:37-47` and `:94-102`, and `docs/adr/0001-packaging-and-release.md:19-31`. Green CI on the PR is still to be confirmed once the PR is open (the orchestrator's DONE condition). | met |
| TS unit tests: account choice per origin, token refresh, error mapping | `ts/test/accounts.test.ts`, `token-cache.test.ts` and `errors.test.ts` (contract-driven `it.each`) are green: 146/146. | met |
| Python tests: valid / expired / wrong-aud / unknown-kid token, ETag cache, JWKS cooldown | `python/tests/test_access_token.py:48` (valid), `:54` (expired), `:65` (wrong aud), `:115` (unknown kid). ETag: `test_jwks.py:52` (`304_keeps_keys_and_extends_ttl`) and `test_catalog.py:44/56/66` (ETag 304, no revalidation within 60 s, revalidation after 60 s). Cooldown: `test_jwks.py:72/83/95/106` (refetch once, none within 60 s, again after cooldown, cooldown after a failed fetch) and `:137` (flush). Unknown-kid refetch through the verifier: `test_verifier.py:33`. All 148 green. | met |
| org-admin-api `docs/consumers.md` links to the libraries | The diff adds `## Client libraries` (links to `ts/README.md` and `python/README.md` on GitHub, tag-pinned install lines, and "this page wins"), plus the pointer above `## PyJWT sketch`. | met |
| React wrapper (provider + hooks) usable by admin-pan | `ts/src/react/index.tsx:57-130` (`AuthProvider`), `:139` (`useAuth`), `:146` (`useAccessToken`, functions only), `:159` (`useJoin`). `./react` is exported (`package-exports.test.ts`). `react.test.tsx:274` is the admin-pan smoke test (`appKey "admin"` → `audience admin`, `X-MM-App: admin`). The admin-pan example is in `ts/README.md` §7 (`:195`+). | met |
| `getToken` in-memory cache, refresh before expiry, single-flight, never persisted | Reviewed at A+B: `token-cache.ts` skew `min(60 s, 20 %)`, single-flight, generation guard. Hardening: an in-flight result that lands after logout or a switch is now rejected with `account_changed` and is neither cached nor returned (`token-cache.ts` `#fetch`). The storage-hygiene test still passes with `apiOrigins`. | met |
| `listAccounts()`, `mm_account_id` per origin, logout one / all | Reviewed at A+B (`accounts.ts`, `client.ts` logout clears synchronously). React `logout` reloads after the POST (`react/index.tsx:109-118`). | met |
| Typed errors `not_member`, `email_unverified`, `session_invalid`, `rate_limited` (`retryAfter`) plus a generic fallback | Reviewed at A+B. Hardening: `errors.ts` `CODE_RE` (an untrusted code becomes `unexpected_response`); `retry-after.ts` clamps to [0, 900] s, with a second clamp in `token-cache.ts`. Errors table: `ts/README.md` §6. | met |
| `credentials:"include"` + `X-MM-App` only on `/api/v1/auth/*`; Bearer never sends cookies | Reviewed at A+B. Hardening: `transport.ts` `isAuthPath` (refuses `..` and `%2e`, checks the resolved prefix). The Bearer destination guard `bearer.ts` `assertDestination` requires https or opted-in loopback, an origin in `apiOrigins`, and never the identity `/api/v1/auth` (decoded, slash-collapsed, case-insensitive). Tests: `bearer.test.ts:164-221`. | met |
| Resend verification, then a fresh token after the email is verified | Reviewed at A+B (`endpoints.test.ts`, `token-cache.test.ts` "fresh token after verifyEmail"). Documented flow: `ts/README.md` §5. | met |
| Python scope: JWKS by `kid` (flushable, ≤ 1 refetch / 60 s), ES256 (`iss`, `aud`, `exp`, `typ`), catalog with ETag, `require_permission()` | `jwks.py:97-105` (flush with generation), `:117-128` (unknown-kid cooldown under a lock with re-check), `:30-49` (EC P-256 / ES256 / `sig` only; private `d` and off-curve points refused). `access_token.py:48-85` is the verbatim server logic plus the planned leeway and `kid` changes. Drift guard: `test_drift.py:92-118`, comparing against the org-admin-api checkout. `catalog.py` (admin refused, `APP_KEY_RE` identical to server `rbac/models.py:33`, 60 s / 15 min bounds, 404 → config error, foreign prefixes dropped). `fastapi.py:296` permission regex equals server `PERMISSION_KEY_RE` (`rbac/models.py:34`) with the audience fixed; 401, 403 and 503 use the R6 envelope; logs carry reason, jti and required only. | met |
| Integration guide and publishing | `ts/README.md` (§1–8), `python/README.md` (§1–9), root `README.md` Install/Releases, and ADR-0001 (public repo, no token, Release tarball vendored, git-tag install, provenance deferred, LICENSE open). | met |

### Focus review (B hardening, C, D, E1)
- **B hardening** (`1f6dcf7`) closes the security review's 🟡1–3 and 💭4–9 as specified:
  - stale-token rejection, with the account fixed for the 401 retry (`bearer.ts` checks `getAccountId() !== accountId` before and after the forced refresh);
  - the destination guard is lazy, so SSR clients that never call `fetchWithToken` need no `apiOrigins`;
  - `pip-audit==2.10.1` is pinned;
  - hatchling is bounded `>=1.27,<2`;
  - the storage fallback clears tokens and is installed only without `BroadcastChannel`.
  - No regressions: the A+B tests remain green.
- **C:** the wrapper holds no auth logic. A sequence ref drops stale `listAccounts()` results, and unmount unsubscribes and invalidates. No token ever enters state or context: `react.test.tsx` deep-walks the context, hooks and rendered HTML. The core graph never imports `react` (`package-exports.test.ts`).
- **D:** the verifier never fetches JWKS for a header that would fail anyway (`verifier.py:40-49`). It never reads `jku`, `x5u` or `jwk`. JWKS/catalog unavailability → 503 `auth_unavailable`, fail closed. `Principal` keeps `email` and `claims` out of its repr. `_http.py` streams with a byte cap, enforces a 5 s total timeout via `asyncio.timeout`, refuses redirects, and is HTTPS-only.
- **E1:** release.yml has top-level `contents: read` and job-level `contents: write` only. Actions are SHA-pinned (the same SHAs as CI), `persist-credentials: false`, caches are disabled, all `${{ }}` values pass through `env:`, and `gh release create --verify-tag` uses `github.token` only. zizmor is clean.

### Scope (R7)
Every changed file is in the plan or justified in the impl-log:
- `python/tests/__init__.py` (imports).
- `python/tests/test_drift.py` (orchestrator request; pulls part of F2 forward).
- `allow_insecure_loopback` on `MmlabsAuth` (local development; does not weaken production defaults).

org-admin-api source diff is `docs/consumers.md` only, as planned. The E2 wording "public, no token" replaces the plan's "private repo" line, following the 2026-11-16 owner decision recorded in ticket.md.

### Rules R1–R11
No violations.
- R1, R2, R4 and R9 do not apply: there is no org-admin-api code change.
- R3: each TS transport class matches its endpoint class.
- R5: there are no secrets. Tests mint ephemeral keys, and `contract/` holds dummies only.
- R6: the envelope is used in `require_permission` (`fastapi.py:58-65`).
- R10: there is no OIDC or social login.
- R11: tokens stay in memory, there is no console or log output of tokens or emails, and the JWKS cooldown and flush are implemented.

### Issues
None blocking.

### Nits (do not block)
- 💭 `.github/workflows/release.yml:24-25,81-82`: `contents: write` covers the whole job, including install and test steps. Splitting it into a read-only build job (uploading an artifact) and a small publish job with write access would narrow this further. zizmor does not flag it, and installs run with `ignore-scripts`.
- 💭 `ts/src/react/index.tsx:163-176`: `useJoin` can call `setPending` or `setError` after unmount. This is harmless in React 18+.
- 💭 `python/src/mmlabs_auth/jwks.py:227-248`: when the TTL has expired and the `kid` is unknown, one call can make two fetches (the TTL refresh, then the unknown-`kid` refresh). This is bounded by the cooldown.

### Process notes for the orchestrator (not code issues)
- `security-review.md` covers only stages A+B. CLAUDE.md requires the appsec pass at REVIEW for `auth`/`security` areas. If no appsec pass for C–E1 is planned, run one before DONE. This review found no security defect in C, D or E1.
- DONE still needs both PRs opened and the mmlabs-auth PR green on `ts.yml` and `python.yml`.

### Follow-ups (out of scope)
- The `ts-v0.1.0` and `py-v0.1.0` tags and Releases do not exist yet. The install lines in `docs/consumers.md` and both READMEs go live once the owner tags after the merge.
- LICENSE for mmlabs-auth (owner decision).
- F1: CLAUDE.md R11 cookie wording versus D20.
- F2: org-admin-api side of the `error-codes.json` drift check.
- F4: provenance.
- F5: Angular.
- `join()` must accept `"pending"` when USR-03g lands.
- Expose `X-Request-ID` in identity CORS (server).
