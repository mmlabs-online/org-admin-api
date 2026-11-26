# Consult — identity-access-engineer — USR-39 (2026-11-26)

**Findings**
- `app/modules/tokens/service.py:118-126`: the `not_member` gate runs on `effective_roles(user, app)`; a holder of the (app-scope) designated role passes it with no extra code.
- `service.py:132-134`: the only branch point is `if not org_roles` (today `OrgAccessDeniedError(details={})`); the new branch must end in that same raise.
- `service.py:135`: `effective_roles(..., org_id)` is the union; for a non-member = app-scope roles, so `roles` contains `simap:super-admin`.
- `service.py:94,159,172`: single `self.ttl` for `exp` and `expires_in`; `auth/router.py:197` wires `access_token_ttl_seconds`; `app/core/config.py:158` default **900**, bounds 60..43200; model validators at 216+ for cross-field checks.
- `rbac/repository.py:463-483`: `org_role_keys` checks org active + role unexpired via assignments; a non-member has no row, so the new branch needs its own "org active and of the app" query. `organizations/models.py` has no `app_id` (OQ1).
- `app/core/rate_limit.py:148-160`: `rate_limit("token")` per IP only; `enforce_subject_limit` (176) is the per-subject pattern.
- `sessions/models.py:80`: `account_sessions.created_at` proposed as sign-in time for a re-auth check (**contradicted by the security consult: `upsert_account` keeps `created_at` on re-sign-in — see consult-security-architect.md finding 3**).
- `admin:super-admin` re-check: `"admin:super-admin" in authz.effective_roles(user.id, ADMIN_APP_KEY)` (ignores expired grants, plain read, no admin lock).

**Recommendations**
1. Branch only inside `if not org_roles:` in order: designation set → holder holds it at app scope (unexpired) → live `admin:super-admin` → org active and of the app. Every failure raises the same `OrgAccessDeniedError("No access to this organization", details={})`; org check last so a non-holder never triggers the org query (no timing oracle).
2. One repository method for the org check (OQ89 b): `status = active` AND (EXISTS active role assignment of this app in the org OR EXISTS entitlement for (org, app)). Designation and `admin:super-admin` read in the mint transaction, no cache.
3. Member path first: non-empty `org_roles` → normal token, normal TTL, no `org_access`, no audit, even for a holder.
4. Claims: `org`, `org_roles: []`, `roles` = app-scope roles filtered by `<app>:` prefix, `ent` as today, `org_access: "super_admin"`; never `sid` or `admin:*`. Per-mint `ttl` so `exp` and `expires_in` agree.
5. Config `super_admin_org_token_ttl_seconds: int = Field(default=3600, ge=60, le=43200)` + validator failing startup when above `access_token_ttl_seconds`; `.env.example`. **Default 3600 > access default 900** — decide at PLAN (effective default `min()` or adjust defaults).
6. Audit `token.super_admin_org_issued`, `actor=EndUser(user.id)`, target org, `{app, org_id, jti}`, no email; written in the request session before the response.
7. OQ87: per-account bucket `super_admin_org_token` via `enforce_subject_limit` (user id, e.g. 30/h), only on this branch after holder checks; re-auth max-age (`SUPER_ADMIN_REAUTH_MAX_AGE_SECONDS`, 12 h) — `reauth_required` only reachable after holder checks.
8. Tests: holder cannot reach an org of another app or a suspended org; revocation of each of the three sources; claim snapshot with no `admin:` keys; exactly one audit row; none for normal mints; non-holder 403 body byte-identical for unknown and real orgs.

**Risks**
- If the designated role holds org-scope permissions, `roles` leaks them into every org token (ADR-0015) — supports "no org-scope permissions; power from `org_access`".
- No deny-list, no `sid`: a token lives until `exp` after logout or role removal; short TTL is the only bound.
- Cookie channel (XSS on the app origin) gives cross-org reach; 6 and 7 limit and expose it, do not prevent it.

**Open questions for Ming (defaults)**
- OQ84 `org_access: "super_admin"`, absent otherwise; member path wins.
- OQ85 3600; startup fails above the normal TTL; effective default `min(3600, ACCESS_TOKEN_TTL_SECONDS)`? — yes.
- OQ86 audit successful super-admin mints only.
- OQ87 30/h per account + 12 h re-auth max-age; staff MFA deferred (OQ3).
- OQ89 (b); suspended refused.
- Non-app-scope designated role or expired holder grant = non-holder (silent `org_access_denied`) — yes.
