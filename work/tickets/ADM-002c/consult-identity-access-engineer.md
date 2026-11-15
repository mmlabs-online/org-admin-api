# consult-identity-access-engineer — ADM-002c

**Findings**
- `register-app` refuses `admin` in two places: the CLI (`_parse_register`, exit 64) and the service (`register()`, reason `admin_app`). `AppService.update` blocks the `admin` app from changing `join_policy`, `status` or `default_role_id`, but it does not yet enforce the `admin:*` check on origin changes (only a comment, line ~272). There is no CLI path to change admin origins.
- How origins are checked: `normalize_origins` applies D19 and caps the list at 20. `allowed_origins()` ignores loopback origins in staging/production. `app_keys_holding_origins(origins, exclude_key)` finds other apps (any status, `admin` included) that hold an origin, so `register-app` already refuses origins held by `admin`.
- `AppContextService.check` compares the server-side `Origin` header with the stored origins. The check is CSRF protection, not authentication: a non-browser client can send any `Origin`. `DynamicCORSMiddleware` covers only `/api/v1/auth/*`, with credentials, using every active app's origins and a 60 s cache. The CLI is a separate process, so its changes take effect only when that cache expires.
- `TokenService.issue` requires `audience == X-MM-App`, a verified email and at least one effective role in the app. For `admin` that means the `grant-super-admin` assignment. `join` stays closed, which is correct.
- After 3 failed logins, login needs a Turnstile token, and the check validates `TURNSTILE_ALLOWED_HOSTNAMES`.

**Recommendations**
1. **Service:** add `AppService.set_admin_origins(origins, *, app_env) -> AdminOriginsResult`. The caller owns the transaction. It should:
   - lock the app with `_locked_app(ADMIN_APP_KEY)`;
   - return a `not_seeded` refusal if the app is missing, and refuse if it is not `active` (that means the data is corrupt; do not repair it here);
   - refuse loopback in staging/production (`loopback_not_allowed`), the same check as `register()`;
   - refuse origins held by another app with `app_keys_holding_origins(claimed, ADMIN_APP_KEY)` (`origin_taken`);
   - write **only** `app.origins`, then `mark_apps_changed()`;
   - do nothing on a set-equal re-run (`unchanged`, no write).
   Every refusal happens before the first write. Reuse `AppRegister`'s origin validator, or a small `AdminOriginsSet` schema with `min_length=1`.
2. **Never changes on `admin`:** `key`, `name`, `join_policy=closed`, `status=active`, `default_role_id=NULL`, `org_mode`, permissions, roles, assignments. Tests should assert these are unchanged after a successful run.
3. **CLI:** `set-admin-origins --origin <o> [--origin …] [--dry-run]`.
   - Use the same `_Parser`, with `allow_abbrev=False`, `add_help=False` and no `--key`.
   - Reuse `_Rollback`, the mapping of service `reason`s to fixed lines, and `MSG_BAD_ORIGIN`, `MSG_LOOPBACK`, `MSG_ORIGIN_TAKEN`, `MSG_NOT_SEEDED`, `MSG_CONFLICT`. Add a `MSG_SET_ORIGINS_UNEXPECTED` line.
   - Exit codes: 0 for updated, unchanged or dry run; 1 for settings, `DB_ECHO`, not seeded, origin taken, conflict or database errors; 64 for usage, a bad origin or loopback in staging/production. These match `register-app`.
   - Output uses a dedicated printer, not `_print_result`, which would print a misleading `member role: missing`:
   ```
   updated app admin            (or: unchanged app admin)
   join_policy: closed
   status: active
   origins: https://admin.mmlabs.online
   + origin https://admin.mmlabs.online
   - origin …
   note: the API applies origin changes within 60 s
   dry-run: rolled back, nothing was saved
   ```
4. **Which origin:** register one dedicated host, `https://admin.mmlabs.online`, reserved for the future admin UI. It must not be the apex domain, any app's host, or any host serving user content. D19 already limits origins to mmlabs hosts, so adding a separate "same-site only" rule is unnecessary.
5. **Getting a token without a UI (runbook):** use curl with a cookie jar. Nothing needs to be hosted at the origin.
   ```
   curl -c jar -b jar -H 'Origin: https://admin.mmlabs.online' -H 'X-MM-App: admin' \
        -H 'Content-Type: application/json' \
        -d '{"email":…,"password":…}' https://api.mmlabs.online/api/v1/auth/login
   curl -c jar -b jar -H 'Origin: https://admin.mmlabs.online' -H 'X-MM-App: admin' \
        -H 'Content-Type: application/json' \
        -d '{"audience":"admin","account_id":"…"}' https://api.mmlabs.online/api/v1/auth/token
   ```
   - Keep the password out of shell history (`read -s`, or `-d @file`), keep `jar` at mode 600, and delete it afterwards.
   - The browser console only works from a page actually served at that origin, so do not document it until the UI exists.
   - Get the password right: after 3 failures curl cannot pass Turnstile.
6. **Non-credentialed admin CORS: not now.** The curl flow does not need it. It belongs to the admin-UI ticket, as Bearer-only CORS on `/api/v1/*` with no credentials and a fixed allow-list of headers (`authorization`, `content-type`). Never widen the credentialed middleware beyond `/api/v1/auth/`.
7. **Tests:** happy path; re-run unchanged with no write; loopback refused in production (exit 64) but accepted locally; origin held by another app refused (exit 1); dry run leaves the database unchanged; invariants hold; no submitted value appears in stderr. Until ADM-004, the workflow run is the audit trail (R4).

**Risks**
- **Device-wide blast radius:** the token endpoint accepts any account signed in on the device. Script running on the admin origin (an XSS, or a takeover of that subdomain's DNS or tunnel) can mint admin tokens for a super-admin who is already signed in. The admin host must stay dedicated and minimal.
- **Origin squatting:** registering the origin now keeps another app from claiming it. The flip side is that if the hostname is later routed to something else in the Cloudflare tunnel, that service inherits admin login. Document that the hostname is reserved.
- **Up to 60 s delay:** CLI changes apply only when the cache expires, so an immediate login may get a 403 `origin_not_allowed`.
- **Fail-open temptation:** never treat an empty origin list as "allow all". The existing code already fails closed.

**Open questions**
1. Should there be a `--clear` break-glass option to empty the admin origins and shut admin access? It is cheap to add, but the ticket says replace semantics with at least one origin. Recommend it as a follow-up unless the owner wants it now.
2. Is the owner OK reserving `admin.mmlabs.online`, and should the deployment ops workflow also get a `set-admin-origins` choice with `dry_run` on by default? That would be a separate owner PR.
3. Should the service also log `admin_origins_changed` with the added and removed counts? Origins are not secret.
