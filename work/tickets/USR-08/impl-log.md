# Implementation log — USR-08

## Stage A — Attempt 1 — 2026-11-16

Repo `/home/user/mmlabs-auth`, branch `claude/blissful-wozniak-fx4lm9`. Working tree only; not committed (the orchestrator commits).

### Files touched
| File (mmlabs-auth) | Lines | Required because |
|---|---|---|
| `README.md` | 39 (rewrite, +35 / −4) | Layout, guide links, stage-gate commands, release summary stub (filled in at E1) |
| `.gitignore` | unchanged | Already covers `.venv/`, `node_modules/`, `dist/`, caches |
| `contract/SOURCE` | 3 | Drift guard: org-admin-api `origin/main` = `74e9b629d36188758765d8714f4a3733a111f4d4` + refresh note |
| `contract/error-codes.json` | 41 | Every code from the plan's error-mapping table (`codes`: status/class/typed) + `library_codes` (ts, python) |
| `contract/token-response.example.json` | 10 | Dummy, non-JWT token example |
| `contract/catalog.example.json` | 20 | `RoleCatalog` shape (`app`, `roles[{key,name,is_system,permissions}]`) from org-admin-api `app/modules/rbac/schemas.py` |
| `ts/package.json` | 51 | `@mmlabs/auth-client` 0.1.0, `private`, ESM, exports `.`/`./react`, optional React peer, no `dependencies`, no lifecycle scripts, exact devDeps |
| `ts/pnpm-lock.yaml` | 1272 (generated) | S1 |
| `ts/tsconfig.json` | 20 | strict, `exactOptionalPropertyTypes`, `noUncheckedIndexedAccess`, NodeNext, ES2022 + DOM, `react-jsx`, `declaration` |
| `ts/tsconfig.build.json` | 9 | Emits `src/` only into `dist/` |
| `ts/biome.json` | 24 | Lint + format, `noConsole: error` (verified it fires) |
| `ts/vitest.config.ts` | 8 | `environment: "node"`, includes `test/**/*.test.ts(x)` |
| `ts/.npmrc` | 2 | `ignore-scripts=true`, `engine-strict=false` (S2) |
| `ts/src/index.ts` | 1 | Placeholder `VERSION` |
| `ts/test/smoke.test.ts` | 9 | VERSION matches package.json |
| `python/pyproject.toml` | 48 | `mmlabs-auth` 0.1.0, deps/extras/dev group, hatchling, ruff/mypy/pytest config per plan |
| `python/uv.lock` | 842 (generated) | S1 |
| `python/.python-version` | 1 | Out of plan, see below |
| `python/src/mmlabs_auth/__init__.py` | 3 | `__version__` |
| `python/src/mmlabs_auth/py.typed` | 0 | Typed-package marker |
| `python/tests/test_smoke.py` | 27 | Version check, contract files parse, `SOURCE` is a 40-hex SHA |
| `.github/workflows/ts.yml` | 44 | TS CI |
| `.github/workflows/python.yml` | 47 | Python CI |
| `.github/dependabot.yml` | 14 | npm `/ts`, uv `/python`, github-actions `/`, weekly (S4) |

Hand-written: ~430 lines (excluding the two lockfiles).

**Tool versions:** pnpm 10.28.0 (`packageManager`), Node 22.23.4 locally, uv 0.11.32, Python 3.12.13.
**devDependencies (exact):** typescript 7.0.2, vitest 5.1.0, happy-dom 21.0.4, @testing-library/react 16.3.3, @testing-library/dom 10.4.2, react / react-dom 19.3.1, @types/react / @types/react-dom 19.3.0, @biomejs/biome 2.6.8.
**Python locked:** PyJWT 2.15.1, httpx 0.28.1, cryptography 50.0.2; dev: ruff 0.17.5, mypy 2.4.1, pytest 9.2.0, pytest-asyncio 1.5.0, respx 0.23.1, fastapi 0.163.1.

**Action SHAs** (`gh api` is blocked for third-party repos in this session, so resolved with `git ls-remote` on the release tag, peeled `^{}` for annotated tags, and confirmed by a shallow clone of each tag whose `HEAD` matched):
| Action | Tag | Commit |
|---|---|---|
| `actions/checkout` | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| `pnpm/action-setup` | v6.1.1 | `73e46992e05b19abd31f2f3c20e0e7370d762115` (tag object `9bbca1e9…`) |
| `actions/setup-node` | v7.1.0 | `949feb2413d6458794dcd2491c4babbbce0c15c1` |
| `astral-sh/setup-uv` | v10.3.3 | `68559e99cc807c1eb7e9238fd43a15bba460eec2` |

Input names (`package_json_file`, `cache-dependency-path`, `python-version`, `enable-cache`, `cache-dependency-glob`) were checked against each action's `action.yml` at those commits.

### Tests added / changed
- `ts/test/smoke.test.ts › VERSION matches package.json`
- `python/tests/test_smoke.py › test_version_matches_pyproject`
- `python/tests/test_smoke.py › test_contract_files_parse`

### Checks (actual output summary)
- `pnpm -C ts install --frozen-lockfile`: "Lockfile is up to date … Already up to date" (exit 0)
- `pnpm -C ts lint`: "Checked 7 files … No fixes applied." (exit 0)
- `pnpm -C ts typecheck`: no errors (exit 0; a deliberate type error was confirmed to fail, then removed)
- `pnpm -C ts test`: "Test Files 1 passed (1), Tests 1 passed (1)"
- `pnpm -C ts build`: emits `dist/index.js`, `dist/index.d.ts` (exit 0)
- `pnpm -C ts audit --prod`: "No known vulnerabilities found"
- `uv sync --locked --directory python`: "Resolved 34 packages … Checked 33 packages" (exit 0)
- `ruff check .`: "All checks passed!"
- `ruff format --check .`: "2 files already formatted"
- `mypy`: "Success: no issues found in 2 source files"
- `pytest`: "2 passed"
- pip-audit (CI step, run locally): "No known vulnerabilities found"
- `uvx zizmor --offline .github/workflows`: "No findings to report" (2 suppressed, default-level)

### Deviations from the plan
- **`ts/package.json` devDependencies** add `@testing-library/dom` 10.4.2: a required peer of `@testing-library/react` 16, needed under pnpm's strict layout for stage C.
- **`ts/biome.json`** uses `"preset": "recommended"`: Biome 2.6 deprecates `"recommended": true` (produced by `biome migrate`).
- **`python/pyproject.toml`**: no `readme` field yet (`python/README.md` arrives in stage D; hatchling fails on a missing readme). Added `asyncio_default_fixture_loop_scope = "function"` to silence a pytest-asyncio deprecation warning.
- **`python.yml` pip-audit** uses `uv export … --no-emit-project` and writes to `$RUNNER_TEMP/req.txt` instead of `/tmp/req.txt`, so the local editable project is not passed to pip-audit.
- **`contract/SOURCE`** uses `origin/main` (orchestrator instruction) instead of `HEAD`.
- Workflows add `concurrency` and `timeout-minutes` (no permission or trigger change).
- No `LICENSE` (orchestrator: repo visibility undecided); `"private": true` kept.

### Out-of-plan files
- `python/.python-version` (`3.12`): the machine's default `python3` is 3.13, so without it the gate's `uv sync --locked --directory python` would build the venv on 3.13 rather than the CI target 3.12.

### Follow-ups (noticed, not done)
- TypeScript 7.0.2 (native compiler) is the current `latest`. If stage B/C hit TS 7 gaps (e.g. with vitest or `@types/react`), pin back to 5.9.x.
- README links to `ts/README.md` and `python/README.md` are dead until stages C and D.

### Scope self-check
- Could the diff be smaller? No: every file is in the plan's stage A list except `python/.python-version` (justified above).
- Diff size: about +430 hand-written lines, plus lockfiles (+1272 / +842); README +35 / −4.

## Stage B — Attempt 1 — 2026-11-16

Repo `/home/user/mmlabs-auth`, branch `claude/blissful-wozniak-fx4lm9`. Working tree only. Nothing committed or pushed; the orchestrator commits. The `smoke.test.ts` deletion is unstaged.

### Files touched
| File (mmlabs-auth) | Lines | Required because |
|---|---|---|
| `ts/src/index.ts` | 33 (rewrite) | Public exports: `createAuthClient`, `AuthClient`, every error class, types. The placeholder `VERSION` was dropped (the plan's surface does not list it). |
| `ts/src/types.ts` | 57 | `AuthClientOptions`, `Account`, `SignupResult`, `LoginResult`, `LogoutTarget`, `GetTokenOptions`, `AuthEvent`, `StorageLike`, `BroadcastLike`/`BroadcastFactory` |
| `ts/src/client.ts` | 304 | `AuthClient` (`#private` state), all 10 endpoints, local methods, `onChange`, `dispose` |
| `ts/src/transport.ts` | 94 | `normaliseBaseUrl`, `authFetch` (asserts `/api/v1/auth/` prefix, include + `X-MM-App` + no-store, Content-Type only with a body), `publicFetch` (omit, no `X-MM-App`), envelope → `mapError`, fetch failure → `NetworkError` |
| `ts/src/bearer.ts` | 37 | `fetchWithToken`: forces omit, strips `X-MM-App`, sets Bearer; one forced refresh + one retry on 401, gated to once per 30 s; no retry for a streamed body |
| `ts/src/errors.ts` | 167 | `AuthError` and its subclasses, fixed messages, allow-listed details, `toJSON`, `mapError` |
| `ts/src/retry-after.ts` | 21 | delta-seconds, then HTTP-date, then `details.retry_after`, then 5 |
| `ts/src/accounts.ts` | 62 | `AccountStore` (`mm_account_id`): UUID check on read (removes a bad value), a throwing storage falls back to memory |
| `ts/src/token-cache.ts` | 104 | Cache keyed `account\|audience`, single-flight, generation guard (per account + global epoch), per-audience `blockedUntil`, no timers |
| `ts/src/broadcast.ts` | 71 | `BroadcastChannel("mmlabs-auth:<appKey>")` with strict message validation, `storage` event fallback, a no-op when neither exists |
| `ts/test/helpers.ts` | 180 | `fakeFetch`, `fakeClock`, `memoryStorage`, `throwingStorage`, `FakeBroadcastHub`, `deferred`, `makeClient`, sentinels |
| `ts/test/transport.test.ts` | 140 | Transport tests |
| `ts/test/bearer.test.ts` | 101 | Bearer tests |
| `ts/test/errors.test.ts` | 167 | Error tests (table-driven from `contract/error-codes.json`) |
| `ts/test/accounts.test.ts` | 143 | Account tests |
| `ts/test/token-cache.test.ts` | 161 | Token cache tests |
| `ts/test/rate-limit.test.ts` | 59 | 429 tests |
| `ts/test/broadcast.test.ts` | 134 | Broadcast tests (happy-dom) |
| `ts/test/storage-hygiene.test.ts` | 116 | Storage hygiene tests (happy-dom) |
| `ts/test/endpoints.test.ts` | 131 | Endpoint tests |
| `ts/test/contract.test.ts` | 15 | Contract test |
| `ts/test/smoke.test.ts` | deleted | Superseded (plan) |

Size: src 950 lines, tests 1,347 lines (Biome-formatted, width 100). That is about 2× the plan's ~450 / ~550 estimate. Most of the extra comes from formatter line wrapping and from per-field response validation.

### Tests added
109 tests in 10 files, all green. Every test the plan lists for stage B is there, under the plan's names. The `transport` table runs as 8 cases (signup, login, logout, token, accounts, join, resend, forgot). The `errors` contract table runs as 25 cases, one per code in `contract/error-codes.json`.

Extra tests, not in the plan:
- `transport › rejects an invalid appKey`
- `bearer › uses the headers of a Request input and still omits cookies`
- `broadcast › uses the channel name mmlabs-auth:<appKey>`
- `endpoints › signup maps 409 email_taken`

Mutation sanity check, reverted afterwards. Each of these changes made at least one test fail:
- bearer `credentials:"include"`
- skew changed from `min` to `max`
- generation guard removed
- `Content-Type` dropped

### Checks (actual output)
- `pnpm -C ts install --frozen-lockfile`: "Lockfile is up to date … Already up to date"
- `pnpm -C ts lint`: "Checked 26 files … No fixes applied."
- `pnpm -C ts typecheck`: exit 0, no errors
- `pnpm -C ts test`: "Test Files 10 passed (10)", "Tests 109 passed (109)"
- `pnpm -C ts build`: exit 0. It emits `dist/*.js` and `dist/*.d.ts` for all 10 modules; `dist/` was removed afterwards and is git-ignored.
- `pnpm -C ts audit --prod`: "No known vulnerabilities found"
- `uv sync --locked --directory python`: "Resolved 34 packages … Checked 33 packages"
- `ruff check .`: "All checks passed!"
- `ruff format --check .`: "2 files already formatted"
- `mypy`: "Success: no issues found in 2 source files"
- `pytest`: "2 passed"

TypeScript 7.0.2 worked for everything. There was no pin back to 5.9.

### Deviations from the plan
- **Skew test numbers.** The plan's test row says "900 s → at 720 s". Its own formula, `skew = min(60 s, 20 %)`, and `docs/consumers.md` ("about 60 s before") both give **840 s** for 900 s. 100 s still gives 80 s. I implemented the formula and wrote the test as "900 s → at 840 s; 100 s → at 80 s".
- **Programmer errors throw `TypeError`**, not an `AuthError`, and make no request. This covers a bad `baseUrl` or `appKey`, a non-UUID passed to `selectAccount` / `join` / `logout`, `join()` without an argument, and `authFetch` outside `/api/v1/auth/`. The messages are fixed and never echo the value. This keeps `contract/error-codes.json` `library_codes.ts` at the three planned codes.
- **`unexpected_response` keeps the real HTTP status** (for example 502), not `0`, so that callers can tell a proxy error from an empty body. `no_account_selected`, `network_error` and the local `audience_mismatch` use status `0`. A locally blocked `RateLimitedError` keeps status 429.
- **`ValidationError` exposes its kept `{type, loc}` items as `issues`.** The plan did not name the field.
- **`getToken` has two overloads**, `getToken(options?)` and `getToken(audience, options?)`, so that both `getToken()` / `getToken({forceRefresh:true})` and `getToken("admin")` type-check.
- **`baseUrl` also rejects** userinfo, a query or a fragment.
- **Cross-tab `account_changed`** carries only `{type}`. The receiver re-reads `mm_account_id` itself. With `broadcast: false`, the `storage` listener is off too.
- **`join()` returns `"member"`** and treats any other `status` as `unexpected_response`. USR-03g's `"pending"` will need a change here.

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- When USR-03g adds `202 {status:"pending"}`, `join()` must accept it.
- A 429 that comes without an R6 envelope (for example from a CDN) maps to `unexpected_response`, not to `RateLimitedError`. Consider mapping on status alone.
- `fetchWithToken` lets a network `TypeError` from the app's own backend through unchanged; it is not wrapped in `NetworkError`.

### Scope self-check
- Could the diff be smaller? Only cosmetically. Every file is on the plan's stage B list, and the size comes from formatting and validation, not from extra features.

## Stage B hardening — Attempt 1 — 2026-11-16

Scope: only the items from `security-review.md` "Stages A+B — Attempt 1" (🟡1–3, 💭4–9) and the matching `review.md` nits. Repo `/home/user/mmlabs-auth`, branch `claude/blissful-wozniak-fx4lm9`, on top of `9b723d9`. Not committed or pushed, as instructed.

### Changes per item
- **🟡1 stale token.** `TokenCache.#fetch` now rejects with a new local `AuthError("account_changed", 0)` when the account's stamp changed in flight. The token is neither cached nor returned. I chose `account_changed` over `session_invalid` so that `getToken`'s `SessionInvalidError` handling (clearAccount) and the "sign in again" meaning stay untouched. `createFetchWithToken` reads `getAccountId()` once and passes that account to both `getToken` calls. Before the retry, and again after the forced refresh, it returns the first response if the selection changed. An `account_changed` rejection during the refresh also returns the first response. The old test that expected `"before"` now expects the rejection.
- **🟡2 Bearer destination guard.** A new option `apiOrigins?: string[]`. Entries are normalised to origins, and an invalid entry throws `TypeError` at construction. The default is `[location.origin]` when `location` exists; otherwise nothing is allowed. The target is resolved with `new URL(input, location?.href)`. It must be `https:`, or loopback `http:` with `allowInsecureLocalhost`, and its origin must be in the list. On the identity `baseUrl` origin, any path that, once percent-decoded and slash-collapsed, contains `/api/v1/auth` (case-insensitive) is refused. Otherwise it throws `ConfigurationError("bearer_origin_not_allowed", 0)` before `getToken`/`fetch`. The check is lazy (at call time), so SSR or Node clients that never call `fetchWithToken` need no `apiOrigins`.
- **🟡3** CI: `uvx pip-audit==2.10.1` (latest, released 2026-06-10). I ran it locally: "No known vulnerabilities found".
- **💭4** `[build-system] requires = ["hatchling>=1.27,<2"]`. `uv build --wheel` succeeds.
- **💭5** `parseRetryAfter` clamps every path to [0, 900] s (`MAX_RETRY_AFTER`). Non-finite values (a 400-digit header) become 900. `TokenCache` clamps again before setting the block.
- **💭6** `mapError` accepts a server `code` only if it matches `/^[a-z][a-z0-9_]{0,63}$/`; anything else becomes `unexpected_response`, keeping the HTTP status.
- **💭7** `getToken({accountId})` and `resendVerification(accountId)` run `requireAccountId()`, which throws `TypeError` with no request.
- **💭8** `authFetch` refuses a path containing `..` or `%2e` (any case). It also requires `new URL(baseUrl + path).pathname` to start with the resolved `baseUrl + /api/v1/auth/` pathname, which works with a `baseUrl` path prefix.
- **💭9** The `storage` fallback handler now calls `#tokens.clearAll()` before emitting. The `storage` listener is installed only when no `BroadcastChannel` was created, so one change in another tab emits `account-changed` once.
- `contract/error-codes.json` `library_codes.ts` adds `account_changed` (AuthError) and `bearer_origin_not_allowed` (ConfigurationError).

### Files touched (mmlabs-auth)
- `ts/src/token-cache.ts`: in-flight stale rejection; clamp on block.
- `ts/src/bearer.ts`: destination guard, `normaliseOrigins`, fixed account, retry rules.
- `ts/src/client.ts`: wires `apiOrigins`, `baseUrl`, `allowInsecureLocalhost`, `getAccountId` and per-account `getToken` into the bearer; UUID checks (💭7); storage fallback clears tokens (💭9).
- `ts/src/types.ts`: `AuthClientOptions.apiOrigins`.
- `ts/src/transport.ts`: exports `LOOPBACK`; traversal-safe `authFetch` prefix check.
- `ts/src/retry-after.ts`: `MAX_RETRY_AFTER`, clamp.
- `ts/src/errors.ts`: `CODE_RE` check in `mapError`.
- `ts/src/broadcast.ts`: the `storage` listener is a fallback only.
- `contract/error-codes.json`: two library codes.
- `.github/workflows/python.yml`: pinned `pip-audit`.
- `python/pyproject.toml`: bounded hatchling.
- Tests:
  - `ts/test/token-cache.test.ts`: the rejection replaces `"before"`; all waiting callers are rejected after logout; non-UUID `accountId` gives `TypeError`.
  - `ts/test/bearer.test.ts`: `apiOrigins` in setup; the retry keeps its account and is skipped when the selection changes before or during the refresh. Destination guard tests: cross-origin (string, `URL`, `Request`, suffix host), `http:`, identity `/api/v1/auth/token` (incl. `..`, `//`, upper-case, `%61`), relative URL without `location`, no list and no `location`, allowed origin, loopback with and without the opt-in, an invalid entry.
  - `ts/test/broadcast.test.ts`: the fallback (no `BroadcastChannel`) clears tokens and emits once; with a channel, one event only.
  - `ts/test/rate-limit.test.ts`: clamp for 901, 400 digits, a far-future date, a past date, and `details` 1e9.
  - `ts/test/errors.test.ts`: invalid server codes give `unexpected_response`; 64 chars is accepted.
  - `ts/test/endpoints.test.ts`: `resendVerification("not-a-uuid")` gives `TypeError`.
  - `ts/test/transport.test.ts`: traversal paths are refused (`..`, `%2e`, `%2E`, `.%2e`) with and without a `baseUrl` path; a path-prefixed `baseUrl` is accepted.
  - `ts/test/storage-hygiene.test.ts`: the full-flow client passes `apiOrigins`.

### Stage gate (run from a modified tree)
- `pnpm -C ts install --frozen-lockfile`: "Done"
- `pnpm -C ts lint`: "Checked 26 files … No fixes applied."
- `pnpm -C ts typecheck`: exit 0
- `pnpm -C ts test`: "Test Files 10 passed (10); Tests 128 passed (128)", up from 109.
- `pnpm -C ts build`: exit 0
- `uv sync --locked --directory python`: OK
- `ruff check .`: "All checks passed!"
- `ruff format --check .`: "2 files already formatted"
- `mypy`: "Success: no issues found in 2 source files"
- `pytest`: "2 passed"
- Extra checks: `uvx pip-audit==2.10.1 -r <exported runtime reqs>` reported "No known vulnerabilities found". `uv build --wheel` built the wheel into the scratchpad.

### Deviations
- With no `apiOrigins` and no `location`, `fetchWithToken` throws `bearer_origin_not_allowed` at call time, not at construction. Otherwise every Node or SSR client would have to configure an option it never uses.
- The identity-path check also catches percent-encoded (`%61uth`), double-slash and upper-case variants, because a server router may decode or normalise them. A test found the `%61` case.

### Out-of-plan files
None. Every file was named by the review items or already belongs to stage B.

### Follow-ups (noticed, not done)
- `ts/README.md` / integration guide (stage E): document `apiOrigins` and the `account_changed` / `bearer_origin_not_allowed` codes.
- Review nits that were not in this pass: serve a fresh cache during a 429 block for other keys; non-forced retry for concurrent 401s; `redirect: "error"`; check `token_type`/`audience` in `/token`.

## Stage C — Attempt 1 — 2026-11-16

Repo `/home/user/mmlabs-auth`, branch `claude/blissful-wozniak-fx4lm9`, on top of `1f6dcf7`. Working tree only: nothing committed or pushed (the orchestrator commits).

### Files touched
| File (mmlabs-auth) | Lines | Required because |
|---|---|---|
| `ts/src/react/index.tsx` | 179 (new) | `AuthProvider({client, children})`, `useAuth()`, `useAccessToken()`, `useJoin()`, types `AuthStatus`, `AuthState`, `AccessToken`, `JoinState`. Imports only `react` and core modules; core never imports it |
| `ts/test/react.test.tsx` | 353 (new) | Every plan Stage C React test, plus the no-token-in-rendered-output test and a join-failure test |
| `ts/test/package-exports.test.ts` | 83 (new) | The 4 packaging tests |
| `ts/README.md` | 280 (new, Biome-formatted) | Integration guide, following the plan's 10-point outline |

`ts/package.json` was unchanged: `./react` export, optional `react` peer and the React/testing devDeps were already there from stage A.

### Design notes
- The wrapper has no auth logic. It calls `listAccounts()` on mount and on every `client.onChange` event. Status: empty list or `SessionInvalidError` → `signed-out`; the stored id is listed → `ready`; other accounts are listed → `needs-account`; any other error → `error` (with `error`). `accountId` is set only when `ready`. A stale stored id counts as `needs-account`.
- There are two contexts. One holds the `AuthClient`, whose tokens live in `#private` fields. The other holds the state, and `useAuth()` returns that context value unchanged. Neither ever holds a token. `useAccessToken()` returns only stable `getToken` / `fetchWithToken` functions.
- A sequence ref means only the latest `listAccounts()` result sets state. Unmount invalidates any call still in flight and unsubscribes.
- `logout` calls `reload()` again after the POST settles. The client emits `signed-out` before the POST, so without this the first list could still show the account that was just logged out.
- `useJoin().join(accountId)` sets `pending` and `error`, and rethrows so the caller can use `instanceof`. Nothing in the wrapper calls `join`.

### Tests added (18; TS total 128 → 146 in 12 files)
`react.test.tsx` (14):
- status loading → ready when the stored account is listed
- signed-out on empty list or session_invalid
- needs-account when accounts exist but none chosen (also covers a stale stored id)
- error status on NetworkError
- selectAccount updates accountId and status
- login and logout update state (also checks that the last list call comes after the logout POST)
- another tab's broadcast re-renders (two clients, `FakeBroadcastHub`, shared storage)
- hooks outside provider throw (all three hooks)
- unmount unsubscribes
- useAccessToken exposes getToken/fetchWithToken and no token value. It fetches a real token and calls `fetchWithToken`. It then checks `JSON.stringify` and a deep walk of all reachable strings, over the `useAuth()` context value, the client in context, `useAccessToken()` and `useJoin()`. It also checks that the callbacks stay stable.
- no token value appears in rendered output. A component renders `JSON.stringify` of all three hooks and fetches a token on click. The test checks `container.innerHTML` and `document.documentElement.outerHTML`. (extra, requested by the orchestrator)
- admin-pan smoke: appKey "admin", getToken() requests audience admin, with `X-MM-App: admin`
- useJoin calls join only when invoked; NotMemberError surfaces via instanceof. It checks 0 join calls after `not_member`, then `pending` true → false, 1 join call, then a token.
- join failure sets error and rethrows (extra)

`package-exports.test.ts` (4):
- exports . and ./react (also checks the optional peer)
- core does not import react. It walks relative imports from `src/index.ts`: no `src/react` file and no bare specifier at all. As a positive control, the React entry's graph does contain `react`.
- no runtime dependencies
- no lifecycle scripts

Mutation check, reverted afterwards. Each of these changes failed exactly the matching test:
1. the `useAccessToken` result keeps the last token
2. the `SessionInvalidError` branch is removed
3. no unsubscribe on unmount
4. auto-join on `not_member`
5. no reload after logout

### Stage gate (actual output)
- `pnpm -C ts install --frozen-lockfile`: "Lockfile is up to date … Already up to date"
- `pnpm -C ts lint`: "Checked 30 files … No fixes applied." (exit 0)
- `pnpm -C ts typecheck`: exit 0
- `pnpm -C ts test`: "Test Files 12 passed (12)", "Tests 146 passed (146)"
- `pnpm -C ts build`: exit 0, emits `dist/react/index.js` and `index.d.ts`. `dist/` was removed afterwards and is git-ignored.
- `uv sync --locked --directory python`: "Resolved 34 packages … Checked 33 packages"
- `ruff check .`: "All checks passed!"
- `ruff format --check .`: "2 files already formatted"
- `mypy`: "Success: no issues found in 2 source files"
- `pytest`: "2 passed"
- Extra checks:
  - `pnpm -C ts audit --prod`: "No known vulnerabilities found"
  - `pnpm pack` (into the scratchpad): `mmlabs-auth-client-0.1.0.tgz` contains `dist/react/index.{js,d.ts}`, `README.md` and `package.json`. This matches the README install line.

### Deviations from the plan
- **Biome 2.6 also formats Markdown.** `ts/README.md` was run through `biome format --write`, so its tables are column-padded. I did not add a Biome exclude, because that would change `biome.json`, which is outside the plan.
- **`useAuth().error` is typed `Error | null`.** A non-`Error` rejection is wrapped as `Error("mmlabs-auth: unexpected error")`.
- **README install.** The repo is now public, so the README gives a token-free `curl` of the Release asset, vendored as `file:vendor/…tgz` and checked against the SHA-256 in the release notes. It also uses the D20 cookie wording from the spec and makes no claims about `mm_device` paths.

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- Root `README.md`: the `ts/README.md` link is live now. "Install instructions follow with the release workflow" is still a stub (E1).
- The README's CSP is a starting point. Each app must add its own backend origins to `connect-src`.
- When USR-03g adds `202 {status:"pending"}`, `JoinState.join` has to widen its return type together with the core `join()`.

### Scope self-check
- Could the diff be smaller? Not meaningfully. The src is 179 lines against the planned ~150. The tests are 436 lines against ~150, because they cover 18 cases including the extra no-token-render test. The README is 280 lines against ~200, partly from Biome's table padding.

## Stage D — Attempt 1 — 2026-11-16

Repo `/home/user/mmlabs-auth`, branch `claude/blissful-wozniak-fx4lm9`, on top of `7079675`. Working tree only: nothing committed or pushed (the orchestrator commits).

### Files touched (mmlabs-auth)
| File | Change |
|---|---|
| `python/src/mmlabs_auth/access_token.py` | new: verbatim copy of org-admin-api `app/core/access_token.py`. Only the planned changes: `keys: Mapping[str, EllipticCurvePublicKey]`, local `ALGORITHM`/`TOKEN_TYP`, `leeway > 30` → `ValueError`, `kid` > 128 chars → reason `kid` |
| `python/src/mmlabs_auth/authz.py` | new: verbatim `permits()` (with the `str` guard) |
| `python/src/mmlabs_auth/_http.py` | new: `check_url` (HTTPS, or loopback `http` with opt-in), `new_client` (verify on, no redirects, `Timeout(5, connect=3)`, `User-Agent`), `fetch_json` (`asyncio.timeout(5)` total, 3xx → error, streamed byte cap, `If-None-Match`, `max-age` parse), `FetchError(reason)` |
| `python/src/mmlabs_auth/errors.py` | new: `JwksUnavailableError`, `CatalogUnavailableError`, `CatalogConfigError` (fixed messages) |
| `python/src/mmlabs_auth/jwks.py` | new: `JwksCache` (TTL = max-age capped 3600, default 300; 304 extends; unknown-kid refetch ≤ 1/60 s, also after failure; failed refresh not retried for 60 s; `asyncio.Lock` single-flight with re-check; EC P-256 / ES256 / `use=sig` only, private `d` and off-curve points refused; stale until `max_ttl` after the last success, then fail closed; `flush()` with a generation counter so an in-flight fetch cannot repopulate after a flush; `aclose()`) |
| `python/src/mmlabs_auth/catalog.py` | new: `RoleCatalogCache` (refuses `admin` and keys not matching the server's `APP_KEY_RE`; ETag; ≥ 60 s between checks, also after failure; 15 min max staleness; 404 → `CatalogConfigError` and cache dropped; shape validation; foreign-prefix roles/permissions dropped; single-flight) |
| `python/src/mmlabs_auth/verifier.py` | new: async `AccessTokenVerifier`; reads only the header `kid`; a header that fails anyway (malformed, wrong `typ`/`alg`, bad `kid`) causes no JWKS fetch |
| `python/src/mmlabs_auth/fastapi.py` | new: `MmlabsAuth`, `.require_permission()` (definition-time check `<audience>:<res>:<action>` or `<audience>:*`), `Principal` (frozen, `email`/`claims` not in repr), `AuthHTTPError`, `register_exception_handler`, `aclose()`; 401 `unauthorized` + `WWW-Authenticate: Bearer` / `Bearer error="invalid_token"`, 403 `permission_denied` + `details.required`, 503 `auth_unavailable`; logger `mmlabs_auth` logs reason / jti / required only |
| `python/src/mmlabs_auth/__init__.py` | exports the core names; does not import `fastapi` |
| `python/pyproject.toml` | restored `readme = "README.md"` |
| `python/README.md` | new: integration guide, plan outline points 1–9 |
| `python/tests/__init__.py` | new, empty (so tests import `tests.conftest`) |
| `python/tests/conftest.py` | new: ephemeral P-256 keys per run, `mint`, `mint_raw` (hand-signed ES256 for headers PyJWT refuses to encode), JWKS builder, `FakeClock` |
| `python/tests/test_access_token.py`, `test_authz.py`, `test_http.py`, `test_jwks.py`, `test_catalog.py`, `test_verifier.py`, `test_fastapi.py`, `test_contract.py` | new: every planned Stage D test plus a few extras (below) |
| `python/tests/test_drift.py` | new: drift guard against the org-admin-api checkout |
| `python/tests/test_smoke.py` | contract parsing moved to `test_contract.py`; added "core does not import fastapi" (subprocess) |

### Tests (Python 2 → 148)
- All tests listed in the plan for `test_access_token.py` (incl. list `aud`, issuer prefix / CVE-2024-53861, alg none / HS256 (hand-signed with the PEM as HMAC secret) / ES384, wrong and missing `typ`, each missing claim, future `iat`, `email_verified` false/missing/"true"/1, leeway > 30, error repr without token), `test_authz.py`, `test_http.py`, `test_jwks.py`, `test_catalog.py`, `test_verifier.py`, `test_fastapi.py`, `test_contract.py`.
- Extras: 128-char kid accepted; total-timeout (5 s `asyncio.timeout`, patched to 50 ms); body cap without `Content-Length` (streamed); client timeout values; concurrent cold-start single fetch; failed refresh not retried before cooldown; header that fails anyway causes no fetch; catalog 256 KB cap; invalid app keys / `http://` api_base refused; `Principal` repr without email.
- **Drift test** (`test_drift.py`, 5 tests): reads `$ORG_ADMIN_API_DIR` or the sibling `../org-admin-api`; skips cleanly when missing (`ORG_ADMIN_API_DIR=/nonexistent` → "143 passed, 5 skipped"). Compares by AST: `InvalidAccessTokenError` identical; `verify_access_token` body identical after an AST transformer undoes exactly the planned adaptations (drops the leeway `if`, drops the `len(kid) > …` operand, maps `keys` → `key_set.public_keys`); reason-code sequence identical; `REQUIRED_CLAIMS`, `ALGORITHM`, `TOKEN_TYP` equal the server's; `permits` identical (incl. docstring). Mutation check (reverted): renaming reason `issuer`, flipping `strict_aud`, and weakening the `permits` guard each failed the matching drift test.

### Deviation: reason for a non-string `kid`
PyJWT 2.12 `get_unverified_header` already rejects a non-string `kid` (`InvalidTokenError`), so the verbatim code (server included) returns **`malformed`**, not `kid`. As the plan says, the test asserts what the verbatim code produces: `test_non_string_kid_reason_malformed` (int, list; hand-signed via `mint_raw`). A long (> 128) string `kid` gives `kid` even when that kid is in the key map.

### Other deviations
- `MmlabsAuth` takes `allow_insecure_loopback=False` (passed to both caches) so local development against `http://localhost:8000` is possible; not in the plan's signature.
- `JwksCache`: a failed refresh is also not retried for `cooldown` (60 s), not only unknown-kid refetches, so an outage is not hammered. Consequence (documented in README §4): a failed first fetch at startup gives 503 for up to 60 s. The same holds for the catalog (60 s between checks, also after failure).
- A JWKS 200 with no usable key counts as a failed fetch (keeps the last good set / fails closed) instead of replacing the keys with an empty set.
- Catalog: role keys without the `<app_key>:` prefix are also dropped (the plan mentions permissions only).
- `ruff format` 0.17 also formats Markdown, so `python/README.md` went through it.

### Stage gate (actual output)
- `pnpm -C ts install --frozen-lockfile`: "Done in 386ms using pnpm v10.28.0"
- `pnpm -C ts lint`: "Checked 30 files in 35ms. No fixes applied."
- `pnpm -C ts typecheck`: exit 0
- `pnpm -C ts test`: "Test Files 12 passed (12)", "Tests 146 passed (146)"
- `pnpm -C ts build`: exit 0 (`dist/` removed afterwards)
- `uv sync --locked --directory python`: "Checked 33 packages"
- `ruff check .`: "All checks passed!"
- `ruff format --check .`: "22 files already formatted"
- `mypy`: "Success: no issues found in 21 source files"
- `pytest`: "148 passed" (with the org-admin-api checkout); "143 passed, 5 skipped" without it (CI case)
- Extra: `uv build --wheel` built `mmlabs_auth-0.1.0-py3-none-any.whl` into the scratchpad (readme field OK).

### Out-of-plan files
- `python/tests/__init__.py` (empty): needed so tests can `from tests.conftest import …`.
- `python/tests/test_drift.py`: requested by the orchestrator for this attempt (it pulls plan follow-up F2's "pin the copies" part into the library repo, local-only).

### Follow-ups (noticed, not done)
- Root `README.md`: the `python/README.md` link is live now; release/install summary still a stub (E1).
- F2 remains for the org-admin-api side (its `AppError` codes vs `contract/error-codes.json`).
- `contract/SOURCE` is `74e9b62…`; org-admin-api HEAD is `e4d6890…`. `access_token.py`, `authz.py` and `jwt_keys.py` are unchanged between the two (`git diff --stat` empty), so no refresh is needed for Stage D; E1 may bump it.
- Consider exposing `require_permission` in OpenAPI (`x-permission`) like the server's admin deps.

### Scope self-check
- src ≈ 795 lines incl. docstrings (plan ~500), tests ≈ 1,380 (plan ~550), README ≈ 190. The extra is the drift test, the security-consult test cases and docstrings; no features beyond the plan's surface except `allow_insecure_loopback` on `MmlabsAuth`.

## Stage E — Attempt 1

### Touched files
**mmlabs-auth** (branch `claude/blissful-wozniak-fx4lm9`, uncommitted)
- `.github/workflows/release.yml` (new): `on.push.tags: ts-v*, py-v*`; top-level `permissions: contents: read`; jobs `ts` / `py` gated by `startsWith(github.ref_name, …)`, each with job-level `contents: write` (commented). Steps: checkout (`persist-credentials: false`), tag-vs-manifest version check (`node -p` on `ts/package.json`; `tomllib` on `python/pyproject.toml`), full suite (frozen/locked install, lint, typecheck/mypy, test, build), `pnpm -C ts pack` / `uv build` into `$RUNNER_TEMP/dist`, `gh release create "$TAG" … --notes-file notes.md --verify-tag` with `sha256sum` in the notes. Only `github.token` (`GH_TOKEN`); all `${{ }}` values pass through `env:` (no template injection). Same action SHAs as `ts.yml`/`python.yml`; no new actions. Caches disabled (`package-manager-cache: false`, `enable-cache: false`) to avoid cache poisoning in a release workflow. No `pull_request_target`.
- `docs/adr/0001-packaging-and-release.md` (new): independent 0.x semver + tag scheme; release workflow; TS Release tarball vendored as `file:vendor/…tgz`; Python git-tag subdirectory install or Release wheel/sdist; no registry (GitHub Packages rejected: forced `@mmlabs-online` scope, token for reads); `"private": true` only blocks accidental `npm publish`; repo PUBLIC since 2026-11-16 so no token; provenance deferred; LICENSE pending owner decision.
- `README.md`: new "Install" section (no token, links to `ts/README.md#1-install` / `python/README.md#1-install`); "Releases" section rewritten (bump → merge → tag; workflow behaviour; ADR link; LICENSE pending).

**org-admin-api** (branch `claude/blissful-wozniak-fx4lm9`, uncommitted)
- `docs/consumers.md`: `## Client libraries` section after the intro/prerequisites (links to `ts/README.md` and `python/README.md` on GitHub, tag-pinned install lines for `ts-v0.1.0` / `py-v0.1.0`, "this page wins" line); one pointer line above `## PyJWT sketch`.

### Deviation from plan
- Plan E2 text "Private repo: installs need GitHub read access" replaced by "the repository is public; no token needed" (orchestrator instruction: repo public since 2026-11-16). ADR records the same.

### Checks
- `uvx zizmor@1.16.3 --offline .github/workflows`: "No findings to report. Good job! (4 suppressed)" — suppressed are `anonymous-definition` (auditor persona, unnamed `run` steps), same pattern as ts.yml/python.yml.
- YAML parse (`pyyaml==6.0.2`): all three workflows load; `release.yml` jobs `['py', 'ts']`.
- Version-check dry run: `ts-v0.1.0` matches, `ts-v0.2.0` rejected; `py-v0.1.0` matches, `py-v9.9.9` rejected.
- `pnpm pack` → `mmlabs-auth-client-0.1.0.tgz`; `uv build` → `mmlabs_auth-0.1.0.tar.gz` + `mmlabs_auth-0.1.0-py3-none-any.whl` (scratchpad); names match the README download URLs.
- mmlabs-auth stage gate: install "Already up to date"; lint "Checked 30 files … No fixes applied"; typecheck exit 0; test "12 passed (12) / 146 passed (146)"; build exit 0; `uv sync --locked` "Checked 33 packages"; ruff "All checks passed!"; format "22 files already formatted"; mypy "Success: no issues found in 21 source files"; pytest "148 passed".
- org-admin-api `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` (local Postgres started with `service postgresql start`): ruff "All checks passed!"; "222 files already formatted"; mypy "Success: no issues found in 101 source files"; pytest "1716 passed, 1 warning".

### Follow-ups (noticed, not done)
- The `ts-v0.1.0` / `py-v0.1.0` tags and Releases do not exist yet; the install lines in both repos go live once the owner pushes them after the mmlabs-auth PR merges.
- `contract/SOURCE` not bumped in E1 (no contract change needed for release).
- Optional: build provenance (`actions/attest-build-provenance`) once the owner wants it (ADR "Deferred").
- LICENSE for mmlabs-auth (owner decision).
