Status: READY

# Plan — ADM-002d: Admin auth hardening and runbook fixes

## Spec summary
This ticket adds no new feature. It hardens what these spec lines already require:

- §5.2: "Only authenticated mmlabs staff can call `/api/v1/*`."
- §5.3: "One model for every mmlabs app, the admin API included (app `admin`) … a default role `<app>:member` holding all its permissions except those flagged `is_admin` … `admin` = `closed`".
- §5.4: "Append-only record of every mutating admin action: actor, action, target type/id, before/after (or diff), timestamp, request id."
- §5.8 "Verification by apps": "apps verify signature, `iss`, `aud` and expiry locally … Backends use `mmlabs-auth` (Python)."
- §5.8 "Email flows": "email verification and password reset by one-time, expiring links".
- §5.8 "Admin": "staff can list, look up, disable/enable users …"
- §6: "No secrets in code or logs; config via env only."

Ticket scope: items 1–10 of `work/tickets/ADM-002d/ticket.md`, which come from the ADM-002, ADM-002b, ADM-002c and ADM-004b/c security reviews. All 10 stay in this ticket (orchestrator decision; see Split proposal).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | `verify_access_token` rejects a list-valued `aud`, including `["admin"]` and `["stoktok","admin"]`, with reason `audience` (`strict_aud`). The reference verifier, the consumers sample, the consumers checklist and the `mmlabs-auth` bullet say the same thing. | `test_access_token.py › test_list_audience_rejected[…]`; `test_admin_auth.py › test_rejected_tokens_401[list_aud]` and `[list_aud_multi]`; `test_token_verification.py › test_reference_rejects_list_audience`. Read `docs/consumers.md` (checklist item 4 + sketch) and `docs/identity-architecture.md` (`mmlabs-auth` bullet). |
| 2 | A data migration sets `is_admin = true` on every `admin`-app permission. Its `downgrade()` is a documented no-op (R2 exception, no audit row). Admin-app permission create stores `is_admin=true` when it is omitted or null, and returns 422 `admin_app_permission_must_be_admin` on an explicit `false`. PATCH `is_admin:false` on an admin-app permission returns the same 422. | `test_rbac_migrations.py › test_admin_permissions_backfill`; `test_rbac_permissions_api.py › test_create_admin_app_permission_forced_admin[…]`, `test_create_admin_app_permission_not_admin_422`, `test_patch_admin_app_permission_not_admin_422`; `alembic check` clean; `alembic downgrade -1 && alembic upgrade head` succeeds. |
| 3 | `set-admin-origins --clear` works on a drifted admin row. Only the drift check is skipped, and only when origins is empty. The app lock, the missing-row refusal, the origins-only write and the audit row all stay. Non-empty origins on a drifted row are still refused. | `test_cli_set_admin_origins.py › test_service_clear_on_drift[…]`, `test_service_clear_on_drift_already_empty_unchanged`, `test_service_refuses_missing_admin_on_clear`, `test_cli_clear_on_drift_exit_0`. The existing `test_service_refuses_drift[…]` and `test_cli_drift_exit_1` stay green. Read the ADR-0008 amendment and the `deploy.md` wording. |
| 4 | The `deploy.md` admin-token runbook is a POSIX `sh` subshell. It uses `umask 077` and `trap` cleanup, reads the password with `stty -echo`, builds JSON with python3, keeps the token only in a 0600 header file used via `-H @file`, never puts a secret in argv, history or stdout, and logs out on exit. It notes that the access token lives until its TTL, and that `JWT_PUBLIC_KEYS_EXTRA` holds only this service's own keys. | Reviewer reads the runbook, extracts the block and runs `sh -n` on it (`dash -n` if available, plus `shellcheck -s sh` if installed). Checks: no `read -p`/`-s`, no `Bearer` literal on a command line, logout in the EXIT trap, the TTL sentence, and the `JWT_PUBLIC_KEYS_EXTRA` sentence. |
| 5 | `X-MM-App: admin` is refused on signup, password forgot and verify-email resend. The response is the same 403 `origin_not_allowed` envelope as an unregistered app. It is sent before Turnstile, before any write and before any email. Login, token, logout, verify-email and reset are unchanged. Join on admin is still refused. | `test_auth_app_context.py › test_admin_context_refused_on_mail_routes[signup|forgot|resend]`, `test_admin_context_refusal_matches_unknown_app`, `test_admin_context_refused_even_if_link_origin_overridden`. Existing `test_admin_auth.py` login/token-for-admin tests and `test_auth_join.py › test_join_admin_refused_even_if_open` stay green. |
| 6 | `Settings` refuses to build when `APP_ENV` is not set explicitly and any `DATABASE_URL` host is not loopback. An empty `APP_ENV=` counts as unset. Local compose sets `APP_ENV: local`. The error never echoes the DSN. | `test_config.py › test_app_env_required_for_remote_db[…]`, `test_app_env_default_ok_for_loopback[…]`, `test_app_env_explicit_ok_for_remote_db[…]`, `test_app_env_from_env_counts_as_explicit`, `test_app_env_empty_counts_as_unset`, `test_app_env_error_does_not_echo_dsn`. `docker-compose.yml` diff. Release smoke test unaffected (it runs without `DATABASE_URL`, so the default localhost applies). |
| 7 | Disabling a user marks all of their unused reset and verify email tokens as used, in the same transaction. After re-enable, an old link is rejected with `token_invalid`. | `test_users_admin.py › test_disable_invalidates_email_tokens`, `test_disable_leaves_other_users_tokens`. |
| 8 | PATCH `org_mode` ≠ `none` on the admin app → 409. `org_mode=none` (same value) → 200. | `test_rbac_apps_api.py › test_admin_app_locked_fields_409[org_mode]` (new param) and `test_admin_app_same_value_patch_ok` (body gains `org_mode: none`). |
| 9 | `build_default_role` has no `actor` parameter. | `test_rbac_default_role.py › test_build_default_role_has_no_actor_param`; mypy strict green; the existing callers are unchanged. |
| 10 | The request id is always a fresh UUID4, stored and echoed. An inbound `X-Request-ID` is ignored from any peer, and the trusted-peer branch is removed. ADR-0009 gets an amendment, and `deploy.md` notes that older rows may hold client-supplied ids. | `test_request_id.py › test_inbound_ignored[trusted|untrusted]`, `test_same_inbound_gives_distinct_ids`, `test_echoed_on_error_response`, `test_echoed_on_health`; `test_audit_coverage.py › test_row_carries_request_context` and `test_auth_join.py › test_join_writes_audit_row` (flipped). Read ADR-0009 and `deploy.md`. |
| 11 | `make check` is green. | `make check` |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/core/access_token.py` | `options={"require": REQUIRED_CLAIMS, "strict_aud": True}`. Docstring: a list `aud` → `audience` | Item 1 |
| `tests/jwt_verifier.py` | Same `strict_aud: True` in the reference verifier | Item 1 (mirrors the consumers sample) |
| `docs/consumers.md` | Checklist item 4: "`aud` must be a **string** equal to `"ricotdin"` (a list is rejected; PyJWT `strict_aud=True`)". The sketch gains `"strict_aud": True` in `options`. Item 9 (`PyJWT[crypto]>=2.10.1`) already covers `strict_aud`, so add "(also needed for `strict_aud`)" | Item 1 |
| `docs/identity-architecture.md` | `mmlabs-auth` bullet: "JWT verification (`iss`, string-only `aud` via `strict_aud`, `exp`, `kid`)" | Item 1 |
| `migrations/versions/20261115_a7c3e5f19b42_admin_permissions_is_admin.py` (new) | Data migration, `down_revision = "e91b6c3d58a2"` (current head) | Item 2 |
| `app/modules/rbac/service.py` | (2) Admin-app `is_admin` in `PermissionService._create` and `update`, plus a new `_admin_app_permission_must_be_admin()` helper. (3) `set_admin_origins` drift check only `if origins`. (8) Pin `org_mode` in `AppService._update`. (9) Drop `actor` from `build_default_role` | Items 2, 3, 8, 9 |
| `app/modules/auth/deps.py` | New `get_mail_link_app_context` + `MailLinkAppContextDep`. `get_link_origin` depends on it | Item 5 |
| `app/modules/auth/router.py` | `signup`, `forgot_password`, `resend_verification`: route-level `_app: MailLinkAppContextDep` replaces `AppContextDep`. Docstrings get one line about the refusal | Item 5 |
| `app/core/config.py` | New `_app_env_explicit_gate` model validator + a loopback host set | Item 6 |
| `docker-compose.yml` | `api.environment.APP_ENV: local` | Item 6 (the `full` profile uses DB host `db`) |
| `.env.example` | Comment above `APP_ENV=`: "required (explicitly) when DATABASE_URL is not localhost". No value change | Item 6, R5 |
| `app/modules/auth/repository.py` | `EmailTokenRepository.invalidate_all(user_id)`: one UPDATE over all purposes where `used_at IS NULL` | Item 7 |
| `app/modules/users/service.py` | In `set_status`, on DISABLED: `await EmailTokenRepository(session).invalidate_all(user.id)` right after `revoke_all_accounts_of_user` | Item 7 |
| `app/core/request_id.py` | Always `str(uuid.uuid4())`. Drop the inbound parse, `_SAFE`, the `trusted_proxy_cidrs` parameter and the `peer_is_trusted` import. Rewrite the docstring | Item 10 |
| `app/main.py` | `app.add_middleware(RequestIdMiddleware)` with no kwargs | Item 10 (signature change) |
| `docs/deploy.md` | (4) Replace the step 6–7 curl runbook (≈ l.412–441) with the new block and text, and add the `JWT_PUBLIC_KEYS_EXTRA` sentence (≈ l.95–97). (3) "Set admin origins" drift bullet + break-glass paragraph (≈ l.492–504). (6) New `APP_ENV` bullet under "Required runtime config". (10) Audit-log request-id bullet (≈ l.658–660) | Items 3, 4, 6, 10 |
| `docs/adr/0008-admin-api-authentication.md` | New "Amendment — hardening (ADM-002d, 2026-11-15)": `strict_aud`, admin-app permissions always `is_admin`, `--clear` skips the drift check, admin context refused on mail-link routes, admin `org_mode` pinned. Also edit the "fails closed on drift" bullet: "(except `--clear`, see the ADM-002d amendment)" | Items 1, 2, 3, 5, 8 (decisions that outlive the ticket) |
| `docs/adr/0009-audit-log.md` | New "Amendment — generated request id (ADM-002d, 2026-11-15)". It supersedes §5's "keeps a valid inbound `X-Request-ID`" and the ADM-004b `X-Request-ID` bullet | Item 10 |
| `tests/test_access_token.py` | New list-aud test | Item 1 |
| `tests/test_admin_auth.py` | Two new entries in the rejected-token table | Item 1 |
| `tests/test_token_verification.py` | New reference-verifier list-aud test | Item 1 |
| `tests/test_rbac_migrations.py` (new) | Backfill migration test (plain `def`, same pattern as `test_audit_migrations.py`) | Item 2 |
| `tests/test_rbac_permissions_api.py` | Admin-app `is_admin` create/patch tests | Item 2 |
| `tests/test_cli_set_admin_origins.py` | `--clear` on drift tests | Item 3 |
| `tests/test_auth_app_context.py` | Admin refusal on mail-link routes | Item 5 |
| `tests/test_config.py` | `APP_ENV` gate tests | Item 6 |
| `tests/test_users_admin.py` | Disable → tokens invalidated | Item 7 |
| `tests/test_rbac_apps_api.py` | `org_mode` param + same-value body | Item 8 |
| `tests/test_rbac_default_role.py` | Signature pin | Item 9 |
| `tests/test_request_id.py` | Rewritten for always-generate | Item 10 |
| `tests/test_audit_coverage.py`, `tests/test_auth_join.py` | Flip the request-id assertions (stored == echoed, ≠ inbound) | Item 10 |

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `test_access_token.py › test_list_audience_rejected` (param `["admin"]`, `["stoktok","admin"]`) | Item 1: reason `audience` |
| `test_admin_auth.py › test_rejected_tokens_401` + `"list_aud": mint(u, aud=["admin"])`, `"list_aud_multi": mint(u, aud=["stoktok","admin"])` | Item 1: Bearer → 401 envelope, denied log `reason=audience` if that table asserts the log |
| `test_token_verification.py › test_reference_rejects_list_audience` (`aud=["stoktok"]`, audience `stoktok` → `jwt.InvalidAudienceError`) | Item 1: the reference verifier matches the sample |
| `test_rbac_migrations.py › test_admin_permissions_backfill` | Item 2. Commit an admin-app permission `admin:zz-backfill:read` with `is_admin=false` and a non-admin-app permission with `false` (create a committed `zz-backfill` app via SQL or the factories with a committed session). `command.downgrade(cfg, "e91b6c3d58a2")` → `command.upgrade(cfg, "head")` → the admin row is `true` and the other row is still `false`. The `finally` deletes the inserted rows and always ends at head |
| `test_rbac_permissions_api.py › test_create_admin_app_permission_forced_admin` (body without `is_admin`, and with `is_admin: null`) | Item 2: 201, `is_admin` true in response and DB |
| `test_rbac_permissions_api.py › test_create_admin_app_permission_not_admin_422` | Item 2: `is_admin:false` → 422 `{"field":"is_admin","reason":"admin_app_permission_must_be_admin"}`, nothing stored |
| `test_rbac_permissions_api.py › test_patch_admin_app_permission_not_admin_422` | Item 2: PATCH `is_admin:false` on `admin:users:read` → 422, row unchanged. PATCH `is_admin:true` (same value) → 200 |
| `test_cli_set_admin_origins.py › test_service_clear_on_drift` (param: `status_disabled`, `join_open`, `org_required`, `default_role_set`) | Item 3: start with origins `[ADMIN]` + drift. `--clear` → `UPDATED`, origins `[]`, the 4 drifted columns unchanged, exactly one `app.admin_origins_set` audit row |
| `test_cli_set_admin_origins.py › test_service_clear_on_drift_already_empty_unchanged` | Item 3: drifted + already empty → `UNCHANGED`, no write, no audit row |
| `test_cli_set_admin_origins.py › test_service_refuses_missing_admin_on_clear` | Item 3: `lock_app` → None with `--clear` → 409 `admin_not_seeded` |
| `test_cli_set_admin_origins.py › test_cli_clear_on_drift_exit_0` | Item 3: CLI `--clear` on `join_policy=open` → exit 0, origins `[]`, `join_policy` still `open` |
| (existing) `test_service_refuses_drift[…]`, `test_cli_drift_exit_1` | Item 3: non-empty origins on a drifted row are still refused |
| `test_auth_app_context.py › test_admin_context_refused_on_mail_routes` (param signup / forgot / resend; admin origins set to `https://admin.mmlabs.online` in the test session) | Item 5: 403 `origin_not_allowed`, `fake_turnstile.calls == []`, no new `users`/`email_tokens`/`email_send_log` rows, `fake_email.calls == 0`. Resend: the device cookie is not rotated (no `Set-Cookie`) |
| `test_auth_app_context.py › test_admin_context_refusal_matches_unknown_app` | Item 5: the signup response body for `X-MM-App: admin` equals the one for an unregistered key with the same Origin (no oracle) |
| `test_auth_app_context.py › test_admin_context_refused_even_if_link_origin_overridden` | Item 5: with `get_link_origin` overridden, forgot and resend for admin still → 403 (the route-level dep refuses) |
| (existing) `test_admin_auth.py` login + token for `X-MM-App: admin`; `test_auth_join.py › test_join_admin_refused_even_if_open` | Item 5: login/token unchanged; join pin |
| `test_config.py › test_app_env_required_for_remote_db` (param hosts `db`, `10.0.0.5`, multi-host `localhost,db`, socket DSN `postgresql+asyncpg://u:p@/x?host=/run/pg`; `monkeypatch.delenv("APP_ENV")`, `_env_file=None`) | Item 6: `ValidationError` matching `APP_ENV must be set` |
| `test_config.py › test_app_env_default_ok_for_loopback` (param `localhost`, `127.0.0.1`, `[::1]`) | Item 6: `app_env == "local"` |
| `test_config.py › test_app_env_explicit_ok_for_remote_db` (param `local`, `test`, and `production` with `_PROD_EMAIL`-style kwargs) | Item 6 |
| `test_config.py › test_app_env_from_env_counts_as_explicit` (`setenv("APP_ENV","local")`, remote host) | Item 6: env-sourced value is in `model_fields_set` |
| `test_config.py › test_app_env_empty_counts_as_unset` (`setenv("APP_ENV","")`, remote host → raises) | Item 6: `env_ignore_empty` |
| `test_config.py › test_app_env_error_does_not_echo_dsn` (password marker in the DSN, not in `str(exc)`) | Item 6, R5 |
| `test_users_admin.py › test_disable_invalidates_email_tokens` | Item 7. Issue reset and verify tokens via `EmailTokenRepository.issue`. PATCH `disabled`, then PATCH `active`. POST `/auth/password/reset` with the old reset token → 422 `token_invalid`; POST `/auth/verify-email` with the old verify token → 422 `token_invalid`. No `email_tokens` row of the user has `used_at IS NULL` |
| `test_users_admin.py › test_disable_leaves_other_users_tokens` | Item 7: another user's unused token stays unused |
| `test_rbac_apps_api.py › test_admin_app_locked_fields_409` + param `{"org_mode": "required"}` | Item 8 |
| `test_rbac_apps_api.py › test_admin_app_same_value_patch_ok` (body + `"org_mode": "none"`) | Item 8: the no-op still passes |
| `test_rbac_default_role.py › test_build_default_role_has_no_actor_param` (`inspect.signature(...).parameters` == `{"self"?, "app_key"}`) | Item 9 |
| `test_request_id.py › test_inbound_ignored` (param trusted peer via `trusted_client`, untrusted peer via `make_client()`; inbound `req-proxied`) | Item 10: echoed ≠ inbound, valid UUID4 |
| `test_request_id.py › test_same_inbound_gives_distinct_ids` | Item 10: two requests, same inbound → two different ids |
| `test_request_id.py › test_echoed_on_error_response` (flipped) | Item 10: 404 envelope echoes a UUID4, not `req-404` |
| `test_request_id.py › test_echoed_on_health` (flipped) | Item 10: exactly one header value, a UUID4 |
| (kept) `test_request_id.py › test_generated_when_absent` | Item 10 |
| Delete `test_valid_inbound_kept`, `test_invalid_inbound_replaced`, `test_inbound_kept_from_trusted_peer`; `test_inbound_ignored_from_untrusted_peer` is folded into `test_inbound_ignored` | Item 10: their behaviour is removed |
| `test_audit_coverage.py › test_row_carries_request_context` (flipped) | Item 10: `row.request_id == resp.headers["x-request-id"] != "req-abc.123_X"`, UUID4 |
| `test_auth_join.py › test_join_writes_audit_row` (flipped) | Item 10: same; the "kept only from a trusted proxy" comment is removed |

## Approach
1. **Item 1.** Add `"strict_aud": True` to the `jwt.decode` options. PyJWT ≥ 2.10.1 is already pinned in `pyproject.toml` (`pyjwt[crypto]>=2.10.1`; lock 2.15.1), and `strict_aud` exists there. No pyproject change is needed. With `strict_aud`, a list claim raises `InvalidAudienceError` → `audience`. Mirror it in `tests/jwt_verifier.py`, `docs/consumers.md` (sketch + checklist 4 + item 9 note) and the `mmlabs-auth` bullet. Our own tokens already carry a string `aud` (`tokens/service.py`), so runtime behaviour is unchanged.
2. **Item 2.** Write the migration first:
   - SQL: `UPDATE permissions SET is_admin = true WHERE app_id = (SELECT id FROM apps WHERE key = 'admin') AND NOT is_admin`.
   - Docstring: data only; idempotent; R2 exception (`downgrade()` is a deliberate no-op, because the prior per-row values cannot be known and restoring `false` would re-open the gap); no audit row (migrations are not admin actions; same as `5e2a9c7d1f04`). It is safe because the admin app never has a default role (`AppService` invariant), so neither the default-role guard nor G3 applies.
   - Service: add `_admin_app_permission_must_be_admin()` (422, `field: is_admin`, `reason: admin_app_permission_must_be_admin`, message "Admin app permissions must be is_admin").
   - In `_create`, after the prefix check: if `app.key == ADMIN_APP_KEY`, then `data.is_admin is False` → that 422, else `is_admin = True`. The wildcard branch stays. This runs before `require_super`, as the wildcard 422 does.
   - In `update`, next to the wildcard check: `if "is_admin" in fields and data.is_admin is False and app.key == ADMIN_APP_KEY: raise …`.
   - Audit: a 422 is not a denial (`DENIED_ERRORS`), so nothing changes there (same as the wildcard case).
   - `PermissionCreate` docstring in `rbac/schemas.py`: optional one-line note. If touched, record it in the impl-log.
3. **Item 3.** In `set_admin_origins`: keep `_validate_admin_origins` and `lock_app`. Then `if app is None or (origins and (<4 drift predicates>))` → `admin_not_seeded`. The write, `mark_apps_changed` and the audit row are unchanged (origins only). Update the docstring. ADR-0008 amendment + `deploy.md` wording: "`--clear` skips the drift check (break-glass must work on a drifted row); it still refuses a missing row and changes only origins."
4. **Item 4 (docs only).**
   - Replace runbook steps 6–7 with the security consult's POSIX block, with one change: the **logout goes into the EXIT trap**, so it runs on any exit after login, Ctrl-C included. The trap does: `stty echo 2>/dev/null; if [ -s "$jar" ]; then curl -sS -o /dev/null -b "$jar" -X POST "$api/auth/logout" -H 'Origin: https://admin.mmlabs.online' -H 'X-MM-App: admin' -H 'Content-Type: application/json' -d '{"all":true}' || true; fi; rm -f "$jar" "$body" "$hdr"`. Keep `trap 'exit 130' HUP INT TERM` and `set -eu`.
   - The token response is piped straight into python3, which writes `Authorization: Bearer …` to `$hdr` (0600 via `umask 077`). Admin calls are written inside the marked section with `-H @"$hdr"`. Requirements: python3 and curl ≥ 7.55.
   - Text after the block:
     - The token never enters argv, a variable, history or stdout.
     - Logout revokes the device session, but an already issued access token stays valid until `ACCESS_TOKEN_TTL_SECONDS` (15 min).
     - The Turnstile-after-3-failures note.
   - Add to the `JWT_PUBLIC_KEYS_EXTRA` bullet: "Holds only this service's own previous/next public keys: `verify_access_token` trusts every key in it, so any key placed there can mint admin tokens."
5. **Item 5.** In `auth/deps.py`:
   ```python
   async def get_mail_link_app_context(app: AppContextDep) -> App:
       """App context for routes that mail a link to the request's Origin (signup, forgot,
       resend): the `admin` app is refused with the same 403 as an unregistered app (no oracle)."""
       if app.key == ADMIN_APP_KEY:
           raise OriginNotAllowedError("Origin is not allowed")
       return app


   MailLinkAppContextDep = Annotated[App, Depends(get_mail_link_app_context)]
   ```
   - `get_link_origin(_app: MailLinkAppContextDep, origin: …)`.
   - In the three routes, the route-level `_app` becomes `MailLinkAppContextDep`, so the refusal holds even if `get_link_origin` is overridden. It also stays before `DeviceDep` on resend.
   - The refusal comes after the rate-limit dependency (unchanged) and before Turnstile, which runs in the services. Login, token, logout and accounts keep `AppContextDep`. Verify-email and reset have no app context and stay untouched.
   - R1: the dependency raises an `AppError` subclass, the same as the existing deps. The ORM is not touched in the router.
6. **Item 6.** Add to `config.py`:
   ```python
   _LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


   @model_validator(mode="after")
   def _app_env_explicit_gate(self) -> Self:
       if "app_env" in self.model_fields_set:
           return self
       hosts = [h.get("host") for h in self.database_url.hosts()]
       if not hosts or any(h not in _LOOPBACK_HOSTS for h in hosts):
           raise ValueError("APP_ENV must be set explicitly when DATABASE_URL is not localhost")
       return self
   ```
   - Put it first among the model validators. A socket DSN (no host) fails closed.
   - The error never formats the URL (R5); `hide_input_in_errors` is already on.
   - Pre-merge check (done while planning):
     - `ci.yml`: tests set `APP_ENV=test` in `conftest.py`, and the `alembic check` step uses `localhost` → OK.
     - `release.yml`: the smoke test runs the image with no `DATABASE_URL` → default localhost → OK.
     - `Dockerfile` sets no env.
     - Production compose sets `APP_ENV: production` in the `x-app` anchor for `org-admin-api` and `migrate` (orchestrator + security consult verified); the ops CLI runs through `migrate`.
     - Local `docker-compose.yml` `api` gets `APP_ENV: local`.
   - `deploy.md` gets an `APP_ENV` bullet: required and explicit whenever the DB is not loopback; production sets it in `x-app`; a missing value refuses startup of the API, `migrate` and the CLI.
7. **Item 7.**
   - Add `EmailTokenRepository.invalidate_all(user_id)`: `UPDATE email_tokens SET used_at = now() WHERE user_id = :id AND used_at IS NULL`. One statement covers both purposes.
   - Purpose-agnostic, so `users/service.py` does not import the auth service modules (`VERIFY`/`RESET` constants), which avoids an import cycle. The users service already uses the sessions repository the same way.
   - Call it in `set_status` on DISABLED, after `revoke_all_accounts_of_user` and before the audit record, in the same transaction.
   - Lock order: `set_status` already holds the user row `FOR NO KEY UPDATE`. Forgot/resend take `FOR SHARE` on the user and then insert tokens, so they serialize behind the disable. Any token issued after the commit is refused, because the issuers skip non-active users (`password_reset.py:81`, `email_verification.py:107`).
   - The audit diff stays `status` only. Mentioning token invalidation in the audit changes is not required by the ticket.
8. **Item 8.** Add `or ("org_mode" in fields and data.org_mode != OrgMode.NONE)` to the admin invariant. Message: "The admin app must stay active, closed, without org mode and without a default role". The existing tests check only the status/code, so the new message does not break them.
9. **Item 9.** Change the signature to `async def build_default_role(self, app_key: str) -> DefaultRoleResult`. Nothing passes `actor` (grep: only tests, all positional `app_key`).
10. **Item 10.**
    - `RequestIdMiddleware.__init__(self, app)`. In `__call__`, set `request_id = str(uuid.uuid4())`, store it in `scope["state"]` and echo it.
    - The docstring says: always generated and never taken from the client, so the stored id is unique and no client can choose it; the echoed id equals the stored id.
    - `main.py`: drop the kwarg.
    - The ADR-0009 amendment supersedes §5's inbound clause and the ADM-004b bullet, and adds:
      - Cloudflare correlation, if ever wanted, logs a validated `cf-ray` next to the id in the app log (no schema change; not in this ticket).
      - Rows written before this release may hold client-supplied ids.
    - `deploy.md` l.658–660: "Every response carries `X-Request-ID`, a UUID4 generated per request; the same id is stored on the audit row. An inbound `X-Request-ID` is ignored. Rows written before ADM-002d may hold a client-chosen id (correlation only, not evidence)."
    - Behaviour change visible to callers: an inbound id is no longer echoed. Note it in the PR body.
11. Run `make check`, plus `alembic upgrade head && alembic check` against the test DB, and `alembic downgrade -1 && alembic upgrade head`.

Rules:
- **R1:** layering kept. Service rules are in services, and the dependency raises an `AppError`.
- **R2:** one data migration, with the documented no-op downgrade as a stated exception (the ticket asks for it).
- **R3:** auth classes are unchanged. The mail-link routes stay public/cookie with a narrower app set.
- **R4:** item 3 keeps its audit row. Items 2 and 8 refusals write nothing (422/409 are not denials). Item 7 lives inside the audited disable.
- **R5:** the `APP_ENV` gate never echoes the DSN.
- **R6:** existing envelope and codes.
- **R7:** only the 10 items.
- **R8:** tests listed above.
- **R11:** stronger: `aud`, email tokens, runbook secrets.

**Estimate**

| | Lines |
|---|---|
| Production code (`app/`), excluding docs | ≈ 75–95 changed |
| Migration file | ≈ 40 |
| Tests | ≈ 300–350 |
| Docs (`deploy.md`, ADRs, `consumers.md`, `identity-architecture.md`) | ≈ 110 |
| `docker-compose.yml` / `.env.example` | 2–3 |

Production code by item:

| Item | Lines |
|---|---|
| 1 | 1 |
| 2 | ~15 |
| 3 | ~4 |
| 5 | ~15 |
| 6 | ~15 |
| 7 | ~15 |
| 8 | ~2 |
| 9 | -1 |
| 10 | ~-10 net |

One implement/review cycle is enough. The changes are many small, independent edits.

## Specialist input used
**identity-access-engineer:**
- Adopted:
  - 1, strict_aud and its mirrors.
  - 2, the migration and forcing `is_admin`. Answer to its open question: 422 on an explicit `false` at create, forced `true` when omitted or null.
  - 3, the drift check only `if origins`.
  - 5, a wrapping dependency with `OriginNotAllowedError`.
  - 6, the `model_fields_set` validator plus the compose edit.
  - 7, invalidate on disable. Adopted as one purpose-agnostic `invalidate_all` instead of two calls with purpose constants, to avoid importing the auth service modules into `users`.
  - 8 and 9.
  - 10, always generate.
- Rejected:
  - Its split proposal (items 4 and 6 into ADM-002e). The orchestrator confirmed `APP_ENV: production` in the deployment `x-app` anchor for both services, so item 6 can fail closed now.
  - Its signup/forgot-only scope for item 5. Resend is added too, following the security consult: it also mails a link to the admin origin.

**security-architect:**
- Adopted:
  - #3 with the tests: drifted row + `--clear` → UPDATED with one audit row; non-empty origins → still refused.
  - #7 with the redeem-after-re-enable test. Adopted as consume (`used_at`) rather than delete: the existing redemption already treats used tokens as invalid, and the rows stay for the USR-04d retention cleanup.
  - #4, the POSIX block. Changed: logout moved into the EXIT trap so an interrupted run still revokes the device session.
  - #10, always UUID4, no CF-Ray, with its tests.
  - #5, signup + forgot + resend, same 403, no verify/reset/login/token/logout change, join pin.
  - #6, fail closed + compose `APP_ENV: local`; release.yml checked.
  - #1, strict_aud. The pin already exists (`>=2.10.1`), so no pyproject edit.
  - #2, migration + 422 on PATCH false. For create, an explicit `false` → 422 and omitted → forced true (its open question 1).
  - #8 with its tests.
  - #9.
- Not adopted: a changelog. The repo has no CHANGELOG file, so the request-id behaviour change goes in the PR body and the ADR-0009 amendment.

## Assumptions
- **Owner-level defaults recorded as decisions, per orchestrator; not blockers:**
  - (a) Item 6 fails closed rather than warning, on the strength of the verified deployment compose.
  - (b) Item 10 always generates a UUID4; CF-Ray is not adopted.
  - (c) Item 2 create: `is_admin` omitted or null → forced `true`; explicit `false` → 422. PATCH `false` → 422.
  - (d) Item 5 refusal reuses 403 `origin_not_allowed` (no dedicated reason).
  - (e) Item 5 covers resend too.
- Whether the ADM-002b release reached production does not matter: the migration is idempotent and runs everywhere.
- `PostgresDsn.hosts()` (pydantic v2 multi-host URL) returns IPv6 hosts in brackets. Both `::1` and `[::1]` are in the loopback set, so either form passes. The implementer confirms with the `[::1]` test case.
- The admin app row is seeded by the `create_rbac` migration with id `…a1`. The backfill selects it by `key = 'admin'`, so it does not hard-code the id.
- `test_admin_auth.py`'s rejected-token table may also assert the denied log line. If it does, the new entries expect `reason=audience`.
- Turnstile-protected signup/forgot in the item 5 tests: the refusal happens before the service, so `fake_turnstile.calls == []` holds without configuring the fake.

## Blocking questions
None.

## Split proposal
None. All 10 items stay here (orchestrator decision). The prerequisite for item 6, `APP_ENV: production` on both deployment services, is already in place. Changed production code is well under ~400 lines, and there is exactly one migration.

## Data model / migration
No schema change. One **data** migration:

- File `migrations/versions/20261115_a7c3e5f19b42_admin_permissions_is_admin.py`, `revision = "a7c3e5f19b42"`, `down_revision = "e91b6c3d58a2"`.
- `upgrade()`: `op.execute(sa.text("UPDATE permissions SET is_admin = true WHERE app_id = (SELECT id FROM apps WHERE key = 'admin') AND NOT is_admin"))`.
- `downgrade()`: `pass`. The docstring states:
  - This is an R2 exception (an irreversible data fix; the previous per-row values are unknown, and restoring `false` would reopen the gap).
  - No audit row is written.
  - It is idempotent.
  - It is safe because the admin app never has a default role, and every admin-app role is privileged.
- `alembic check`: no drift, since the models are untouched.

## API contract
No new endpoint. Behaviour changes on existing ones:

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/apps/admin/permissions` | `is_admin` omitted/null → stored `true`; `false` → refused | `Permission` | 201; **422** `admin_app_permission_must_be_admin`; existing 403/404/409 | `admin:roles:manage` + `admin:*` (unchanged) |
| PATCH | `/api/v1/apps/admin/permissions/{id}` | `is_admin:false` → refused | `Permission` | 200; **422** `admin_app_permission_must_be_admin`; existing | unchanged |
| PATCH | `/api/v1/apps/admin` | `org_mode` ≠ `none` → refused | `App` | 200; **409** `conflict`; existing | unchanged |
| PATCH | `/api/v1/users/{id}` | `status: disabled` | `User` | unchanged; side effect: the user's unused email tokens are consumed | unchanged |
| POST | `/api/v1/auth/signup`, `/api/v1/auth/password/forgot`, `/api/v1/auth/verify-email/resend` | `X-MM-App: admin` | error envelope | **403** `origin_not_allowed` (same as an unregistered app), before Turnstile/writes/email | public / public / cookie (unchanged) |
| any | any | inbound `X-Request-ID` ignored | `X-Request-ID: <uuid4>` always generated | unchanged | unchanged |
| — | Bearer admin routes | access token with list `aud` | error envelope | **401** (reason `audience`) | unchanged |
