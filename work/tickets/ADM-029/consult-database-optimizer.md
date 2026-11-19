# Consult — database-optimizer — ADM-029 (2026-11-19)

**Findings**

Indexes on `bug_reports` (from ADM-028, migration `b3f7a9c1d452`):
- `ix_bug_reports_app_id_status_created_at_id` (app_id, status, created_at, id)
- `ix_bug_reports_created_at_id` (created_at, id)
- `ix_bug_reports_open_user_id_app_id`, partial, WHERE status='open' (user_id, app_id)
- `ix_bug_reports_org_id`, partial, WHERE org_id IS NOT NULL
- `ix_bug_reports_user_id_created_at` (user_id, created_at)

Filter and sort coverage for the list endpoint:
- The stable sort is `created_at DESC, id DESC`. The indexes are ascending, but btree scans backward, so ASC/DESC on both columns is served as long as both columns flip together. Mixed directions would not be served, so fix the sort to `(created_at, id)` in one direction.
- No filter, or date range only: `ix_bug_reports_created_at_id` serves it, including the keyset or offset sort.
- app + status filter: served by the (app_id, status, created_at, id) index. This covers the app filter alone only when status is also fixed or planner skip-scan applies. PG17 has no skip scan, so app-only with sort by created_at is served by a prefix scan on app_id followed by a sort. That is acceptable at the expected volume, and `created_at_id` may win with a filter on app_id.
- status only (the most common staff view, "open"): no leading status index. It falls back to `created_at_id` with a filter, or a seq scan. Fine for thousands of rows. Do not add an index now.
- reporter filter (user_id): `ix_bug_reports_user_id_created_at` serves it.
- date range: served by `created_at_id`.
- org filter is not in the ticket. The partial `ix_bug_reports_org_id` would serve it only if the query includes `org_id IS NOT NULL` or equality (equality implies it).
- Conclusion: no new indexes are needed for ADM-029, and no schema change except the seed migration. Optionally run EXPLAIN on a seeded table to confirm, but volume is tiny. Flag a status-only index as a follow-up only if the table grows past ~100k rows.
- `offset` paging is fine at this size. The `Page[T]` total count is a `count(*)` over the filtered set and is cheap at this size.

Constraints already present (acceptance criterion "DB constraint keeps status and handler consistent" is met by ADM-028):
- `ck_bug_reports_handled_iff_done`: (status='done') = (handled_at IS NOT NULL)
- `ck_bug_reports_handler_only_when_done`
- `ck_bug_reports_status_valid`
- `ck_bug_reports_resolution_note_length`: 1..5000, so an empty note must be normalized to NULL or rejected with 422, not sent to the DB (otherwise a 500 from a CheckViolation).
- `handled_by` is FK to users with ON DELETE SET NULL. If the handler user is deleted, the row keeps status=done and handled_at, handled_by=NULL. This is consistent with the CHECKs. Detail must tolerate a null handler on a done report.
- Reopen must clear status, `handled_by` and `handled_at` in the same UPDATE statement. The CHECKs are not deferrable, so set all three at once.
- `updated_at` has a server default only. Confirm the model's `onupdate` (the TimestampMixin) bumps it, because the ADM-024 ETag/If-Match is likely based on `updated_at`. Check that precision (microseconds) is sufficient and that note-only edits bump it.

Permission seed migration (pattern: `migrations/versions/20261117_e2b7a94d1c58_adm015_stats_permission.py`):
- New file `migrations/versions/<date>_<rev>_adm029_bug_report_permissions.py`, `down_revision = "b3f7a9c1d452"`. I grepped and nothing else revises it, so it is the single head. Re-check at implement time for a parallel branch.
- Data only. Insert both keys with the admin app id `00000000-0000-4000-8000-0000000000a1` (constant `ADMIN_APP_ID`, as in ADM-015), `is_admin = true`, `INSERT ... ON CONFLICT (app_id, key) DO NOTHING`, with `gen_random_uuid()`.
- Keys: `admin:bug-reports:read`, `admin:bug-reports:write`. Descriptions should say that read exposes reporter email (PII) and that write marks done, reopens and edits the resolution note.
- Granted to no role (`admin:*` covers both). Do not touch role_permissions.
- `downgrade()` deletes the keys with `app_id = :app_id AND key IN :keys` (expanding bindparam, as in ADM-015). `role_permissions` rows cascade; note that in the docstring as lossy.
- Check that the permission key format allows a hyphen, because `bug-reports` has one. Look at the key CHECK or regex in `b638a7c84c31_create_rbac.py` and at `d6f03e8b2a15_usr06c_permission_scope.py` (grep matched `ck_permissions` there). If the regex disallows hyphens, the key needs an underscore or a constraint change, which would be a breaking High-path change. Verify first.
- Test file modelled on `tests/test_stats_migration.py`: downgrade to `b3f7a9c1d452`, assert no rows, upgrade to head, assert two rows with `is_admin=true` and 0 role_permissions. Expect `[(True, 0)]` per key.

Other places to update: the BEARER map in `tests/test_route_auth_classes.py` (line ~75 shows the format: `("GET", f"{V1}/bug-reports"): "admin:bug-reports:read"`, plus `/{id}` GET read, PATCH write). Also `docs/identity-architecture.md` and `docs/spec-v1.md` if they list the admin permission catalog, and `docs/deploy.md` (it mentions `admin:stats:read`, so it may list permissions). The OpenAPI contract test needs `x-permission`.

**Recommendations**
1. Reuse ADM-028 indexes. Sort by `(created_at DESC, id DESC)` only. Add no index, and say so in the plan.
2. Build the list query with explicit columns (no `SELECT *`), and use a separate light list schema without `description` or `resolution_note`. Join `users` for the reporter email and name in a single query (no N+1), and join `apps` for the app key. Use `selectinload` or an explicit join, not lazy loading.
3. PATCH: do the status transition and the field writes in one UPDATE or ORM flush. Done sets `handled_by=caller.id`, `handled_at=now()`. Open sets all of them to NULL. A no-op (same status and same note) writes no audit row and no UPDATE, and the status-only no-op must not bump `updated_at` or the ETag.
4. Done to done by a different staff member is a no-op: it must not overwrite the original handler.
5. Lock the row `SELECT ... FOR UPDATE` before the If-Match comparison and the transition. This prevents two staff racing and writing two audit rows or the wrong handler.
6. Empty or whitespace-only `resolution_note`: either 422 or normalize to NULL. Decide in the plan. Enforce `max_length=5000` in the Pydantic schema so no CheckViolation 500 occurs.
7. Date range filters: use `created_from` (>=) and `created_to` (<), timestamptz-aware, and reject `from > to` with 422.
8. The status filter value must be validated against `open|done` (422), not passed through.
9. Add `Cache-Control: no-store` on both list and detail, and on the PATCH response (the body contains the email).
10. Audit rows carry ids and status only (report id, app id, from/to status). Note edits are recorded as a "note changed" flag, not text.
11. The seed migration goes through High-path approval (migrations), so state in the plan that it is data-only and expand-only.

**Risks**
- The permission key hyphen may violate an existing key CHECK (verify first, see above).
- PII: the list exposes the reporter email under `admin:bug-reports:read`. Keep it out of logs and audit.
- `handled_by` SET NULL on user deletion gives done reports with no handler. Schemas should make `handled_by` optional.
- A status-only list filter has no leading index, which is acceptable now.
- ETag/`updated_at` precision, and the note-only change needing a bump, may be ill-defined. Check the ADM-024 helper.
- Another migration head created in parallel while this ticket is open.

**Open questions**
- Is the filter set exactly app (key or id?), status, reporter (user id, or email search, which would be a PII search needing care?), and date range? Recommend app by key (D18), reporter by user id only.
- Should an empty `resolution_note` clear the note (set NULL) or be rejected? Is a note allowed on an open report?
- Does reopen keep the `resolution_note`? The ticket says reopen clears handler and handled_at only. Recommend keeping it.
- Should `If-Match` be mandatory (428) or optional? The ticket says optional, so absent means no check.
- Is a new ADR or decision-log entry needed for the permission names? Do the docs list them?
