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
