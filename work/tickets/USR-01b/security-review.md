# Security review — USR-01b

## Attempt 1 — 2026-11-11

**Scope:** `git diff origin/main...HEAD` (app/, tests/, .env.example) against R3/R5/R6/R9/R11; `make check` green (ruff, format, mypy, 79 passed).

**Verdict:** no blockers. The exposure gate fails closed in every case tried, except a missing or empty `APP_ENV` (a deployment-hardening item, listed as a follow-up, not a defect of this diff).

### Findings

💭 **Exposure gate is not bypassable by case, parsing or a wrong value** — `app/core/config.py:19,51,69-75`. Verified via `Settings(_env_file=None)`: `APP_ENV=Production` / `' production'` fail the `Literal` check (fails closed, no start); `APP_ENV=production` with `ADMIN_USERS_API_ENABLED=TRUE` or `1` is refused by the validator; an unparseable bool also stops startup. No fix needed.

💭 **Residual gap: `APP_ENV` falls back to `local`** — `app/core/config.py:15,19`. With `env_ignore_empty=True` and the default `"local"`, an empty or missing `APP_ENV` yields `app_env='local'`. Verified: `APP_ENV= ADMIN_USERS_API_ENABLED=on` → `'local' True`. **Risk:** a production deploy that omits `APP_ENV` **and** sets the flag (two mistakes at once) would mount unauthenticated user PII and the disable action (and also expose `/docs`). Pre-existing setting, needs two errors → not a blocker. **Fix:** follow-up 1 — do not change the validator to "mount when env != production" (that fails open).

💭 **Router mounting is correct** — `app/main.py:37-38`: the users router is included only when the flag is true, so with the flag off it is absent from routing and from `openapi.json`; `tests/test_users_admin.py:63-66` checks the default app returns 404. No fix needed.

💭 **`UserRead` exposes only the expected fields** — `app/modules/users/schemas.py:9-20`: explicit fields with `from_attributes`; keys `id, email, status, email_verified_at, display_name, created_at, updated_at`; no `password_hash`. `test_user_read_has_exact_keys` (~line 280) checks the exact key set using a hash value as a canary; `User.__repr__` also omits the hash. No fix needed.

💭 **LIKE escaping correct; no injection** — `app/modules/users/repository.py:92-94,109`: `escape_like` escapes `\` first, then `%` and `_`; the pattern is a bound parameter with `ESCAPE '\'`; tests at lines 139-155 cover `%`, `_`, `\` and `a_b`. `min_length=1`/`max_length=254` (`router.py:155`) stop `email=` from meaning "match all"; `status` is an enum; `limit` capped at 100. No fix needed.

💭 **PATCH accepts only `status`** — `schemas.py:207-215`: `extra="forbid"`, non-nullable `UserStatus`; `{}`, `{"status": null}` and extra keys → 422; the service writes only `status` (`service.py:246-255`). No fix needed.

💭 **404/422 bodies don't echo input** — `service.py:238` returns a fixed "User not found" (test at line 205 checks the id is absent); the 422 handler (`app/core/errors.py:76-80`) strips `input`/`ctx`. Only an attacker-supplied *extra key name* can appear in `loc` — not a risk. No fix needed.

💭 **Email search terms reach the access log** — no app logging was added in `app/modules/users/`, but uvicorn's default access log (Dockerfile `CMD`) writes the full query string, e.g. `GET /api/v1/users?email=alice%40example.com`. Today only in local/test; once ADM-002 removes the gate, full emails would land in production logs (R11, security-architect consult). No fix in this ticket → follow-up 2.

💭 **Permission declarations in place** — `app/core/permissions.py`, `router.py:151,169,174`: `admin:users:read` (list, get), `admin:users:write` (PATCH) via `openapi_extra` `x-permission`. Each route is class 1 (Bearer), so R3 is met for the pre-ADM-002 period.

💭 **Unbounded `offset` plus a count query on every request** — `repository.py:112-119`: leading-wildcard `ILIKE` + `count(*)` = sequential scan per call. Fine while gated; matters for ADM-002 (follow-up 3).

### Follow-ups (out of scope)
1. **Deploy / ADM-002:** make sure the deployment repo sets `APP_ENV=production` explicitly. Better: make `app_env` required (no default) outside local, or refuse to start when `APP_ENV` is unset in the container image. (`.env.example:1` has `APP_ENV=local` with a value — a small R5 inconsistency.)
2. **ADM-002:** before the users router is mounted in production, keep the email filter out of logs: run uvicorn with `--no-access-log`, or add a log filter that redacts query strings (at least `email=`) on `/api/v1/users`.
3. **ADM-002 must:** remove `admin_users_api_enabled`, its validator and the conditional mount; mount the router behind Bearer `aud=admin` + `require_permission(...)` reading each route's `x-permission`; add 401/403 tests for list/get/patch (R8); stop staff from disabling themselves or the last `admin:*` holder; rate-limit `/api/v1/users` list/search (enumeration and the count/`ILIKE` cost); record in the release notes that the §6.1 accepted risk does not cover user PII.
4. **ADM-004:** audit every status change in the same transaction (R4; hook comment at `service.py:250`). Decide whether staff *reads* of PII are audited.
5. **USR-03/03b:** reject login, revoke device sessions and refuse tokens for `status=disabled`. Until then, "disable" has no effect.
6. **Separate ticket:** disable `/openapi.json` in production (`openapi_url=None`, as `docs_url` already is).
7. **ADM-002 (CSRF):** if any admin route ever accepts cookie auth, it needs an `Origin` check, because `CORSMiddleware` runs with `allow_credentials=True`. Bearer-only is not CSRF-exposed.
