# consult-identity-access-engineer — USR-03c

### Findings
- `AppContextService.check` already returns **403 `origin_not_allowed`** for an unknown or disabled `X-MM-App`. `AppContextDep` runs before the handler, and `audience` must equal `X-MM-App`, so the ticket's `403 app_disabled` can never be reached. This conflicts with the ticket.
- `ResolvedDevice.accounts_created_before` and `repo.get_active_account(..., created_before=)` already cover N1, so the plan only has to pass that value through.
- `AuthzService.effective_roles` does not check user status, email verification or app status. It drops expired roles and roles belonging to other apps. Callers have to do those checks themselves.
- D16: USR-04 is still in the backlog, so no real account has `email_verified_at`. ricotdin cannot get a token until USR-04 ships. Tests have to set `email_verified_at` directly in the database.
- Identity architecture §4 lists claims without `jti` or `typ`. The ticket adds both, and that is fine.

### Recommendations
1. **Check order** (acceptance criteria 2–4):
   1. `AppContextDep` runs first, so a cross-site request can never trigger a rotation or a reuse revocation.
   2. `DeviceDep` (401 and clears the cookie).
   3. Body validation (422; `audience` matches `APP_KEY_RE`; `extra=forbid`).
   4. `audience != app.key` → **403 `audience_mismatch`**.
   5. `org_id` set → **422 `org_not_supported`**.
   6. `get_active_account(device_id, account_id, created_before=accounts_created_before)`; none found → 401 `session_invalid` **without clearing the cookie**, because other accounts on the device are still valid.
   7. `user.status == disabled` → the same 401.
   8. `email_verified_at is None` → 403 `email_unverified`.
   9. `effective_roles(user, app.key)` is empty → 403 `not_member` with `{"join_available": app.join_policy != "closed"}`.
   10. Sign the token and update `last_used_at`.
2. **Claims:**
   - `iss` comes from settings (default `https://api.mmlabs.online`, so staging can use its own value).
   - `aud` is a single string: the app key.
   - `sub` is the user id, never the account-session id.
   - Also `email`, `email_verified: true`, `roles` (sorted list, possibly empty for an admin app), `iat`, `exp = iat + 900`, and `jti` (uuid4).
   - Leave out `nbf`. Leave out `org` until USR-06.
   - Header: `alg: EdDSA`, `kid` (RFC 7638 thumbprint), `typ: at+jwt`.
3. **TTL:** one setting, `ACCESS_TOKEN_TTL_SECONDS=900`, with an upper bound checked by validation. Leave a shorter admin TTL to ADM-002 (open question 1).
4. **Cookie rotation:** `/token` does not force a rotation. It only passes through `DeviceDep`'s rotation every 24 h, sending `Set-Cookie` and committing before the response (ADR-0004 §4). The response also carries `Cache-Control: no-store`.
5. **Verifiers** (ricotdin, `mmlabs-auth`) must:
   - pin `algorithms=["EdDSA"]`
   - look up the key by `kid` in a cached JWKS, refetching on an unknown `kid` at most once per minute
   - check `iss` exactly and `aud == "ricotdin"`
   - require `exp`, `iat`, `sub`, `jti`, with leeway of 60 s or less
   - require `typ == at+jwt`
   - require `email_verified is true`
   - map roles to permissions only through ricotdin's own catalog

   Write this in `docs/deploy.md` or the README of the future `mmlabs-auth` library.
6. **Tests:**
   - **Reference verifier** (PyJWT against the served JWKS):
     - a valid token passes
     - a token with one changed payload byte fails
     - a token from another key with the same `kid` fails
     - `alg:none` fails
     - HS256 signed with the public key fails
     - an expired token fails (use an injected clock)
     - wrong `aud` fails
     - wrong `iss` fails
     - an unknown `kid` fails
   - **Endpoint:**
     - two accounts on one device, plus revoking one while the other still works
     - previous cookie value with an account created after `rotated_at` → 401; with an older account → 200
     - disabled user
     - unverified email
     - `not_member` for each policy: open/approval → `true`, closed → `false`
     - only an expired assignment → `not_member`
     - roles from other apps left out
     - `audience_mismatch`
     - `org_id` → 422
     - missing or invalid `X-MM-App` or `Origin`
     - disabled app
     - no cookie
     - `no-store` header present
     - the rotation path sends `Set-Cookie`
   - **JWKS:**
     - no `d` field
     - 304 on `ETag`
     - `Access-Control-Allow-Origin: *` without the credentials header

### Risks
- **Revocation lag:** a disabled user or a removed role keeps working for up to 15 minutes, and a leaked signing key is only fixed by rotating keys. The runbook must cover both.
- **`email` is in a bearer token** that anyone holding it can read (D7 accepts this). Never log tokens.
- If the account-session 401 reuses `DeviceDep`'s cookie-clearing, it would log the user out of every account on the device. It must not.
- `typ: at+jwt` is not fully RFC 9068 (there is no `client_id` claim). Say so in the ADR.

### Open questions
1. Should `aud=admin` tokens get a shorter TTL (5 min) now, or should ADM-002 decide?
2. Error code names: `audience_mismatch` rather than reusing `origin_not_allowed`, and should `app_disabled` be dropped from the acceptance criteria (owner or planner to confirm)?
3. Should a missing account session use its own code (`account_session_invalid`), so the client re-signs in only that account?
4. Should the D16/USR-04 dependency for ricotdin go in the release notes?
