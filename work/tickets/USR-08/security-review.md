## Stages A+B — Attempt 1

# security-review — USR-08 Stages A+B (mmlabs-auth `main..HEAD`: 4ad6e5c, 9b723d9)

Scope: `ts/src/*`, `.github/workflows/*`, `.github/dependabot.yml`, `ts/package.json`, `ts/.npmrc`, `python/pyproject.toml`, the lockfiles and `contract/*`. I ran `pnpm -C ts test`: 10 files and 109 tests passed. `git status` was clean before and after the run.

## Findings

**🟡 1. A stale token reaches callers after logout or an account switch.** Files: `ts/src/token-cache.ts:79`, `ts/src/client.ts:69`, `ts/src/bearer.ts:27-36`.
- `TokenCache.#fetch` checks the generation stamp only to decide whether to cache. It still returns `issued.token` to every waiting caller.
- The test `token-cache.test.ts:115-131` asserts this: after `logout({accountId: A})` or `selectAccount(B)`, the in-flight `getToken()` resolves to `"before"`.
- So a `getToken()` started before logout hands out a token for the signed-out account. `fetchWithToken` then sends it.
- Worse, `fetchWithToken` reads the current account on each call (`getToken: (f) => this.getToken({forceRefresh: f})`). Sequence: a request built for account A gets a 401, the user switches to B in the meantime, and the retry goes out with **B's** token. A mutation intended for A then runs as B, which is identity confusion.
- **Fix:**
  - In `#fetch`, when `this.#stamp(accountId) !== stamp`, reject with `new SessionInvalidError("session_invalid", 0)` (or a new `AuthError("account_changed", 0)`) instead of returning the token.
  - In `createFetchWithToken`, take the `accountId` once at the start and pass it to both `getToken` calls. If `getAccountId()` no longer equals it before the retry, return the first response without retrying.
  - Change the test so it expects a rejection.

**🟡 2. `fetchWithToken` has no destination guard.** File: `ts/src/bearer.ts:19-24`.
- Any `input` gets `Authorization: Bearer`: other origins, `http:` URLs, and the identity API's own `/api/v1/auth/*`.
- A consumer that builds the URL from route params, a CMS or a query string leaks the token to a third party. A typo in `http://` sends it in clear.
- The ask "never send Bearer to /auth" is not enforced.
- **Fix:** add an `apiOrigins?: string[]` option, defaulting to `[location.origin]` when a `location` exists. Resolve `new URL(input, location.href)` and require that:
  - the protocol is `https:`, or loopback with `allowInsecureLocalhost`;
  - the origin is in the list;
  - the target is not on the identity `baseUrl` origin under `/api/v1/auth/`.
  
  Otherwise throw `ConfigurationError("bearer_origin_not_allowed", 0)` before calling `getToken` or `fetch`. Add tests for a cross-origin URL, an `http:` URL and a `${baseUrl}/api/v1/auth/token` URL.

**🟡 3. CI runs an unpinned tool.** File: `.github/workflows/python.yml:47`.
- `uvx pip-audit` resolves the latest `pip-audit` (and its dependencies) from PyPI on every run. That is unlocked code running in CI on a now-public repo. The token is read-only and there are no secrets, so the impact is limited, but it breaks the "everything pinned" rule.
- **Fix:** `uvx pip-audit==<exact version>`, or add `pip-audit` to the `dev` group so `uv.lock` covers it and run it with `uv run pip-audit`.

**💭 4. The build backend is not pinned.** File: `python/pyproject.toml:25`.
- `requires = ["hatchling"]` is fetched fresh in build isolation on `uv sync`, and `uv.lock` does not cover it.
- **Fix:** pin a range, e.g. `hatchling>=1.27,<2`, or an exact version.

**💭 5. `Retry-After` has no upper bound.** Files: `ts/src/retry-after.ts:9`, `ts/src/token-cache.ts:82`.
- A huge value (e.g. 400 digits makes `Number(...)` return `Infinity`), or a misconfigured proxy or CDN, blocks `/token` in that tab until reload.
- **Fix:** clamp to `[0, 900]` s, the 15-minute cap from T5.

**💭 6. The server `code` is copied into the error unchecked.** File: `ts/src/errors.ts:25`, `:160-166`.
- For unknown codes, the server-supplied string goes straight into `message` (`mmlabs-auth: ${code}`) and into `code`. A non-identity responder, such as an intermediary error page with JSON, could put arbitrary text, including PII, into logged errors.
- **Fix:** accept `code` only if it matches `/^[a-z][a-z0-9_]{0,63}$/`, otherwise use `unexpected_response`.
- Otherwise the error hygiene is good:
  - `toJSON` spreads only own enumerable fields, and `details` is never stored;
  - `stack` and `cause` are not copied;
  - `NetworkError` drops the native error, which may contain the URL;
  - the subclasses keep only allow-listed fields (`joinAvailable`, `reasons`, `issues[type, loc]`, `retryAfter`).

**💭 7. Several `accountId` arguments are not validated.** Files: `ts/src/client.ts:167` (`getToken({accountId})`) and `:214` (`resendVerification(accountId)`).
- These are not checked as UUIDs, unlike `selectAccount`, `join` and `logout`.
- No injection is possible: the value only goes into a JSON body and the cache key. But it allows `|` in the cache key and costs a pointless 422 round-trip.
- **Fix:** run `requireAccountId()` in both places.
- Separately, `mm_account_id` is UUID-checked on read and dropped if invalid, and no value ever goes into a URL path. `join` uses only the regex-checked `appKey`. Both are correct.

**💭 8. `authFetch` prefix check is not traversal-safe.** File: `ts/src/transport.ts:73`.
- `startsWith("/api/v1/auth/")` would accept `/api/v1/auth/../orgs`, which fetch normalises to `/api/v1/orgs`, and would send the cookie and `X-MM-App` there.
- All callers pass constants today, so this is not exploitable.
- **Fix:** check `new URL(baseUrl + path).pathname.startsWith(prefix)` and reject paths containing `..` or `%2e`.

**💭 9. Fallback cross-tab path keeps old tokens.** File: `ts/src/broadcast.ts:52-54` (with `client.ts:73-74`).
- Without `BroadcastChannel`, the `storage` event fallback only emits an event and does not clear the token cache.
- Selection itself is re-read on each `getToken`, so the main path is safe. But tokens for an account logged out in another tab stay in memory (≤15 min) and can still be returned via `getToken({accountId: X})`.
- **Fix:** call `this.#tokens.clearAll()` in `onAccountStorage`.

**💭 10. Unthrottled refresh paths and the order of logout.**
- `getToken({forceRefresh: true})` and `invalidateToken()` are not throttled; only the 429 block bounds them. The block is also per client instance, so a re-created client (React remount) bypasses it.
- `logout()` clears local state before the API call. If `/logout` fails, the device session stays valid on the server. The error does reach the caller, so the README should say to retry or surface it.

## Verified OK (attack attempts that failed)

- **(1) Token storage and exposure:**
  - tokens live only in `#private` Maps;
  - no `localStorage`, `sessionStorage`, IndexedDB, cookie or global writes;
  - the BroadcastChannel payload is `{type[, accountId]}`, and parsing checks exact keys and a UUID;
  - `AuthEvent` carries no token;
  - no `console.*` calls (Biome `noConsole: error`);
  - `storage-hygiene.test.ts` covers this.
- **(2) Cookies and headers:**
  - `authFetch` sends `credentials:"include"` + `X-MM-App` + `no-store`;
  - `publicFetch` uses `omit` with no `X-MM-App`;
  - Bearer calls force `credentials:"omit"` and delete `X-MM-App` case-insensitively; `init` overrides a `Request`'s credentials.
- **`baseUrl`:**
  - `http:` is rejected except loopback with the opt-in;
  - userinfo, query and fragment are rejected;
  - the `localhost.` and `127.1` style forms are not in the allow-set, so they are refused.
- **(3) Amplification:**
  - single-flight per `account|audience`;
  - one forced refresh per 30 s per client;
  - no retry for streamed bodies;
  - the 429 block is checked before the cache, including on `forceRefresh`, and survives `clearAll` and an account switch;
  - no timers.
- **(7) CI and supply chain:**
  - all four actions are pinned to SHAs that match their tags (checked with `git ls-remote`: checkout v7.0.1, setup-uv v10.3.3, pnpm/action-setup v6.1.1, setup-node v7.1.0);
  - `permissions: contents: read`, `persist-credentials: false`;
  - only `push: main` and `pull_request` triggers, with no `pull_request_target`;
  - no secrets are referenced;
  - `--frozen-lockfile` / `uv sync --locked`;
  - lockfiles carry integrity hashes;
  - `.npmrc` sets `ignore-scripts=true`, plus `onlyBuiltDependencies: []`;
  - zero runtime dependencies, React is an optional peer, `"private": true`, no lifecycle scripts;
  - Dependabot covers npm, uv and actions.
- **(8) `contract/`:** holds dummy values only (`dummy-access-token-not-a-jwt`, a fake ETag). Grepping the diff found no JWTs (`eyJ…`), PEM keys, `ghp_` or `npm_` tokens.

## Follow-ups (out of scope)

- Public-repo settings:
  - branch protection on `main` with required checks (note: path-filtered workflows don't report on unrelated PRs, so use a always-run aggregator or don't require them);
  - "Require approval for all outside collaborators" for fork PR workflows;
  - secret scanning and push protection;
  - private vulnerability reporting / `SECURITY.md`.
- Stage C (React): context should expose `getToken` only, never the token value.
- Stage E (release): OIDC/provenance, with no long-lived npm/PyPI tokens.
- CLAUDE.md R11 still describes `mm_device; Path=/api/v1/auth`, which is out of date since D20 (`__Host-mm_device`, `Path=/`). Already flagged in the consult.

**Verdict:** PASS with should-fix. There are no 🔴 findings. 🟡 1 (stale token reaching callers after logout or an account switch, and a 401 retry sent under a different account) and 🟡 2 (no Bearer destination guard) should be fixed in this ticket, and 🟡 3 is a one-line CI pin.

## Final — Attempt 1

**Scope:** mmlabs-auth `main..HEAD` (1f6dcf7 Stage B hardening, 7079675 Stage C React, cf911ac Stage D Python, 3c84715 Stage E1 release) + org-admin-api `docs/consumers.md` (6933962).

### Re-check of Stages A+B findings
All fixed with no bypass: 🟡1 stale token/identity confusion, 🟡2 Bearer destination guard, 🟡3 pinned pip-audit, 💭4 hatchling range, 💭5 Retry-After clamp, 💭6 code regex, 💭7 accountId validation, 💭8 authFetch traversal, 💭9 fallback-path token clear.

### New findings
- **🟡 A. Deeply nested JSON from JWKS/catalog escapes fail-closed and cooldown** — `python/src/mmlabs_auth/_http.py:117-119`, `jwks.py:155`, `catalog.py` `_refresh`. `json.loads` raises `RecursionError` (not `ValueError`) on deep nesting; only `(ValueError, UnicodeDecodeError)` is caught → unhandled exception → 500 instead of 503, and JWKS cooldown is not set so every request refetches. Needs a hostile/misconfigured HTTPS upstream; access outcome is still deny. Fix: catch `RecursionError` in `fetch_json` → `FetchError("invalid_json")`; optional catch-all around parse in `_refresh`; test with `b"[" * 60000`.
- **💭 B. Decompression not bounded per chunk** — `_http.py:92-106`. Fix: send `Accept-Encoding: identity`.
- **💭 C. JWKS TTL has no floor** — `jwks.py:167`. Fix: `max(min_ttl, …)` with a 30–60 s floor.
- **💭 D. Token length not capped before parsing** — `verifier.py:31`, `fastapi.py:~137`. Reject >8 KB as `malformed`.
- **💭 E. Release job holds `contents: write` while running build/test code** — `release.yml:24-25, 81-82`. Fix: split build (`contents: read`, upload artefact) and release (`contents: write`, no repo code) jobs.
- **💭 F. Release can be cut from any commit; tags can move** — `release.yml:6-10`. Fix: tag ruleset for `ts-v*`/`py-v*`; `git merge-base --is-ancestor "$GITHUB_SHA" origin/main`; docs note on pinning by commit SHA.

### Verified OK
Stage C React (token never in state/context/render; `useJoin` only on explicit call; reload sequence guard). Stage D verifier parity with `org-admin-api/app/core/access_token.py` (typ→alg→kid order, ES256 pinned, strict_aud, issuer, email_verified, PyJWT>=2.10.1). Key selection by header `kid` only (jku/x5u/jwk ignored). JWKS parsing (P-256 only, no `d`, on-curve, empty set keeps old keys, flush generation guard). JWKS fetch (HTTPS only, no redirects, timeouts, byte caps, 60 s unknown-kid / failure cooldown, single-flight, bounded stale serving, fixed error strings). Role catalog (admin refused, app match, prefix filter, 60 s revalidate, 15 min stale cap, 404 → 503). FastAPI dependency (401/403/503 mapping, no token/email in logs, `repr=False`). Release workflow E1 (tag-only trigger, env-only tag use, caches off, SHA-pinned actions, no long-lived secrets). `docs/consumers.md` E2 (links only, tag-pinned installs).

### Follow-ups (out of scope)
- Repo settings: tag ruleset, branch protection, secret scanning + push protection, `SECURITY.md`.
- Release provenance (`actions/attest-build-provenance`) — deferred in ADR-0001.
- `LICENSE` still missing (owner decision).
- CLAUDE.md R11 cookie wording stale vs D20.

**Verdict:** PASS with should-fix — no 🔴; 🟡A is a one-line fix that should land in this ticket.
