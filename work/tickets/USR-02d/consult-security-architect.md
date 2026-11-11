# consult-security-architect — USR-02d

## Findings
1. **D19 origins.** `origins.py` already lowercases and rejects trailing dots, empty labels, userinfo, `%`, non-ASCII and IP literals, and keeps `http` to loopback. It lacks the host allowlist. A substring or plain `endswith("mmlabs.online")` would accept `evilmmlabs.online`; label-based matching avoids that and also rejects `mmlabs.online.attacker.com`. A punycode look-alike of the domain (`xn--…`) changes the registrable domain, so the suffix rule rejects it too.
2. **Wildcard trust has a cost.** Stored origins are exact hosts (`*` already rejected), but D19 lets staff register any subdomain. Once USR-03 echoes origins with credentials, a dangling DNS record on a registered subdomain becomes a credentialed CORS foothold (subdomain takeover). Stored entries must stay exact, and nothing may ever be pattern-matched at echo time.
3. **Loopback is lower risk than it looks.** `mm_device` is `SameSite=Lax` and host-only on `api.mmlabs.online`; a fetch from `localhost` is cross-site, so the cookie isn't sent. A page or process on a user's machine on localhost would still get CORS read access to public/Bearer responses and could run login on its own terms — most relevant for the `admin` app.
4. **Escalation paths in this slice** (data guards only until ADM-002): creating `<app>:*` (forced `is_admin`); PATCH `is_admin` true → false, making a sensitive permission eligible for the default role (step 1 of a two-step escalation: downgrade, then PUT it onto `<app>:member`); PUT on any `admin`-app role; PUT adding `admin:*` to a role other than `admin:super-admin`.
5. **TOCTOU (carried from 02b).** Two races can each put an `is_admin` permission on a default role: PATCH app `default_role_id = R` ‖ PUT adding an `is_admin` permission to R; PATCH permission `is_admin = true` ‖ PUT adding that permission to the default role. Neither race shares a row lock with the other operation.

## Recommendations
- **AC5 (origins).** After normalization: `host == "mmlabs.online" or host.endswith(".mmlabs.online")` with `https` only; loopback (`localhost`, `127.0.0.1`, `[::1]`) on `http` or `https`, any port; for `mmlabs.online` hosts only the default port (`:443` dropped, any other port → 422 — Cloudflare-fronted apps don't need others, and it keeps the set small); keep accepting `xn--` labels under the suffix (we control the zone); add the 02b reviewer's numeric/hex last-label rejection. Reject tests: `https://evilmmlabs.online`, `https://mmlabs.online.attacker.com`, `https://mmlabs.online.`, `https://x.mmlabs.online:8443`, `http://mmlabs.online`, `https://0x7f.1`. Accept tests: `HTTPS://A.MMLABS.ONLINE` normalizes, `https://xn--abc.mmlabs.online`. Keep the generic, non-echoing message (R6/R11).
- **Loopback:** keep it in stored data per D19; write into USR-03 that loopback is not echoed when `APP_ENV` is staging/production. Consider refusing loopback on the `admin` app now.
- **AC4 + TOCTOU carry-over:** start every role/permission mutation by locking the app row (`SELECT … FOR UPDATE`) and take all guard decisions after the lock; app PATCH takes the same lock. One lock serializes both races without lock-ordering trouble.
- **AC2/AC4 data guards to add now (cheap):** `admin:*` may sit only on `admin:super-admin` (409); PATCH `is_admin = false` on `<app>:*` → 422 (contracted); PUT on the app's default role cannot add `is_admin` permissions (contracted).
- **`# ADM-002 guard` markers (need `admin:*` as actor):** any write in the `admin` app (permission/role create, PATCH, PUT); creating or granting `<app>:*` or any `is_admin` permission; `is_admin` true → false; changing `admin` `origins`.
- **R11/R6 input hygiene:** limit role `name` (≤ 100) and permission `description` (≤ 500) and refuse control characters — role names become public via D17 and apps render them.
- **USR-02e catalog:** its own schema separate from `RoleRead` (key, name, `is_system`, permission keys only) so fields later added to `RoleRead` (ids, `is_admin`) can't leak; never expose descriptions, `is_admin`, origins, `join_policy` or ids; same 404 body for unknown, disabled and `admin` apps; consider caps (e.g. ≤ 500 permissions, ≤ 100 roles per app) to bound catalog size and ETag cost.

## Risks
- Manual PUT on `<app>:member` conflicts with `build_default_role`: a rebuild silently overwrites it. Document it or restrict PUT on the member role.
- `apps.origins` rows written before this slice aren't re-validated. Per the ticket none exist outside local/test (accepted); USR-03 should re-validate at read time (02b follow-up).

## Open questions
1. Refuse `https://api.mmlabs.online` (the API host, which also serves the public Swagger UI) as an app origin? Recommend yes.
2. Refuse non-default ports on `*.mmlabs.online` (recommended) or allow them?
3. Restrict `admin:*` to `admin:super-admin` only, enforced as 409 now?
4. Refuse loopback on the `admin` app at write time now, or leave it to USR-03?
