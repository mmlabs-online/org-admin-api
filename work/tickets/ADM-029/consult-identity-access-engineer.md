# Consult — identity-access-engineer — ADM-029 (2026-11-19)

## Consult: identity-access-engineer, ADM-029 (bug reports: staff list, detail, done/reopen)

### Findings
1. **Schema is already there (ADM-028).** `app/modules/bug_reports/models.py` already has `status_valid`, `handled_iff_done` (`(status='done') = (handled_at IS NOT NULL)`) and `handler_only_when_done` (`handled_by IS NULL OR status='done'`). The AC "a DB constraint keeps status and handler fields consistent" is met by these constraints. The only migration this ticket needs is the permission seed (data only, so no `alembic check` drift).
   - Because of `fk_bug_reports_handled_by_users ... ON DELETE SET NULL`, a `done` row can legitimately have `handled_by IS NULL` if the handler account is deleted. The response schema has to allow that, and the constraints already do.
2. **Template for the seed.** `migrations/versions/20261117_e2b7a94d1c58_adm015_stats_permission.py` is the exact pattern to copy: admin app id `00000000-0000-4000-8000-0000000000a1`, `is_admin=true`, `ON CONFLICT (app_id, key) DO NOTHING`, granted to no role, and a downgrade that deletes the keys (lossy on `role_permissions` cascade; document this in the docstring). Both keys pass `_PERMISSION_RE` in `app/modules/admin_auth/deps.py:37`. `admin:*` coverage comes from `permits()`. Permissions are resolved from the DB on each request (`AdminAuthService.principal`), so a revoked grant takes effect immediately and no token-lifetime issue arises.
3. **Places that list every admin permission and must be updated:**
   - `tests/test_rbac_schema.py:48` `ADMIN_LEAF_PERMISSIONS`
   - `tests/test_route_auth_classes.py:64` `BEARER` (3 routes)
   - `tests/test_audit_coverage.py:365` `AUDITED`, asserted equal to non-GET BEARER routes at line 488, so the PATCH must be listed there.
   - `docs/deploy.md:448-451` audit query 2a (otherwise the new keys show up as "outside the seed").
   - `docs/identity-architecture.md` §4 endpoint table (rows like the ADM-015 row at line 160).
   - The admin CORS test (`tests/test_admin_cors.py:273`) is parametrized over BEARER, so it covers the new routes once they are in that map.
4. **Audit.** `AuditTarget.BUG_REPORT` already exists. Only `BUG_REPORT_CREATED` exists as an action (`app/modules/audit/actions.py:80`), so new actions are needed. ADM-028 records `bug_report.created` with `org_id=None` and `changes={"attachment_count": 0}` (`service.py:122-130`).
   - The entitlements update path uses `diff.snapshot`/`diff.diff` (`app/modules/entitlements/service.py:120,143`). Reusing that generic diff for bug reports would copy `resolution_note`, and possibly `description`, into the audit row. That breaks OQ28 and the ticket's "never note or description text".
5. **If-Match pattern.** It is in `app/modules/entitlements/{router,service}.py`: `parse_if_match`, `update_if_version` (conditional UPDATE on `updated_at`), `PreconditionFailedError(current)`, and `ETAG_RESPONSE_HEADER`. `If-Match` is already in the CORS allow-headers. One detail matters here: entitlements returns early as a no-op only when all values are unchanged, and only after the version check passes.

### Recommendations
- **R1 (AC1, R3).** Use `**require_permission("admin:bug-reports:read", errors={404: ...})` on the list and detail routes, and `"admin:bug-reports:write"` with `errors={404, 412, 422}` on PATCH. Put `dependencies=[Depends(current_admin)]` on a new `router.py`, kept separate from `auth_router.py` (cookie class). Do not mount these under `/api/v1/auth/*`.
- **R2 (AC2, privilege integrity).**
  - The PATCH schema should be `extra="forbid"` and accept only `status: Literal["open","done"]` and `resolution_note`. A client must never be able to set `handled_by` or `handled_at`.
  - The handler always comes from the authenticated principal's `user_id`. Never take it from the body.
  - The service sets `handled_at = now()` on the DB or service side, never from client input.
- **R3 (AC2, retention).** `done` → `done` (including a request that only edits the note) must **not** re-stamp `handled_by` or `handled_at`. Spec §5.12 retention counts attachment deletion from `handled_at`, so re-stamping would quietly extend it and also make "no-op writes nothing" false. Reopen (`open`) sets `handled_by = NULL`, `handled_at = NULL` and keeps the note, because the spec only says handler and time are cleared.
- **R4 (AC2/AC3, races).** Without `If-Match`, load the row with `SELECT ... FOR UPDATE` in the repository, or use a conditional `UPDATE ... WHERE status = :expected`. Then decide no-op versus change and write the audit row inside the same locked transaction. Otherwise two staff marking the same report done at once can both "win": the last writer replaces the handler, and you get two `done` audit rows. With `If-Match`, follow the entitlements order: version check first (412 on a stale version, even if the request would be a no-op), then the no-op short-circuit.
- **R5 (AC3, OQ28).** Build the audit `changes` by hand. Do not use the generic diff snapshot.
  - Suggested actions: `bug_report.done`, `bug_report.reopened`, `bug_report.note_updated`. One PATCH can write a status row plus a note row, or one row with combined flags; pick one and test it.
  - Payload limited to ids and status, e.g. `{"status": {"from": "open", "to": "done"}, "handled_by": "<uuid>"}` and `{"resolution_note": {"changed": true}}`. Note-cleared versus set can be a boolean or a length. Never include text.
  - Use `app_key` of the report's app and the same `org_id` convention as `bug_report.created` (currently `None`).
  - Tests should assert that the stored `changes` JSON does not contain the note or description strings, using a distinctive sentinel value in the test.
- **R6 (PII, R11, R5).**
  - Send `Cache-Control: no-store` on list, detail **and** the PATCH response (each body carries the reporter email), and on the 412 body if it includes the resource.
  - Make the reporter filter `reporter_id: UUID`, not an email. Emails in query strings end up in proxy and access logs.
  - Do not log the description, note or email in service logs. Ids only, like the ADM-028 `logger.info`.
- **R7 (AC1/R8).** Tests to include:
  - 401 with no token.
  - 403 with a token holding only `:read` on PATCH, and with an unrelated permission (e.g. `admin:users:read`) on GET.
  - 200 with `admin:*`.
  - `x-permission` present in OpenAPI for all three routes.
- **R8 (migration, R2).** Add a `tests/test_bug_reports_migration.py`-style up/down test, as ADM-015 did (`tests/test_stats_migration.py`): the keys exist after upgrade with `is_admin=true` and no `role_permissions` rows; they are gone after downgrade; re-running upgrade is idempotent.

### Risks
- **High path.** This touches RBAC seeding and a migration, so it stops at PLAN for Ming's approval and must not be auto-merged.
- **Audit leaking content** if the generic diff helper is reused (Finding 4). This is the most likely defect in this ticket.
- **Last-writer-wins on handler** without a row lock or conditional update (R4). That makes the audit trail inconsistent with the row's final state.
- **Retention drift** if a repeated `done` re-stamps `handled_at` (R3).
- **PII in caches or logs** if `no-store` is missing on PATCH or 412 responses, or if the reporter filter takes an email (R6).
- **The `deploy.md` audit query** will flag the new keys as unexpected unless it is updated (Finding 3).

### Open questions (for planner / Ming)
1. **Clearing the note.** Does `resolution_note: null` clear the note, or is it ignored as "no change" (the entitlements convention)? The CHECK rejects `""`, so an empty string should be a 422. Recommendation: allow explicit `null` to clear, audited as `note_updated` with `{"changed": true, "cleared": true}`.
2. **Note while open.** Can a note be written while the report is `open`? The CHECK allows it, and the spec does not forbid it. Recommendation: allow it.
3. **Audit shape.** One combined action (`bug_report.updated` with status from/to plus a note flag) or the three separate actions from R5? This affects the `AUDITED` map in `test_audit_coverage.py`.
4. **Audit `org_id`.** Should staff-change audit rows carry the report's `org_id`, or `None` to match `bug_report.created`? Keep the two consistent either way.
5. **Handler display.** Should the detail show only the handler's id, or also their email and display name? Expanding it exposes staff PII to every `:read` holder. Recommendation: id plus display name only.
