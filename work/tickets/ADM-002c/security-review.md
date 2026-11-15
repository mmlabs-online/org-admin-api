## Attempt 1

# Security review: ADM-002c, attempt 1 (appsec-engineer)

**Scope:** `git diff origin/main...HEAD` in org-admin-api (service, schema, CLI, Makefile, ADR-0008 amendment, deploy.md, tests) and `deployment` branch `org-admin-api-ops-admin-origins` (`.github/workflows/ops-org-admin-api.yml`). I ran no tests (shared DB in use). The verdict is based on reading the code.

## Findings

### 🔴 Blockers
None.

### 🟡 Should-fix

1. **The `--clear` break-glass is blocked by the drift check, which is the case where it matters most.** `app/modules/rbac/service.py:498-508`: the drift check (`status/join_policy/org_mode/default_role_id`) runs before any write, even when `origins == []`.
   - **Risk:** someone moves `admin` to `join_policy=open` and sets a `default_role_id` (the RBAC API `AppService.update` has no `admin:*` guard yet, see the ADM-002b follow-up). Then anyone verified could join admin and mint `aud=admin` tokens. In that state `set-admin-origins --clear` exits 1 and the kill switch does nothing.
   - Disabled status needs no fix (a disabled app is already closed by `AppContextService.check`).
   - **Fix:** when `origins` is empty, skip the drift refusal (still lock, still write only `origins`). Or skip it only for the open-policy and default-role cases. Add a test: drifted admin plus `--clear` gives `updated` with empty origins and nothing else changed. Update ADR-0008 amendment and deploy.md "Semantics" so they say clear always works.

2. **The runbook puts the admin Bearer token in shell history and `ps`.** `docs/deploy.md`, Runbook step 6 to 7: the token response is printed, then the operator is told to send `Authorization: Bearer <access_token>`. Typed literally, the token lands in `~/.bash_history` and is visible in the process list for its 15-minute life. The runbook protects the password carefully, so it should protect the token the same way.
   - **Fix:** capture the token without echoing it, for example `tok=$(curl … | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')`.
   - Then send the header from a file: `printf 'Authorization: Bearer %s\n' "$tok" > "$hdr"` (0600 via umask), then `curl -H @"$hdr" …`, then `rm -f "$hdr"; unset tok`.
   - `-H @file` needs curl ≥ 7.55.

### 💭 Notes

3. **Temp files survive an interrupted run.** `docs/deploy.md` curl block: if the operator hits Ctrl-C or curl fails, nothing removes `$body` (holds the password) or `$jar` (holds the device cookie). The 0600 mode limits the exposure. Suggest `trap 'rm -f "$jar" "$body"' EXIT INT TERM`, or run the block in a subshell `( … )`, which also stops `umask 077` from persisting in the operator's shell.

4. **Each runbook login leaves a live device session on the server.** `rm -f "$jar"` deletes the local cookie, but the session stays valid until it expires. Suggest ending the block with `POST /api/v1/auth/logout` (same Origin and `X-MM-App` headers, `-b "$jar"`) before deleting the jar.

5. **Setting admin origins also opens the public flows for `X-MM-App: admin`.** Signup, verify-email and password forgot/reset (`auth/router.py:92,350`) all build their email links from the validated Origin. So reset and verification links for `X-MM-App: admin` point at `https://admin.mmlabs.online`, which by policy has no DNS: the links are dead, and if that host is ever taken over, they leak tokens. No admin token results from this: `TokenService.issue` still requires effective admin roles (`tokens/service.py:72`). deploy.md's host policy already covers takeover. Suggest one sentence in it saying reset/verify links also go to the admin origin. Follow-up: refuse signup and forgot for `admin`.

6. **Any unregistered `*.mmlabs.online` host is accepted.** That includes `www.mmlabs.online` and hosts of services that are not registered apps. The policy (dedicated host) is enforced only by docs and the owner's approval step. Optional hardening: a small denylist (for example `www`) in the service, or an exact-allowlist setting. Fine as-is given the approval gate.

7. **Race with register-app (time-of-check/time-of-use).** `set_admin_origins` locks only the admin row and `register` locks only its own row, so the cross-app `origin_taken` checks can race (`service.py:510-517` against the `register` path). The lock order is deadlock-free (one row each). In production the shared `concurrency: deploy-org-admin-api` group serializes every ops command, so this is acceptable. The DB-level guard is already listed as follow-up 4.

8. **Verified as correct.**
   - **Normalisation (D19):** case, trailing slash, `:443` and trailing-dot variants are all handled by `normalize_origin` before the apex, cap and loopback checks. The apex test covers `https://MMLABS.online/`.
   - **Cap:** duplicates are deduplicated before the 2-origin cap. Inputs over 20 fail in pydantic with the fixed message.
   - **Origins held by other apps:** refused regardless of status (disabled is tested).
   - **Loopback:** refused in staging and production.
   - **Write scope and drift:** only `app.origins` is written. Drift fails closed: nothing is repaired and no refusal leaves a write behind.
   - **No echo:** argparse `error()` is swallowed, the pydantic `ValidationError` is never printed, generic exceptions print a fixed line, and the CLI engine uses `hide_parameters=True`.
   - **Output:** results are printed only after the commit; dry run rolls back through an exception.
   - **Exclusivity:** `--clear` and `--origin` are a mutually exclusive group (exit 64).
   - **Exposure:** nothing widens who can obtain an admin token beyond verified users with an effective admin role reached through an admin origin.

9. **Deployment workflow is correct.**
   - **Input handling:** inputs reach the script only through `env:`, never as `${{ inputs.* }}` in `run:`. `set -f` stops glob expansion. Arguments are NUL-delimited in a file.
   - **Validation:** each origin must match the anchored regex `^https://([a-z0-9-]+\.)+mmlabs\.online$`, which excludes the apex, ports, paths, slashes and uppercase. The API host is refused, and the count must be 1 or 2.
   - **Clear/origins:** `clear` refuses non-empty origins and is refused for other commands.
   - **Defaults and gating:** `dry_run` defaults to true. The workflow runs from main only, in the `org-admin-api-ops` environment, in the shared concurrency group.
   - **No echo:** error messages never echo inputs.
   - One small point: the bash loop counts duplicate inputs twice (`a,a` gives n=2), while the CLI deduplicates them, so the result is still safe.

## Follow-ups (out of scope)
- ADM-002b: an `admin:*` guard on admin-app changes in `AppService.update`. This guard is what makes finding 1 reachable today.
- Refuse signup and forgot-password for the `admin` app (note 5).
- DB-level uniqueness of origins across apps (note 7, already listed as follow-up 4).

**Verdict:** PASS with two should-fixes: `--clear` must work on a drifted admin row, and the runbook must keep the Bearer token out of shell history. No blockers.
