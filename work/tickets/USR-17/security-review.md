# USR-17 security review

## Attempt 1

**Verdict: no blockers and no should-fix items.** I reviewed it statically. I did not run pytest or `make check`, as you asked. `ruff check app tests` is clean and `mypy app` reports no issues (147 files).

What I checked: `git diff origin/main...HEAD -- app tests docs`, against `ticket.md`, `plan.md` and the consult notes.

### What holds
- **Auth class (R3).** The route is on the Bearer `rbac` router, which has `current_admin` at router level, and declares `**require_permission("admin:users:read")` (`app/modules/rbac/router.py:209-214`). It is registered in the `BEARER` map (`tests/test_route_auth_classes.py:98`), so the existing 401 sweeps, the permission-map test and the CORS-matcher test cover it.
- **Error order (no existence oracle).**
  - Without a token you get 401 before any 404 or 422 (`tests/test_rbac_role_holders_api.py:370-373`).
  - Without the permission you get 403, even for an unknown app or role (`:354-364`). This covers both neighbouring permissions, `admin:apps:read` and `admin:roles:assign`.
- **Cross-app IDOR.** `RoleService._get` looks up the role with `get_role(app.id, role_id)`, i.e. `Role.app_id == app_id AND Role.id == role_id` (`app/modules/rbac/repository.py:158-161`). The holder query filters on that app-scoped `role.id` (`service.py:1106-1110`). A role from another app that has holders returns 404 and the email does not appear in the body (`tests/...:318-328`).
- **Injection.** All SQLAlchemy expressions use bound parameters. `active` is typed `bool`, `org_id` is a UUID, and there is no free-text or email search parameter. That keeps PII out of URLs and logs (R11).
- **Data exposure (R5/R11).**
  - `RoleHolderRead` lists its fields explicitly and the repository selects named columns only. `password_hash` and other user columns cannot leak, and the exact key-set test locks this (`tests/...:76`).
  - `Cache-Control: no-store` is set in the handler (`router.py:143`) and asserted (`tests/...:72`).
  - 404 messages are generic ("App not found" / "Role not found"). Nothing is logged.
- **The `active` filter matches the `active` field.** Both use the shared `_not_expired()` (`repository.py:46-47`). `NOT(expires_at IS NULL OR expires_at > now())` cannot be NULL, so `active=false` never drops a row by accident, and the partition test proves it. The count and the row select use the same `conds`. The inner join to `users` cannot drift from the join-free count because `user_id` has `ON DELETE CASCADE`.
- **DoS / N+1.** One count plus one joined select. `limit` ≤ 100 is enforced by `PageDep`. The bounded-query test passes 1 holder vs 6.
- **Permission choice (D2).** `admin:users:read` is the same permission that already guards `GET /users`, `/users/{id}/role-assignments` and join-requests. That is no wider than the PII reach staff have today, and it avoids gating a read on the write permission `admin:roles:assign`. The ticket's owner note accepts this.

### Findings
- 💭 Note: **`admin`-app role holders are visible with leaf `admin:users:read`.** Evidence: `tests/test_rbac_role_holders_api.py:331-340` and plan D3. Anyone with `admin:users:read` can list every holder of `admin:super-admin`, so they can enumerate privileged staff accounts as targets. This is consistent with the per-user list today, is documented in `docs/identity-architecture.md:178`, and is locked by a test. **Fix:** none in this ticket. Make sure the PR body flags it as an owner-visible residual risk, as the plan intends.
- 💭 Note: **`active=true` does not mean "can get tokens".** Evidence: `app/modules/rbac/schemas.py:161-174`. A disabled or unverified holder still shows `active=true`. The `user_status` and `email_verified` fields plus the field descriptions handle this, so nothing to fix. admin-pan PAN-015c should show those two fields next to `active`.
- 💭 Note: **`user_status` is typed `str` in the schema.** Evidence: `schemas.py:168`. OpenAPI shows a free string instead of `enum: [active, disabled]`. The DB check constraint holds it to those two values, so there is no security impact. **Fix (optional, additive):** `Literal["active", "disabled"]`, applied together with `JoinRequestRead` so the two stay aligned. Follow-up only.

### Follow-ups (out of scope)
1. A narrower `admin:roles:read` permission, or an `admin:*` gate on listing `admin`-app holders. Needs an owner decision and a seed migration.
2. Set `no-store` explicitly on `GET /users` and `GET /users/{id}/role-assignments`, both of which carry emails (identity consult OQ2).
3. Replace the remaining inline copies of the not-expired predicate in `app/modules/rbac/repository.py` with `_not_expired()` so they cannot drift.

Files reviewed:
- /home/user/org-admin-api/app/modules/rbac/router.py
- /home/user/org-admin-api/app/modules/rbac/service.py
- /home/user/org-admin-api/app/modules/rbac/repository.py
- /home/user/org-admin-api/app/modules/rbac/schemas.py
- /home/user/org-admin-api/tests/test_rbac_role_holders_api.py
- /home/user/org-admin-api/tests/test_route_auth_classes.py
- /home/user/org-admin-api/tests/test_openapi_contract.py
- /home/user/org-admin-api/docs/identity-architecture.md
