# consult-identity-access-engineer — ADM-002d

## Findings (identity-access-engineer, ADM-002d)

1. **strict_aud.** `app/core/access_token.py:51` passes `options={"require": REQUIRED_CLAIMS}` with no `strict_aud`. PyJWT 2.15.1 is locked, so `strict_aud` is available (it was added in 2.7). The server only mints string `aud` (`app/modules/tokens/service.py:88`), so no token we issue today changes behaviour. The sample at `docs/consumers.md:256-264` and checklist item 4 (`:223`), `tests/jwt_verifier.py:20` and the `mmlabs-auth` bullet (`docs/identity-architecture.md:221`) need the same option.
2. **Admin permissions.** Seed `5e2a9c7d1f04` uses `ON CONFLICT DO NOTHING`, so admin-app rows that already existed keep whatever `is_admin` they had. On create, `PermissionService._create` (`rbac/service.py:697-702`) takes `is_admin` from the request even for the admin app (it requires super at :703). Update has the same gap: at `:736` a super-admin can flip an admin-app permission to `is_admin=false`. The admin app never has a default role (invariant at `:377-382`), so the backfill cannot trip the default-role or G3 guards.
3. **`set_admin_origins`** (`rbac/service.py:606-616`) runs the drift check unconditionally, so a drifted row (for example `org_mode` changed through PATCH, which item 8 is about) blocks the break-glass `--clear`. The wording to update is in ADR-0008:61-63 and `deploy.md:492,501,504`.
4. **Runbook** (`deploy.md:412-437`). Step 7 has the operator paste `Bearer <access_token>` onto the command line, which puts it in history and `ps`. The token response is printed to stdout. There is no logout step, and the jar is deleted while the device session stays live server-side. `JWT_PUBLIC_KEYS_EXTRA` (`deploy.md:95-97`) is also trusted by `verify_access_token` (any `kid` in `key_set`), so any key placed there can mint admin tokens.
5. **Admin context on signup/forgot.** `signup` (`auth/router.py:74-76`) and `forgot_password` (`:334-339`) take the generic `AppContextDep` (`auth/deps.py:77`), so `X-MM-App: admin` is accepted once admin origins are set. Join already refuses admin (`membership/service.py:42`).
6. **`APP_ENV`.** `config.py:28` defaults it to `"local"`. Every production gate keys off `app_env`, so a missing value silently skips all of them. **Local `docker-compose.yml` `api` service sets no `APP_ENV` and its DB host is `db`, not localhost.** CI uses localhost (`ci.yml:23,35`). I can't see the deployment repo from here: `deploy.md:301` says `APP_ENV=production` is set for `migrate`, but nothing in this repo shows the API service and the ops workflow get it too.
7. **Disable.** `UserService.set_status` (`users/service.py:78-80`) revokes sessions but leaves email tokens alone. `EmailTokenRepository.invalidate(user_id, purpose)` (`auth/repository.py:113`) already exists, one purpose per call.
8. **Admin-app invariant.** `AppService._update` (`rbac/service.py:377-382`) pins status, join_policy and default_role, but not `org_mode`.
9. **`build_default_role`.** `actor` (`rbac/service.py:245-247`) is never used and nothing audits it. Only tests call the method (`test_rbac_default_role.py`, `test_rbac_locking.py:129`), and none of them pass `actor`.
10. **Request id.** `request_id.py:35` stores the inbound `X-Request-ID` from a trusted peer. Cloudflare forwards client headers, so a client controls the stored value. The DB CHECK `REQUEST_ID_RE` (`audit/models.py:18`) also matches the CF-Ray format.

## Recommendations

1. Add `"strict_aud": True` to the options at `:51`. Test that `["admin"]` and `["stoktok","admin"]` both get 401 `audience`, through `verify_access_token` and through a Bearer call. Mirror the change in `tests/jwt_verifier.py`, the consumers sample and checklist ("`aud` must be a string equal to…"), and the mmlabs-auth bullet.
2. Write a new data migration: `UPDATE permissions SET is_admin = true WHERE app_id = '…a1' AND NOT is_admin`, with a no-op `downgrade()` and a docstring that names the R2 exception and says no audit row is written. In the service, force `is_admin = True` when `app.key == ADMIN_APP_KEY` in `_create`. Refuse `is_admin=false` in `update` for admin-app permissions with 409/422, using the same shape as `_wildcard_must_be_admin`. Test the migration with the existing `test_audit_migrations` pattern.
3. In `set_admin_origins`, keep `lock_app` and the `app is None` refusal. Run the four drift predicates only `if origins:`. The write stays origins-only. Add a test: drifted `org_mode` + `--clear` → UPDATED, and the other columns are unchanged.
4. Docs only:
   - `( umask 077; tmp=$(mktemp -d); trap 'curl … /auth/logout …; rm -rf "$tmp"' EXIT; … )`.
   - Extract the token into a 0600 header file with `python3 -c` (jq may be missing on the Mac), then call with `-H @"$tmp/auth"`.
   - Never echo the token.
   - Add one line saying `JWT_PUBLIC_KEYS_EXTRA` holds only this service's own previous or next keys.
5. Add a dependency `get_user_app_context` that wraps `get_app_context`. When `app.key == ADMIN_APP_KEY` it raises `OriginNotAllowedError` (an identical 403, no oracle), before Turnstile and the service run. Use it on signup and forgot only; keep cookie routes unchanged. Test both routes.
6. Add a model validator: if `"app_env" not in self.model_fields_set` and the `database_url` host is not in {localhost, 127.0.0.1, ::1}, raise `ValueError("APP_ENV must be set …")`. Use the same `model_fields_set` pattern as the `jwt_issuer` gate (`config.py:177`). Add `APP_ENV: local` to the `docker-compose.yml` `api` service and update `deploy.md`. Tests: unset + remote host → refuse; explicit `local` + remote → OK; unset + localhost → OK.
7. After the status flush, call `invalidate(user.id, <reset>)` and `invalidate(user.id, <verify>)`, using the purpose constants. Test: disable, re-enable, then the old reset token is rejected.
8. Add `or ("org_mode" in fields and data.org_mode != OrgMode.NONE)` to the condition at `:377-382` and update the message. Test.
9. Remove the parameter. That is the smallest change, and nothing passes it.
10. Always generate UUID4 and echo the same value. Drop the use of the inbound header, so the "echoed id == stored id" contract in `deploy.md:658` stays true. Don't adopt CF-Ray: it needs extra trust logic and the correlation gain is small.

## Risks

- **Item 6 is the riskiest.** If the deployment compose or the ops workflow lacks `APP_ENV`, both `migrate` and the API refuse to start, and so does the CLI. Local `--profile full` breaks without the compose edit. Prefer the refuse variant only after the planner confirms `APP_ENV` in the deployment-repo compose for both services. Otherwise ship warn-only first.
- **Item 1.** This has zero runtime effect on our own tokens. External consumers that copied the old sample keep the weaker check until they update, which is a docs follow-up and not breaking.
- **Item 10** changes the semantics of stored request ids. Rows written before the change may hold client-supplied ids. ADR-0009 needs an amendment rather than a rewrite. Any test that asserts the inbound header is echoed must flip.
- **Item 2.** The update refusal is a behaviour change for supers. The migration is irreversible by design.
- **Item 3.** It widens a break-glass path. That is acceptable only because the command writes origins and nothing else.

## Open questions

- Is `APP_ENV=production` confirmed on both the `org-admin-api` and `migrate` services in `mmlabs-online/deployment`? Refuse or warn?
- Item 10: always generate, or CF-Ray? I recommend always generate.
- Item 2: is a 409 on admin-app `is_admin=false` acceptable, or should the value just be silently forced on create?

**Split:** yes. ADM-002d (code) takes items 1, 2, 3, 5, 7, 8, 9, 10, at roughly 150-250 production lines plus tests. ADM-002e (ops/docs) takes items 4 and 6, because item 6 is coupled to a deployment-repo change and needs owner confirmation first.
