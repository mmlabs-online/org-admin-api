# Security review — USR-24 (appsec-engineer)

## Attempt 1
**Verdict: PASS from security.** I found no blockers and no should-fix items. One limit: I could not run the tests. `uv run pytest tests/test_rbac_roles_if_match.py tests/test_app_manifest_api.py` gave 62 errors because no Postgres is running in this environment. So I checked the behaviour below by reading the code, and the reviewer still needs a green `make check` (R8).

### Findings

**🟢 The compare and the write are atomic (no TOCTOU).**
- `app/modules/rbac/service.py:1281-1288`: the `updated_at` check runs after `_locked_app`. That lock is `FOR NO KEY UPDATE` on the app row (`app/modules/rbac/repository.py:81-93`).
- `get_role` now uses `populate_existing=True` (`app/modules/rbac/repository.py:194-199`), so the check reads the row as it is after the lock, not an old copy from the session cache. This matters under READ COMMITTED.
- Every path that changes a role's permissions takes the same app lock first:
  - staff PUT (`service.py:1281`)
  - default-role builder (`service.py:288`)
  - manifest (`app/modules/rbac/manifest.py:107`, then `:219`)
  - rename (`service.py:1196`)
  - role delete (`service.py:1233`)
  - permission delete (`service.py:1031`)
- Permission delete cannot change a role's permission set: it returns 409 if any role holds the permission (`service.py:1049`). So skipping the bump there is correct.
- Between the check and the write, no other request can commit a change to this role. A conditional UPDATE is not needed.

**🟢 Nothing is written and nothing is audited on 412.**
- The 412 is raised before any permission lookup, diff, `touch_role` or `audit.record` call.
- The session rolls back on error (`app/db/session.py:25-34`). The only side effect is the app row lock, which ends with the transaction.
- `test_put_stale_if_match_412` and `test_precondition_read_after_app_lock` test this (count of audit rows, no INSERT/UPDATE/DELETE).

**🟢 412 before 403: audit impact is acceptable.** Order is now: route permission 401/403, then 404, then 412, then the body-dependent `admin:*` check that writes a denied audit entry.
- A non-super caller who sends a stale tag gets 412 and leaves no `record_denied` row (`service.py:1306-1319` is never reached).
- This does not let anyone get around the audit. A stale tag can never lead to a write. To actually try the change, the caller must send the current tag or `*` (`precondition.any` goes straight through). That request then reaches the `require_super` check and is audited as today.
- The only gap: repeated stale-tag attempts at privileged changes leave no trail. That is a monitoring signal, not an R4 violation, because R4 covers mutating actions and nothing mutates.
- The route-level `require_permission("admin:roles:manage")` still runs first as a dependency (`app/modules/rbac/router.py:396-411`), so the 412 is only reachable by staff with the right permission. Ming approved this order, and `test_put_stale_and_needs_super_gives_412_no_denied_audit` pins it.

**🟢 The ETag leaks nothing sensitive.**
- `version_etag` is the role's `updated_at` in microseconds since epoch (`app/core/etag.py`, ADM-024, not changed here). No ids, secrets or contents of other tenants.
- The same value is already in `RoleDefinitionRead.updated_at` for `admin:apps:read`, and the audit log has the timestamps.
- The 412 can only come after the 404, so it does not reveal whether a role exists to anyone who could not already find out.
- `RoleRead`, which now has `updated_at`, is only used by admin Bearer routes (`router.py:341, 358, 398`). It does not appear on the cookie, public or app-credential routes.
- The `If-Match` parsing is the existing ADM-024 code. It has a 2048-character cap and an 18-digit cap, and is strict: weak, malformed or over-long values give 412.

**🟢 R3, R4, R6 and R11 are unchanged.**
- R3: same auth class, same `admin:roles:manage` permission, same conditional `admin:*` rule.
- R4: the success-path audit is unchanged and still in the same transaction. A no-op PUT writes no audit row and no bump.
- R6: 412 uses the existing `PreconditionFailedError` envelope with code `precondition_failed` and the current `ETag` header (`app/core/errors.py:135-145`).
- R11: no credential, session or token code is touched.

**🟢 Manifest and default-role paths bump the version.**
- `touch_role` is called inside the shared `_set_role_permissions`, only when the permission set actually changes (`service.py:203-208`). That one function serves the staff PUT, `_build_for_app` (`service.py:328`) and the manifest (`manifest.py:219`).
- The bump is strictly increasing: `greatest(clock_timestamp(), old + 1µs)` (`app/db/base.py:17-23`), so the same tag cannot come back after a change.
- Rename also bumps it (`service.py:1214`), with `refresh(role)` afterwards (`:1218`), so no lazy load is left behind.
- Tests: `test_manifest_change_bumps_role_version`, `test_default_role_rebuild_bumps_member_version`, `test_rename_bumps_updated_at`.

**💭 Note (no change needed).** As consumers.md says, the tag does not change when only derived fields change (`is_default`, `privileged`). For example, if a permission's `is_admin` flag is turned on, a client's cached tag still matches. This does not weaken any security control: the PUT re-checks `require_super`, the default/owner rules and the G3 check against the live data after the lock (`service.py:1302-1347`).

### Follow-ups (out of scope)
1. Add `If-Match` on role PATCH/DELETE, permission PATCH and app PATCH (already listed in the plan).
2. Optionally, if security monitoring wants a trail of repeated stale-tag attempts at privileged changes, add a metric or log counter for 412s on this route. Do not add an audit row.
3. The reviewer must confirm a green `make check` against Postgres; the tests could not run here.
