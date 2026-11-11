# Implementation log — ADM-011

## Attempt 1 — 2026-11-11

### Files touched
| File | Required because |
|---|---|
| `app/core/config.py` | AC1: `hide_input_in_errors=True`; `_trusted_proxy_gate` model validator (staging/production: non-empty, private, no `/0`, IPv4 ≥ /8, IPv6 ≥ /48; messages name only the key) |
| `app/core/rate_limit.py` | AC2: per-scope `OrderedDict` stores, `max_keys_per_scope`, front-pop O(1) amortised eviction; `Scope` Literal + `_LIMITS` table; `rate_limit()` refuses unknown scopes at build time |
| `app/core/errors.py` | AC3: `_http_exception_handler` for Starlette `HTTPException` (status→code map, `HTTPStatus.phrase`, `details: null`, headers kept, 204/304 empty body, `exc.detail` never read) |
| `app/core/text.py` (new) | AC4: `DISALLOWED_CATEGORIES`, `nfc_if_str`, `reject_disallowed_categories` (`PydanticCustomError("display_text")`) |
| `app/modules/rbac/schemas.py` | AC4: `AppName`, `RoleName`, `Description` = NFC before-validator → `Field` length → category after-validator; `_NO_CONTROL` removed |
| `app/modules/rbac/origins.py` | AC5: refuse hosts under `.api.mmlabs.online`; docstring updated |
| `Dockerfile` | AC6: `--no-proxy-headers` in uvicorn CMD |
| `.env.example` | AC1: comment on `TRUSTED_PROXY_CIDRS` format/requirement (no value) |
| `docs/deploy.md` | AC1: new "Required runtime config" section |
| `docs/adr/0002-identity-abuse-controls-runtime.md` | Amended §1 (per-scope stores, O(1) eviction, fail-open note), §2 (startup refusal, `--no-proxy-headers`), Consequences |
| `docs/adr/0003-rbac-keys-and-matching.md` | Amended §11 display-text bullet (plan text) |
| `tests/test_config.py` | AC1 tests; CIDR kwarg in existing staging/production test |
| `tests/test_users_admin.py`, `tests/test_health.py`, `tests/test_turnstile.py` | CIDR kwarg so existing production Settings stay valid |
| `tests/test_rate_limit.py` | Replaced `test_evicts_expired_and_caps_keys` (read `_windows`); AC2 tests |
| `tests/test_errors.py` | AC3 tests |
| `tests/test_role_catalog_api.py` | Extended 405 test (envelope + `Allow`), `%2F` 404 envelope test |
| `tests/test_text.py` (new) | AC4 unit tests |
| `tests/test_rbac_apps_api.py` | Wider `FORMAT_CHARS`; `test_app_name_stored_nfc` (POST + PATCH, response and DB row) |
| `tests/test_rbac_origins.py` | AC5 rejected cases + `https://api2.mmlabs.online` still accepted |
| `tests/test_dockerfile.py` (new) | AC6 |
| `tests/test_rbac_roles_api.py` | Out of plan — see below |

### Tests added / changed
- `tests/test_config.py` › `test_trusted_proxy_cidrs_required_in_staging_and_production`, `test_trusted_proxy_cidrs_rules_in_staging_and_production`, `test_trusted_proxy_cidrs_valid_in_production`, `test_trusted_proxy_cidrs_optional_in_local_and_test`, `test_settings_errors_do_not_echo_input`; `test_rbac_flag_refused_in_staging_and_production` (CIDR kwarg)
- `tests/test_rate_limit.py` › `test_evicts_expired_then_oldest_within_scope`, `test_restarted_window_is_not_oldest`, `test_flooding_one_scope_never_evicts_another`, `test_unknown_scope_refused_at_build` (removed `test_evicts_expired_and_caps_keys`)
- `tests/test_errors.py` › `test_unknown_path_404_envelope`, `test_http_exception_detail_not_echoed_headers_kept`, `test_unmapped_status_uses_http_error`, `test_304_http_exception_has_empty_body`
- `tests/test_role_catalog_api.py` › `test_post_roles_flag_off_is_405` (extended), `test_encoded_slash_key_404_envelope`
- `tests/test_text.py` › `test_rejects_category` (Cc ×2, Cf ×3, Zl, Zp, Co, Cs, Cn), `test_accepts_display_text`, `test_nfc_normalized`, `test_length_checked_after_nfc`, `test_error_does_not_echo_value`, `test_none_passes_through`
- `tests/test_rbac_apps_api.py` › `test_app_name_control_chars_422` (wider list), `test_app_name_stored_nfc`
- `tests/test_rbac_origins.py` › new rejected/accepted params
- `tests/test_dockerfile.py` › `test_uvicorn_cmd_disables_proxy_headers`
- `tests/test_rbac_roles_api.py` › `test_vietnamese_names_accepted` (expects NFC)
- `tests/test_users_admin.py`, `tests/test_health.py`, `tests/test_turnstile.py` (CIDR kwarg only)

### Checks (actual output summary)
- Postgres: already running locally (`pg_isready` → accepting connections); nothing started.
- make check (`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`): ruff `All checks passed!`; format `177 files already formatted`; mypy `Success: no issues found in 51 source files`; pytest `463 passed in 15.54s`.
- alembic check: not applicable (no schema change; runs in pytest's migration fixture only).
- Confirmed pydantic applies `BeforeValidator` before the `Field` length constraint: `test_length_checked_after_nfc` passes (400 NFD code points → 200 NFC accepted for max 255), so the length check stays in `Field` as planned.

### Out-of-plan files
- `tests/test_rbac_roles_api.py` — `test_vietnamese_names_accepted` asserted the NFD input round-trips unchanged; AC4 stores NFC by design, so the assertion now compares to `unicodedata.normalize("NFC", name)`. One line + import.

### Follow-ups (noticed, not done)
- Same as plan follow-ups (catch-all 500 handler, OpenAPI 422 schema, deployment-repo tunnel token / cloudflared pin, pinned `web` subnet, IPv6 /64 rotation within one scope, optional ZWJ allow-list).
- `ValidationError.__str__` from a direct `TypeAdapter` call still includes `input_value`; the API never exposes it (the validation handler keeps only type/loc/msg), so the test checks `msg` and `errors(include_input=False)`.

### Scope self-check
- Could the diff be smaller? No.
- Diff size: +382 / -65 lines in tracked files, plus new files `app/core/text.py` (21), `tests/test_text.py` (70), `tests/test_dockerfile.py` (11). Production code ≈ +150.
