## Attempt 1

## Security review: ADM-006 (org product entitlements)

I read the diff with `git diff origin/main...HEAD` (branch head 37dae1e), plus the auth deps, the delete guards, the earlier permission seed migration and the new tests. As you asked, I did not run `make check` or pytest.

### Findings

**No 🔴 blockers. No 🟡 should-fix items.**

All the focus areas you listed hold up:

- **Per-route authz:** the router depends on `current_admin` (admin-audience Bearer only).
  - GET requires `admin:entitlements:read`; POST, PATCH and DELETE require `admin:entitlements:write`.
  - `tests/test_route_auth_classes.py` pins all four routes, and `test_read_permission_cannot_write` proves read cannot write (403 `permission_denied`).
  - `_with_409` mutates a fresh `dict(_ERROR_RESPONSES)` copy, so the shared responses map is not polluted.
- **Cross-org IDOR on `entitlement_id`:** `get_for_org` and `get_view` filter on both `org_id` and `id` (`app/modules/entitlements/repository.py:86-100`). `test_unknown_entitlement_404` checks that another org's entitlement id returns 404 for PATCH and DELETE.
- **Permission seed:**
  - It inserts on the admin app with `is_admin=true` and the default `scope='app'`, and grants to no role. It uses `ON CONFLICT DO NOTHING`, the same pattern as `20261115_5e2a9c7d1f04`.
  - Admin-app permissions cannot be created through the API (`rbac/service.py:855-863`), so no pre-existing row with `is_admin=false` can be shadowed.
  - The downgrade deletes only those two keys on the admin app (its `role_permissions` rows cascade), then drops the index and the table.
  - Upgrade, downgrade, upgrade is tested in `tests/test_entitlements_migration.py`.
- **Mass assignment and limits:**
  - `extra="forbid"` is set on both the create and update schemas, so `org_id`, `app_id`, `id` and timestamps cannot be set from the body.
  - `limits` allows at most 32 keys. Keys must match `^[a-z][a-z0-9_]{0,63}$`. Values must be `StrictInt` between 0 and 2^53-1, so no nested objects, floats or strings get in.
  - The DB checks `jsonb_typeof(limits)='object'` and the `plan` regex.
  - The worst case is about 3 KB per row.
- **Audit (R4):**
  - Grant, update and revoke each write one row in the request transaction, after the flush, with `org_id` and `app_key`.
  - A no-op PATCH, or explicit nulls, writes no row (`if changes:`), and `test_noop_patch_no_audit` covers this.
  - The 404 paths write nothing, and `test_audit_coverage` covers them.
  - The diff fields `app_id/plan/limits/enabled` contain no secrets.
- **FK RESTRICT and delete guards:**
  - Org delete runs `has_entitlements` and returns 409 `org_has_entitlements`.
  - App delete runs `app_has_entitlements` and returns 409 `app_has_entitlements`.
  - Both deletes already catch `IntegrityError`, so a race ends in a 409, not a 500.
- **No service-facing auth added:** there is no app-credential, cookie or public route, no CORS change, and no `/me` exposure. This matches the owner's "staff only" decision.

**💭 Notes (non-blocking):**

1. `app/modules/organizations/service.py:77` and `app/modules/rbac/service.py:516`: the existing `IntegrityError` handlers assume a role assignment was added concurrently. If an entitlement is granted between the guard check and the DELETE, the FK RESTRICT violation is reported as `org_has_assignments` or "App has role assignments", which is the wrong code and message. It is still a 409, and the risk is only a misleading message.
   - Fix: catch `IntegrityError`, re-run `has_entitlements` / `app_has_entitlements`, and raise the matching error. Or use a neutral code such as `org_in_use`.
2. `app/modules/entitlements/service.py:88`: every `IntegrityError` from the grant insert becomes `entitlement_exists`. That includes an FK violation when the org or app is deleted concurrently, which is a race where the right answer is 404. No 500 is possible.
   - Fix: map it by constraint name, e.g. check `uq_org_entitlements_org_id_app_id` in `str(exc.orig)` (or use asyncpg's `constraint_name`), and send FK violations to a 404 or a generic conflict.
3. `app/modules/entitlements/service.py:101-151`: update and revoke do not lock the row. Two concurrent revokes of the same entitlement each write an `entitlement.revoked` audit row, because SQLAlchemy only warns when a DELETE matches 0 rows. Two concurrent PATCHes can record a `from` value that the other one already overwrote. The impact is audit precision only; there is no authz effect.
   - Fix: load with `.with_for_update()` in `get_for_org` for the write paths, or check the DELETE rowcount before auditing.

### Follow-ups (out of scope)

- There is still no global request-body size limit in `app/main.py`. Pydantic caps `limits` only after the whole JSON body is parsed. This affects every endpoint and should go in its own ADM ticket, e.g. a Content-Length cap in middleware or at the proxy.
- Notes 1 and 2 above could also become one small hardening ticket that maps `IntegrityError` by constraint name across services.

**Verdict: PASS (security).** No blocking or should-fix issues; three non-blocking race-condition notes. The reviewer's `make check` result still decides the overall gate (R8).

Files: `app/modules/entitlements/{router,service,repository,schemas,models}.py`, `/home/user/org-admin-api/migrations/versions/20261117_c8a1f4e72d95_adm006_org_entitlements.py`, `/home/user/org-admin-api/app/modules/organizations/service.py`, `/home/user/org-admin-api/app/modules/rbac/service.py`, `/home/user/org-admin-api/app/core/errors.py`. The first five are under `/home/user/org-admin-api/`.
