# consult-security-architect — ADM-002

## Findings

- **F1 (Critical): Organizations are open in production today.** `organizations_router` is always mounted through `api_router` and declares no `permission(...)`. Anyone on the internet can list, create, PATCH and DELETE organizations on api.mmlabs.online. The users and RBAC routers are not exposed in production: `_admin_apis_gate` refuses either flag in staging/production. So the "no auth in production" exposure is the orgs CRUD plus the public `/docs` page.
- **F2: Permission declarations are metadata only.** `app/core/permissions.py` puts `x-permission` into `openapi_extra`, and nothing enforces it. RBAC, assignments and users routes all declare a permission. Organizations routes declare none.
- **F3: Admin tokens cannot be issued yet.** `TokenService.issue` raises `AudienceNotSupportedError` for `admin`. ADM-002 has to unblock this. The ticket scope never says so.
- **F4: The `# ADM-002 guard` sites are comments only.** There are 8 in `rbac/service.py`: admin origins at 272, permission create/update at 480/516, admin role create/edit at 577/596, `set_role_permissions` at 650, assign at 784, revoke at 835. Services already take `actor`, but routers pass `None`.
- **F5: Some earlier findings are already fixed.**
  - Org-scoped admin assignments are refused: the admin app has `org_mode=none`, `org_mode` is immutable, and `_grant_locked` rejects `org_id`.
  - The admin app is held active and `closed` with no default role (line 252).
  - The last super-admin cannot be revoked, and super-admin cannot expire.
  - `admin:super-admin` must keep `admin:*`.
  - The CLI cannot change the admin app.
- **F6: The last-super-admin guard is missing on user disable.** `UsersService.set_status` has no last-super-admin check and no `lock_app("admin")` (USR-02c F2 is still open).
- **F7: Audit is a stub.** `LogAuditRecorder` only logs, and ADM-004 comes after ADM-002.

## Recommendations

1. **Deny by default (AC3).** Use one dependency, `require_permission(key)`, that does three things: verifies the Bearer token, checks `aud == "admin"`, and runs `permits()`.
   - It replaces `openapi_extra=permission(...)`, so the declaration and the enforcement are one object.
   - The enumeration test walks `app.routes` from `create_app()` with both flags on. It fails when any route outside an explicit allow-list lacks that dependency.
   - Allow-list: health, jwks, `/api/v1/auth/*`, `GET /api/v1/apps/{key}/roles`, `/docs`, and `/openapi.json`.
   - Match on the route object, not on a path prefix. The catalog sits under `/apps`.
2. **Organizations.** Add `admin:organizations:read|write`. This closes F1 and is the main reason to ship quickly.
3. **Bearer only (USR-03f N-d).** Read only `Authorization: Bearer`. Never read `__Host-mm_device`.
   - Accept ES256 only and require `typ=at+jwt`.
   - Check `iss`, `aud=admin`, `exp` (with small leeway) and `kid` against the KeySet.
   - Re-check `users.status == active` in the DB on each request. The token lives about 15 minutes, so a disabled staff member is otherwise only stopped when it expires.
   - Load permissions from the DB through `AuthzService.effective_permissions(sub, "admin")`. Do not trust the `roles` claim, so a revoke takes effect at once.
   - Return 401 with `WWW-Authenticate: Bearer` in the error envelope.
4. **Make the guard sites real in the service layer (R1),** with the actor id passed in from the router:
   - Changing admin origins, any `is_admin` or `<app>:*` permission create/update, any admin-app role or permission, and assign/revoke of admin roles or `is_admin`-bearing roles all require `admin:*`.
   - Self-grant of any admin-app role is refused with 403.
   - "Cannot grant permissions you don't hold" is checked against the actor's effective admin permissions.
   - Return 409 for an `is_admin` grant onto a role that has assignments. This closes the member-role escalation from the USR-02d review.
5. **F6.** Add the last-super-admin guard to user disable under `lock_app("admin")`. Self-disable goes through the same guard.
6. **Bootstrap.** Allow `aud=admin` in `/auth/token` only for users with an active admin role (the existing `NotMemberError` path already covers this). Seed the `admin:*` permission keys in a migration. The runbook order is: deploy → signup/verify → `grant-super-admin` → token.
7. **CORS.** Add a second, non-credentialed CORS layer for admin paths, with origins taken only from `apps[admin].origins`.
   - Use `Allow-Credentials: false`, allow the headers `Authorization` and `Content-Type`, and never use `*`.
   - The admin UI also needs its origin in `apps[admin].origins` so the credentialed `/auth/token` call works.
8. **Flags.** Remove both flags and the `_admin_apis_gate` validator in this ticket, so the users and RBAC routers mount unconditionally behind auth. Keep the tests that check a 401 with no token.

## Risks

- **At the moment of deploy:** orgs CRUD changes from open to 401 for everyone, and nobody holds `admin:*` until someone runs `grant-super-admin` on prod.
  - Is any other mmlabs service calling `/api/v1/organizations` today? It would break.
  - USR-04 is DONE, so the D16 blocker is gone.
- **Path-prefix matching** in the enumeration test, or in an allow-list middleware, could leave the catalog or future `/apps/{key}/manifest` (USR-05, app credential) routes open or wrongly locked. That is why the check should be per-route.
- **`admin:*` vs `admin:super-admin`:** right now any role that holds `admin:*` is effectively super-admin. The guard only checks `admin:*`, so who can create a role holding `admin:*` matters. The guard covers that case, but it is circular unless the seed is locked down.
- **No audit:** admin mutations stay unaudited until ADM-004. Grants made before ADM-002 can't be reconstructed.
- **Public `/docs`** will list every admin route once the flags are removed. That is not exploitable, but it is reconnaissance for an attacker.

## Open questions

1. **Should `admin:*` be held only by `admin:super-admin`?** That would mean a 409 when attaching `admin:*` to any other role (from the USR-02d review). Recommended: yes, in scope.
2. **Who calls the orgs API now, and is a coordinated cutover needed?**
3. **Should `/docs` and `/openapi.json` be hidden in production** now that admin routes appear in them?
4. **Staff 2FA (OQ3):** is password-only acceptable for super-admin at launch?
5. **Reviewing rows created while the flags were on (USR-02c F3):** staging and local only, since prod never had the flags. Can this be a follow-up? Recommended: yes.
6. **Scope split.** Proposed:

| In scope | Follow-up |
|---|---|
| R1–R8 above | F3 row review |
| USR-03f N-d | Real audit (ADM-004) |
| USR-02c S1/F1 (verify only) and F2 | Staff 2FA |
| USR-02d member-role 409 | Hiding `/docs` |
| `admin:*` only on super-admin | |
| CORS | |
