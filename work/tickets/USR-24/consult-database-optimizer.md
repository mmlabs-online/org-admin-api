# Consult — database-optimizer — USR-24 (2026-11-19)

USR-24 consult (database-optimizer, read-only). No migration is needed. The version stays `roles.updated_at`, the same as ADM-024.

## Findings
1. **ADM-024 is ready to reuse.** What exists on main:
   - `app/core/etag.py`: `version_etag`, `parse_if_match` and `IfMatch`.
   - `app/core/errors.py`: `PreconditionFailedError(current)` (412 `precondition_failed`, `details=None`, current `ETag` header).
   - `app/db/base.py`: `bumped_updated_at(col)` = `GREATEST(clock_timestamp(), col + 1µs)`, strictly increasing.
   - `app/core/cors.py`: `ADMIN_POLICY` already allows `If-Match` and exposes `ETag`. No CORS change is needed.
   - The org and entitlement services branch on `precondition is not None and not precondition.any`.
2. **Where the PUT gap is.** `RoleService.replace_permissions` is at `app/modules/rbac/service.py:1258`:
   - It writes through `_set_role_permissions` (`:187`), which only touches `role_permissions` rows. The `roles` row is not updated, so `updated_at` never moves (the schema description at `schemas.py:248` says so).
   - `RoleRead` (`schemas.py:221`) has no `updated_at` at all. `RoleDefinitionRead` has it.
3. **Atomicity is already given by the app lock, so a conditional UPDATE is not needed here.**
   - The PUT starts with `_locked_app` → `repo.lock_app`, which is `SELECT … FOR NO KEY UPDATE` on the app row (`repository.py:78`). Every RBAC writer (PUT, PATCH, DELETE, manifest sync, default-role builder, permission delete) takes it first (ADR-0003 §9/§10).
   - Once the lock is held, reading `role.updated_at` and comparing it with the precondition cannot race with another RBAC writer. The compare happens in the same lock-held critical section as the write, so there is no window.
   - Do not copy the ADM-024 `update_if_version` shape. It would be a second mechanism and is not needed.
   - READ COMMITTED caveat: the compare must read the role after the lock is acquired. `_get` runs after `_locked_app`, so it is fine as long as the Role is not already in the session identity map. Either assert this in the plan or load the role with `populate_existing=True`.
4. **The bump must live in the shared write path, or the version lies.** `_set_role_permissions` is also called by manifest sync (`manifest.py:219`) and the default-role builder (`service.py:322`). A bump only in `replace_permissions` would leave the ETag unchanged after a manifest push changed the same role's permissions. That is the exact lost-update USR-24 is meant to prevent.
5. **Permission delete cannot change a role's set.** It refuses with 409 if any role holds the permission (`service.py:1042`), so no extra bump is needed there.
6. **Rename (PATCH) uses `onupdate=now()`**, the transaction start time. A waiter's `now()` can be earlier than a prior `clock_timestamp()` bump, so `updated_at` can go backwards. The ETag still changes (an exact repeat is practically impossible), but it is not monotonic. I recommend switching the rename to `bumped_updated_at` too, if the planner wants strictness. It is optional and small.

## Recommendations
- **Version:** use `updated_at` and no integer `version`, consistent with ADM-024. The ETag is `version_etag(role.updated_at)` and the format is unchanged.
- **Bump:**
  - Add a repository method, e.g. `touch_role(role_id)`: `UPDATE roles SET updated_at = bumped_updated_at(Role.updated_at) WHERE id = :id`, then refresh the role.
  - Call it inside `_set_role_permissions` only when `to_add or to_remove` (after `apply_role_permission_diff`). An unchanged re-PUT must not bump, matching the "no write when nothing changes" comment there and the ADM-024 no-op rule.
  - Update the `RoleDefinitionRead.updated_at` description.
- **Schema:** add `updated_at: datetime` to `RoleRead` (additive, so no R9 break). Build it from `role.updated_at` after the refresh in `create`, `update` (`_read`), and `replace_permissions` (its hand-built `RoleRead` at `:1342`). Do not touch `RoleCatalogEntry`, because the public catalog ETag is body-hashed.
- **Router:**
  - On the PUT, add the `If-Match` header param (same `Annotated[list[str] | None, Header(alias="If-Match", description=IF_MATCH_DESCRIPTION)]` as orgs), plus a `Response`.
  - Declare 412 in the `errors={…}` dict and `ETag` on the 200.
  - Set `ETag` on the PUT response and on `GET /{key}/role-definitions/{role_id}`. The GET response needs the header declared in OpenAPI, as ADM-024 does.
  - The PATCH and POST can optionally return `ETag` too, since it is cheap and lets admin-pan chain edits.
  - `If-Match` on PATCH and DELETE is out of scope (R7), so list it as a follow-up.
- **Service flow in `replace_permissions`:**
  - Take the lock, load the role (404), then compare `role.updated_at` against `precondition.versions`. If the header is `*`, treat it as unconditional. On mismatch raise `PreconditionFailedError(role.updated_at)`.
  - Raise the 412 before any write, so nothing is written and there is no audit row. Do not use `record_failure` or `record_denied` for 412.
  - Put the check after the `require_super` denial block (`:1297`) and before the 409 state rules. That keeps denied attempts audited (R4/ADR-0008); see open question 1.
  - If the precondition is absent, the existing code path stays unchanged.
- **412, not 409.** The owner OK'd 412 for ADM-024. Reuse `PreconditionFailedError` as is (`details=None`).
  - The ticket's `details: [{"reason": "role_changed"}]` is tied to the 409 alternative. I recommend dropping it so 412 behaves identically across all three resources. Otherwise add an optional `details` argument to the error, which touches `app/core/` (High path).
- **Docs:** add a dated USR-24 note under spec §5.3, add the ETag/If-Match line for roles to `docs/consumers.md`, and extend the OpenAPI declaration. No CLAUDE.md change, because R6 already lists 412.
- **Tests (R8):**
  - Matching tag: 200, the new ETag differs from the old, one audit row, permissions changed.
  - Stale tag: 412 with the envelope and current `ETag`, permissions unchanged, audit row count unchanged.
  - `*` and no header: behave as today.
  - No-op PUT with a matching tag: 200, same ETag, no audit row.
  - Weak or malformed tag: 412.
  - Unknown role with a stale tag: 404 wins.
  - 401 and 403 unchanged. A non-super caller adding an `is_admin` permission with a stale tag: pin the chosen order of 403 vs 412.
  - A manifest push or default-role rebuild that changes a role's permissions changes the ETag. A repository test for `touch_role` strictly increasing `updated_at`.
  - Atomicity: conftest uses one connection, so a true two-connection race is not possible. Cover it with a test that the precondition read happens after `lock_app` (spy/ordering), as ADM-024 did with a monkeypatch.
  - Extend `test_openapi_contract.py` `_EXPECTED_ERRORS` with 412 for the PUT, `If-Match` declared as an optional header, and `ETag` declared on the 200 of the PUT and the role-definition GET.

## Risks
- **Missing a writer path.** If the bump is only in `replace_permissions`, manifest sync and the default-role builder silently leave the ETag stale (finding 4). Mitigate by bumping in `_set_role_permissions` and testing the manifest path.
- **Bumping on a no-op write.** This would spuriously invalidate other staff's tags.
- **Existing tests.** Any test asserting that `updated_at` is unchanged after a permission replace, or that exact `RoleRead` JSON has no `updated_at`, will need updating. The manifest tests that compare `roles.updated_at` are the likeliest. Grep for them before planning the test diff.
- **R3 permission/auth class is unchanged** (`admin:roles:manage`, conditional super), so there is no auth risk. The change touches `app/modules/rbac/service.py`, and the file's auth surface counts as a High path. It stops at PLAN for owner approval anyway.
- **Stale-tag 412 ordering** can hide a 403 from audit if the check goes before the denial block.

## Open questions
1. Order of 403 vs 412 for a privileged PUT with a stale tag: 403 first, so the denied attempt stays audited (my recommendation), or 412 first?
2. Drop `details: [{"reason": "role_changed"}]` for consistency with ADM-024 (my recommendation), or extend `PreconditionFailedError` with optional `details`? The extension touches `app/core/errors.py`, which is High path.
3. Should rename (PATCH) also move to `bumped_updated_at`, to make `updated_at` strictly monotonic across both write kinds? It is optional.
4. Should `If-Match` on PATCH role rename and DELETE be a follow-up? I say yes (R7).

Files read: work/tickets/USR-24/ticket.md, work/tickets/ADM-024/plan.md, app/db/base.py, app/modules/rbac/service.py (lines 187, 1087–1348), app/modules/rbac/router.py (265–403), app/modules/rbac/schemas.py (196–260), app/modules/rbac/repository.py (75–100, 300–312), app/modules/organizations/service.py (62–98).
