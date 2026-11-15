# consult-identity-access-engineer — ADM-002b

**Findings**

1. All eight `# ADM-002 guard` sites in `app/modules/rbac/service.py` are comments only. Each public mutator takes `actor: uuid.UUID | None = None`, and no router passes it. As a result `granted_by` is always NULL, including for grants made through the API while the flags were on.
2. `bootstrap_super_admin` calls `_grant_locked(..., actor=None)`. If "None = skip the guards" becomes the convention, any router that forgets the argument gets no guards at all (fail-open).
3. `AdminPrincipal` (`app/modules/admin_auth/service.py`) holds no ORM or FastAPI objects, so services can take it without breaking R1. `rbac.service` → `admin_auth.service` creates no import cycle. `PermissionDeniedError` already exists in `app/core/errors.py`. FastAPI caches `current_admin` per request, so a route can also declare `principal: AdminDep` without loading it twice.
4. `UserService.set_status` has no lock, no actor, no last-super-admin check and no session revocation. `SessionRepository.revoke_all_accounts_of_user` already exists: it revokes account sessions and keeps device sessions, which is right.
5. Lock order is app → user everywhere: bootstrap (admin app, then user `FOR SHARE`) and membership join. Nothing locks a user and then an app, so disable can lock `lock_app("admin")` → `users.get_for_update(user_id)` without a deadlock.
6. `AppService.create` and `update` never check origins against other apps (USR-03b F4). `set_admin_origins` already has the pattern to reuse: `app_keys_holding_origins(claimed, exclude_key)`, with loopback origins excluded.
7. `set_admin_origins` runs the drift refusal before it looks at `origins`, so `--clear` fails on a drifted row.
8. `verify_access_token` passes `options={"require": ...}` without `strict_aud`. PyJWT is 2.15.1, which supports `strict_aud`.
9. Settings use `extra="ignore"`, so a stale `ADMIN_*_API_ENABLED` left in the deploy env will not crash startup once the fields are removed. Files that reference the flags: `.env.example`, `docs/deploy.md:297`, `tests/test_config.py`, `tests/test_route_auth_classes.py` (its `FLAGS`), `test_users_admin.py`, `test_cli_grant_super_admin.py`, and every `make_client(admin_*_api_enabled=True)` call.
10. Seed migration `5e2a9c7d1f04` uses `ON CONFLICT DO NOTHING`, so rows created earlier with `is_admin=false` stay false.

**Recommendations**

- **Actor plumbing (R1).**
  - Make `actor: AdminPrincipal | SystemActor` a **required keyword with no default** on every mutator. `SystemActor` is a module-level sentinel used only by the CLI paths.
  - Add a helper `_require_wildcard(actor)` that passes for `SystemActor` or when `permits(actor.permissions, "admin:*")` is true. Otherwise it raises `PermissionDeniedError(details={"required": "admin:*"})`, the same shape as `RequirePermission`.
  - Routers add `principal: AdminDep` and pass it on. `granted_by` = `actor.user_id`, or NULL for the CLI.
  - Check order after the lock: 404 → 422 → 403 → 409.
- **Rule per guard site.** Every rule below means "requires `admin:*`" unless stated otherwise.
  - `AppService.update`, when `app.key == "admin"`: any change that would actually write, origins included.
  - `AppService.create` and `update`: refuse with 409 `origin_taken` any non-loopback origin another app holds, `admin` included.
  - `PermissionService.create`: when the app is `admin`, or the resolved `is_admin` is true, or the key is `<app>:*`. Also force `is_admin=true` for admin-app permissions (422 if the caller sends `false`).
  - `PermissionService.update`: when `is_admin` actually changes, or the app is `admin`. `PermissionService.delete` on the admin app is also covered by "anything on admin".
  - `RoleService.create`, `update` and `delete` on the admin app.
  - `replace_permissions`:
    - Compute the diff against `role_permission_keys`.
    - Require `admin:*` if the app is `admin` or any added or removed permission is `is_admin`. Removing one de-escalates everyone holding the role, which is the same as a revoke.
    - Then 409 if `admin:*` is in the target set and the role is not `admin:super-admin`.
    - Then 409 if a newly *added* `is_admin` permission lands on a role where `role_has_assignments`. Base this on the diff, so re-PUTting super-admin unchanged still works.
  - `_grant_locked` and `revoke`:
    - Require `admin:*` if the app is `admin` or `role_has_admin_permission(role)`.
    - 403 `self_assignment` if the app is `admin` and `user_id == actor.user_id`, even for super-admins.
    - Drop the "may not grant what you don't hold" comment. Admin principals only hold `admin:*` keys, so the `admin:*` rule already covers it.
- **Self-revoke.** Refuse it, as the ticket says. That avoids accidental lockout and keeps the rule symmetric. Update the conflicting code comment.
- **User disable.**
  - Add an actor to `set_status`.
  - Refuse self-disable with 409 `self_disable`.
  - Then `lock_app("admin")` → `get_for_update(user)`.
  - Add a new repository helper "counting super-admins other than this user". If the target counts and the others are 0, return 409.
  - Then flip the status and call `revoke_all_accounts_of_user` in the same transaction.
  - Also require `admin:*` to disable or re-enable a user who holds any active admin-app or `is_admin` role. Otherwise a holder of `admin:users:write` can disable super-admins (all but the last). This is an addition to the ticket and the planner should confirm it.
- **`strict_aud`.** Add `"strict_aud": True` to the decode options. Tests: `aud=["admin"]` and `aud=["admin","x"]` → reason `audience`. Mirror this in `docs/consumers.md`.
- **Seed fix.** New data migration: `UPDATE permissions SET is_admin = true WHERE app_id = <admin> AND is_admin = false`. Apply it to all admin-app rows, not only the 8 keys. `downgrade()` is a documented no-op: the earlier values cannot be recovered, and R2 needs this exception noted. Do not edit `5e2a9c7d1f04`.
- **`--clear`.** If `origins == []`, still lock and require that the row exists, then skip only the drift tuple check and write only `origins`. Add a test: a drifted row (for example `join_policy=open`) plus clear gives `UPDATED`, with the other columns unchanged.
- **Flags.** Delete both fields and the validator, mount the routers unconditionally, remove the `.env.example` keys, and fix the router docstrings and the `main.py` `/docs` comment.
- **Audit (impl-log).** `granted_by` cannot tell API grants from CLI grants (finding 1), so the impl-log should list instead:
  - every admin-app role, permission and assignment, with `created_at`;
  - every `role_permissions` row holding an `is_admin` permission on a role that has assignments;
  - the counting super-admins.

  Production should show only the bootstrap super-admin, because the gate refused the flags there.
- **Split.** My estimate is about 350–450 production lines. The guards, user disable, origin checks and flag removal must land together (AC 4); disable and origins are also exposed the moment the flags go. If the plan goes over about 500 lines, move the peripheral items to ADM-002d: `strict_aud`, the seed fix, `--clear`, the deploy.md runbook items and the `X-MM-App: admin` signup/forgot refusal.

**Risks**

- **Stale permissions.** Permissions are loaded before the app lock. A super-admin revoked concurrently can pass one `admin:*` check in that window. This is acceptable for v1. Optionally, re-read `effective_permissions` after the lock for `admin:*`-gated decisions (one query).
- **Origin race.** Two concurrent creates or updates can claim the same origin, because they lock different rows or none. Either accept this (staff-only), or have every origin writer (CLI register, `set_admin_origins`, create, update) take a transaction-scoped `pg_advisory_xact_lock` *after* its app lock.
- **Sessions survive disable.** Disable revokes account sessions, but app access tokens already issued stay valid until they expire. Admin tokens are re-checked on every request, so those stop at once. Pending reset and verification tokens stay unusable because login and reset refuse disabled users; confirm this with a test.
- **Wide test churn.** Every `make_client` call with the flags breaks once they are removed. Budget the test diff (likely more than 800 lines).
- **CORS reach of `admin:apps:write`.** Whoever holds it can add origins to any app, which feeds credentialed CORS on `/auth/*`. That is acceptable as a staff power and goes in the threat note.

**Open questions**

1. Self-revoke: refuse it, as the ticket says, or allow it subject to the last-super-admin guard, as the code comment says? I recommend refusing.
2. Should disabling or enabling an admin-role holder require `admin:*`? I recommend yes.
3. What status code for self-disable: 409 (state) or 403 (policy)? I recommend 409 `self_disable`.
4. Owner decision: keep `/docs` and `/openapi.json` public in production? They list every admin route and its `x-permission`. I recommend `docs_url=None, openapi_url=None` when `app_env == "production"`.
5. Is a no-op `downgrade()` acceptable under R2 for the seed fix migration?
