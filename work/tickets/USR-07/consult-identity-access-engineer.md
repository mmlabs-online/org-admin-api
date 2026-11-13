# consult-identity-access-engineer — USR-07

**Findings**
- Key rules: `APP_KEY_RE ^[a-z][a-z0-9-]{1,31}$`, so `ricotdin-staging` (16 chars) is valid. `AppService.create` refuses `admin` only indirectly (409 "taken"), and `AppService.update` would let the CLI change `admin` origins. The CLI must therefore refuse `admin` itself, before it opens the DB.
- `AppService.create` builds `<key>:member` straight away. With no permissions yet, the role is created empty. `PermissionService.create` checks that the key prefix equals the app key, forces `<app>:*` to `is_admin`, and returns 409 when the permission already exists.
- `build_default_role` sets the member role to exactly "every non-admin permission". It adds and removes as needed, and it takes `lock_app` again (fine inside the same transaction).
- Tokens (`tokens/service.py`): `roles` = `effective_roles` = the user's assigned role keys only. A user assigned an empty `ricotdin:member` still gets `roles: ["ricotdin:member"]`. If they have no assignment, they get `not_member` (`join_available` = policy != closed). The CLI assigns nobody, so ricotdin users get tokens only after USR-04 (verified email) and USR-03b (join).
- CORS: the `after_commit` invalidation only clears registries in the CLI's own process, and that process has none. The API picks up changes through the 60 s TTL. A removed origin keeps working for up to 60 s.

**Recommendations**
- One transaction, in this order: lock or create the app, then upsert permissions, then `build_default_role`. That is app before rows (ADR-0003 §10). On create, a concurrent insert gives `IntegrityError` → 409; map it to a fixed message.
- Change `join_policy` / `org_mode` / name / origins only for flags the operator actually passes (argparse default `None`). On create, the defaults are open/none. Never touch `status`. For a disabled app, print the status instead of quietly re-enabling it. Refuse an `org_mode` change on an existing app.
- `--origin` replaces the list (idempotent). Validate through `AppCreate`/`AppUpdate`. Never print a pydantic `ValidationError`, because it echoes the input. Print a fixed line plus reason codes only.
- Permissions are additive only. Skip ones that already exist, and refuse `<app>:*` and any is_admin flag (that would be an ADM-002-guarded action).
- Rebuild the member role only when the app is created or a permission is added. Otherwise a re-run undoes manual tightening (D9).
- Runbook: changes reach CORS within ≤60 s; loopback origins are ignored in production.

**Risks**
- With an empty member role, every `require_permission()` in ricotdin fails. The backend could then end up checking `"ricotdin:member" in roles` instead of permissions.
- Re-running the rebuild silently widens a role that was tightened by hand.
- If the CLI passes `ricotdin-staging` and `ricotdin` in the wrong order, origins could get swapped between them. Tests should check the separate audiences.

**Open questions**
- Should ricotdin v1 get at least one permission, for example `ricotdin:app:use`, so its backend checks permissions rather than role names?
- Should re-running with no flags be a no-op that prints the current state?
- Should the CLI ever remove permissions? Recommended: no.
