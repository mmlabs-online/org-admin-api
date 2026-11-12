Status: READY

# Plan — ADM-011: Platform hardening for public endpoints (client IP, limiter, error envelope, display text)

## Spec summary
- spec §6: "No secrets in code or logs; config via env only."
- spec §6.1: the API is public at `api.mmlabs.online`, behind Cloudflare Tunnel, with no Access gate.
- identity-architecture.md §5 **S3**: "per-IP ~20 req/min on login/signup/forgot". A per-IP limit only works if the API sees the real client IP, which behind cloudflared means `TRUSTED_PROXY_CIDRS` must be set.
- **R6**: "One error shape: `{"error": {"code", "message", "details"}}` via `app/core/errors.py`."
- **R11** (CORS): the allow-origin header echoes only `apps.origins`.
- ADR-0002 §1–2 covers the limiter and client IP: "With `TRUSTED_PROXY_CIDRS` unset in production, every client shares one bucket."
- ADR-0003 §11 covers display text and the public catalog.
- Ticket ACs come from these follow-ups: USR-02e F1/F2/F3 and FU2/FU3/FU5/FU6, USR-01 review finding 4, and USR-02d FU6.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | In `staging`/`production`, `Settings` refuses an empty `TRUSTED_PROXY_CIDRS`. It also refuses any CIDR that is not private, any `/0`, any IPv4 prefix shorter than /8 and any IPv6 prefix shorter than /48. `local`/`test` keep today's behaviour (empty is allowed; only the syntax is checked). Settings errors never echo input values. | `tests/test_config.py` › `test_trusted_proxy_cidrs_required_in_staging_and_production`, `test_trusted_proxy_cidrs_rules_in_staging_and_production`, `test_trusted_proxy_cidrs_valid_in_production`, `test_trusted_proxy_cidrs_optional_in_local_and_test`, `test_settings_errors_do_not_echo_input` |
| 2 | The rate limiter keeps one store per scope, each capped by `max_keys_per_scope`. Eviction is O(1) amortised: expired entries are popped from the front, then the oldest while the store is over the cap. Filling one scope never evicts another scope's buckets. Call sites are unchanged. An unknown scope fails when the dependency is built, not per request. | `tests/test_rate_limit.py` › `test_evicts_expired_then_oldest_within_scope`, `test_restarted_window_is_not_oldest`, `test_flooding_one_scope_never_evicts_another`, `test_unknown_scope_refused_at_build`, plus the existing limiter tests still passing. Code read: `_evict` has no sort or full scan. |
| 3 | Framework `HTTPException`s (Starlette and FastAPI) use the R6 envelope. The code is mapped from the status, the message is `HTTPStatus.phrase`, `details` is null, and `exc.detail` is never echoed. `exc.headers` (e.g. `Allow`) are kept. 204/304 keep an empty body. `AppError` and validation handlers are unchanged. | `tests/test_errors.py` › `test_unknown_path_404_envelope`, `test_http_exception_detail_not_echoed_headers_kept`, `test_unmapped_status_uses_http_error`, `test_304_http_exception_has_empty_body`; `tests/test_role_catalog_api.py` › `test_post_roles_flag_off_is_405` (extended: envelope + `Allow: GET`), `test_encoded_slash_key_404_envelope` |
| 4 | Display text (app `name`, role `name`, permission `description`, input schemas only) goes through three steps in order: NFC normalization, then the length limits, then a check that rejects any code point in category `Cc, Cf, Zl, Zp, Co, Cs, Cn`. A rejection is a 422 `validation_error` with type `display_text` and the value is not echoed. The value is stored as NFC. ADR-0003 §11 is amended. | `tests/test_text.py` (new): one case per category, accepted samples, NFC, NFC-before-length, error without the value. `tests/test_rbac_apps_api.py` › `test_app_name_control_chars_422` (wider char list), `test_app_name_stored_nfc`. Existing role and permission format-char tests still pass. `docs/adr/0003-…` §11 diff. |
| 5 | (Optional) `normalize_origin` refuses any host under `.api.mmlabs.online`. | `tests/test_rbac_origins.py` › new rejected cases `https://x.api.mmlabs.online`, `https://a.b.api.mmlabs.online` |
| 6 | (Optional) The Dockerfile uvicorn CMD has `--no-proxy-headers`. | `tests/test_dockerfile.py` › `test_uvicorn_cmd_disables_proxy_headers`; ADR-0002 amended |
| 7 | `make check` is green, and the PR body carries the **Deploy prerequisite** section below. | CI or `make check`; PR description |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/core/config.py` | Add `hide_input_in_errors=True` to `model_config`. Add `model_validator` `_trusted_proxy_gate` (staging/production: non-empty, private, no `/0`, IPv4 ≥ /8, IPv6 ≥ /48). | AC1; R5 (the new refusal is meant to fire at prod startup, and pydantic otherwise prints part of the input dict, which may include `DATABASE_URL`) |
| `app/core/rate_limit.py` | Per-scope `OrderedDict` stores and `max_keys_per_scope`, front-pop eviction. `Scope = Literal["signup", "role_catalog"]`. `_LIMITS` table. `rate_limit()` validates the scope when it is called. | AC2 |
| `app/core/errors.py` | Add `_http_exception_handler` for `starlette.exceptions.HTTPException` and register it. | AC3, R6 |
| `app/core/text.py` (new) | `DisplayText` building blocks: `nfc_if_str`, `reject_disallowed_categories`, `DISALLOWED_CATEGORIES`. | AC4 (reusable for future org/user display names) |
| `app/modules/rbac/schemas.py` | Replace `_NO_CONTROL` with the new validators in `AppName`, `RoleName` and `Description`. Read schemas stay unchanged. | AC4 |
| `app/modules/rbac/origins.py` | Refuse `host.endswith("." + _API_HOST)`. Update the docstring. | AC5 |
| `Dockerfile` | Add `"--no-proxy-headers"` to CMD. | AC6 |
| `.env.example` | Comment above `TRUSTED_PROXY_CIDRS=`: JSON list, required in staging/production, e.g. `["172.18.0.0/16"]`. No value. | AC1 (env format trap, security consult finding 4) |
| `docs/deploy.md` | New "Required runtime config" subsection: `TRUSTED_PROXY_CIDRS` (JSON list, set in compose `x-app.environment`, which both `migrate` and the API read). How to find the `web` subnet. Startup refusal behaviour. | AC1, deploy prerequisite |
| `docs/adr/0002-identity-abuse-controls-runtime.md` | Add an `Amended 2026-11-11 (ADM-011)` line. §1: per-scope stores, cap per scope, O(1) eviction, fail-open note for evicted live buckets. §2: staging/production refuse an empty or unsafe `TRUSTED_PROXY_CIDRS`; uvicorn runs with `--no-proxy-headers`. Update Consequences. | Decisions that outlive the ticket (planner rule 7) |
| `docs/adr/0003-rbac-keys-and-matching.md` | Add an `Amended 2026-11-11 (ADM-011): §11.` line. Replace the §11 display-text bullet (text under Approach step 4). | AC4 |
| `tests/test_config.py` | New AC1 tests. Pass `trusted_proxy_cidrs=["172.18.0.0/16"]` in the existing staging/production test. | AC1 |
| `tests/test_users_admin.py` | Same CIDR kwarg in `test_users_api_flag_refused_in_staging_and_production`. | Keep existing tests valid under AC1 |
| `tests/test_health.py` | Same CIDR kwarg in `test_docs_enabled_in_production`. | Same |
| `tests/test_turnstile.py` | Same CIDR kwarg in `test_cloudflare_test_secret_rejected_in_production`. | Same |
| `tests/test_rate_limit.py` | Replace `test_evicts_expired_and_caps_keys`, which reads `_windows`. Add the AC2 tests. | AC2 |
| `tests/test_errors.py` | AC3 tests | AC3 |
| `tests/test_role_catalog_api.py` | Extend the 405 test. Add the `%2F` test. | AC3 |
| `tests/test_text.py` (new) | Unit tests for the display-text type | AC4 |
| `tests/test_rbac_apps_api.py` | Widen `FORMAT_CHARS`. Add the NFC storage test. | AC4 |
| `tests/test_rbac_origins.py` | Rejected `*.api.mmlabs.online` cases | AC5 |
| `tests/test_dockerfile.py` (new) | Assert the CMD flag | AC6 |

Production code is estimated at about 150–190 changed lines: config ~25, rate_limit ~±45, errors ~35, text ~30, schemas ~±10, origins ~3, Dockerfile 1. Docs add about 40 lines and tests about 250.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `test_config.py › test_trusted_proxy_cidrs_required_in_staging_and_production` (param staging/production) | Empty list → `ValidationError` matching `TRUSTED_PROXY_CIDRS` |
| `test_config.py › test_trusted_proxy_cidrs_rules_in_staging_and_production` (param `["0.0.0.0/0"]`, `["::/0"]`, `["8.8.8.0/24"]`, `["10.0.0.0/7"]`, `["fd00::/40"]`) | Public, /0 and too-wide CIDRs refused (`10.0.0.0/7` is not private as a whole and is also wider than /8) |
| `test_config.py › test_trusted_proxy_cidrs_valid_in_production` | `["172.18.0.0/16"]`, `["172.31.250.2/32"]`, `["fd00:1::/64"]` accepted |
| `test_config.py › test_trusted_proxy_cidrs_optional_in_local_and_test` | Empty list and `["0.0.0.0/0"]` accepted in local/test (rules apply only to staging/production) |
| `test_config.py › test_settings_errors_do_not_echo_input` | With `DATABASE_URL` containing a marker password and production + empty CIDRs, `str(exc)` does not contain the marker |
| `test_rate_limit.py › test_evicts_expired_then_oldest_within_scope` | Cap 3. Expired keys are dropped first. Over the cap, the oldest key is evicted, so an exhausted then evicted key gets a fresh window (`hit` → `None`). Behavioural, with no private attribute access. |
| `test_rate_limit.py › test_restarted_window_is_not_oldest` | A key whose window restarts moves to the back and survives the next cap eviction |
| `test_rate_limit.py › test_flooding_one_scope_never_evicts_another` | Exhaust `signup`/`ip`. Add 1 000 new `role_catalog` keys with cap 10. `signup`/`ip` is still blocked. |
| `test_rate_limit.py › test_unknown_scope_refused_at_build` | `rate_limit("nope")` raises `ValueError` (with `# type: ignore[arg-type]`) |
| `test_errors.py › test_unknown_path_404_envelope` | `GET /api/v1/nope` → 404 `{"error": {"code": "not_found", "message": "Not Found", "details": null}}` |
| `test_errors.py › test_http_exception_detail_not_echoed_headers_kept` | A bare app raises `HTTPException(400, detail="secret-marker", headers={"X-Test": "1"})` → `bad_request`, no marker in the body, header kept |
| `test_errors.py › test_unmapped_status_uses_http_error` | `HTTPException(418)` → code `http_error`, message `"I'm a Teapot"` (whatever `HTTPStatus(418).phrase` is) |
| `test_errors.py › test_304_http_exception_has_empty_body` | `HTTPException(304)` → empty body |
| `test_role_catalog_api.py › test_post_roles_flag_off_is_405` (extend) | Envelope code `method_not_allowed`, `Allow` header contains `GET` |
| `test_role_catalog_api.py › test_encoded_slash_key_404_envelope` | `GET /api/v1/apps/a%2Fb/roles` → 404 with the envelope (`not_found`) and no `detail` key |
| `test_text.py › test_rejects_category` (param `\x00` Cc, `\x85` Cc, `­` Cf, `‍` Cf, `\U000E0041` Cf, ` ` Zl, ` ` Zp, `` Co, `\ud800` Cs, `͸` Cn) | Every category refused through `TypeAdapter`/`model_validate` (Cs cannot come through JSON reliably) |
| `test_text.py › test_accepts_display_text` (param `"Quản trị viên"`, `"Owner 👑"`, `"Admin ❤️"` (VS16 is Mn), `"a b"`) | Normal text kept |
| `test_text.py › test_nfc_normalized` | NFD `"Quản"` → NFC `"Quản"` |
| `test_text.py › test_length_checked_after_nfc` | `"ả" * 200` (400 code points NFD, 200 NFC) is accepted for a max-255 field. `"a" * 256` is refused. |
| `test_text.py › test_error_does_not_echo_value` | The error `type == "display_text"`, and neither `msg` nor the error string contains the input |
| `test_text.py › test_none_passes_through` | `Description \| None` with `None` → `None` |
| `test_rbac_apps_api.py › test_app_name_control_chars_422` (wider list) | Add `­`, `‍`, `\U000E0041`, ``, `͸`, `᠎` to `FORMAT_CHARS` |
| `test_rbac_apps_api.py › test_app_name_stored_nfc` | POST with NFD name → 201, the response `name` is NFC, and the DB row is NFC. PATCH likewise. |
| `test_rbac_origins.py` (add to the rejected parametrization) | `https://x.api.mmlabs.online`, `https://a.b.api.mmlabs.online` refused. `https://api2.mmlabs.online` is still accepted. |
| `test_dockerfile.py › test_uvicorn_cmd_disables_proxy_headers` | The Dockerfile `CMD` line contains `"--no-proxy-headers"` |

## Approach
1. **AC1: trusted-proxy gate (`app/core/config.py`).**
   - Add `hide_input_in_errors=True` to `SettingsConfigDict`. Pydantic otherwise prints a truncated `input_value={…}` of the whole env dict, which can include `DATABASE_URL` (verified locally). The message text still names the key, so the existing `match=` tests keep passing (R5).
   - Add `@model_validator(mode="after") _trusted_proxy_gate`. If `app_env in {"staging", "production"}`:
     - If the list is empty, raise `ValueError("TRUSTED_PROXY_CIDRS must be set in staging/production (JSON list of the cloudflared network CIDRs, see docs/deploy.md)")`.
     - For each CIDR, `net = ip_network(c, strict=False)`. Refuse it if `net.prefixlen == 0`, or `not net.is_private`, or `net.prefixlen < (8 if net.version == 4 else 48)`. Message: `"TRUSTED_PROXY_CIDRS entries must be private networks of at most /8 (IPv4) or /48 (IPv6) in staging/production"`. The value is not echoed.
   - **Design decision: one check in `Settings`, applied to both API and `migrate`. No exemption for migrate.** Reasons:
     - (a) It follows the existing `_argon2_floor`/`_admin_apis_gate` pattern.
     - (b) Failing at the `migrate` step is the cleanest failure in the deploy flow (docs/deploy.md step 5: "fail → stop, old API keeps running"). If migrate were exempt, the deploy would migrate, replace the API, fail `/readyz`, roll back and push a revert. That is noisier and has the same end result.
     - (c) ADM-011 has no migration, so exempting migrate gains nothing.
     - (d) The release smoke test (`release.yml`) runs with the default `APP_ENV=local`, so it is unaffected.
   - The current production image already accepts `TRUSTED_PROXY_CIDRS`, so the deployment change can ship before this release (see Deploy prerequisite).
2. **AC2: limiter (`app/core/rate_limit.py`).**
   - Keep `RateLimit`, the `RateLimiter` protocol (`hit(scope: str, key, limit)`) and `rate_limit(scope)`, so call sites and `main.py` do not change.
   - `InMemoryRateLimiter(*, clock=time.monotonic, max_keys_per_scope: int = 10_000)` holds `self._scopes: dict[str, OrderedDict[str, tuple[float, int]]]`.
   - `hit` works on `store = self._scopes.setdefault(scope, OrderedDict())`.
   - New or expired window: assign `(now + window, 1)`, call `store.move_to_end(key)`, then `_evict(store, now)`. A count increment reassigns an existing key, so its position is kept.
   - `_evict`: `while store` → look at the front item. Stop if its end is in the future and `len(store) <= cap`. Otherwise `popitem(last=False)`. The just-inserted key is at the back and not expired, so it is never popped (cap ≥ 1).
   - Insertion order equals window-end order because each scope's window length is constant for the life of the process (it comes from `Settings`). If it ever differed, expired entries behind a live front would only linger until cap pressure, which is still bounded. Write this down in a code comment and in the ADR.
   - Add `Scope = Literal["signup", "role_catalog"]` and `_LIMITS: dict[str, Callable[[Settings], RateLimit]]`. `rate_limit(scope: Scope)` raises `ValueError(f"unknown rate-limit scope")` when `scope not in _LIMITS`, at build time. Routers are imported at app creation, so a typo fails at startup.
3. **AC3: R6 envelope (`app/core/errors.py`).**
   - `from starlette.exceptions import HTTPException as StarletteHTTPException`.
   - Add `_HTTP_CODES = {400: "bad_request", 401: "unauthorized", 403: "permission_denied", 404: "not_found", 405: "method_not_allowed", 406: "not_acceptable", 413: "payload_too_large", 415: "unsupported_media_type", 429: "rate_limited"}`. Anything else maps to `"http_error"`.
   - Handler:
     - For `status_code in {204, 304}`, return `Response(status_code=…, headers=exc.headers)`, mirroring Starlette's default.
     - Otherwise return a `JSONResponse` with `_envelope(code, HTTPStatus(sc).phrase, None)` and `headers=exc.headers`.
     - If `HTTPStatus(sc)` raises `ValueError`, use the message `"HTTP error"`.
     - Never read `exc.detail`.
   - Register it in `register_error_handlers`. `AppError` is not an `HTTPException`, so it is unaffected.
   - R9: this is not a breaking change. Framework bodies were undocumented and R6 already required the envelope. Record it in the PR body.
4. **AC4: display text (`app/core/text.py`, `rbac/schemas.py`).**
   - In `text.py`:
     - `DISALLOWED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Co", "Cs", "Cn"})`.
     - `nfc_if_str(v: object) -> object` returns `unicodedata.normalize("NFC", v)` for a `str`, else `v`.
     - `reject_disallowed_categories(v: str) -> str` raises `PydanticCustomError("display_text", "contains a disallowed character")` if any `unicodedata.category(ch)` is in the set.
   - In `schemas.py`, for example:

     ```
     AppName = Annotated[str, BeforeValidator(nfc_if_str), Field(min_length=1, max_length=255), AfterValidator(reject_disallowed_categories)]
     ```

     Define `RoleName` and `Description` (max 500, no min) the same way. Delete `_NO_CONTROL` and its comment, and point to ADR-0003 §11 instead.
   - The implementer must confirm through `test_length_checked_after_nfc` that the length check sees the NFC string. If pydantic applies the `Field` constraints before the `BeforeValidator`, move the length check into the after-validator instead and record why in impl-log.
   - The new set is a strict superset of the old regex: C0/C1/DEL are Cc, U+061C/200B–200F/202A–202E/2060–2069/FEFF are Cf, and U+2028/2029 are Zl/Zp. So no input that was refused becomes accepted.
   - **ZWJ (U+200D, Cf) stays rejected**, as it is today. Variation selectors (Mn) are accepted, so single emoji and emoji with VS16 work. Only ZWJ sequences (family or profession emoji) are refused.
   - Read schemas (`AppRead`, `PermissionRead`, catalog document) stay plain `str`, so older rows never cause a 500. No data migration: production has no API-created RBAC data, the admin RBAC API is barred there, and seed rows are ASCII.
   - ADR-0003 §11 new bullet text: "Display text (app names, role names, permission descriptions; input only) is NFC-normalized, then length-checked, then refused if any code point has Unicode category Cc, Cf, Zl, Zp, Co, Cs or Cn (Python's Unicode database: 15.0 on Python 3.12, so newly assigned characters are refused until Python is upgraded). This is a superset of the USR-02e list. ZWJ (U+200D) is refused, so emoji ZWJ sequences are not allowed; variation selectors are allowed. 422 `validation_error`, error type `display_text`, value not echoed. Stored values are NFC; read paths return stored values unchanged. Consumers render names as plain text and authorize from token claims; the catalog is for display and role → permission mapping only."
5. **AC5: origins.** In `normalize_origin`, replace `host == _API_HOST` with `(host == _API_HOST or host.endswith("." + _API_HOST))` and update the module docstring ("never the API's host or its subdomains").
6. **AC6: Dockerfile.** CMD gets `"--no-proxy-headers"` after the port. Uvicorn then never rewrites `request.client` from `X-Forwarded-*`, so `client_ip()` sees the real peer (cloudflared). Record this in ADR-0002 §2.
7. **Docs.** ADR-0002 and ADR-0003 amendments, a `docs/deploy.md` subsection and an `.env.example` comment, as listed in the file table.
8. Run `make check`. Tests that build staging/production `Settings` get `trusted_proxy_cidrs=["172.18.0.0/16"]`.

Rules: R1 holds (no ORM changes; handlers live in core). R5 holds (env only, and settings errors hide input). R6 is AC3. R7: out-of-scope items are listed in Follow-ups. R8: `make check` plus a test for every item. R9: the envelope is not a breaking change and paths are unchanged. R11: the CORS origin rule is tightened (AC5) and client IP is correct (AC1, AC6). R2 does not apply (no schema change).

## Deploy prerequisite (copy into the PR body)
> **Before releasing the image built from this PR**, the deployment repo (`mmlabs-online/deployment`) must set `TRUSTED_PROXY_CIDRS` for org-admin-api. Otherwise both the `migrate` step and the API refuse to start in production. The deploy then stops at migrate and the old API keeps running, so it is safe but the release is blocked.
>
> - Put it in `apps/org-admin-api/compose.yml` → `x-app.environment`, which both `migrate` and the API read. It is not a secret. Do **not** put it in the env file: `deploy-org-admin-api.yml` rewrites that file with only `DATABASE_URL` on every run, so a value added by hand is lost.
> - The format is a **JSON list in a quoted string**: `TRUSTED_PROXY_CIDRS: '["172.18.0.0/16"]'`. A bare `172.18.0.0/16` fails to parse.
> - Rules in staging/production: private networks only, no `/0`, IPv4 ≥ /8, IPv6 ≥ /48.
> - Value, either:
>   - **now**: the current subnet of the `web` network (`docker network inspect web -f '{{(index .IPAM.Config 0).Subnet}}'`); or
>   - **better** (owner choice): recreate `web` with a pinned subnet, give cloudflared a fixed IP, and trust that `/32`. This needs a short outage for every app on `web`.
> - The current production image already accepts this setting, so the deployment PR can be merged and deployed **before** this release.
> - This PR has no migration. Rolling back to the previous image is safe.
> - The framework 404/405 bodies change from `{"detail": …}` to the R6 envelope. They were never documented, so this is not a v1 breaking change.

## Owner actions (outside this repo)
1. Merge a deployment-repo PR that sets `TRUSTED_PROXY_CIDRS` as above, **before** merging the ADM-011 release PR. Choose the current `web` subnet, or a pinned subnet with a fixed cloudflared IP.
2. Separately: rotate the Cloudflare Tunnel token committed in `deployment/proxy/compose.yml`, move it to a host secret, and pin `cloudflared` (security consult finding 7, urgent).
3. Separately: `TURNSTILE_SECRET_KEY` and `TURNSTILE_ALLOWED_HOSTNAMES` are not set in production, so sign-up fails closed (403) until they are.

## Specialist input used
- **api-platform-engineer**:
  - Adopted:
    - Handler on the Starlette `HTTPException`, with a status→code map, `HTTPStatus.phrase`, `details: null`, headers kept and 204/304 untouched.
    - Limiter protocol kept, with per-scope ordered dicts and a `Literal` scope.
    - `app/core/text.py` with a `PydanticCustomError("display_text")`, NFC before length, and the per-category test list.
    - The size estimate (no split).
  - Answers to its open questions:
    - `max_keys_per_scope` stays a constructor argument, not env (YAGNI).
    - A 405 carries its methods only in the `Allow` header.
    - The OpenAPI 422 schema goes to Follow-ups.
  - Changed: the scope is validated when `rate_limit()` is called. Limits cannot be resolved at build time because settings live on `app.state`.
- **security-architect**:
  - Adopted:
    - The validator follows the Argon2 pattern and its message names only the key.
    - The private / no-/0 / minimum-prefix rules: /8 for IPv4, plus /48 for IPv6, which I chose.
    - The value goes in compose `environment`, not the env file.
    - The JSON-list format is documented.
    - Release-order risk → Deploy prerequisite.
    - NFC → length → category order, input schemas only.
    - `.api.mmlabs.online` refusal; `--no-proxy-headers`; ADR-0002 note on fail-open eviction.
  - Answers to its open questions:
    - Q2: the check applies to both `migrate` and the API, for the reasons in Approach 1.
    - Q3: ZWJ stays rejected, per the orchestrator default; the owner can relax this later.
    - Q1 (subnet choice) and Q4 (tunnel token) are owner actions outside this repo.
  - Added: `hide_input_in_errors`, which neither consult raised. It is needed because AC1 makes a settings refusal fire in production on purpose.
  - Rejected for now: the optional catch-all 500 handler. It was not in the ticket ACs, so it goes to Follow-ups.

## Assumptions
- ZWJ (U+200D) stays rejected. Emoji ZWJ sequences in role and app names are not supported (orchestrator default, matches current behaviour). The owner can ask to allow-list it later.
- `Cn` uses Python 3.12's Unicode 15.0 data. Characters assigned later are refused until Python is upgraded (documented in the ADR).
- The minimum IPv6 prefix is /48. Docker IPv6 networks are normally /64 or /80, so this does not get in the way.
- Staging has no running deployment today. The same rule applies to it when it exists.
- No NFC data migration is needed (see Approach 4).
- Each scope's window length is fixed for the life of the process (it comes from `Settings`).

## Blocking questions
None

## Split proposal
None. About 150–190 production lines, no migration, and one cohesive hardening theme.

## Data model / migration
None. Display-text changes are validation only. Stored columns are unchanged and `alembic check` is unaffected.

## API contract
No new endpoints and no path changes. Behaviour changes on existing responses:

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| any | any unmatched path or method | — | `{"error": {"code": "not_found" \| "method_not_allowed" \| …, "message": <HTTP phrase>, "details": null}}`, `Allow` kept on 405 | 404, 405, other framework 4xx | — |
| POST/PATCH | `/api/v1/apps`, `/api/v1/apps/{key}` (flagged admin RBAC) | `name` is display text | `name` returned as NFC | 422 `validation_error` (type `display_text`) | none until ADM-002 (will need `admin:apps:write`, per USR-02b) |
| POST/PATCH | `/api/v1/apps/{key}/permissions[/…]` | `description` is display text | stored as NFC | 422 as above | same |
| POST/PATCH | `/api/v1/apps/{key}/roles[/…]` | `name` is display text | stored as NFC | 422 as above | same |
| GET | `/api/v1/apps/{key}/roles` (public) | — | unchanged; framework 404 for `%2F` keys now uses the envelope | 200, 304, 404, 429 | public (R3 class 3) |

## Follow-ups (out of scope, R7)
- A catch-all `Exception` handler that returns a 500 `internal_error` envelope (unhandled errors are plain-text "Internal Server Error" today).
- The OpenAPI document still describes 422 as `HTTPValidationError` (`{"detail": …}`). Align it with the R6 envelope.
- Deployment repo: Cloudflare Tunnel token in plain text in `proxy/compose.yml` (rotate it and move it to a host secret), and `cloudflared:latest` is not pinned.
- Deployment repo: pin the `web` subnet and give cloudflared a fixed IP, then trust only that `/32`.
- An attacker rotating IPv6 /64s can still evict live buckets within one scope (fail-open). A shared store with a per-prefix budget is needed before scale-out (ADR-0002).
- Possibly allow-list ZWJ for emoji sequences if the owner wants them.
