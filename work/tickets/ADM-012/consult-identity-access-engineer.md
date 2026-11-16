# consult-identity-access-engineer — ADM-012

**Findings**
1. The login and token flow for `X-MM-App: admin` already works once the origin is registered.
   - `DynamicCORSMiddleware` covers only `/api/v1/auth/`. It is credentialed and checks against `AppOriginRegistry`, which holds the origins of every active app in one set (`active_app_origins`).
   - `set_admin_origins` refuses to run unless the admin app is `ACTIVE`, so the admin origins land in that set.
   - `login`, `token`, `accounts` and `logout` use `AppContextDep`, which binds `Origin` to the `admin` app exactly. No identity-side code change is needed for admin-pan to sign in and get a token.
2. `TokenService.issue` already does what admin needs: it checks `audience == app.key`, requires a verified email, and gets roles from `effective_roles(user, "admin")`. A user with no admin role gets 403 `not_member` with `join_available=false`, because admin is `CLOSED` with no default role.
3. Signup, forgot and resend refuse admin through `MailLinkAppContextDep`, using the same 403 as an unregistered origin (ADM-002d). This is consistent. Mail links are built from the request's Origin, so admin-portal can never be a link origin. Staff must sign up, verify and reset through another registered app or `create-account`. The SPA must not offer those screens.
4. The registry stores one set of origins for all apps. It cannot answer "is this origin registered for `admin`?". Reusing it as-is for admin CORS would let any app's origin pass, which breaks the ticket AC.
5. The cookie is `__Host-mm_device` with `Path=/`. CLAUDE.md R11 says `Path=/api/v1/auth`, which is stale. admin-portal and api are same-site, so a credentialed fetch from the SPA to `/api/v1/*` would send the device cookie. `current_admin` ignores it, since it reads Bearer only.
6. Token TTL is 900 s (`ACCESS_TOKEN_TTL_SECONDS`, maximum 3600). There is no refresh token. `docs/consumers.md` already describes the re-call `/token` pattern.
7. `docs/deploy.md` step 4 still says `admin.mmlabs.online`. The owner says the host is `admin-portal`; ADM-014 fixes the doc.

**Recommendations**
- R1 (admin-only origins): extend `AppOriginRegistry` to load `key → origins` in the same cached load and add `is_allowed_for(app_key, origin)`. Keep the TTL, single-flight refresh, fail-closed behaviour and commit invalidation. A second cache would be separate state that can drift; avoid it.
- R2: add a second middleware instance, or a mode of the existing one, for `/api/v1/` paths outside `/api/v1/auth/`.
  - No `Allow-Credentials`; allowed headers `Authorization, Content-Type`; expose `Retry-After, ETag`; always add `Vary: Origin`.
  - Exclude the existing public routes (`/.well-known/jwks.json`, which keeps its `ACAO: *`, and `GET /api/v1/apps/{key}/roles`) or decide on them explicitly. The catalog path sits under the admin `/apps` prefix, so match by route, not by prefix alone.
- R3: the admin CORS must never read `X-MM-App`. Bearer endpoints need no CSRF check, because they take no cookie.
- SPA pattern for `docs/consumers.md`:
  - `fetch(api + "/api/v1/auth/token", {credentials: "include", headers: {"X-MM-App": "admin", "Content-Type": "application/json"}, body: {audience: "admin", account_id}})`.
  - Keep the token in memory only and re-call `/token` about 60 s before `expires_in`.
  - On a 401 from an admin endpoint, re-call `/token` once, then send the user to login. A 401 `session_invalid` from `/token` means log in again.
  - Admin calls use `credentials: "omit"` (or the default), never `"include"`.
  - Re-call `/token` after an admin role change, since `roles` are baked into the token (`current_admin` reloads permissions per request anyway).
- Operator steps after release:
  1. Run `set-admin-origins --origin https://admin-portal.mmlabs.online` through `ops-org-admin-api`, dry-run first. During a host move, keep the old origin as the second of the 2 allowed.
  2. Wait 60 s; the CLI runs in another process, so the API picks the change up only through the TTL.
  3. Add `admin-portal.mmlabs.online` to `turnstile_allowed_hostnames` if that list is non-empty. Login needs Turnstile after 3 failures.
  4. Check the preflight with curl from the admin origin and from another origin.

**Risks**
- If the SPA's fetch wrapper sets `credentials: "include"` globally, admin calls fail CORS. This fails closed, but it will look like an outage.
- A Bearer token stored outside memory (`localStorage`) turns any XSS on admin-portal into a 15-minute admin takeover. The admin-portal CSP is outside this repo.
- With the union registry, a mismatched origin gets CORS on `/auth/*` but 403 from `AppContext`. This is acceptable; do not copy that leniency into admin CORS.
- Origins are compared as the raw strings the browser sends, against stored normalized values. Browsers send the lowercase, default-port form, so this is fine.

**Open questions**
- Should the role catalog and JWKS get admin CORS, or stay as they are? I suggest leaving the catalog as it is and recording that in the ADR.
- Will the SPA show an account picker from `/auth/accounts` (multi-account device), or always use the account returned by login?
- Should CLAUDE.md R11 be corrected to `__Host-mm_device; Path=/` as a follow-up, not in this diff?
