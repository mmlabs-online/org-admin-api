# consult-api-platform-engineer — ADM-011

## Findings
- `register_error_handlers` (`app/core/errors.py`) handles only `AppError` and `RequestValidationError`. Starlette's own 404s and 405s use the default `{"detail": …}` body. The 405 already carries `Allow: GET` through `exc.headers`. Only `tests/test_role_catalog_api.py:222` covers a framework error, and it checks the status code only. No test checks for a `{"detail": …}` body, so changing the handler breaks no test.
- The limiter keeps one `_windows` dict shared by every scope, with a 10,000-key cap. When it is full, `_evict` scans and sorts the whole dict, which is O(n log n) on every insert. That means a burst of new role-catalog keys can evict signup buckets.
- `_limit_for` raises `KeyError` at request time if a scope name is unknown. The `rate_limit(scope)` call sites are `auth/router.py:26` and `rbac/catalog_router.py:20`.
- `_NO_CONTROL` is a regex. When it fails, Pydantic's message includes the whole pattern. No text is NFC-normalized.
- The `Settings` validators already follow a "refuse in staging/production" pattern (`_argon2_floor`, `_admin_apis_gate`), so a check for an empty `TRUSTED_PROXY_CIDRS` fits there.

## Recommendations
1. **Error envelope (AC3, R6).** Register a handler on `starlette.exceptions.HTTPException`, which also covers FastAPI's subclass.
   - Map status codes to fixed codes: 400 `bad_request`, 401 `unauthorized`, 403 `permission_denied`, 404 `not_found`, 405 `method_not_allowed`, 406 `not_acceptable`, 413 `payload_too_large`, 415 `unsupported_media_type`, 429 `rate_limited`. Anything else gets `http_error`.
   - Set `message` to `HTTPStatus(code).phrase` and never use `exc.detail`. Set `details: null`.
   - Pass `exc.headers` through so `Allow` survives. Keep Starlette's empty body for 204 and 304.
   - The `AppError` handler is a separate class and is not affected.
   - **R9:** I don't count this as a breaking change. The framework bodies were never documented, R6 requires the envelope, and the change fixes non-compliant responses. Record it in the PR or release note.
   - Tests: extend the existing 405 test to check the envelope and `Allow: GET`. Add an unknown-path 404 test and a `%2F`-key 404 test.
2. **Limiter (AC2).** Keep the `RateLimiter.hit(scope, key, limit)` protocol and the `rate_limit(scope)` signature, so no call site changes.
   - Internally, use one insertion-ordered dict per scope, capped by `max_keys_per_scope` (default 10_000). When a bucket starts a new window, delete it and re-insert it. Each scope has a constant window and the clock is monotonic, so insertion order equals window-end order. Eviction then pops expired or oldest entries from the front, which is O(1) amortised.
   - Type `scope` as `Literal["signup", "role_catalog"]` and check the limit when the dependency is built, so a typo fails at startup instead of returning a 500.
   - Rewrite `test_evicts_expired_and_caps_keys`, which reads `_windows` directly. Add a test showing that filling scope A never evicts a bucket in scope B.
3. **Display text (AC4).** Put it in `app/core/text.py`, since future org and user display names will reuse it.
   - Shape: `DisplayText = Annotated[str, BeforeValidator(nfc_if_str), AfterValidator(reject_categories)]`, combined with each type's `Field(min_length, max_length)`.
   - Normalize first so the length limits apply to the stored form. NFC can change the length.
   - Raise `PydanticCustomError("display_text", "contains a disallowed character")`. The message has no character and no regex, and the `type` is stable.
   - Replace `_NO_CONTROL` in `AppName`, `RoleName` and `Description`.
   - Tests: one case per category (`\x00`, `\x85`, U+00AD, U+E0041, U+2028, U+2029, U+E000, U+D800 via direct `model_validate`, U+0378). Accept Vietnamese, emoji and spaces. Check that decomposed `"Quản"` is stored as NFC (through the API and the DB). Check that the 422 body does not contain the rejected character.
4. **AC1.** Add a `model_validator` that raises `"TRUSTED_PROXY_CIDRS must be set in staging/production"`. Existing staging/production `Settings(...)` tests must then pass a CIDR.
5. **Size.** About 130–180 production lines (errors ~30, limiter ~±40, text ~30, schemas ~10, config ~10, origins ~10, Dockerfile 1) plus about 250 test lines. It fits in one ticket under ~400, so I see no need to split.

## Risks
- Rejecting `Cn` depends on Python 3.12's Unicode 15.0 data, so characters assigned in later Unicode versions are refused until Python is upgraded.
- `Cf` includes ZWJ and ZWNJ, which emoji sequences and Persian/Indic text need. The current regex already rejects them, so nothing gets worse.
- An attacker rotating through IPv6 /64s can still evict throttled buckets within the same scope. Bucketing by /64 limits this but does not remove it.
- The framework 404 message ("Not Found") differs from the catalog's "App not found". The security review rated this harmless.
- Unhandled 500s still return plain-text "Internal Server Error". That is outside this ticket; add it as a follow-up.

## Open questions
- Should `max_keys_per_scope` be configurable from env, or stay a constant?
- Should a 405 put the allowed methods in `details`, or only in the `Allow` header? I recommend the header only.
- FastAPI's generated OpenAPI still documents 422 as `{"detail": …}` (`HTTPValidationError`). Should that be fixed now or as a follow-up? I recommend a follow-up (R7).
