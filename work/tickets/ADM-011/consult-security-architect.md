# consult-security-architect — ADM-011

## Findings
1. **Production doesn't set `TRUSTED_PROXY_CIDRS` today, and a value set by hand there would not survive the next deploy.** `deploy-org-admin-api.yml` (envfile step) overwrites `~/.config/org-admin-api/org-admin-api.env` on every run, and that file only ever holds `DATABASE_URL`. `apps/org-admin-api/compose.yml` sets only `APP_ENV`, `APP_VERSION` and `TZ`. So the ticket's note ("set by hand in the deployment repo") is not durable. `TURNSTILE_SECRET_KEY` is also absent, so signup fails closed with 403.
2. **The `web` subnet is whatever Docker picked.** `deploy-proxy.yml` runs `docker network create web` with no `--subnet`, so Docker takes a subnet from its default pool. Recreating the network can give a different one. cloudflared has no fixed IP.
3. **The migrate step would also fail.** `migrate` shares `x-app` (`APP_ENV: production`), and `migrations/env.py` calls `get_settings()`. A new startup check on `Settings` would therefore make `alembic upgrade` fail too. The deploy would stop at "migrate-failed" and the old API would keep running. That is safe but blocks the release.
4. **Env format trap.** `trusted_proxy_cidrs: list[str]` is parsed as JSON by pydantic-settings. `TRUSTED_PROXY_CIDRS=172.18.0.0/16` fails to parse; it must be written `["172.18.0.0/16"]`.
5. **The category validator does not cover everything F2 listed.** Variation selectors (U+FE0F, U+E0100–E01EF) are category `Mn`, so the `{Cc,Cf,Zl,Zp,Co,Cs,Cn}` set still accepts them. That is acceptable, because emoji need them. The set does reject U+00AD, U+180E, tag characters and ZWJ U+200D. Rejecting ZWJ breaks family/profession emoji; accept that or allow-list it. `Cn` follows Python's Unicode database (3.12 uses Unicode 15.0), so newly assigned characters are rejected until Python is upgraded.
6. **The rate limiter evicts with a full sort.** `_evict` scans and sorts every key (O(n log n)) on each new bucket once the store holds more than 10k keys. All scopes share one store.
7. **Not part of this ticket, but urgent: the Cloudflare Tunnel token is committed in plain text** in `deployment/proxy/compose.yml`. Anyone with read access to that repo can run a connector for the tunnel and take traffic for `api.mmlabs.online`. Also, `cloudflared:latest` is not pinned.

## Recommendations
- **AC1 (empty `TRUSTED_PROXY_CIDRS`)**
  - Add a `model_validator` like the Argon2 one. Its message names the key only, e.g. "TRUSTED_PROXY_CIDRS must be set in staging/production (JSON list of cloudflared's network CIDRs, see docs/deploy.md)".
  - In staging/production, also refuse `/0` and prefixes shorter than /8, and refuse non-private networks (`not ip_network(c).is_private`).
  - The deployment repo should put the value in compose `x-app.environment` (it is not a secret), not in the env file.
  - CIDR choice:
    - **Now:** the current subnet of `web` (`docker network inspect web -f '{{(index .IPAM.Config 0).Subnet}}'`).
    - **Better:** recreate `web` once with `--subnet 172.31.250.0/24 --ip-range 172.31.250.128/25`, give cloudflared `ipv4_address: 172.31.250.2`, and trust `["172.31.250.2/32"]`. Trusting the whole subnet lets any app on `web` (pgadmin, stoktok, …) forge `CF-Connecting-IP`.
    - Update `deploy-proxy.yml` so it creates the network with that subnet and checks it.
  - Document the JSON-list format in `.env.example` and `docs/deploy.md`.
- **AC2 (limiter)**
  - Use `dict[scope, OrderedDict[key, (end, count)]]` with a cap per scope (default 10k each, set per scope).
  - Each scope has one fixed window, so insertion order equals expiry order:
    - When a key starts a new window, call `move_to_end`.
    - Evict by popping from the front while entries are expired, then pop the front while over the cap.
    - Both steps are amortised O(1).
  - Require one window per scope, or the ordering breaks.
  - Test that flooding `role_catalog` cannot evict a `signup` bucket, and that eviction work stays bounded.
- **AC3 (R6 envelope)**
  - Register a handler for `starlette.exceptions.HTTPException`. It also catches FastAPI's subclass.
  - Codes: 404 `not_found`, 405 `method_not_allowed`, otherwise `http_error`.
  - The message comes from `HTTPStatus(code).phrase`. **Never echo `exc.detail`.** Keep `exc.headers` (`Allow`, `WWW-Authenticate`).
  - Optionally add an `Exception` handler that returns 500 `internal_error` with no traceback.
  - Test an unknown path, the 405 with its `Allow` header, and a `%2F` key.
- **AC4 (display text + NFC)**
  - Build the type in this order: `BeforeValidator` (NFC if `str`) → `Field(min/max_length)` → `AfterValidator` (category check). The length check then sees the normalized string; NFC can change the length.
  - Error messages must not echo the value.
  - Apply it to input schemas only. Read schemas stay plain `str`, so older rows never cause a 500.
  - Tests:
    - Vietnamese NFD `"Quản trị"` is stored as NFC.
    - Rejected: `"a\U000E0041b"`, `"a­b"`, `"a​b"`, and an unassigned code point.
    - An emoji is accepted.
  - Amend ADR-0003 §11.
- **AC5 (origins + uvicorn)**
  - In `origins.py`, refuse `host.endswith("." + _API_HOST)` as well, and add a test for it.
  - Add `"--no-proxy-headers"` to the Dockerfile CMD. Test it by asserting the CMD contents, or record it in ADR-0002.

## Risks
- **Release order (blocking).** The deployment PR that sets `TRUSTED_PROXY_CIDRS` in compose `environment` must merge **before** the release PR for the ADM-011 image. Otherwise migrate fails, and if migrate were skipped the health check would fail and trigger a rollback. The PR body must state this as a deploy prerequisite.
- **Recreating `web` takes every app offline briefly** (cloudflared and all apps on it must be detached and reattached). Schedule it, or trust the current subnet first.
- **NFC and stored data.** NFC changes the stored bytes only for non-NFC input. Production has no data created through the API (the RBAC admin API is barred there), and the seed rows are ASCII, so no data migration is needed. A later PATCH with NFD input changes the ETag once.
- **Evicting live buckets under the cap fails open** (that bucket's count resets). An attacker rotating IPv6 /64s could cause this. Acceptable for one process; document it in ADR-0002.

## Open questions
1. Will the owner recreate `web` with a pinned subnet and a fixed IP for cloudflared, or accept trusting the current subnet?
2. Should the startup check also apply to `migrate`, or only to the API process (e.g. check in `create_app`)? I recommend applying it to both, after the deployment change has shipped.
3. Should ZWJ U+200D be allowed so emoji sequences work in role and app names?
4. The tunnel token (Finding 7): rotate it and move it to a host secret. This is for the deployment repo, outside ADM-011.
