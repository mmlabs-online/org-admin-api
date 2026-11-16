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
