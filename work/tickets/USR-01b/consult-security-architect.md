# consult-security-architect — USR-01b

## Findings

1. **Exposure in production would be Critical.** The §6.1 accepted risk ("anyone who can reach the hostname can call the admin endpoints") was taken on 2026-11-10, when endpoints held only org metadata. Signup is now live (USR-01), so `users` holds real PII. Mounted on `api.mmlabs.online`, `/api/v1/users` would let anyone (a) dump every email (paged at 100 per request, no filter needed), and (b) disable any account — a cheap, repeatable lockout/harassment loop (disable → re-enable → disable). Under D16, ADM-002 ships with or after USR-04, several tickets away, so this would not be a short window.
2. **The router is easy to gate.** `create_app()` builds the router list per `Settings`, so a settings-gated mount is trivial and testable. `Settings` already has a "refuse in staging/production" pattern (`_argon2_floor`) to reuse.
3. **"ADM-002 present" can't be detected at runtime in production.** The guard has to be a plain rule: the flag is never allowed in staging/production; ADM-002 then deletes the flag.
4. **The email filter is an enumeration/wildcard surface.** A `contains` filter with unescaped `%`/`_` turns `email=%` into "match all" and `a_` into a broader match. Harmless only if escaped (the AC requires it) and gated.
5. **Disable has no effect yet.** No login/sessions until USR-03, so `status=disabled` only matters once USR-03/USR-03b enforce it.
6. **`openapi.json` stays public in production** (`openapi_url` not disabled); an unmounted router doesn't appear in it. Separate issue.

## Recommendations

- **AC 5 (exposure) — safe without the owner's answer:** `admin_users_api_enabled: bool = False` in `Settings`; mount `users_router` in `create_app` only when true. Add a `model_validator` that raises if it is true and `app_env in {"staging","production"}` (staging is also internet-reachable through the tunnel). `ADMIN_USERS_API_ENABLED=` in `.env.example` (R5). Tests: functional tests build the app with the flag on; one test that the default app returns **404** on `/api/v1/users`; one test that `Settings(app_env="production", admin_users_api_enabled=True)` raises. This fails closed, so the planner should not BLOCK on it. Rejected: "mount whenever `app_env != production`" (fail-open if `APP_ENV` is missing or wrong — the default is `local`) and deferring entirely (wastes finished work and gains nothing over the flag).
- **AC 3 (PII minimisation):** keep `UserRead` exactly as specified (`email_verified_at` and `display_name` are acceptable for support). Never add `password_hash`, session/IP data, or Turnstile/failure counters. `model_config = ConfigDict(from_attributes=True)` with explicit fields — never `model_validate(user.__dict__)`. Test the exact key set.
- **AC 1 (email filter):** escape `\`, `%`, `_` (`ILIKE … ESCAPE '\'` or SQLAlchemy `contains(..., autoescape=True)`); `max_length=254`, plus `min_length=1` so `email=` is rejected or ignored, not "match all" — decide which and test it. No regex. `limit` ≤ 100 (R9).
- **AC 2 (PATCH):** `extra="forbid"`, `status: Literal["active","disabled"]` with no `None`; a no-op change is idempotent (200, no write); the 404 body does not echo the id. Add a code comment and a follow-up: ADM-004 must audit every status change (R4), and USR-03/USR-03b must reject login for disabled users, revoke their device sessions and refuse token issuance.
- **R5/R11 logging:** no request/response bodies; never log the email filter value or the result set; log only the user id and the new status.
- **AC 4 (R3):** declare `admin:users:read` / `admin:users:write` per route and keep them listed in one place so ADM-002 can turn them into `require_permission` mechanically.
- **What ADM-002 must do:** remove `admin_users_api_enabled` and its validator; mount the router unconditionally behind Bearer `aud=admin` + `require_permission`; add 401/403 tests (R8); guard against a staff user disabling themselves or the last `admin:*` holder; record in the release notes that §6.1's accepted risk no longer applies to user data.

## Risks

- A deploy-repo/env typo cannot expose the endpoints in staging/production (the validator stops startup). A `local`/`test` instance exposed through a tunnel would still be open — out of scope.
- Disable without session revocation (until USR-03b) gives a false sense of having blocked someone.
- After ADM-002, the email `contains` search is still a full directory for any `admin:users:read` holder; audit reads of PII later (ADM-004 scope decision).
- Disable/enable can still be used for insider harassment after ADM-002; mitigated by audit (ADM-004) and the last-admin guards.

## Open questions

1. Owner: confirm user PII and the disable action are **excluded** from the §6.1 accepted risk, so the flag stays forbidden in production until ADM-002 (recommended: yes). Update the spec note?
2. Owner: does staging exist and is it public? (Assumed yes, so it is refused too.)
3. Should ADM-004 audit staff **reads** of user PII (list/get), or only mutations?
4. Empty `email=` filter: 422, or treated as absent?
5. Disable `/openapi.json` in production as well? (Follow-up, not USR-01b.)
