## Attempt 1

## Security review: ADM-014, attempt 1 (docs only)

Scope: `git diff origin/main...HEAD` on branch `claude/blissful-wozniak-fx4lm9`. I checked it against `app/modules/auth/router.py`, `app/modules/auth/deps.py`, `app/modules/auth/cookies.py` and `app/core/cors.py`. I did not run `make check`, `pytest` or `sh -n`, as you asked. Besides git I used read-only `grep`, `sed` and `cat` to read code.

### Findings

**🟡 should-fix: `docs/consumers.md:42`, "Only login sets the cookie."**
- **The sentence is wrong.** Every cookie-class route can send `Set-Cookie`:
  - `/token`, `/accounts`, `/apps/{key}/join`, `/logout` and `/verify-email/resend` call `set_device_cookie` on rotation (`router.py:170,199,229,267,325`).
  - `/logout` clears the cookie (`router.py:168`).
  - Any 401 from `get_device` clears it (`deps.py:133-141`).
- **Risk:** a developer may decide `omit` is fine on a "non-setting" cookie call. If a rotated cookie is dropped and reused after the 60 s grace, the whole device session is revoked (R11 reuse detection), so users get signed out at random. The sentence also contradicts the previous sentence on lines 40-41.
- **Fix:** replace it with: "Only login creates the cookie; every cookie-class call may rotate it, and logout or a 401 clears it." Line 118 ("sign-up sets no cookie; only login does") is fine as written.

**💭 note: `docs/deploy.md:559-561`, "no tunnel ingress or catch-all route".**
- cloudflared requires a final catch-all ingress rule, so as written this cannot be followed literally and may confuse operators.
- **Fix:** "no tunnel ingress rule for it; the tunnel's catch-all rule must be `service: http_status:404`, never a real service". This matters together with the wildcard DNS clause already in the doc.

**💭 note: `docs/deploy.md:562-564`, the one-off clean-up.**
- It is phrased conditionally ("if `admin.mmlabs.online` was ever set"), but the operator usually cannot know that without looking.
- **Fix:**
  - Run the dry run unconditionally and read its `origins:` / `- origin` lines.
  - Add "wait 60 s" after the real run (as in the other runbooks).
  - Note that admin tokens already issued stay valid up to `ACCESS_TOKEN_TTL_SECONDS` (15 min).

**💭 note: `docs/deploy.md:550-557`, Host policy.**
- "Check them whenever DNS or tunnel routes change" has no concrete check.
- **Fix:** add one, e.g.:
  - `dig +short admin.mmlabs.online` must return nothing, which also proves the wildcard is absent.
  - `admin-portal.mmlabs.online` resolves only to the `admin-pan` route.

### Verified OK
- **Credentials split matches the code.** Only `login` (`router.py:127`, `read_device_cookies`) and the `DeviceDep` routes read the `__Host-mm_device` cookie. `signup`, `verify-email`, `password/forgot` and `password/reset` neither read nor set it: no `DeviceDep`, no cookie reads in `MailLinkAppContextDep` / `get_link_origin` / `get_signup_hook`, and reset's docstring says it sets no cookie. So `omit` there is correct and is a small hardening (the device cookie is no longer sent where it is not needed).
- **CORS behaviour with `omit`.** Preflights never carry credentials, and `DynamicCORSMiddleware._preflight` decides only on Origin, method and headers (`cors.py`). The `IDENTITY_POLICY` response's `ACAC: true` does no harm on a non-credentialed request. So "the credentials mode does not change whether CORS answers it" (consumers.md:46-47) is correct. Signup and forgot still send `Origin` (browsers always send it on cross-origin POST), so the app-context 403 checks still apply.
- **Admin section** (consumers.md:62-80): Bearer only, `omit`, no `X-MM-App` on admin calls. This matches `ADMIN_POLICY` (credentials false; allowed headers are only `authorization` and `content-type`).
- **No `admin.mmlabs.online` left as an admin origin** in docs, the ADR, the Makefile or CLAUDE.md. The only remaining mentions are the "not used / must not exist" statements (`deploy.md:209,559-564`, `ADR-0008:56-60`).
- **Host policy cannot leave an attacker-reachable admin origin by itself:**
  - `admin-portal` is the only production origin.
  - Replace semantics plus the explicit "do not keep it as the second origin" (`deploy.md:209-210`) prevent the old host from staying in `apps[admin].origins`.
  - The doc forbids DNS (including a wildcard), tunnel routes, other apps' `origins` and `TURNSTILE_ALLOWED_HOSTNAMES` for it.
- **Runbook shell block** (`deploy.md:441-463`): only the literal origin inside quoted strings changed (trap logout, login, token), so it parses exactly as before (impl-log reports `sh -n` exit 0). Nothing else changed: the trap ordering, `umask 077`, password and token handling, and the `--max-time 10` logout. The `set-admin-origins` bash examples and the `Makefile` help line changed only the host.

### Follow-ups (out of scope)
1. **CLAUDE.md R11** still describes the cookie as `mm_device` with `Path=/api/v1/auth`. The code uses `__Host-mm_device` with `Path=/` (`cookies.py:12-13`). This was already listed by ADM-012.
2. **Test constants** still use `https://admin.mmlabs.online` as the sample admin origin (`tests/test_admin_guards.py:44`, `test_auth_app_context.py:179`, `test_auth_join.py:35`, `test_cli_set_admin_origins.py:42`, `test_cli_register_app.py:331+`). This is harmless, but switching to `admin-portal` would keep the grep check clean and stop the old host looking sanctioned.
3. **Optional hardening:** have `set-admin-origins` refuse `https://admin.mmlabs.online` in staging/production. That is a code change, so it needs an owner decision.
4. **Deployment repo:** add `admin-portal.mmlabs.online` to `TURNSTILE_ALLOWED_HOSTNAMES` if that list is non-empty, and confirm production `apps[admin].origins` and wildcard DNS state (plan F-1, F-2, F-5).

**Verdict:** PASS from security. No blockers; fix the 🟡 in `consumers.md:42` before merge if a rework round is open.
