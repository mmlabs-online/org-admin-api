# consult-software-architect — USR-08

**Findings**

- `/home/user/mmlabs-auth` holds only a README and a .gitignore. The .gitignore already covers both `node_modules/dist` and `.venv/__pycache__`, so the `ts/` + `python/` split it implies fits.
- What the TS client wraps is cookie-class only: `signup`, `login`, `logout` (`{account_id}` XOR `{all:true}`), `token`, `accounts`, `apps/{key}/join`, `verify-email/resend`.
  - signup, login, forgot and resend all need `X-MM-App` plus a registered `Origin`.
  - login and signup take `turnstile_token`.
  - `/token` checks `audience == X-MM-App`, otherwise 403 `audience_mismatch`. So `getToken(audience)` only works when the audience is the client's own app key: `admin` for admin-pan.
- Error codes the client sees:
  - `/token` and join: `session_invalid`, `not_member` (`details.join_available`), `email_unverified`, `audience_mismatch`, `app_header_required`, `origin_not_allowed`, `org_not_supported`, `validation_error`, `rate_limited` (`Retry-After` is exposed), `join_closed`, `join_approval_unavailable`, `join_unavailable`, `app_mismatch`.
  - Sign-in and account flows: `invalid_credentials`, `turnstile_required`, `turnstile_failed`, `email_taken`, `weak_password` (`details.reasons`), `email_already_verified`, `token_invalid`.
  - A CORS failure shows up as `TypeError`, not as an envelope.
- The cookie rotates on the server with a 60 s grace window (ADR-0004), so the client needs no rotation logic. Single-flight just cuts down on rotation races.
- The Python verifier already exists in a form that is pure and marked "copyable":
  - `app/core/access_token.py` raises `InvalidAccessTokenError(reason)` with fixed reasons. It needs `typ=at+jwt`, `alg=ES256`, a known `kid`, `strict_aud`, the claims exp/iat/sub/jti/iss/aud, and `email_verified is True`.
  - `app/modules/rbac/authz.py:permits()` does an exact match or `<app>:*`, and is also marked "copy as is".
- Role catalog `GET /api/v1/apps/{key}/roles`: body `{app, roles:[{key,name,is_system,permissions}]}`, a strong ETag/304, `Cache-Control: no-cache`, per-IP rate limit, and 404 for unknown or disabled apps and for `admin`.
  - So `require_permission` in `mmlabs-auth` is for app backends only. Admin permissions come from the database on every request (ADR-0008) and never through the catalog.
- JWKS: `max-age=300`, ETag, `Access-Control-Allow-Origin: *`. USR-03c S1/S2 require a flushable cache and a cooldown of at least 60 s for unknown-`kid` refetches, and ban `cache_keys=True`.
- `PyJWKClient` is synchronous (urllib). Calling it from async FastAPI blocks the event loop.

**Recommendations**

1. **Layout: two independent packages, no root workspace.**
   - `ts/` is one pnpm 10 package, `@mmlabs/auth-client`, with `packageManager` pinned and Node 22. Exports are `.` and `./react`. Make it a pnpm workspace only when an Angular wrapper is actually needed.
   - `python/` is a uv project in src layout (`src/mmlabs_auth`) with hatchling. The `fastapi` extra keeps FastAPI out of the core.
   - `contract/` sits at the root and is shared by both test suites.
2. **TS stack.**
   - TypeScript strict (`exactOptionalPropertyTypes`, `noUncheckedIndexedAccess`), ESM only, built with plain `tsc`. Consumers bundle it anyway, so tsup is not needed.
   - Zero runtime dependencies. React ≥18 is an optional `peerDependency` (via `peerDependenciesMeta`).
   - vitest, with happy-dom and @testing-library/react for the React part only. Lint and format with Biome (one dev dependency), or typescript-eslint if the owner prefers.
   - Inject `fetch`, `storage` and `now()` so the tests are deterministic.
3. **TS behaviour (one line per AC).**
   - Use `createAuthClient({apiBase, appKey})`. `getToken()` defaults `audience` to `appKey` and refuses any other value on the client side, which avoids a needless 403 `audience_mismatch`.
   - Cache is in memory, keyed by `(audience, accountId)`. Refresh at `receivedAt + expires_in - 60 s`, based on `expires_in` and the local clock, never on decoding `exp`. One in-flight promise per key.
   - `invalidateToken()` is called on a 401 from a Bearer API. After verify-email or resend, drop the cache and call `getToken` again.
   - `mm_account_id` goes through the injected storage (`localStorage` by default) with try/catch. It is never used for tokens.
   - `logout({account})` clears the token cache and clears `mm_account_id` if it matches. `logout({all:true})` clears everything.
   - `credentials:"include"` and `X-MM-App` are set only inside the `/api/v1/auth/*` transport. A separate `bearerFetch()` always uses `credentials:"omit"` and never sends `X-MM-App`. Test both explicitly.
   - Errors: one `AuthError` base with `status`, `code`, `details`, and subclasses `NotMemberError(joinAvailable)`, `EmailUnverifiedError`, `SessionInvalidError`, `RateLimitedError(retryAfter)` (parsed as seconds or as an HTTP-date). Add `NetworkError` for a CORS `TypeError` and a generic fallback for other codes and 5xx.
   - Tokens and passwords never appear in error messages.
4. **React wrapper.** `<AuthProvider client>` with `useAuth()` (status, accounts, current account, signIn, signOut, switchAccount, join) and `useAccessToken()`. The wrapper holds no logic of its own; it subscribes to client events.
5. **Python stack.**
   - PyJWT[crypto] ≥2.10.1, httpx, ruff, mypy `--strict`, pytest, pytest-asyncio, respx.
   - Write our own async `JwksCache` instead of using `PyJWKClient`:
     - TTL is `min(max-age, 3600)`, sent with `If-None-Match`.
     - At most one unknown-`kid` refetch per 60 s, single-flight through `asyncio.Lock`.
     - `flush()` for emergency rotation, and a configurable `User-Agent`.
   - Copy `verify_access_token` and `permits()` verbatim, with only `KeySet` swapped for a `kid` → key map. Keep the same reason codes.
   - `RoleCatalogCache`: revalidate with `If-None-Match` no more often than a minimum interval (e.g. 60 s), not on every request. On a fetch error, serve stale data up to a bound. An unknown role grants nothing (fail closed). A 404 is a configuration error.
   - `mmlabs_auth.fastapi.require_permission("<app>:<res>:<action>")`:
     - 401 with `WWW-Authenticate: Bearer` and no reason given.
     - 403 `permission_denied` with `details.required`.
     - Same R6 envelope shape as the API.
6. **CI.** One workflow per language, path-filtered (`ts/**`, `python/**`, `contract/**`), with `permissions: contents: read`.
   - TS job: pnpm/action-setup, Node 22, `pnpm install --frozen-lockfile`, lint, typecheck, test, build.
   - Python job: astral-sh/setup-uv, Python 3.12, `uv sync --locked`, ruff check and format check, mypy, pytest.
7. **Versioning: independent semver at 0.x, no registry yet.**
   - Tags are `ts-v0.1.0` and `py-v0.1.0`.
   - Python consumers install `mmlabs-auth @ git+https://github.com/mmlabs-online/mmlabs-auth@py-v0.1.0#subdirectory=python`.
   - TS: on each tag, CI runs `pnpm pack` and attaches the `.tgz` to a GitHub Release, and consumers depend on that tarball. This is safer than a git dependency, because a git dependency needs a `prepare` build and pnpm 10 blocks dependency lifecycle scripts by default.
   - Record the owner's choice in an ADR in mmlabs-auth.
8. **Keeping the libraries in sync with the API.**
   - `contract/` holds:
     - `error-codes.json` (code → status, endpoints, typed or generic);
     - `catalog.example.json` with its ETag;
     - `token-response.example.json`;
     - `contract/SOURCE` with the org-admin-api commit it was taken from.
   - Both suites load these files. A TS test checks that every code marked typed maps to its class. Python tests mint tokens at runtime with ephemeral P-256 keys, so no keys are committed.
   - Follow-up in org-admin-api (R7, not part of this diff): a test that compares the codes the app defines in its `AppError` subclasses against `contract/error-codes.json`.
9. **Stages inside USR-08.** Over 1,500 lines in total. Make one commit per stage and run REVIEW at the end of each stage.
   - **A.** Scaffold, both CI workflows, `contract/` (about 250 lines).
   - **B.** TS core plus tests: transport, errors, accounts, token cache (about 700). Comes first because it unblocks PAN-010.
   - **C.** React wrapper plus tests, and the TS integration README (about 300).
   - **D.** Python JWKS, verifier, catalog, FastAPI dependency plus tests (about 650).
   - **E.** Release workflow and tags, plus the org-admin-api PR that adds links in `docs/consumers.md` (about 60).

**Risks**

- The ticket needs two PRs, one in mmlabs-auth and one in org-admin-api (the consumers.md AC). The DONE criterion has to name both.
- The repo is private, so every consumer's CI and Docker build needs a GitHub token to install from git or from a Release. Without it, admin-pan's Docker build fails.
- GitHub Packages cannot host `@mmlabs/*`: its scope must match the owner name (`@mmlabs-online`). Choosing it later means renaming the package.
- If someone points `require_permission` at `aud=admin`, the catalog answers 404. Document "app backends only" and fail loudly at startup.
- The vendored copies of the verifier and `permits()` can drift from the server. The contract files and the follow-up test above are the guard.
- Local clock skew can expire cached tokens early or late. Using `expires_in` with the 60 s margin limits this.

**Open questions**

- Publishing: the git tag plus Release tarball approach above, or a private registry later (which one, and under what scope name)?
- Should `getToken` accept an audience other than `appKey` at all, or should the parameter be dropped, given the API answers `audience_mismatch`?
- Should forgot, reset and verify-email (owner note, "optional") be in this ticket? I suggest stage C+ only if it stays small.
- Python: async only, or a sync verifier as well for non-async backends?
- Minimum React version (18 or 19)? Angular wrapper deferred? (I assume yes.)
- Catalog cache bounds: minimum revalidation interval (60 s?) and maximum staleness on outage (15 min?).
