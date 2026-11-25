# Consult — api-platform-engineer — USR-33 (2026-11-25)

**USR-33 api consult** (read-only; not exhaustive: I did not read docs/consumers.md or docs/spec-v1.md §5.8)

**Findings**
- Cookie CORS allows only GET/POST (`app/core/cors.py:36`). Staff CORS allows PATCH/PUT/DELETE (`cors.py:44`). A cookie-class PATCH would fail in browsers, so self-service has to be a POST.
- `AccountRead.display_name` is already exposed on the cookie route `GET /api/v1/auth/accounts` (`auth/schemas.py:86-90`). It is the natural read-after-write surface.
- The auth router has no per-account sub-resource routes. Existing cookie routes put `account_id` in the body: `LogoutRequest` (`schemas.py:50`) and `ResendVerificationRequest` (`:69`).
- The staff PATCH `/api/v1/users/{user_id}` already exists, but only for status (`users/router.py:53-71`). It uses `admin:users:write` plus a conditional super-admin rule. `UserStatusUpdate` has `extra="forbid"` and rejects null (`users/schemas.py:23-31`). `UserRead` already carries `display_name` (`:18`).
- `DisplayName` in `usage/schemas.py:47-52` is the proven NFC → trim ≤100 → disallowed-category chain. It is local to usage. Its `PydanticCustomError` doesn't echo the value (`core/text.py:20`), and `errors.py:215` strips `input` and `ctx` from 422s.
- `users/models.py` has no `version` column. `updated_at` is the only candidate for an ETag. Other modules use `parse_if_match` and `version_etag` (e.g. `organizations/router.py:140`).
- Signup (`auth/router.py:69`) takes email, password and turnstile only.

**Recommendations**
1. **Self-service:** `POST /api/v1/auth/accounts/{account_id}/profile`, cookie class, 200 with `AccountRead`. `account_id` is the device-session id, consistent with ADR-0004 and R3(2). Body `{"display_name": <str|null>}` with `extra="forbid"`. The field is required, with no default, so `{}` is 422. Explicit null clears it. Document the POST-as-update exception in R9. Per R11, check Origin and `X-MM-App`. Another device's `account_id` returns 404. Do not require a verified email, because setting a name is harmless.
2. **Staff:** extend the existing `PATCH /api/v1/users/{user_id}` rather than adding a route. Use a new `UserUpdate` with `display_name` and `status`, both optional, `extra="forbid"`. At least one field must be present, and explicit null is allowed only on `display_name`. Keeping `UserStatusUpdate` unchanged for status preserves the USR-01b contract. The super-admin conditional applies only when `status` is present. A name change needs `admin:users:write` and no super check.
   - Alternative: a separate `PUT /api/v1/users/{user_id}/display-name`, which is cleaner for permissions. It is less consistent with R9.
3. **Shared type:** move `DisplayName` to `app/core/text.py`. Reuse it in usage and users, with no behavior change.
4. **Clearing:** treat a blank or whitespace-only string as 422, and clear only through null. The 422 code is `display_text` with no echo. Add a 422 test asserting the response body contains no submitted value.
5. **If-Match:** make it optional on the staff PATCH, with a 412 response. Without a version column, derive the ETag from `updated_at`. Self-service needs none (last write wins). Low value, so I'd skip it unless Ming wants it.
6. **Signup:** do not add an optional name. It enlarges the unauthenticated surface and Turnstile payload, and the user can set a name right after login.
7. **Audit and log:** record ids and changed field names, never the value. In the audit diff, whitelist `display_name` as changed with no value (`audit/diff.py` has per-entity field tuples).
8. **Docs and registry:** update `tests/test_route_auth_classes.py`, `docs/consumers.md` (`name` claim lag ≤ TTL) and the OpenAPI summaries. Add an ADR-0005 note on lag.

**Risks**
- A user can set a misleading name (impersonation), since there is no uniqueness. Apps must render it as text and never trust it for identity. The `UNTRUSTED` description convention is already used (`bug_reports/schemas.py:57`).
- A DB CHECK would be a High-path migration. A name already in the DB could violate it, so the migration needs a data check first.
- Widening the PATCH body changes semantics for existing clients only additively, so v1 stays compatible.

**Open questions for Ming (defaults)**
1. Self-service and staff both, or one? Default: both.
2. Signup name? Default: no.
3. Add a DB CHECK on length? Default: no (Pydantic only). Bounded by the String(100) column.
4. If-Match on staff PATCH? Default: optional, ETag from `updated_at`.
5. Can staff clear a user's name, for example an abusive one? Default: yes, audited.
