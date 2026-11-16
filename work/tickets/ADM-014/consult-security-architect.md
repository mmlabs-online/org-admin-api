ADM-014 consult (security-architect). Read-only; I changed no files.

**Summary:** The hostname rename is safe as a pure docs change. The ticket has two premises that need correcting:
- Signup does **not** set the cookie. Only login does, and login also reads it.
- Not every remaining `admin.mmlabs.online` reference is in docs. The Makefile and the test fixtures also have it, so the grep check has to be scoped.

## Findings

### F1. Where `admin.mmlabs.online` still appears as the admin origin

**`docs/deploy.md`** (9 lines, all in "Admin token" runbook / "Set admin origins (ADM-002c)"):
- l.433: runbook step 4, `set-admin-origins --origin https://admin.mmlabs.online`
- l.446: the EXIT trap's logout `Origin:` header (inside the single-quoted trap string)
- l.455: login `Origin:` header
- l.457: token `Origin:` header
- l.505: fallback `docker compose … --origin … --dry-run`
- l.508: `make set-admin-origins ARGS='--origin …'`
- l.517–518: the expected-output block (`origins:` and `+ origin` lines)
- l.551: the **Host policy** paragraph, which says it is "reserved for the future admin UI". That wording is stale: `admin-pan` now exists at `admin-portal`.

**Other stale wording in `docs/deploy.md`:**
- l.482–483: "The browser console is not a supported path until an admin UI exists." An admin UI now exists. Reword, for example: "Operators without the admin UI use this curl runbook; the browser console is not a supported path."
- l.203–218 ("Admin UI CORS (ADM-012)") already uses `admin-portal`. It also says "During a host move keep the old origin as the second allowed one." See F3: here the old origin must **not** be kept.

**`docs/adr/0008-admin-api-authentication.md`:**
- l.55–57: the ADM-002c amendment, "production uses `https://admin.mmlabs.online`, reserved for the future admin UI (no DNS or tunnel route until it exists…)". This is the only ADR hit.
- ADR-0011 l.7 and l.51 already say `admin-portal`.

**`Makefile` l.29:** the help comment `## … ARGS='--origin https://admin.mmlabs.online'`.
- It is a help string and changes no behaviour, but the ticket says "No code change". The planner must decide (open question 1).

**Tests use `admin.mmlabs.online` as a fixture constant:**
- `tests/test_cli_set_admin_origins.py:42,498,501`, `tests/test_admin_guards.py:44`, `tests/test_auth_join.py:35`, `tests/test_auth_app_context.py:179`, `tests/test_cli_register_app.py:331,402,404,414`.
- They are arbitrary valid D19 origins, not policy, so leave them alone (R7, no code change).
- This means the "grep test" in the acceptance criteria must be scoped, or it will fail.

**`work/tickets/*`:** historical records. Leave them.

**Already clean:** `README.md`, `CLAUDE.md`, `docs/spec-v1.md` (l.74 says `admin-portal`), `docs/identity-architecture.md`, `docs/consumers.md` (l.58 says `admin-portal`), ADR-0011.

### F2. Which `/api/v1/auth/*` endpoints read or set `__Host-mm_device`

Source: `app/modules/auth/router.py` and `service.py`.

| Endpoint | Reads cookie | Sets/clears cookie | Origin + X-MM-App check | Correct `credentials` |
|---|---|---|---|---|
| `POST /signup` | no | **no** (no `Response` param; `SignupService` has no device logic) | yes (`MailLinkAppContextDep`) | `omit` works |
| `POST /login` | **yes** (`read_device_cookies` → `start_for_login` adds the account to the existing device session) | **yes** (`set_device_cookie`) | yes (`AppContextDep`) | **must be `include`** |
| `POST /logout`, `/token`, `GET /accounts`, `POST /apps/{key}/join`, `POST /verify-email/resend` | yes | yes (rotation; logout `all` clears) | yes | **must be `include`** |
| `POST /password/forgot` | no | no | yes (`MailLinkAppContextDep`) | `omit` works |
| `POST /verify-email` | no | no | no (the token is the credential) | `omit` works |
| `POST /password/reset` | no | no ("sets no cookie and signs nobody in") | no | `omit` works |

What breaks if login used `omit`:
- The browser drops the `Set-Cookie` from a non-credentialed cross-origin response, so no session is ever stored.
- The existing cookie is not sent, so multi-account on one device (D5/D6) breaks too.

### F3. `omit` against the identity CORS policy (`app/core/cors.py`, `IDENTITY_POLICY`)

**`omit` gets a readable response:**
- The middleware echoes `ACAO: <exact origin>` (never `*`) and always adds `ACAC: true`.
- A non-credentialed request only needs the ACAO match. The extra `ACAC: true` is ignored and harmless.
- The preflight still happens (`X-MM-App`, and `Content-Type: application/json` is non-simple). It is answered the same way whatever the credentials mode.

**The server-side checks still run:**
- Browsers always send `Origin` on a cross-origin POST, whatever the credentials mode, so `MailLinkAppContextDep`'s Origin + `X-MM-App` check still works.
- Turnstile and the per-IP limits do not depend on cookies.

**Wrong rationale in `consumers.md`:**
- l.112 (signup) and l.161 (forgot) say to use `include` "so CORS answers the preflight". That is wrong: the credentials mode does not decide whether a preflight is answered. This sentence is what the ticket must remove.

**Why `omit` is better, not just equal:**
- Least privilege: the device cookie is not sent to endpoints that ignore it.
- These calls still work when the browser blocks or has cleared cookies.

**Pages that call verify-email/reset must still be on a registered app origin:**
- Otherwise the response is not CORS-readable, even though the server does not check the origin.
- Consumers already serve `/verify-email` and `/reset-password` on every registered origin (l.121), so this holds.

**Existing examples:**
- The verify-email (l.129) and reset (l.183) examples set no `credentials`. The default is `same-origin`, which acts like `omit` for a cross-origin call.
- Make them explicit (`credentials: "omit"`) and remove "Every call: include" (l.39).

### F4. Why `admin.mmlabs.online` must have no DNS record or tunnel route

The real control is that the host is **not in any app's `origins`**, above all not in `apps[admin].origins`.

If a stale `admin.mmlabs.online` stayed in `apps[admin].origins`, anyone able to serve a page there could:
- make credentialed `/api/v1/auth/*` calls with `X-MM-App: admin` that pass CORS and the Origin check;
- in a signed-in admin's browser, read `/auth/token` → `aud=admin` Bearer token → full admin API. That is privilege escalation through subdomain takeover.

Ways to "serve a page there":
- a dangling CNAME;
- a wildcard `*.mmlabs.online` DNS record;
- a catch-all tunnel ingress rule pointing at some app that hosts user content;
- a future service that takes the name.

"No DNS/tunnel route" is defence in depth. It also prevents a look-alike phishing host for the admin UI.

## Recommendations

**R-1 (AC 1, R7): deploy.md.** Replace all 9 occurrences with `https://admin-portal.mmlabs.online`.
- Only hostname text changes inside the trap and curl lines. Quoting is untouched, so `sh -n` stays green. The reviewer should extract the fenced block (l.440–462) and run `sh -n` on it.
- Fix l.482–483 as in F1.

**R-2 (AC 1): rewrite the Host policy paragraph (l.551) to say:**
- `https://admin-portal.mmlabs.online` is the admin UI host (`admin-pan`). It is the only production entry in `apps[admin].origins`.
- Its DNS record and tunnel route point only at `admin-pan` and are never shared with another service or user content.
- Check it whenever DNS or tunnel routes change: whoever serves pages on an admin origin can drive the admin login and token flow (takeover = admin compromise, ADR-0011).
- `admin.mmlabs.online` is **not used**:
  - it must have no DNS record (including via a wildcard `*.mmlabs.online`) and no tunnel ingress or catch-all route;
  - it must not appear in any app's `origins` or in `TURNSTILE_ALLOWED_HOSTNAMES`;
  - reusing it needs an owner decision.

**R-3 (AC 1, F3): one-off check, and an edit to the ADM-012 section.**
- If `admin.mmlabs.online` was ever set in production, run `set-admin-origins --origin https://admin-portal.mmlabs.online`. It uses replace semantics, so this drops the old host.
- Confirm the `- origin https://admin.mmlabs.online` line in the dry run.
- Change "keep the old origin as the second allowed one" (l.208) so it does **not** apply to `admin.mmlabs.online`. It was never live, so there is no overlap to keep.

**R-4 (AC 2): ADR-0008 amendment l.55–57.**
- Say production uses `https://admin-portal.mmlabs.online` (admin UI `admin-pan`, ADR-0011), and that `admin.mmlabs.online` is not used and must have no DNS or tunnel route.
- Add a dated note (owner, 2026-11-16) rather than silently rewriting the accepted text.

**R-5 (AC 2): grep check.** Scope it to `docs/ README.md CLAUDE.md Makefile`, for example `grep -rn 'admin\.mmlabs\.online' docs README.md CLAUDE.md Makefile`. The only expected hits are the "not used / must not exist" sentences in deploy.md and ADR-0008. Exclude `tests/` and `work/`.

**R-6 (AC 3): consumers.md.**
- l.39: replace "Every call: include" with a two-line rule:
  - `credentials: "include"` for login and every cookie-class call (`/token`, `/accounts`, `/apps/{key}/join`, `/logout`, `/verify-email/resend`);
  - `credentials: "omit"` for forgot, verify-email and reset, which neither read nor set the cookie.
- l.161 (forgot): use `credentials: "omit"`, still with `X-MM-App` from a registered origin. Delete the "so CORS answers the preflight" rationale.
- l.129 and l.183: add `credentials: "omit"` to both examples.
- l.112 (signup): it has the same wrong rationale, and signup does not touch the cookie. Recommend `omit` here too and fix the rationale (open question 2).
- Do not say "login/signup set the cookie". Only login does.

**R-7 (follow-up, not in this diff):**
- Check that `@mmlabs/auth-client` (repo `mmlabs-auth`) uses `include` only for login and cookie-class calls. consumers.md l.32 says the page wins and the library gets a fix.
- Optionally annotate the `identity-architecture.md` l.17 diagram ("credentials: include" for cookie-class calls).

## Risks

- **Stale origin left in production (High if it happened):** `admin.mmlabs.online` in `apps[admin].origins` plus any way to serve content there means a path to an admin token. R-2 and R-3 cover it, but only the operator can confirm the production state.
- **Over-correcting to `omit`:** if a writer applies `omit` to login or the cookie-class calls, sign-in silently breaks (no cookie stored, new device each time). The table in F2 should go into consumers.md, or at least its explicit login line.
- **Breaking the runbook:** a stray quote edit in the l.446 trap breaks `sh -n`. Change hostname text only.
- **Merge conflict with ADM-002e:** it edits the same runbook block. Rebase after it merges, as the ticket's dependency says.

## Open questions

1. **Makefile l.29 help string:** update it under "docs only", or log it as a follow-up? I recommend updating it. It is a comment with no behaviour, and leaving it makes the scoped grep fail.
2. **Signup:** the ticket lists forgot, verify-email and reset. Signup also neither reads nor sets the cookie, and l.112 has the same wrong rationale. Should signup move to `omit` in this ticket, or is that a follow-up? This needs the owner or planner.
3. **Production state:** was `admin.mmlabs.online` ever set on production `apps[admin].origins`? If yes, R-3's replace run is required, not optional.
4. **Wildcards:** does `mmlabs.online` have a wildcard DNS record or a catch-all tunnel ingress? If yes, "no DNS record" alone does not make the host unreachable; it must be excluded explicitly.

Key files:
- /home/user/org-admin-api/docs/deploy.md
- /home/user/org-admin-api/docs/adr/0008-admin-api-authentication.md
- /home/user/org-admin-api/docs/consumers.md
- /home/user/org-admin-api/Makefile
- /home/user/org-admin-api/app/modules/auth/router.py
- /home/user/org-admin-api/app/core/cors.py
- /home/user/org-admin-api/docs/adr/0011-admin-cors.md
