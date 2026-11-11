# Review — ADM-011

## Attempt 1 — 2026-11-11
Verdict: PASS

### Checks (run by reviewer)
- make check (`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, exit 0): ruff `All checks passed!`; format `178 files already formatted`; mypy `Success: no issues found in 51 source files`; pytest `463 passed in 14.60s`. Matches impl-log (same test count; format count 178 vs 177 logged, see Nits).
- alembic check: not applicable (no models or migrations changed; `git diff --stat` touches no `app/**/models.py` or `migrations/`).

### Acceptance criteria
- [x] AC1 trusted-proxy gate in staging/production (non-empty, private, no /0, IPv4 ≥ /8, IPv6 ≥ /48; local/test unchanged; errors hide input) — evidence: `app/core/config.py:69-86` (`_trusted_proxy_gate`), `app/core/config.py:19` (`hide_input_in_errors=True`); `tests/test_config.py` › `test_trusted_proxy_cidrs_required_in_staging_and_production`, `test_trusted_proxy_cidrs_rules_in_staging_and_production` (0.0.0.0/0, ::/0, 8.8.8.0/24, 10.0.0.0/7, fd00::/40), `test_trusted_proxy_cidrs_valid_in_production`, `test_trusted_proxy_cidrs_optional_in_local_and_test`, `test_settings_errors_do_not_echo_input` — all pass.
- [x] AC2 per-scope stores, cap per scope, O(1) amortised front-pop eviction, unknown scope refused at build — evidence: `app/core/rate_limit.py:29-69` (`_scopes: dict[str, OrderedDict]`, `move_to_end` on new/restarted window, `_evict` pops from the front only, no sort/scan), `app/core/rate_limit.py:71-95` (`Scope` Literal, `_LIMITS`, `ValueError` in `rate_limit()`); call sites unchanged (`auth/router.py:26`, `rbac/catalog_router.py:20`); tests `test_evicts_expired_then_oldest_within_scope`, `test_restarted_window_is_not_oldest`, `test_flooding_one_scope_never_evicts_another`, `test_unknown_scope_refused_at_build` pass, and they no longer read private attributes.
- [x] AC3 framework `HTTPException` → R6 envelope (status→code map, phrase message, `details: null`, headers kept, 204/304 empty, `exc.detail` never read) — evidence: `app/core/errors.py:99-133`, registered at `:133`; tests `test_unknown_path_404_envelope`, `test_http_exception_detail_not_echoed_headers_kept`, `test_unmapped_status_uses_http_error`, `test_304_http_exception_has_empty_body`, `test_post_roles_flag_off_is_405` (envelope + `Allow: GET`), `test_encoded_slash_key_404_envelope` pass. Codes agree with existing `AppError` codes (`not_found`, `permission_denied`, `rate_limited`).
- [x] AC4 display text: NFC → length → category check (Cc, Cf, Zl, Zp, Co, Cs, Cn), 422 type `display_text`, value not echoed, stored NFC, ADR-0003 §11 amended — evidence: `app/core/text.py`, `app/modules/rbac/schemas.py` (`AppName`, `Description`, `RoleName`; `_NO_CONTROL` removed; read schemas untouched); `tests/test_text.py` (one case per category incl. Cs via `TypeAdapter`, NFC, length-after-NFC 400→200 accepted, error not echoed, None passthrough); `tests/test_rbac_apps_api.py` › `test_app_name_control_chars_422` (13 chars), `test_app_name_stored_nfc` (POST + PATCH, response and DB row); `docs/adr/0003-rbac-keys-and-matching.md` §11 + amendment line. New set is a strict superset of the old regex (checked against the removed pattern).
- [x] AC5 (optional) refuse `*.api.mmlabs.online` — evidence: `app/modules/rbac/origins.py:40-45`; `tests/test_rbac_origins.py` rejects `https://x.api.mmlabs.online`, `https://a.b.api.mmlabs.online`, still accepts `https://api2.mmlabs.online`.
- [x] AC6 (optional) `--no-proxy-headers` in Dockerfile CMD — evidence: `Dockerfile:18`; `tests/test_dockerfile.py` › `test_uvicorn_cmd_disables_proxy_headers`; ADR-0002 §2 amended.
- [x] AC7 `make check` green — evidence above. The PR body must carry the plan's **Deploy prerequisite** section (orchestrator step at PR time; not verifiable in the diff).
- [x] Tests for each item — see above.

### Issues
None.

### Scope
Every changed file is in the plan's file table, except `tests/test_rbac_roles_api.py` (one assertion now expects NFC), which is justified under impl-log `Out-of-plan files` and is a direct consequence of AC4. `work/` changes are workflow bookkeeping.

### Rules R1–R11
No violations found. R1: handlers live in core, no ORM in routers. R2: no schema change. R3: no new endpoints. R5: settings errors hide input (`hide_input_in_errors`), validator messages name only the key, `.env.example` lists the key with no value. R6: this ticket closes the framework-error gap. R8: tests for every AC. R9: no path changes; the 404/405 body change aligns undocumented framework bodies with R6. R11: CORS origin rule tightened, client IP no longer rewritten by uvicorn.

### Nits (do not block)
- impl-log records `177 files already formatted`; the reviewer run shows 178. Lint, mypy and pytest results (463 passed) match, so this is probably a count taken before the last file was added.
- `InMemoryRateLimiter(max_keys_per_scope=0)` would evict the key it just inserted; a `max_keys_per_scope >= 1` assertion in `__init__` would make the comment at `app/core/rate_limit.py:63` enforceable. It is a constructor-only argument, so this does not matter in practice.
- `_HTTP_CODES` uses `permission_denied` for 403, which matches `PermissionDeniedError`; consider exporting one shared map later if more framework codes appear.

### Follow-ups (out of scope)
- As listed in the plan: catch-all 500 `internal_error` envelope; OpenAPI 422 schema still `HTTPValidationError`; deployment repo: rotate the committed Cloudflare Tunnel token and pin `cloudflared`; pin the `web` subnet / fixed cloudflared IP; IPv6 /64 rotation can still evict live buckets within one scope until a shared store exists; optional ZWJ allow-list.
- Owner action before release: set `TRUSTED_PROXY_CIDRS` in the deployment repo's `apps/org-admin-api/compose.yml` (`x-app.environment`, JSON list), or the migrate step and the API refuse to start in production.
