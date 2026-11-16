Status: READY

# Plan — USR-08: Client libraries @mmlabs/auth-client (TS) and mmlabs-auth (Python) — separate repo

Work happens in two repos:

- **`mmlabs-online/mmlabs-auth`** (`/home/user/mmlabs-auth`): stages A–E, on branch `claude/blissful-wozniak-fx4lm9`, with **one PR into `main`**. The owner merges it.
- **`mmlabs-online/org-admin-api`** (`/home/user/org-admin-api`): only the `docs/consumers.md` links (stage E2), plus the orchestrator's `work/` bookkeeping. This goes in a **separate PR into `main`**.

**DONE** means both PRs are open, and the mmlabs-auth PR is green on both CI workflows.

The orchestrator runs the stages as sequential implementer passes inside this one ticket, one commit per stage (`USR-08: <stage summary>`). **Every stage must leave both test suites green** (see "Stage gate").

## Spec summary

`docs/spec-v1.md` §5.8 (owner, 2026-11-11):

> - **Login and sessions:** each app has its own login page and uses `@mmlabs/auth-client`, which posts credentials straight to this API (D4). A browser holds one device session (cookie `__Host-mm_device`, rotated with a 60 s grace) with any number of signed-in accounts; each app remembers its own chosen account, so signing into another account in one app does not change another app (D5, D6). Logout per account or for the whole device.
> - **Per-app access tokens:** an app asks for its own short-lived JWT for a chosen account (and org, if any), carrying only that app's roles (D7). Not a member → `not_member` with `join_available`; joining follows the app's `join_policy` (D8–D9).
> - **Verification by apps:** public keys at `/.well-known/jwks.json`; apps verify signature, `iss`, `aud` and expiry locally. Claims: `sub`, `email`, `email_verified`, `aud`, `roles`, `org` (if scoped), `iat`, `exp`. Backends use `mmlabs-auth` (Python) (D4).
> - **Endpoints:** all under `/api/v1/auth/*` (cookie `__Host-mm_device`, `Path=/`, host-only; D13 as amended by D20); JWKS at `/.well-known/jwks.json` (D13).

§5.3 (D17):

> The role catalog `GET /api/v1/apps/{key}/roles` is public.

§3 non-goals that still apply: no social login, no OIDC provider, no hosted login UI.

Ticket scope:

- `@mmlabs/auth-client` covers the TS core plus a React wrapper. Angular comes only when an Angular app needs it.
- `mmlabs-auth` (Python) covers JWKS, ES256, the role catalog with ETag, and `require_permission()`.
- An integration guide, and publishing.

Owner notes, 2026-11-16:

- Both libraries ship in this ticket.
- forgot, reset and verify-email are "optional". The orchestrator decided to include them.

## Acceptance criteria → verification

All TS test files are under `ts/test/`, and all Python test files are under `python/tests/`.

| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | The libraries live in the separate repo `mmlabs-online/mmlabs-auth`, with `ts/` and `python/`, and CI and versioning there. | Check the tree in `/home/user/mmlabs-auth`. `.github/workflows/ts.yml` and `python.yml` are green on the PR. `release.yml` and `docs/adr/0001-packaging-and-release.md` exist. Tags follow `ts-v*` / `py-v*`. |
| 2 | TS unit tests cover account choice per origin, token refresh and error mapping. | `accounts.test.ts`, `token-cache.test.ts` and `errors.test.ts` (Tests, stage B) are green under `pnpm -C ts test`. |
| 3 | Python tests cover a valid, expired, wrong-`aud` and unknown-`kid` token, the ETag cache and the JWKS cooldown. | `test_access_token.py`, `test_jwks.py` and `test_catalog.py` (stage D) are green under `uv run --directory python pytest`. |
| 4 | org-admin-api `docs/consumers.md` links to the libraries. | org-admin-api PR diff: a new "Client libraries" section, with links to `ts/README.md` and `python/README.md` and the tag-pinned install lines (stage E2). |
| 5 | The React wrapper (provider plus hooks) is usable by `admin-pan`. | `react.test.tsx` is green, including `admin-pan smoke: appKey "admin"`. `ts/README.md` has a React section with an admin-pan example. `./react` is exported from `package.json`. |
| 6 | `getToken('admin')` (any audience) uses an in-memory cache, refreshes before expiry, makes concurrent calls single-flight, and never persists the token. | `token-cache.test.ts` covers the refresh skew, 10 concurrent calls making 1 fetch, and failures not being cached. `storage-hygiene.test.ts` shows only `mm_account_id` is ever written. "Any audience" means the client's own `appKey` (orchestrator decision; see Assumptions A1). |
| 7 | `listAccounts()` exists, and the account choice is persisted per origin as `mm_account_id`. Logout works for one account or all. | `accounts.test.ts`: persistence, UUID validation, memory fallback, `logout({accountId})` and `logout({all:true})`. |
| 8 | Typed errors exist for `not_member`, `email_unverified`, `session_invalid` and `rate_limited` (with `retryAfter` from `Retry-After`), plus a generic fallback. | `errors.test.ts` is table-driven from `contract/error-codes.json`. It covers `retryAfter` given as seconds, as an HTTP-date and from the `details` fallback, and the generic `AuthError` for unknown codes. |
| 9 | `credentials:"include"` and `X-MM-App` are sent only on `/api/v1/auth/*`. Bearer-only calls never send cookies. | `transport.test.ts` records the exact `RequestInit` for every endpoint. `bearer.test.ts` asserts `credentials:"omit"` and no `X-MM-App` header, even when the caller passes them. |
| 10 | Resend verification (`POST /api/v1/auth/verify-email/resend`) works, and a fresh token can be fetched after the email is verified. | `endpoints.test.ts › resendVerification` covers both 202 → `"sent"` and 409 → `"already_verified"`. `token-cache.test.ts › fresh token after verifyEmail` checks that `forceRefresh` hits the network. |
| G | Stage gate: both suites are green after every stage. | Run the commands in "Stage gate" after each stage. Their output goes in `impl-log.md`. |

## Files to create / modify

### Stage A: scaffold, CI, contract (~300 lines hand-written, lockfiles excluded)

| File (mmlabs-auth) | Change | Required because |
|---|---|---|
| `README.md` | Rewrite: layout, links to `ts/README.md` and `python/README.md`, the stage gate commands, and the release summary (filled in at E). | Integration guide entry point |
| `.gitignore` | Keep it. Add `python/.venv` only if the existing patterns miss it. | Already covers both stacks |
| `contract/error-codes.json` | Every error code the org-admin-api auth flows can return, as `{code: {status, class, typed}}`. The list is in the "API contract" section. | Shared source for TS error mapping and Python contract tests |
| `contract/token-response.example.json` | `{"status":200,"headers":{"Cache-Control":"no-store"},"body":{"access_token":"<dummy, non-JWT>","token_type":"Bearer","expires_in":900,"audience":"admin"}}` | TS contract test. The token is a dummy string, not a real JWT. |
| `contract/catalog.example.json` | `{"headers":{"ETag":"\"…\"","Cache-Control":"no-cache"},"body":{"app":"ricotdin","roles":[{"key":"ricotdin:member","name":"Member","is_system":true,"permissions":["ricotdin:notes:read"]}, …]}}`. The shape comes from `RoleCatalog`/`RoleCatalogEntry` in org-admin-api `app/modules/rbac/schemas.py`. | Python catalog tests |
| `contract/SOURCE` | The full org-admin-api commit SHA these files were taken from (`git -C /home/user/org-admin-api rev-parse HEAD`), and a one-line note on how to refresh it. | Drift guard |
| `ts/package.json` | `@mmlabs/auth-client` `0.1.0`, details below. | TS package |
| `ts/pnpm-lock.yaml` | Generated and committed. | S1, frozen installs |
| `ts/tsconfig.json`, `ts/tsconfig.build.json` | `strict`, `exactOptionalPropertyTypes`, `noUncheckedIndexedAccess`, `module`/`moduleResolution` `NodeNext`, `target` ES2022, `lib` ES2022 + DOM, `jsx` `react-jsx`, `declaration`. The build config emits from `src/` only into `dist/`. | TS strict ESM via plain `tsc` |
| `ts/biome.json` | Lint and format. Forbid `console` (`noConsole: error`). | Lint; T7, no console logging |
| `ts/vitest.config.ts` | Default `environment: "node"`. Tests that need the DOM opt in per file with `// @vitest-environment happy-dom`. Include `test/**/*.test.ts(x)`. | Tests |
| `ts/.npmrc` | `ignore-scripts=true`, `engine-strict=false`. | S2 |
| `ts/src/index.ts` | Placeholder `export const VERSION = "0.1.0";`. Replaced in B. | Green build at A |
| `ts/test/smoke.test.ts` | Asserts that `VERSION` matches `package.json`. | Green suite at A |
| `python/pyproject.toml` | `mmlabs-auth` `0.1.0`, details below. | Python package |
| `python/uv.lock` | Generated and committed. | S1 |
| `python/src/mmlabs_auth/__init__.py` | `__version__ = "0.1.0"`. Expanded in D. | Package |
| `python/src/mmlabs_auth/py.typed` | Empty marker file. | Typed package for consumers' `mypy --strict` |
| `python/tests/test_smoke.py` | Asserts that `__version__` matches `pyproject.toml`, and that the `contract/*.json` files parse. | Green suite at A |
| `.github/workflows/ts.yml` | TS CI, details below. | AC 1 |
| `.github/workflows/python.yml` | Python CI, details below. | AC 1 |
| `.github/dependabot.yml` | Ecosystems `npm` (`/ts`), `uv` (`/python`) and `github-actions` (`/`), weekly. | S4 |

**`ts/package.json`:**

- `"type":"module"`, `"private": true`. This prevents an accidental public `npm publish` under a scope mmlabs does not own. `pnpm pack` still works.
- `"packageManager":"pnpm@10.x.y"`: the exact version the implementer installs.
- `"sideEffects": false`, `"files": ["dist"]`.
- `exports`:
  - `"."` → `{types: "./dist/index.d.ts", import: "./dist/index.js"}`
  - `"./react"` → `{types: "./dist/react/index.d.ts", import: "./dist/react/index.js"}`
- `peerDependencies: {react: ">=18"}` with `peerDependenciesMeta.react.optional = true`.
- No `dependencies` at all.
- devDependencies: `typescript`, `vitest`, `happy-dom`, `@testing-library/react`, `react`, `react-dom`, `@types/react`, `@types/react-dom`, `@biomejs/biome`, all exact versions.
- `"pnpm": {"onlyBuiltDependencies": []}`.
- Scripts:
  - `lint`: `biome check .`
  - `typecheck`: `tsc -p tsconfig.json --noEmit`
  - `test`: `vitest run`
  - `build`: `tsc -p tsconfig.build.json`
- No `prepare`, `postinstall` or `prepack` scripts.

**`python/pyproject.toml`:**

- `requires-python = ">=3.12"`.
- `dependencies = ["PyJWT[crypto]>=2.10.1,<3", "httpx>=0.27,<1"]`.
- `[project.optional-dependencies] fastapi = ["fastapi>=0.110"]`.
- `[dependency-groups] dev`: ruff, mypy, pytest, pytest-asyncio, respx, fastapi.
- Build backend hatchling, with `[tool.hatch.build.targets.wheel] packages = ["src/mmlabs_auth"]`.
- `[tool.ruff]`: line length 100, rules E, F, I, B, UP, S, and S101 allowed in tests.
- `[tool.mypy]`: `strict = true`, `files = ["src", "tests"]`.
- `[tool.pytest.ini_options]`: `asyncio_mode = "auto"`, `testpaths = ["tests"]`.

**`.github/workflows/ts.yml`:**

- Triggers:
  - `on.push.branches: [main]`
  - `on.pull_request`
  - both with `paths: [ts/**, contract/**, .github/workflows/ts.yml]`
- `permissions: contents: read`. No `pull_request_target`.
- Steps:
  - `actions/checkout` (with `persist-credentials: false`), `pnpm/action-setup`, `actions/setup-node` (Node 22, pnpm cache), all pinned by full commit SHA with a `# vX.Y.Z` comment.
  - `pnpm -C ts install --frozen-lockfile`
  - lint, typecheck, test, build
  - `pnpm -C ts audit --prod`

**`.github/workflows/python.yml`:**

- Same trigger shape, with `python/**`, `contract/**` and the workflow file.
- `permissions: contents: read`.
- Steps:
  - `actions/checkout` (`persist-credentials: false`) and `astral-sh/setup-uv`, both SHA-pinned.
  - Python 3.12.
  - `uv sync --locked --directory python`
  - `ruff check`, `ruff format --check`, `mypy`, `pytest`
  - `uv export --directory python --no-dev --no-hashes --format requirements-txt > /tmp/req.txt && uvx pip-audit -r /tmp/req.txt`

Resolve each action's SHA from its release tag with `gh api repos/<owner>/<action>/commits/<tag> --jq .sha` and record it in `impl-log.md`.

### Stage B: TS core (~1,000 lines: ~450 src, ~550 tests)

| File (mmlabs-auth) | Change | Required because |
|---|---|---|
| `ts/src/index.ts` | Public exports: `createAuthClient`, the `AuthClient` type, all error classes, and the types. | API surface |
| `ts/src/types.ts` | `AuthClientOptions`, `Account`, `SignupResult`, `LoginResult`, `LogoutTarget = {accountId: string} \| {all: true}`, `AuthEvent`. | Types |
| `ts/src/client.ts` | `createAuthClient(opts)`: class `AuthClient` with `#private` state, the methods listed in "API contract", `onChange`, `dispose`. | AC 6–10 |
| `ts/src/transport.ts` | `authFetch(path, init)`: asserts `path.startsWith("/api/v1/auth/")`, sends `credentials:"include"`, `X-MM-App`, `cache:"no-store"`, and `Content-Type` only with a body. `publicFetch(path, body)` is for verify-email and reset: `credentials:"omit"`, no `X-MM-App`. Both check the `baseUrl` scheme, parse the envelope and throw mapped errors. | AC 9, T3 |
| `ts/src/bearer.ts` | `fetchWithToken(input, init)`: forces `credentials:"omit"`, strips any `X-MM-App` header, sets `Authorization: Bearer`. On a 401 it refreshes once and retries once, gated to once per 30 s per audience. | AC 9, T4 |
| `ts/src/errors.ts` | `AuthError` base and its subclasses. `mapError(status, body, headers, now)` maps a response to the right class. Allow-listed `details`, safe `toJSON`, fixed messages. | AC 8, T7 |
| `ts/src/retry-after.ts` | Parses `Retry-After` given as delta-seconds or an HTTP-date, with fallback to `details.retry_after`, then 5 s. | AC 8 |
| `ts/src/accounts.ts` | `AccountStore`: reads and writes `mm_account_id` through the injected `Storage` with try/catch, validates the UUID on read (an invalid value is removed), and falls back to memory. | AC 7, T2 |
| `ts/src/token-cache.ts` | `Map` keyed by `${accountId}\|${audience}` to `{token, freshUntilMs}`. Single-flight `Map<key, Promise>`. Per-account generation counters, so an in-flight result that lands after logout or a switch is dropped. `blockedUntil` per audience. | AC 6, T1, T4, T5 |
| `ts/src/broadcast.ts` | Wraps `BroadcastChannel("mmlabs-auth:<appKey>")` with strict message validation, plus the `storage` event fallback. A no-op when neither exists. | T6 |
| `ts/test/helpers.ts` | `fakeFetch` (records requests, scripted responses), `fakeClock`, `memoryStorage`, `throwingStorage`, `FakeBroadcastHub`, `SENTINEL` password, email and token. | Deterministic tests |
| `ts/test/transport.test.ts`, `errors.test.ts`, `accounts.test.ts`, `token-cache.test.ts`, `rate-limit.test.ts`, `bearer.test.ts`, `broadcast.test.ts`, `storage-hygiene.test.ts`, `endpoints.test.ts`, `contract.test.ts` | Tests (see Tests to write). | AC 2, 6–10 |
| `ts/test/smoke.test.ts` | Delete. Replaced by the tests above. | Superseded |

### Stage C: React wrapper and TS guide (~450 lines: ~150 src, ~150 tests, ~200 README)

| File (mmlabs-auth) | Change | Required because |
|---|---|---|
| `ts/src/react/index.tsx` | `AuthProvider({client, children})`, `useAuth()`, `useAccessToken()`, `useJoin()`. The wrapper holds no logic of its own: it subscribes to `client.onChange`. Context values never contain a token. | AC 5, T1 |
| `ts/test/react.test.tsx` | happy-dom plus @testing-library/react. | AC 5 |
| `ts/test/package-exports.test.ts` | `package.json` exports `.` and `./react`. No file reachable from `src/index.ts` imports `react`. `dependencies` is absent or empty. There are no lifecycle scripts. | S2, optional peer |
| `ts/README.md` | Integration guide, outline below. | Ticket "Integration guide" |

**`ts/README.md` outline:**

1. Install from the Release tarball (see E).
2. `createAuthClient` options.
3. A table of every method and the endpoint it calls.
4. The sign-in / account-choice / "Continue as" flow, with the D8 rule: join only on the user's click.
5. The email-unverified flow (resend, then `getToken({forceRefresh:true})`), and the `/verify-email` and `/reset-password` pages (strip the fragment, `Referrer-Policy: no-referrer`).
6. Errors table.
7. Bearer calls with `fetchWithToken`.
8. React section with an admin-pan example (`appKey:"admin"`). Note that `admin` cannot sign up, forgot or resend (403 per ADM-002d).
9. Security notes T9: CSP, Trusted Types, and the fact that the library cannot defeat XSS.
10. Rule: tokens stay in memory only.

### Stage D: Python (~1,050 lines: ~500 src, ~550 tests)

| File (mmlabs-auth) | Change | Required because |
|---|---|---|
| `python/src/mmlabs_auth/access_token.py` | **Verbatim copy** of org-admin-api `app/core/access_token.py`, with only three changes. (1) `key_set: KeySet` becomes `keys: Mapping[str, EllipticCurvePublicKey]`, and `ALGORITHM`/`TOKEN_TYP` become local constants. (2) Before decoding, `leeway > 30` raises `ValueError`. (3) A `kid` that is not a string or is longer than 128 characters fails with reason `kid`. Same `InvalidAccessTokenError` class, `reason` values and `__repr__`. The docstring cites the source path and the `contract/SOURCE` commit. | P1; parity with the server |
| `python/src/mmlabs_auth/authz.py` | **Verbatim copy** of `permits()` from `app/modules/rbac/authz.py`, including the `str` guard. | P1 |
| `python/src/mmlabs_auth/_http.py` | `fetch_json(client, url, *, etag, max_bytes)`. Enforces `https://`, or loopback `http` only with `allow_insecure_loopback=True`. Never follows redirects; a 3xx is an error. Uses `httpx.Timeout(5.0, connect=3.0)`. Streams the body with a byte cap and sends `User-Agent` and `If-None-Match`. Returns `(status, body\|None, etag, max_age)`. | P2, P3 |
| `python/src/mmlabs_auth/errors.py` | `JwksUnavailableError`, `CatalogUnavailableError`, `CatalogConfigError` (404). The messages are fixed strings. | Fail closed |
| `python/src/mmlabs_auth/jwks.py` | `JwksCache(url, *, user_agent, default_ttl=300, max_ttl=3600, cooldown=60, allow_insecure_loopback=False, clock=time.monotonic)`, with `async keys_for(kid)`, `async keys()`, `flush()` and `async aclose()`. It owns its own `httpx.AsyncClient` with TLS verify on. No client, `verify` or redirect parameter is exposed. | P2; USR-03c S1/S2 |
| `python/src/mmlabs_auth/catalog.py` | `RoleCatalogCache(api_base, app_key, *, user_agent, min_revalidate=60, max_stale=900, max_bytes=262_144, allow_insecure_loopback=False, clock)`, with `async permissions_for(roles) -> frozenset[str]` and `async aclose()`. An `app_key` of `"admin"` raises `ValueError`. | P3 |
| `python/src/mmlabs_auth/verifier.py` | `AccessTokenVerifier(jwks, *, audience, issuer, leeway=30)`, with `async verify(token) -> dict`. It reads the unverified header `kid`, awaits `jwks.keys_for(kid)`, then calls the sync `verify_access_token`. It never reads `jku`, `x5u` or `jwk`. | Sync pure function plus async caches |
| `python/src/mmlabs_auth/fastapi.py` | `MmlabsAuth(*, audience, issuer="https://api.mmlabs.online", api_base=None, jwks_url=None, user_agent, leeway=30)`. Also: `.require_permission(perm)` returns a dependency that yields a `Principal`; `AuthHTTPError(HTTPException)`; `register_exception_handler(app)`; `Principal`; and `aclose()`. Only the extra (FastAPI) imports `fastapi`. | P4, ticket scope |
| `python/src/mmlabs_auth/__init__.py` | Exports the core names. It does **not** import `mmlabs_auth.fastapi`. | Optional extra |
| `python/tests/conftest.py` | Ephemeral P-256 key pairs, `mint(**overrides)` (ES256, `typ=at+jwt`, `kid`), JWKS body builders and a fake monotonic clock. No keys are committed. | Tests |
| `python/tests/test_access_token.py`, `test_authz.py`, `test_http.py`, `test_jwks.py`, `test_catalog.py`, `test_verifier.py`, `test_fastapi.py`, `test_contract.py` | Tests (see Tests to write). | AC 3 |
| `python/tests/test_smoke.py` | Keep the version check. Move the contract parsing to `test_contract.py`. | — |
| `python/README.md` | Integration guide, outline below. | Ticket "Integration guide" |

**`python/README.md` outline:**

1. Install with git and a tag, or the wheel from the Release.
2. Quick start for FastAPI.
3. The pure `verify_access_token` for other frameworks.
4. JWKS and catalog cache behaviour, including the numbers.
5. Emergency key rotation: call `flush()`.
6. App backends only. `admin` is refused, because admin permissions come from the database (ADR-0008).
7. Map roles only through the catalog.
8. Logging rules.
9. Revocation lag of 15 min.

### Stage E1: release (~150 lines in mmlabs-auth)

| File (mmlabs-auth) | Change | Required because |
|---|---|---|
| `.github/workflows/release.yml` | Release workflow, details below. | AC 1 "versioning" |
| `docs/adr/0001-packaging-and-release.md` | ADR, details below. | Planner rule 7; the decision outlives the ticket |
| `README.md` | Release and install section: links to the ADR and the two READMEs. | Guide |

**`.github/workflows/release.yml`:**

- `on.push.tags: ["ts-v*", "py-v*"]`.
- Top-level `permissions: contents: read`. Only the release job gets `contents: write`.
- Job `ts`, when the tag starts with `ts-v`:
  - Check that the tag version equals `ts/package.json` `version`, otherwise fail.
  - Run install (frozen), lint, typecheck, test and build.
  - `pnpm -C ts pack`, then `sha256sum`.
  - `gh release create "$TAG" ts/mmlabs-auth-client-<v>.tgz --notes "<sha256>"`, using `GH_TOKEN: ${{ github.token }}`.
- Job `py`, when the tag starts with `py-v`:
  - Check that the tag version equals `pyproject` `version`.
  - Run `uv sync --locked`, ruff, mypy and pytest.
  - `uv build --directory python`, then `sha256sum`.
  - `gh release create` with the sdist and wheel.
- Actions are SHA-pinned. No long-lived secrets.

**`docs/adr/0001-packaging-and-release.md`** records:

- Independent semver at 0.x, with tags `ts-v*` and `py-v*`.
- TS ships as a GitHub Release tarball. Consumers vendor it into their own repo as `file:vendor/…tgz`, so their Docker build needs no token.
- Python installs with `mmlabs-auth @ git+https://github.com/mmlabs-online/mmlabs-auth@py-vX.Y.Z#subdirectory=python`, or from the Release wheel.
- No registry yet. GitHub Packages was rejected: it forces the `@mmlabs-online` scope.
- `"private": true` in `package.json`.
- Provenance attestation is deferred.

### Stage E2: org-admin-api (separate PR, ~40 lines)

| File (org-admin-api) | Change | Required because |
|---|---|---|
| `docs/consumers.md` | Add a `## Client libraries` section right after the intro paragraph (see details below). Add one line above "## PyJWT sketch": "Python backends: prefer `mmlabs-auth`, which implements this checklist; the sketch stays for other stacks." | AC 4 |

The new `## Client libraries` section contains:

- Links to `https://github.com/mmlabs-online/mmlabs-auth/blob/main/ts/README.md` (`@mmlabs/auth-client`, browser plus React) and `…/python/README.md` (`mmlabs-auth`, backend verification).
- The two tag-pinned install lines.
- "Private repo: installs need GitHub read access (see the mmlabs-auth ADR-0001)."
- "The library follows the contract on this page; if they disagree, this page wins and the library gets a fix."

Only `docs/consumers.md` changes in org-admin-api source. The orchestrator also updates `work/tickets/USR-08/*` and `work/backlog.md` (state, PR links). No code, migration or config changes.

## Tests to write

### Stage A

| Test | Covers |
|---|---|
| `ts/test/smoke.test.ts › VERSION matches package.json` | Green scaffold |
| `python/tests/test_smoke.py › test_version_matches_pyproject` | Green scaffold |
| `python/tests/test_smoke.py › test_contract_files_parse` | `contract/*.json` are valid JSON, and `SOURCE` holds a 40-hex SHA |

### Stage B (TS core)

Tests use the sentinels `SENTINEL_PASSWORD`, `SENTINEL_EMAIL` and `SENTINEL_TOKEN`.

**`transport.test.ts`**

| Test | Covers |
|---|---|
| `auth endpoints send credentials include, X-MM-App and no-store` (table: signup, login, logout, token, accounts, join, resend, forgot) | AC 9, T3 |
| `verifyEmail and resetPassword send credentials omit and no X-MM-App` | T3, public endpoints |
| `authFetch refuses a path outside /api/v1/auth/` | T3 |
| `Content-Type is set only when there is a body` (GET accounts has none) | Correct headers |
| `baseUrl must be https` | T3 |
| `http://localhost is allowed only with allowInsecureLocalhost` | T3 |
| `baseUrl with path/trailing slash is normalised` | Correct URLs |

**`bearer.test.ts`**

| Test | Covers |
|---|---|
| `fetchWithToken sends Bearer, credentials omit, no X-MM-App even when the caller passes include/X-MM-App` | AC 9 |
| `401 triggers exactly one forced refresh and one retry` | T4 |
| `a second 401 is returned to the caller without another retry` | T4, bounded |
| `forced refresh at most once per 30 s per audience` | T4 |
| `403/500 are not retried` | T4 |
| `no retry when the body is a ReadableStream` | Correctness |

**`errors.test.ts`**

| Test | Covers |
|---|---|
| `every typed code in contract/error-codes.json maps to its class and status` | AC 8, contract |
| `unknown code maps to generic AuthError with the raw code` | AC 8, fallback |
| `RateLimitedError.retryAfter from Retry-After seconds` | AC 8 |
| `RateLimitedError.retryAfter from Retry-After HTTP-date` | AC 8 |
| `RateLimitedError.retryAfter falls back to details.retry_after, then 5` | AC 8 |
| `NotMemberError.joinAvailable` | Identity mapping |
| `WeakPasswordError.reasons` | Identity mapping |
| `ValidationError keeps only type/loc` | T7, allow-list |
| `requestId from X-Request-ID` | T7 |
| `fetch TypeError → NetworkError` | CORS failure |
| `non-JSON or non-envelope body → AuthError unexpected_response` | Robustness |
| `JSON.stringify/String/message of every error contain no sentinel token, email or password` (the server message and the request body both carry sentinels) | T7, R11 |

**`accounts.test.ts`**

| Test | Covers |
|---|---|
| `login stores account_id as mm_account_id and returns LoginResult` | AC 7 |
| `listAccounts maps items` | AC 7 |
| `selectAccount rejects a non-UUID` | T2 |
| `invalid stored mm_account_id is dropped and removed on read` | T2 |
| `throwing storage falls back to memory without throwing` | Robustness |
| `storage: null uses memory` | Robustness |
| `logout({accountId: current}) clears mm_account_id; another account leaves it` | AC 7 |
| `logout({all:true}) clears mm_account_id and all tokens` | AC 7 |
| `logout body is {account_id} XOR {all:true}` | Server contract |
| `logout clears cached tokens synchronously before the request resolves` | Identity risk "logout leaks" |
| `session_invalid from getToken clears that account's tokens but keeps mm_account_id` | Assumption A3 |

**`token-cache.test.ts`**

| Test | Covers |
|---|---|
| `getToken() defaults audience to appKey and sends {audience, account_id}` | AC 6 |
| `getToken with another audience rejects ConfigurationError audience_mismatch without fetch` | A1 |
| `cached token reused until expires_in - skew` | AC 6 |
| `refetch after skew` (900 s → at 720 s; 100 s → at 80 s, skew = min(60, 20 %)) | AC 6 |
| `10 concurrent calls → 1 fetch, same token` | AC 6 |
| `concurrent failure rejects all callers and is not cached` | T4 |
| `forceRefresh bypasses cache` | AC 10 |
| `per-account cache; selectAccount clears` | AC 6 |
| `in-flight result discarded after logout or account switch` | Generation guard |
| `no account selected → AuthError no_account_selected, no fetch` | Correctness |
| `fresh token after verifyEmail` (`verifyEmail` 204, then `getToken({forceRefresh:true})` fetches) | AC 10 |
| `no timers are scheduled` (`vi.getTimerCount() === 0`) | T5, lazy refresh only |

**`rate-limit.test.ts`**

| Test | Covers |
|---|---|
| `429 → RateLimitedError; calls before Retry-After reject locally with no fetch` | T5 |
| `after Retry-After the next call fetches` | T5 |
| `HTTP-date Retry-After blocks until that date` | T5 |
| `no automatic retry after 429` | T5 |

**`broadcast.test.ts`**

| Test | Covers |
|---|---|
| `logout posts exactly {type:"logout", accountId} — no token, no email` | T6 |
| `logout_all message has only {type}` | T6 |
| `receiving logout clears tokens (next getToken fetches)` | T6 |
| `receiving account_changed re-reads mm_account_id and emits account-changed` | T6 |
| `malformed or unknown messages are ignored` | T6 |
| `no BroadcastChannel → no throw` | Robustness |
| `storage event on mm_account_id triggers account-changed` (happy-dom) | T6 fallback |
| `dispose closes the channel and removes listeners` | Hygiene |

**`storage-hygiene.test.ts`** (happy-dom)

| Test | Covers |
|---|---|
| `full flow login → getToken → fetchWithToken → logout writes only mm_account_id (a UUID) to localStorage; nothing to sessionStorage, indexedDB.open, document.cookie` | AC 6, T1, T2 |
| `token not found in JSON.stringify(client), own keys of client, globalThis values, location` | T1 |
| `no console.* calls during any flow` | T7 |
| `password not retained after login` | T9 |

**`endpoints.test.ts`**

| Test | Covers |
|---|---|
| `signup sends {email,password,turnstile_token} and returns SignupResult` | Endpoints |
| `login with and without turnstileToken` | Endpoints |
| `join(accountId) POSTs /api/v1/auth/apps/<appKey>/join {account_id}` | Endpoints |
| `join without accountId throws` (runtime and type) | T8 |
| `not_member from getToken never triggers a join request` | D8, T8 |
| `resendVerification 202 → "sent"; 409 email_already_verified → "already_verified"` | AC 10 |
| `forgotPassword 202 sends X-MM-App + include` | Owner optional ask |
| `resetPassword 204 sends {token,new_password}, omit` | Owner optional ask |
| `verifyEmail 204` | Owner optional ask |

**`contract.test.ts`**

| Test | Covers |
|---|---|
| `contract token-response example is accepted by getToken` | Contract |

### Stage C (React)

**`react.test.tsx`**

| Test | Covers |
|---|---|
| `status loading → ready when the stored account is listed` | AC 5 |
| `signed-out on empty list or session_invalid` | AC 5 |
| `needs-account when accounts exist but none chosen` | AC 5 |
| `error status on NetworkError` | AC 5 |
| `selectAccount updates accountId and status` | AC 5 |
| `login and logout update state` | AC 5 |
| `another tab's broadcast re-renders` | T6 |
| `useAccessToken exposes getToken/fetchWithToken and no token value` (JSON.stringify of the hook result and the context value) | T1 |
| `useJoin calls join only when invoked; NotMemberError surfaces via instanceof` | D8 |
| `hooks outside provider throw` | Usability |
| `unmount unsubscribes` | Hygiene |
| `admin-pan smoke: appKey "admin", getToken() requests audience admin` | AC 5 |

**`package-exports.test.ts`**

| Test | Covers |
|---|---|
| `exports . and ./react` | Packaging |
| `core does not import react` | Packaging |
| `no runtime dependencies` | S2 |
| `no lifecycle scripts` | S2 |

### Stage D (Python)

**`test_access_token.py`** (sync, pure)

| Test | Covers |
|---|---|
| `test_valid_token_returns_claims` | AC 3 |
| `test_expired_reason_expired` | AC 3 |
| `test_within_leeway_accepted` | Parity |
| `test_wrong_audience_reason_audience` | AC 3 |
| `test_list_audience_reason_audience` | strict_aud |
| `test_wrong_issuer_reason_issuer` | P1 |
| `test_issuer_prefix_reason_issuer` | P1, CVE-2024-53861 |
| `test_alg_none_rejected` | P1 |
| `test_alg_hs256_rejected` | P1 |
| `test_alg_es384_rejected` | P1 |
| `test_wrong_or_missing_typ_reason_typ` | P1 |
| `test_unknown_kid_reason_kid` | AC 3 |
| `test_non_string_or_long_kid_reason_kid` | P1 |
| `test_bad_signature_reason_signature` | Parity |
| `test_missing_claim_reason_claims[exp,iat,sub,jti,iss,aud]` | P1 |
| `test_future_iat_reason_claims` | P1 |
| `test_email_verified_false_or_missing` | P1 |
| `test_malformed_reason_malformed` | Parity |
| `test_leeway_over_30_raises_value_error` | P1 |
| `test_error_repr_contains_no_token` | R11 |

Expected reasons equal the server's (`tests/` in org-admin-api). If PyJWT puts a case under a different exception, the implementer asserts the reason the verbatim code produces and records it.

**`test_authz.py`**

| Test | Covers |
|---|---|
| `test_exact_match` | permits() |
| `test_app_wildcard` | permits() |
| `test_no_global_star` | permits() |
| `test_no_resource_wildcard` | permits() |
| `test_other_app_wildcard_denied` | permits() |
| `test_malformed_required_false` | permits() |
| `test_str_granted_type_error` | permits() |

**`test_http.py`**

| Test | Covers |
|---|---|
| `test_http_url_refused` | P2 |
| `test_loopback_http_only_with_flag` | P2 |
| `test_redirect_not_followed` | P2 |
| `test_timeout_raises` | P2 |
| `test_body_over_cap_rejected` | P2 |
| `test_sends_user_agent_and_if_none_match` | P2 |

**`test_jwks.py`**

| Test | Covers |
|---|---|
| `test_fetch_and_select_by_kid` | AC 3 |
| `test_ttl_from_max_age_capped_3600_default_300` | P2 |
| `test_304_keeps_keys_and_extends_ttl` | ETag |
| `test_unknown_kid_refetches_once` | AC 3, cooldown |
| `test_second_unknown_kid_within_60s_no_fetch` | AC 3, S1/S2 |
| `test_unknown_kid_after_cooldown_refetches` | Cooldown |
| `test_cooldown_applies_after_failed_fetch` | P2 |
| `test_concurrent_unknown_kid_single_fetch` (`gather` × 10) | Single-flight |
| `test_flush_drops_keys_and_resets_cooldown` | S1/S2 |
| `test_non_es256_keys_filtered` (RSA, P-384, `alg` RS256, `use` enc) | P2 |
| `test_fetch_error_serves_stale_until_max_ttl_then_fails_closed` | P2 |
| `test_timeout_without_cache_fails_closed` | P2 |
| `test_invalid_json_fails_closed` | P2 |

**`test_catalog.py`**

| Test | Covers |
|---|---|
| `test_permissions_union_of_roles` | P3 |
| `test_unknown_role_grants_nothing` | Fail closed |
| `test_etag_304_keeps_body` | AC 3 |
| `test_no_revalidation_within_60s` | AC 3 |
| `test_revalidates_after_60s` | AC 3 |
| `test_404_raises_config_error` | Config error |
| `test_outage_serves_stale_up_to_15_min` | Bound |
| `test_outage_beyond_15_min_fails_closed` | Bound |
| `test_invalid_shape_keeps_last_good_or_fails_closed` | P3 |
| `test_foreign_prefix_permissions_dropped` | Defence in depth |
| `test_admin_app_key_refused` | ADR-0008 |
| `test_concurrent_single_flight` | Load |
| `test_contract_example_parses` | Contract |

**`test_verifier.py`**

| Test | Covers |
|---|---|
| `test_async_verify_valid` | Integration |
| `test_unknown_kid_triggers_one_jwks_refetch` | AC 3 |
| `test_jku_header_ignored_no_extra_request` | P1 |
| `test_rotation_new_kid_after_flush` | S1/S2 |

**`test_fastapi.py`**

| Test | Covers |
|---|---|
| `test_valid_token_with_permission_returns_principal` | P4 |
| `test_missing_header_401_bearer_challenge_envelope` | P4 |
| `test_invalid_token_401_invalid_token_no_reason_in_body` | P4 |
| `test_expired_and_wrong_aud_401` | P4 |
| `test_missing_permission_403_permission_denied_details_required` | P4 |
| `test_wildcard_role_permits` | P4 |
| `test_permission_prefix_mismatch_raises_at_definition` | P4 |
| `test_admin_audience_refused_at_construction` | ADR-0008 |
| `test_jwks_or_catalog_unavailable_503_auth_unavailable` | Fail closed |
| `test_logs_reason_without_token_or_email` (caplog) | R11 |
| `test_status_correct_without_exception_handler` | Robustness |

**`test_contract.py`**

| Test | Covers |
|---|---|
| `test_permission_denied_in_contract` | Contract |
| `test_source_is_sha` | Contract |

### Stage E

| Test | Covers |
|---|---|
| `actionlint` (or `uvx zizmor .github/workflows` if available) on all workflows. Otherwise the reviewer reads `release.yml`. | E1 |
| Dry run of the version-check script against the current versions. | E1 |
| Both stage-gate suites still green. | G |

## Approach

### Stage gate (run after every stage; paste output into `impl-log.md`)

```bash
pnpm -C /home/user/mmlabs-auth/ts install --frozen-lockfile
pnpm -C /home/user/mmlabs-auth/ts lint
pnpm -C /home/user/mmlabs-auth/ts typecheck
pnpm -C /home/user/mmlabs-auth/ts test
pnpm -C /home/user/mmlabs-auth/ts build
uv sync --locked --directory /home/user/mmlabs-auth/python
uv run --directory /home/user/mmlabs-auth/python ruff check .
uv run --directory /home/user/mmlabs-auth/python ruff format --check .
uv run --directory /home/user/mmlabs-auth/python mypy
uv run --directory /home/user/mmlabs-auth/python pytest
```

`pnpm -C ts lint typecheck test build` is not one pnpm command, so run the four scripts separately. In stage A the first install creates the lockfiles (`pnpm install` and `uv lock`). From then on, use frozen/locked installs.

For E2 in org-admin-api, run `make check`. It is a docs-only change, but R8 applies. If Postgres is not available in the session, record that.

### Stage order and why

1. **A** scaffold, CI and contract.
2. **B** TS core. It unblocks admin-pan PAN-010.
3. **C** React and the TS guide.
4. **D** Python.
5. **E1** release and ADR.
6. **E2** the org-admin-api docs PR.

Each stage is one commit on `claude/blissful-wozniak-fx4lm9`. The orchestrator runs REVIEW per stage, or once at the end, as it chooses. The mmlabs-auth PR opens after E1. The org-admin-api PR opens after E2.

### TS client behaviour (B)

- **Options:** `{ baseUrl, appKey, fetch?, storage?: Storage | null, now?: () => number, broadcast?: boolean | BroadcastFactory, allowInsecureLocalhost?: boolean }`.
  - `appKey` must match `^[a-z][a-z0-9-]{1,31}$`, or the server's `APP_KEY_RE`; the implementer copies the server regex.
  - `fetch`, `storage`, `now` and the broadcast factory are injected so tests are deterministic.
  - `fetch` defaults to `globalThis.fetch.bind(globalThis)`.
  - `storage` defaults to `globalThis.localStorage` when it is reachable.
- **Tokens:**
  - Tokens live only in `#private` fields: no globals, storage, context or URLs (T1).
  - A token counts as fresh while `now < receivedAt + expires_in*1000 - skew`, where `skew = min(60 s, 0.2 × expires_in)`. This reconciles the architect's 60 s with the security consult's 20 %.
  - Refresh is lazy, on the next `getToken()`. There are no timers and no background refresh (identity risk; satisfies T5 trivially).
  - The cache uses `expires_in`, never a decoded `exp`.
- **Single-flight:**
  - One promise per cache key. Every caller shares the result or the error, and the entry is deleted in `finally`.
  - `forceRefresh` drops the cached token. If a request is already in flight, it joins that one.
  - A generation counter per account is bumped on logout, account switch and `session_invalid`. A result whose generation is stale is returned to its awaiting callers but not cached.
- **429:**
  - `RateLimitedError{retryAfter}` sets `blockedUntil[audience]`.
  - `getToken` before that time rejects with a new `RateLimitedError` (remaining seconds) and makes no fetch.
  - There is no automatic retry.
  - Other endpoints throw `RateLimitedError` without a local block. They are user-initiated and not looped.
- **`fetchWithToken`:**
  - On a 401: if the last forced refresh for this audience was ≥ 30 s ago, call `getToken({forceRefresh:true})` and retry once, then return the second `Response` whatever its status.
  - Otherwise return the 401 as is.
  - This does not throw `session_invalid`. Only `/token` itself can say `session_invalid`, and `getToken` already throws it.
- **Accounts:**
  - `login()` and `selectAccount()` write `mm_account_id`, clear that app's token cache, emit `account-changed` and broadcast `account_changed`.
  - `logout(target)` first clears the tokens synchronously, plus `mm_account_id` when it matches or for `all`. It then broadcasts, then POSTs.
  - If the POST fails, the local state stays cleared: fail safe.
- **Join:** `join(accountId: string)` takes a required argument and is never called internally (D8).
- **Verification:** `resendVerification(accountId?)` defaults to the current account and returns `"sent" | "already_verified"`.
- **Errors:** messages are fixed (`mmlabs-auth: <code>`). Only `code`, `status`, `requestId` and the allow-listed `details` are kept:

| Code | `details` kept |
|---|---|
| `not_member` | `join_available` → `joinAvailable` |
| `rate_limited` | `retry_after` |
| `weak_password` | `reasons` |
| `validation_error` | `[{type, loc}]` |
| any other code | none |

- **Logging:** the library makes no `console.*` calls (Biome `noConsole`).

### React (C)

- `useAuth()` returns `{status: "loading"|"signed-out"|"needs-account"|"ready"|"error", accounts, accountId, error, selectAccount, login, logout, reload}`.
- `useAccessToken()` returns `{getToken, fetchWithToken}`, as stable callbacks with no token state. This follows security T1, rejecting identity's `{token}`.
- `useJoin()` returns `{join(accountId), pending, error}`.
- The provider calls `listAccounts()` on mount and on every `onChange`. Status comes from the result:
  - an empty list or `SessionInvalidError` → `signed-out`;
  - the stored id is in the list → `ready`;
  - other accounts are listed → `needs-account`;
  - any other error → `error`.

### Python (D)

- **`verify_access_token`** is pure and sync.
- **`JwksCache`:**
  - TTL = `clamp(max-age, 0, 3600)`, default 300. Revalidation uses `If-None-Match`, and a 304 extends the TTL.
  - On an unknown `kid`: if `now - last_attempt ≥ cooldown`, refetch under an `asyncio.Lock`. It re-checks after acquiring the lock (single-flight) and records `last_attempt` even on failure. Otherwise the result is reason `kid`.
  - On a fetch failure, keep the last good set until `fetched_at + max_ttl`. After that, raise `JwksUnavailableError`.
  - Keep only JWKs with `kty=EC`, `crv=P-256`, `alg ∈ {ES256, absent}`, `use ∈ {sig, absent}`, and a string `kid`.
  - `flush()` clears the keys, the ETag and the cooldown.
- **`RoleCatalogCache`:**
  - Revalidate with `If-None-Match` only when `now - last_checked ≥ 60 s`. A 304 refreshes `last_checked`.
  - On 5xx, 429, timeout or a bad shape: serve the last good catalog while `now - last_ok ≤ 900 s`. After that, raise `CatalogUnavailableError`.
  - A 404 raises `CatalogConfigError`, which fails closed.
  - Validate the shape: `app == app_key`, role keys and permissions must be strings, and permissions without the `<app_key>:` prefix are dropped.
  - Unknown roles add nothing.
- **FastAPI:**
  - The `Authorization` header must be `Bearer <token>`, with a case-insensitive scheme.
  - Missing header → 401 `unauthorized`, `WWW-Authenticate: Bearer`.
  - Invalid token → 401 `unauthorized`, `WWW-Authenticate: Bearer error="invalid_token"`. The body never carries the reason.
  - Permissions = `catalog.permissions_for(claims["roles"])`. A `roles` value that is not a list counts as `[]`.
  - `permits(perms, perm)` fails → 403 `permission_denied`, `details: {"required": perm}`.
  - JWKS or catalog unavailable, or a catalog config error → 503 `auth_unavailable` (A6).
  - The envelope is `{"error": {code, message, details}}`, rendered by `register_exception_handler(app)`. Without the handler, `AuthHTTPError` still has the right status and headers.
  - Logger `mmlabs_auth` logs the reason code and, after verification, `jti`. It never logs the token or email.
  - `Principal(sub, email, roles: tuple[str, ...], permissions: frozenset[str], jti, claims)` is a frozen dataclass.

### Rules

R1, R2, R4 and R9 apply to org-admin-api code, which this ticket does not touch. The libraries mirror them where relevant:

- **R6:** same envelope in `require_permission`.
- **R11:** ES256, `kid`, JWKS cooldown and flush, no token, password or full email in logs or errors, tokens in memory only, `credentials`/`X-MM-App` rules.
- **R3:** the client uses the endpoint classes exactly: cookie class, public, and Bearer for app backends.
- **R7:** anything else goes to follow-ups.
- **R10:** no social login or OIDC. Only the client of the existing API.
- **R5:** no secrets in either repo. Tests mint ephemeral keys.

## Specialist input used

**Software architect**

- Adopted:
  - layout and stack (all);
  - `contract/`;
  - stages A–E and their order;
  - Release tarball plus git-subdirectory install;
  - the own async `JwksCache` (not `PyJWKClient`);
  - verbatim copies;
  - a catalog min-revalidation of 60 s;
  - app backends only;
  - an ADR in mmlabs-auth.
- Rejected: Biome vs typescript-eslint is settled as Biome (orchestrator).

**Identity-access engineer**

- Adopted:
  - the endpoint map and header classes (verify-email and reset are `omit`);
  - the typed error set, plus `JoinUnavailableError`, `ConfigurationError`, `TurnstileRequired/FailedError`, `EmailTaken`, `EmailAlreadyVerified`, `WeakPassword`, `TokenInvalid` and `InvalidCredentials`;
  - cache keyed by `account|audience`;
  - single-flight;
  - lazy refresh;
  - synchronous cache clear on logout;
  - `email_already_verified` counts as success;
  - `session_invalid` clears the cache only;
  - a principal-level `require_permission` with a definition-time prefix check;
  - `use=sig` filtering;
  - shape validation.
- Changed:
  - Base class name: `AuthError`, not `MmAuthError` (architect and security agree).
  - Skew: `min(60 s, 20 %)`, not 25 %.
  - `join` requires an explicit `accountId` (security T8).
  - `useAccessToken` returns no token (T1).
  - Catalog max staleness is 15 min (orchestrator), not 10.
- Deferred: shared test vectors across repos. They would need committed keys; follow-up F3.

**Security architect**

- Adopted: T1–T9, P1–P5, S1–S3, all acceptance tests, the `ValueError` on leeway > 30, and `kid` ≤ 128.
- Adapted:
  - T4 "then raise session_invalid": `fetchWithToken` returns the second response instead (see Approach).
  - T5 "background backoff": there is no background refresh at all.
  - S3 "OIDC/provenance": no registry publish and no long-lived token. The release uses `github.token` only. Attestation is deferred (F4).
  - S4: Dependabot plus `pip-audit` and `pnpm audit --prod` in CI.
  - The "debug hook (codes only)" is not added. It is not needed for the ACs (R7).
- Flagged to the owner as F1: CLAUDE.md R11 still describes the `mm_device`, `Path=/api/v1/auth` cookie, while spec §5.8 and D20 say `__Host-mm_device`, `Path=/`. The spec wins. This ticket does not edit CLAUDE.md.

## Assumptions

Each assumption is an owner default, set by the orchestrator. The owner can override any of them.

- **A1:** `getToken(audience = appKey)`. Any other audience rejects locally with `ConfigurationError("audience_mismatch")`, because the API would answer 403. The AC's "any audience" is read as "the app's own audience, whatever app it is".
- **A2:** forgot, reset and verify-email are included in the client: owner optional, orchestrator decision.
- **A3:** `SessionInvalidError` clears the account's cached tokens but not `mm_account_id`. The app re-lists accounts.
- **A4:** React ≥ 18 is an optional peer. Angular is deferred. Biome is the linter.
- **A5:** Python's verifier is a sync pure function, and the caches are async only (httpx.AsyncClient). There is no sync cache API.
- **A6:** When JWKS or the catalog is unavailable, `require_permission` answers 503 `auth_unavailable`, not 401. This avoids client refresh loops while staying fail closed. `auth_unavailable` is a library-local code and goes into `contract/error-codes.json` under `library_codes`.
- **A7:** The catalog bounds are 60 s minimum revalidation and 15 min maximum staleness. The JWKS max TTL is 1 h and the cooldown 60 s.
- **A8:** Local-only TS codes: `no_account_selected`, `network_error`, `unexpected_response` (status `0`). They are not in the server contract.
- **A9:** `package.json` `"private": true`. Distribution is by Release tarball, vendored by consumers.
- **A10:** `org_id` on `/token` is not exposed: it is not supported until USR-06 (the server answers 422 `org_not_supported`).
- **A11:** The sandbox can reach the npm registry, PyPI and the GitHub API (for action SHAs). If not, the implementer records what is blocked.

## Risks and follow-ups (owner)

**Risks**

- **Private repo:**
  - Every consumer needs GitHub read access to install: a token in CI or Docker for the Python git install, or `gh release download` for the TS tarball.
  - Mitigation (ADR): consumers vendor the `.tgz` (and optionally the wheel) into their own repo, so Docker builds need no token.
- **GitHub Packages** would force the `@mmlabs-online/*` scope, because the scope must equal the owner name. Choosing it later means renaming the package in every consumer. The public npm `@mmlabs` scope is not owned by us, which is why `"private": true` is set.
- **Drift:** the verifier and `permits()` are copies. Guards: the `contract/SOURCE` commit, the docstrings citing the source, and follow-up F2.
- **XSS** on an app origin can mint that app's tokens. This is inherent, and CSP is the control (T9). For `admin-portal` it means a full admin compromise.
- **Revocation lag:** tokens stay valid up to 15 min after logout, by design (D7). Documented.
- **Size:** about 2,950 hand-written lines in mmlabs-auth plus about 40 in org-admin-api. That is well over the ~400-line guide, so the work is staged (see Split proposal). Reviews may take more than one pass per stage.

**Follow-ups (not in this diff)**

- **F1:** update CLAUDE.md R11 to the D20 cookie.
- **F2:** an org-admin-api test that compares its `AppError` codes against `mmlabs-auth/contract/error-codes.json`. Also a test pinning `access_token.py` and `permits()` against the copies.
- **F3:** shared token test vectors.
- **F4:** provenance attestations, which on private repos need a paid plan.
- **F5:** an Angular wrapper when needed.
- **F6:** a private registry decision.

## Blocking questions

None.

## Split proposal

The ticket stays one ticket, at the owner's request ("both libraries in this ticket"). It runs as sequential stages inside it, each one implementer pass with green suites and one commit:

| Stage | Content | Est. lines (hand-written, excl. lockfiles) |
|---|---|---|
| A | Scaffold of both packages, CI × 2, Dependabot, `contract/` | ~300 |
| B | TS core: transport (auth vs public vs bearer), errors, accounts, token cache, 429 block, 401 retry, broadcast, all endpoints, plus tests | ~1,000 (450 src / 550 tests) |
| C | React wrapper, tests, `ts/README.md` | ~450 |
| D | Python verifier (verbatim), `permits`, http, JWKS, catalog, async verifier, FastAPI, plus tests, `python/README.md` | ~1,050 (500 src / 550 tests) |
| E1 | `release.yml`, ADR-0001, README release section | ~150 |
| E2 | org-admin-api `docs/consumers.md` (separate PR) | ~40 |
| **Total** | | **~2,990** |

This plan covers all stages.

## Data model / migration

None. There is no schema change in org-admin-api.

## API contract

No endpoint change in org-admin-api. The libraries consume these endpoints.

### Endpoints the TS client calls

| Client method | Method + path | Request body | Response | `credentials` / `X-MM-App` |
|---|---|---|---|---|
| `signup({email,password,turnstileToken})` | POST `/api/v1/auth/signup` | `{email,password,turnstile_token}` | 201 `{id,email,email_verified,created_at}` | include / yes |
| `login({email,password,turnstileToken?})` | POST `/api/v1/auth/login` | `{email,password,turnstile_token?}` | 200 `{account_id,user{id,email,email_verified}}` | include / yes |
| `listAccounts()` | GET `/api/v1/auth/accounts` | — | 200 `{items:[{account_id,email,email_verified,display_name}]}` | include / yes |
| `getToken(audience=appKey,{accountId?,forceRefresh?})` | POST `/api/v1/auth/token` | `{audience,account_id}` | 200 `{access_token,token_type,expires_in,audience}` | include / yes |
| `join(accountId)` | POST `/api/v1/auth/apps/{appKey}/join` | `{account_id}` | 200 `{status:"member"}` | include / yes |
| `logout({accountId}\|{all:true})` | POST `/api/v1/auth/logout` | `{account_id}` XOR `{all:true}` | 204 | include / yes |
| `resendVerification(accountId?)` | POST `/api/v1/auth/verify-email/resend` | `{account_id}` | 202, or 409 → `"already_verified"` | include / yes |
| `forgotPassword({email,turnstileToken})` | POST `/api/v1/auth/password/forgot` | `{email,turnstile_token}` | 202 | include / yes |
| `verifyEmail(token)` | POST `/api/v1/auth/verify-email` | `{token}` | 204 | **omit / no** |
| `resetPassword({token,newPassword})` | POST `/api/v1/auth/password/reset` | `{token,new_password}` | 204 | **omit / no** |
| `fetchWithToken(input,init)` | the app's own backend | caller's | caller's | **omit / no**, `Authorization: Bearer` |

The client also has local methods that make no request: `getAccountId()`, `selectAccount(id)`, `clearAccount()`, `invalidateToken()`, `onChange(listener) → unsubscribe` (events `account-changed`, `signed-out`) and `dispose()`.

### Error mapping (`contract/error-codes.json`)

| Status | Code | Class |
|---|---|---|
| 401 | `session_invalid` | `SessionInvalidError` |
| 401 | `invalid_credentials` | `InvalidCredentialsError` |
| 403 | `not_member` | `NotMemberError{joinAvailable}` |
| 403 | `email_unverified` | `EmailUnverifiedError` |
| 403 | `turnstile_required` | `TurnstileRequiredError` |
| 403 | `turnstile_failed` | `TurnstileFailedError` |
| 403 | `join_closed`, `join_approval_unavailable`, `join_unavailable` | `JoinUnavailableError` |
| 403 | `audience_mismatch`, `app_header_required`, `origin_not_allowed`, `app_mismatch` | `ConfigurationError` |
| 422 | `org_not_supported` | `ConfigurationError` |
| 403 | `permission_denied` | generic `AuthError` |
| 409 | `email_taken` | `EmailTakenError` |
| 409 | `email_already_verified` | `EmailAlreadyVerifiedError` |
| 422 | `validation_error` | `ValidationError` |
| 422 | `weak_password` | `WeakPasswordError{reasons}` |
| 422 | `token_invalid` | `TokenInvalidError` |
| 429 | `rate_limited` | `RateLimitedError{retryAfter}` |
| 404 / 405 / 413 / 415 | `not_found`, `method_not_allowed`, `payload_too_large`, `unsupported_media_type` | generic `AuthError` |

`library_codes` (local to the libraries):

- TS: `no_account_selected`, `network_error`, `unexpected_response`.
- Python: `unauthorized` (401), `permission_denied` (403), `auth_unavailable` (503).

### Endpoints the Python library calls

| Method + path | Behaviour |
|---|---|
| GET `/.well-known/jwks.json` | `If-None-Match`, 304 handled, `max-age` ≤ 3600, 64 KB cap, HTTPS-only, no redirects, timeouts 3 s connect / 5 s total |
| GET `/api/v1/apps/{key}/roles` | `If-None-Match`, 304 handled, 60 s minimum revalidation, 15 min maximum staleness, 404 → config error, 256 KB cap |

### Python public surface

- `verify_access_token(token, keys, *, audience, issuer, leeway=30) -> dict` (sync)
- `InvalidAccessTokenError(reason)`
- `permits(granted, required) -> bool`
- `JwksCache`
- `RoleCatalogCache`
- `AccessTokenVerifier`
- `mmlabs_auth.fastapi`: `MmlabsAuth`, `.require_permission(perm)` (dependency returning `Principal`), `AuthHTTPError`, `register_exception_handler`, `Principal`

`require_permission` responses use the R6 envelope:

| Status | Code | Headers / details |
|---|---|---|
| 401 | `unauthorized` | `WWW-Authenticate` |
| 403 | `permission_denied` | `details.required` |
| 503 | `auth_unavailable` | — |
