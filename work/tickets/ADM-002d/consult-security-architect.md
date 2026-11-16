# consult-security-architect — ADM-002d

## Findings

- **Deploy compose (checked: `origin/main` matches the local checkout, HEAD 9783cac).** The `x-app` anchor sets `APP_ENV: production` for both `org-admin-api` and `migrate`. The ops workflow runs the CLI through `run --rm --no-deps migrate`, so the CLI also starts as production. That means item 6 is defence in depth only: a validator that requires an explicit `APP_ENV` would have no effect on today's production deploy.
- **The local `docker-compose.yml` `api` service** uses `DATABASE_URL=…@db:5432` and sets no `APP_ENV`. A rule that only exempts localhost would break `make`'s `full` profile.
- **Seed `5e2a9c7d1f04`** already inserts `is_admin=true`. The real gap is in `PermissionService._create` (`service.py:702`): `is_admin = bool(data.is_admin)`, so since ADM-002b a super-admin can create **admin-app permissions with `is_admin=false`** in production, and the PATCH path also allows false.
- **`verify-email` and `password/reset`** have no app context: the token is the only credential. **`resend`, `signup` and `forgot`** build their links from `get_link_origin`. **`join` on admin** is already refused by `join_policy=closed`.
- **`set_admin_origins`** (`service.py:606-617`) runs the drift check before it looks at the origins, which confirms ADM-002c 🟡1.
- **The runbook** (`deploy.md:417-437`) uses `read -p` and `read -s`, which are bash-only and not POSIX. It also uses `printf`-built JSON, which breaks on a password containing `"` or `\`. The token is printed and then pasted by hand, it has no trap, and it never logs out.

## Recommendations (by real risk)

1. **#3 `--clear` on drift.** This is the kill switch, and today it fails in exactly the case where it is needed. When `origins == []`: still `lock_app`, still refuse a missing row, skip the drift check, write only `origins`, and write the audit row.
   - **Test:** an admin row with `join_policy=open` and a `default_role_id` set, run `--clear` → `UPDATED`, `origins == []`, all other columns unchanged, and one audit row. With non-empty origins on the same drifted row → still `admin_not_seeded`.
2. **#7 email tokens on disable.** If the disable was a response to a compromise, a reset link the attacker already holds comes back to life on re-enable, which is a staff account takeover. Delete or consume the user's `email_tokens` in the same transaction as `revoke_all_accounts_of_user`.
   - **Test:** issue reset and verify tokens, disable, re-enable, redeem → the same error as for an unknown token. Add a check that no `email_tokens` rows are left for that user.
3. **#4 runbook.** Replace the curl block in `deploy.md` with the block below.
   - **Acceptance (reviewer):** no secret appears in argv or in history, the block runs under `sh` (dash), and interrupting it with Ctrl-C leaves no temp files and restores terminal echo.
4. **#10 request id.** **Always generate a UUID4** for the stored id and echo that id. Do not echo or store the inbound value. Drop the trusted-peer branch.
   - What an auditor needs is an id that is unique and that no client can choose. Collision and injection are the threat; how the id correlates with Cloudflare's logs is not.
   - `CF-Ray` can be forged by any container on the `web` network (all of it is inside `TRUSTED_PROXY_CIDRS`). It matches `_SAFE`, and Cloudflare request logs need Logpush, which this plan does not have.
   - If correlation is wanted later, log `cf-ray` (validated) in the app log next to the generated id. That needs no schema change.
   - **Test:** a trusted peer sending `X-Request-ID: abc` → stored and echoed id ≠ `abc` and is a valid UUID. Two requests with the same inbound id → different stored ids.
   - Update the ADR-0009 amendment and `deploy.md:599`.
5. **#5 admin public flows.** Refuse `X-MM-App: admin` in **signup, forgot and resend**: all three mail a link to the admin origin. Do it in one place: `get_link_origin`, or a dependency that wraps it.
   - Do **not** refuse `verify-email` or `reset`: they have no app context, and a token minted for another app must still work.
   - Do **not** refuse `login`, `token` or `logout`: the runbook depends on them.
   - Leave join to the closed-policy check, plus a pinning test.
   - Answer with the same 403 envelope as an unregistered app context, and send nothing.
   - **Test:** each of the three returns 403 and the dispatcher gets no call. Login and token for admin still work. `join` on admin → the existing closed-policy refusal.
6. **#6 `APP_ENV`.** Add a `model_validator`: if `"app_env" not in self.model_fields_set` and the `DATABASE_URL` host is not in `{localhost, 127.0.0.1, ::1}`, then `raise ValueError("APP_ENV must be set explicitly …")`.
   - Add `APP_ENV: local` to `docker-compose.yml`'s `api` service rather than exempting `db`.
   - A fail-closed startup is safe here because production sets the variable in the anchor, and `.env` or CI set it everywhere else. With `env_ignore_empty`, an empty `APP_ENV=` still counts as unset.
   - **Tests:** remote host with no `APP_ENV` → raises. Loopback with no `APP_ENV` → `local`. Remote host with `APP_ENV=production` plus the required keys → passes.
7. **#1 `strict_aud: True`**, using the parametrized list cases.
   - Pin `pyjwt>=2.10` (where `strict_aud` was added) in `pyproject` and in `docs/consumers.md`. An older PyJWT in a consumer app ignores unknown options without raising.
8. **#2 forcing `is_admin`.** Force `is_admin=True` on admin-app create, and return 422 on a PATCH to false (same pattern as the wildcard).
   - Ship the data migration anyway, as `UPDATE permissions SET is_admin = true FROM apps WHERE … key='admin' AND NOT is_admin` with a no-op `downgrade()`. Production has run the ADM-002b API, so "never had the flags" no longer proves that no such rows exist. The migration is idempotent and safe: the admin app cannot have a default role, and every admin-app role is already privileged.
   - The docstring should say that the migration writes no audit row.
   - **Tests:** create with `is_admin:false` → stored as true (or 422; pick one). PATCH to false → 422. A migration test on a seeded `false` row.
9. **#8** In `AppService.update`, return 409 for admin when `"org_mode" in fields and data.org_mode != NONE`.
   - **Test:** a super-admin PATCH of `org_mode=…` on admin → 409. A no-op PATCH (`org_mode=none`) → 200.
10. **#9** Remove the `actor` parameter, since it has no caller. mypy is the gate. The existing tests already call without it.

**Runbook block (POSIX sh, needs python3 and curl ≥ 7.55):**
```sh
(
  set -eu
  umask 077
  api=https://api.mmlabs.online/api/v1
  jar=$(mktemp) body=$(mktemp) hdr=$(mktemp)
  trap 'stty echo 2>/dev/null; rm -f "$jar" "$body" "$hdr"' EXIT
  trap 'exit 130' HUP INT TERM
  printf 'email: '; IFS= read -r email
  printf 'password: '; stty -echo; IFS= read -r pw; stty echo; printf '\n'
  printf '%s\n%s\n' "$email" "$pw" | python3 -c 'import json,sys;e,p=sys.stdin.read().split("\n")[:2];print(json.dumps({"email":e,"password":p}))' > "$body"
  pw=
  acct=$(curl -fsS -c "$jar" -X POST "$api/auth/login" -H 'Origin: https://admin.mmlabs.online' -H 'X-MM-App: admin' -H 'Content-Type: application/json' -d @"$body" | python3 -c 'import json,sys;print(json.load(sys.stdin)["account_id"])')
  : > "$body"
  curl -fsS -b "$jar" -c "$jar" -X POST "$api/auth/token" -H 'Origin: https://admin.mmlabs.online' -H 'X-MM-App: admin' -H 'Content-Type: application/json' -d "{\"audience\":\"admin\",\"account_id\":\"$acct\"}" | python3 -c 'import json,sys;print("Authorization: Bearer "+json.load(sys.stdin)["access_token"])' > "$hdr"
  # --- admin calls (token only via -H @file) ---
  curl -fsS -H @"$hdr" "$api/users?limit=5"
  # ---
  curl -fsS -b "$jar" -X POST "$api/auth/logout" -H 'Origin: https://admin.mmlabs.online' -H 'X-MM-App: admin' -H 'Content-Type: application/json' -d '{"all":true}'
)
```
Each admin call is written into the marked section, so the token never enters a shell variable, argv or history.

## Risks
- **Logout does not end the access token.** It revokes the device session, but the access token still works until its TTL (15 min). The runbook should say so.
- **The `APP_ENV` host check** treats a socket or `?host=` DSN as remote, which fails closed. That is acceptable.
- **The image build.** If the release build imports `Settings` (a smoke test or `alembic check` against a remote DB with no `APP_ENV`), it would now fail. Check `release.yml` before merging.
- **`#10`** changes behaviour that callers can see: inbound ids are no longer echoed. Note it in the changelog.

## Open questions
1. **#2 create path:** silently force `true`, or return 422 on `false`? I suggest 422, to match the wildcard.
2. **Has the ADM-002b release been deployed to production?** If not, the migration is provably a no-op there, but I would still ship it.
3. **#5 response shape:** 403 `app_context` (same as an unknown app), or a dedicated reason? I suggest the same reason, so the refusal tells a caller nothing new.
