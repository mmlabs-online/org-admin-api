Status: READY

# Plan — ADM-029: Bug reports — staff list, detail, mark done and reopen

Batch 2026-11-19-3 plan round. Status is READY because every blocking question below has a default that can be
implemented as written. Ming answers the questions in the batch round. If an answer differs from its default, only
the parts named in that question change. Gate: **plan-approval (High path: `migrations/**`)**.

## Scope summary
- `docs/spec-v1.md` §5.12 **Two sides**, Triage: *"staff in admin-pan with Bearer `aud=admin` tokens (R3 class 1):
  list all reports from every app, open one, view its attachments, mark it done or reopen it, and keep a resolution
  note (owner, 2026-11-19, OQ22, OQ25). Two new admin permissions: **`admin:bug-reports:read`** (list, detail,
  download attachments) and **`admin:bug-reports:write`** (mark done / reopen). The owner chooses which staff roles
  get them; `admin:*` covers both. Both keys follow the ADR-0003 grammar (hyphen, not underscore)."*
- §5.12 **A report**: *"On "done": the handler account (`handled_by`) and `handled_at`; an optional staff
  resolution note (OQ25)."*
- §5.12 **Lifecycle** (OQ25): *"Two states: `open` and `done`. Marking done records the handler (`handled_by`) and
  `handled_at`. Staff can **reopen** a report (`done` → `open`), which clears the handler and `handled_at`. Staff can
  write a **resolution note**, visible to staff only."*
- §5.12 **Staff side** (OQ22, OQ28): *"list as `Page[T]` (newest first; filters app, status, reporter, date range),
  detail, attachment download — `admin:bug-reports:read`; mark done, reopen, edit the resolution note —
  `admin:bug-reports:write`. … **No staff delete** in v1."*
- §5.12 **Audit** (OQ28): *"Every staff state change (done, reopen, note edit) writes an audit entry in the same
  transaction (R4, §5.4), again ids and status only. Staff reads and attachment downloads are **not** audited."*
- §5.12 **Retention** (OQ29): *"counted from `handled_at`; a reopen clears `handled_at`"*. A repeated "done"
  therefore must not re-stamp `handled_at`.
- §5.12 **Notifications and dashboard** (OQ31, OQ32): none in v1.
- Ticket scope: both permissions are seeded by migration, `is_admin`, and granted to no role. `GET /api/v1/bug-reports` returns
  `Page[T]` with filters and a stable sort, plus `Cache-Control: no-store`. `GET /{id}` returns the reporter's
  id/email/display name and attachment metadata, which stays empty until ADM-030. `PATCH /{id}` takes
  `status` = `done` or `open` and `resolution_note`, with an optional `If-Match` (412). Each change is audited
  with ids and status only; a no-op writes nothing.
- `work/decisions.md`: no entries.
- CLAUDE.md R3 (class 1 Bearer + `require_permission`), R4, R6 (412), R8, R9 (`/api/v1/bug-reports`, `Page[T]`,
  `PATCH`), R11 (no PII in logs or errors).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | List/detail require `admin:bug-reports:read`; PATCH requires `admin:bug-reports:write`; 401/403 tests, `x-permission` declared, BEARER map updated | `tests/test_bug_reports_staff_api.py` › `test_routes_401_without_token`, `test_read_routes_403_with_unrelated_permission`, `test_patch_403_with_read_only`, `test_admin_wildcard_allows_all`. `tests/test_route_auth_classes.py` (3 BEARER entries; `test_every_route_is_classified` green). `tests/test_admin_cors.py` is parametrized over BEARER. `tests/test_openapi_contract.py` › `test_bug_reports_operations` (x-permission on all three) |
| 2 | Mark done records caller as handler and the time; reopen clears them; a DB constraint keeps status and handler fields consistent | `test_mark_done_sets_handler_and_time` (`handled_by` = caller user id, `handled_at` set from the DB clock), `test_reopen_clears_handler_and_time_keeps_note`, `test_done_again_keeps_original_handler`. The constraint already exists from ADM-028 (`app/modules/bug_reports/models.py:28-29`, `ck_bug_reports_handled_iff_done`, `ck_bug_reports_handler_only_when_done`), and the existing `tests/test_bug_reports_schema_db.py` covers it. Reviewer confirms those tests still pass |
| 3 | Audit rows for done, reopen and note edits, without text content; no row for a no-op | `test_audit_rows_ids_and_status_only` uses a sentinel note and description string and asserts it is absent from `changes`. Also `test_status_and_note_in_one_patch_writes_two_rows`, `test_noop_writes_nothing` (same status, same note, done→done by another staff member; `updated_at`/ETag unchanged), and `tests/test_audit_coverage.py` (PATCH in `AUDITED`, count 27) |
| 4 | 404 unknown id; 422 invalid status; 412 on stale `If-Match` | `test_get_unknown_404`, `test_patch_unknown_404`, `test_patch_invalid_status_422`, `test_patch_empty_body_422`, `test_patch_extra_field_422` (`handled_by` in body), `test_patch_note_empty_422`, `test_patch_note_too_long_422`, `test_patch_stale_if_match_412` (ETag header = current; nothing changed; no audit row), `test_patch_if_match_current_200`, `test_patch_stale_if_match_on_noop_412` |
| 5 | Filters, paging and sort covered by tests; reports from several apps listed together | `test_list_across_apps_newest_first` (≥ 2 apps, order `created_at DESC, id DESC`, tie on `created_at` broken by id), `test_list_filter_app_key`, `test_list_filter_unknown_app_key_empty`, `test_list_filter_status`, `test_list_filter_reporter_id`, `test_list_filter_date_range` (from inclusive, to exclusive), `test_list_from_after_to_422`, `test_list_naive_datetime_422`, `test_list_invalid_status_422`, `test_list_paging_bounds` (limit 0/101 → 422; offset/total), `test_list_and_detail_no_store` |
| 6 | (contract, ADM-028 security review 🟡) Untrusted text is returned verbatim and documented as plain text | `test_metacharacters_round_trip` (`<script>`, `"`, `'`, `&` in description, `page_url` path and host, `app_version`, `user_agent`, and a U+202E bidi character, all returned byte-identical by list and detail), and `test_openapi_contract.py` › `test_bug_report_untrusted_text_documented` |
| 7 | Seed migration has a working downgrade | `tests/test_bug_reports_permissions_migration.py` › `test_downgrade_removes_bug_report_permissions` (down to `b3f7a9c1d452` → no rows; up → both keys `(True, 0)`); `uv run alembic check` clean; `tests/test_rbac_schema.py` › `test_admin_leaf_permissions_seeded` |
| 8 | Concurrent "done" by two staff gives one handler and one audit row | `tests/test_bug_reports_concurrency.py` › `test_concurrent_mark_done_one_row` |

All of them: `make check` green.

## Ming verification after merge
- Run `alembic upgrade head` on production (deploy step, `docs/deploy.md`). Then run audit query 2a and confirm the two new
  keys do not show up as "outside the seed".
- Decide which staff role(s) get `admin:bug-reports:read` / `:write`. Until then only `admin:*` holders have them, as with ADM-015 and ADM-025.
- In admin-pan, once its ticket lands: the list shows reports from several apps, and done/reopen works. Every field renders as text (see the follow-up).

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `migrations/versions/20261119_<rev>_adm029_bug_report_permissions.py` | new, data-only seed of 2 keys, `down_revision = "b3f7a9c1d452"` | AC1, R2 | **YES** (`migrations/**`) |
| `app/modules/bug_reports/router.py` | new admin Bearer router `prefix="/bug-reports"`, `dependencies=[Depends(current_admin)]`; list, detail, PATCH | AC1, AC4, AC5 | no |
| `app/modules/bug_reports/staff_service.py` | new `BugReportStaffService` (list/get/update, audit); HTTP-free (R1) | AC2, AC3, AC4 | no |
| `app/modules/bug_reports/triage.py` | new pure module: transition and no-op decision, audit entries (no I/O) | AC2, AC3, rule 6 | no |
| `app/modules/bug_reports/repository.py` | add `ListFilter`, `page()`, `get_view()`, `get_for_update()`, `apply()` (one UPDATE with `bumped_updated_at`) | AC2, AC5 | no |
| `app/modules/bug_reports/schemas.py` | add `BugReportPerson`, `BugReportListItem`, `BugReportRead`, `BugReportAttachmentRead`, `BugReportUpdate`; untrusted-text field descriptions | AC1–AC6 | no |
| `app/modules/bug_reports/text.py` | add `RESOLUTION_NOTE_MAX = 5000` and `clean_resolution_note()` (same rule as `clean_description`, `text.py:20-27`, via a shared private helper) | AC4, DB CHECK `resolution_note_length` | no |
| `app/modules/audit/actions.py` | add `BUG_REPORT_MARKED_DONE`, `BUG_REPORT_REOPENED`, `BUG_REPORT_NOTE_UPDATED` (after line 80) | AC3 | no |
| `app/api/router.py` | `include_router(bug_report_router)` | mount | no |
| `tests/test_bug_reports_staff_api.py` | new API tests (AC1–AC6) | R8 | no |
| `tests/test_bug_reports_triage.py` | new unit tests for `triage.py` | rule 6 | no |
| `tests/test_bug_reports_permissions_migration.py` | new up/down test, modelled on `tests/test_stats_migration.py` | AC7 | no |
| `tests/test_bug_reports_schemas.py` | add `BugReportUpdate` / `clean_resolution_note` unit tests | AC4 | no |
| `tests/test_bug_reports_concurrency.py` | add `test_concurrent_mark_done_one_row` | AC8 | no |
| `tests/test_route_auth_classes.py` | 3 BEARER entries (lines 64-121); add `"report_id"` to `_fill` (lines 312-330) | AC1 | no |
| `tests/test_audit_coverage.py` | `AUDITED` entry for PATCH + success/failure scenarios (lines 365-457); `len(AUDITED) == 27` (line 489) | AC3, R4 | no |
| `tests/test_rbac_schema.py` | add 2 keys to `ADMIN_LEAF_PERMISSIONS` (lines 48-60) | AC7 | no |
| `tests/test_openapi_contract.py` | `_EXPECTED_ERRORS` entries (lines 307-340); PATCH 412/If-Match + ETag (lines 419-444 pattern); new `test_bug_reports_operations`, `test_bug_report_untrusted_text_documented` | AC1, AC4, AC6 | no |
| `docs/adr/0020-bug-report-triage.md` | new ADR: permissions, list contract, transitions/no-op, note semantics, audit shape, untrusted-text contract | outlives ticket (loop.config Domain focus) | no |
| `docs/identity-architecture.md` | §4 endpoint table row after line 160 (pattern of the ADM-015 row); decision-log row D29 after line 319 | R3 catalog | no |
| `docs/deploy.md` | add both keys to audit query 2a (lines 449-451) | otherwise flagged as "outside the seed" | no |

No other files. The new endpoints use the existing `app/core/etag.py` and `app/core/errors.py` and do not
modify them, so nothing under `app/core/**` or `app/api/deps.py` changes.

## Data model / migration
No change to `bug_reports`. The table, CHECKs and indexes from ADM-028 (`b3f7a9c1d452`) cover everything:
- Consistency: `ck_bug_reports_handled_iff_done` `(status='done') = (handled_at IS NOT NULL)` and
  `ck_bug_reports_handler_only_when_done` (`models.py:28-29`). `handled_by` may be NULL on a done row: the FK is
  `ON DELETE SET NULL` (`models.py:75-77`), so the response schemas make `handler` nullable.
- `ck_bug_reports_resolution_note_length` 1..5000: an empty note never reaches the DB (422 at the schema).
- Indexes (database-optimizer consult): `ix_bug_reports_created_at_id` serves the default sort and the date range,
  `ix_bug_reports_app_id_status_created_at_id` serves app/status, and `ix_bug_reports_user_id_created_at` serves reporter. A btree
  scans backward for `created_at DESC, id DESC`, as long as both columns flip together. **No new index.** A status-only
  index is a follow-up, only past about 100k rows.

**Migration `adm029_bug_report_permissions`** (High path). Expand-only and data-only. Copy
`migrations/versions/20261117_e2b7a94d1c58_adm015_stats_permission.py`:
- `revision` from `make migration m="adm029 bug report permissions"` (empty autogenerate, body by hand),
  `down_revision = "b3f7a9c1d452"`. Re-check at implement time that this is still the single head.
- `ADMIN_APP_ID = "00000000-0000-4000-8000-0000000000a1"`; `PERMISSIONS = [("admin:bug-reports:read", "Read bug
  reports of every app (list, detail, attachments); exposes reporter email"), ("admin:bug-reports:write", "Mark bug
  reports done, reopen them and edit the staff resolution note")]`.
- `upgrade()`: `INSERT … (gen_random_uuid(), CAST(:app_id AS uuid), :key, :description, true) ON CONFLICT (app_id,
  key) DO NOTHING`. Granted to no role, and `role_permissions` is not touched.
- `downgrade()`: `DELETE FROM permissions WHERE app_id = … AND key IN :keys` (expanding bindparam). The docstring states
  that it is lossy: `role_permissions` rows cascade.
- Both keys match `PERMISSION_KEY_RE` (`app/modules/rbac/models.py:34`) and `_PERMISSION_RE`
  (`app/modules/admin_auth/deps.py:37`). Hyphens are allowed.

## API contract
All three routes are R3 class 1: Bearer `aud=admin`, `current_admin` on the router, and `**require_permission(...)` per route.
Paths follow R9 (plural noun, UUID id), so no R9 exception is needed. `Cache-Control: no-store` is set on every 2xx response. Error
bodies carry no resource data, and 412 carries only `ETag`.

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/bug-reports` | query: `app_key` (pattern `APP_KEY_RE`), `status` (`open`\|`done`), `reporter_id` (UUID), `from` / `to` (`AwareDatetime`, aliases as in `app/modules/audit/router.py:49-50`; `created_at >= from`, `< to`), `limit` ≤ 100, `offset` (`PageDep`) | `Page[BugReportListItem]`, order `created_at DESC, id DESC`; unknown `app_key` → empty page (audit-logs precedent) | 200, 401, 403, 422 (bad enum/UUID/pattern, naive datetime, `from > to` → `details=[{"field":"from","reason":"after_to"}]`) | `admin:bug-reports:read` |
| GET | `/api/v1/bug-reports/{report_id}` | — | `BugReportRead` + `ETag` (`version_etag(updated_at)`) | 200, 401, 403, 404, 422 (non-UUID) | `admin:bug-reports:read` |
| PATCH | `/api/v1/bug-reports/{report_id}` | `BugReportUpdate`; optional `If-Match` header (`IF_MATCH_DESCRIPTION`) | `BugReportRead` + `ETag` | 200, 401, 403, 404, 412 (`precondition_failed`, current `ETag`), 422 | `admin:bug-reports:write` |

Schemas (`app/modules/bug_reports/schemas.py`):
- `BugReportPerson`: `id: UUID`, `display_name: str | None`, `email: str | None`. `email` is filled only for the
  reporter and is always `None` for the handler (Q5). Two separate classes are also acceptable (`BugReportReporter` with email, `BugReportHandler` without).
- `BugReportListItem`: `id`, `app_key`, `org_id: UUID | None`, `status: BugReportStatus`, `reporter: BugReportPerson`
  (with email, Q4), `description_preview: str` (first 200 characters, `func.left(description, 200)`, Q6),
  `handler: BugReportPerson | None`, `handled_at: datetime | None`, `created_at`, `updated_at`.
- `BugReportRead`: the list fields, plus `description`, `page_url`, `app_version`, `user_agent`, `resolution_note`
  (all `str | None` except `description`), plus `attachments: list[BugReportAttachmentRead]`, always `[]` until ADM-030.
  `description_preview` is not repeated.
- `BugReportAttachmentRead` (placeholder; ADM-030 fills it): `id: UUID`, `content_type: str`, `size_bytes: int`,
  `created_at: datetime`. ADM-030 may add fields, which is additive. It must not remove these.
- `BugReportUpdate`: `model_config = ConfigDict(extra="forbid")`; `status: BugReportStatus | None = None`;
  `resolution_note: Annotated[str, AfterValidator(text.clean_resolution_note)] | None = None`. A `model_validator`
  enforces three things: at least one field must be in `model_fields_set` (else 422), an explicit `"status": null` is 422, and an explicit
  `"resolution_note": null` means **clear** (Q2). `handled_by`, `handled_at` and every other field are refused by
  `extra="forbid"`.
- Field descriptions (OpenAPI, Q1): `description`, `description_preview`, `page_url`, `app_version`, `user_agent`,
  `resolution_note`, `display_name` get *"Untrusted plain text supplied by the reporter's app (or staff), returned
  verbatim. It may contain HTML metacharacters (`< > & " '`) and invisible or bidi characters. Render it as text, never as
  HTML."* `page_url` adds: *"http(s) only (enforced at submit); re-check the scheme before rendering it as a link."*
  Detail-route description: reporter email is PII, and the route is not audited (OQ28).

PATCH semantics (implemented in `triage.py`, decided under the row lock):
| Request vs current | Write | Audit rows |
|---|---|---|
| `status: done` on `open` | `status='done'`, `handled_by=<caller user_id>`, `handled_at=now()` (DB clock) | `bug_report.marked_done` |
| `status: open` on `done` | `status='open'`, `handled_by=NULL`, `handled_at=NULL`; note kept | `bug_report.reopened` |
| `status` equal to current (incl. done→done by another staff member) | none: the handler and `handled_at` are not re-stamped | none |
| `resolution_note` string ≠ current | `resolution_note=<cleaned>` (allowed on `open` and `done`) | `bug_report.note_updated` |
| `resolution_note: null` and current not null | `resolution_note=NULL` | `bug_report.note_updated` |
| note equal to current (after cleaning), or null on null | none | none |
| status change + note change | one UPDATE | two rows, status row first |

Audit row fields: `actor=principal`, `target=(AuditTarget.BUG_REPORT, id)`, `app_key=<report's app key>`,
`org_id=None` (same as `bug_report.created`, ADR-0019 §8; Q3). `changes`:
`marked_done`/`reopened` → `{"status": {"from": "<old>", "to": "<new>"}}`;
`note_updated` → `{"resolution_note": {"changed": true, "cleared": <bool>}}`. Never note or description text, and
never lengths.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_bug_reports_triage.py` › `test_open_to_done`, `test_done_to_open`, `test_same_status_noop`, `test_note_set_change_clear`, `test_note_same_noop`, `test_null_on_null_noop`, `test_status_and_note_two_entries_status_first`, `test_entries_never_contain_text` | pure transition and no-op rules and audit payloads (rule 6, domain focus "PII in logs") |
| `tests/test_bug_reports_schemas.py` › `test_update_requires_a_field`, `test_update_status_null_422`, `test_update_note_null_means_clear`, `test_update_note_blank_rejected`, `test_update_note_strip_and_max`, `test_update_note_control_chars_rejected`, `test_update_extra_field_rejected` (`handled_by`, `handled_at`) | schema validation, mass assignment |
| `tests/test_bug_reports_staff_api.py` › `test_routes_401_without_token`, `test_read_routes_403_with_unrelated_permission`, `test_patch_403_with_read_only`, `test_admin_wildcard_allows_all` | AC1 |
| … › `test_list_across_apps_newest_first`, `test_list_filter_app_key`, `test_list_filter_unknown_app_key_empty`, `test_list_filter_status`, `test_list_filter_reporter_id`, `test_list_filter_date_range`, `test_list_from_after_to_422`, `test_list_naive_datetime_422`, `test_list_invalid_status_422`, `test_list_paging_bounds`, `test_list_item_shape` (reporter email present, no `description`/`resolution_note`, preview ≤ 200) | AC5, Q4, Q6 |
| … › `test_detail_shape` (reporter id/email/display name, handler without email, `attachments == []`, `ETag` header), `test_detail_done_with_deleted_handler` (`handled_by` NULL → `handler: null`), `test_get_unknown_404`, `test_list_and_detail_no_store`, `test_patch_response_no_store` | detail, PII headers, SET NULL handler |
| … › `test_mark_done_sets_handler_and_time`, `test_reopen_clears_handler_and_time_keeps_note`, `test_done_again_keeps_original_handler`, `test_note_on_open_report`, `test_note_clear_with_null`, `test_patch_bumps_etag`, `test_noop_writes_nothing` (no audit row; `updated_at`/ETag unchanged) | AC2, Q2, Q7 |
| … › `test_audit_rows_ids_and_status_only` (sentinel text absent from `changes`; `app_key`, target, `org_id is None`), `test_status_and_note_in_one_patch_writes_two_rows` | AC3, Q3 |
| … › `test_patch_unknown_404`, `test_patch_invalid_status_422`, `test_patch_empty_body_422`, `test_patch_extra_field_422`, `test_patch_note_empty_422`, `test_patch_note_too_long_422`, `test_patch_stale_if_match_412`, `test_patch_stale_if_match_on_noop_412`, `test_patch_if_match_current_200`, `test_patch_if_match_star_200` | AC4 |
| … › `test_metacharacters_round_trip` | AC6 (security review 🟡, Q1) |
| `tests/test_bug_reports_concurrency.py` › `test_concurrent_mark_done_one_row` (two sessions PATCH `done` at the same time → one `marked_done` row, handler = the first committer) | AC8, row lock |
| `tests/test_bug_reports_permissions_migration.py` › `test_downgrade_removes_bug_report_permissions` | AC7, R2 |
| `tests/test_route_auth_classes.py` (BEARER entries) and `tests/test_admin_cors.py` (parametrized, no edit) | AC1 |
| `tests/test_audit_coverage.py` › `AUDITED[("PATCH", f"{V1}/bug-reports/{{report_id}}")] = ({"bug_report.marked_done", "bug_report.reopened", "bug_report.note_updated"}, _bug_done, _bug_unknown, 404)` | R4 harness (the success scenario marks an open report done, which writes one row) |
| `tests/test_rbac_schema.py` › `test_admin_leaf_permissions_seeded` | seed |
| `tests/test_openapi_contract.py` › `test_bug_reports_operations` (x-permission ×3; PATCH 412 envelope + optional `If-Match` header + `ETag` on 200; GET detail `ETag`), `_EXPECTED_ERRORS` (`get` list: ∅; `get`/`patch` item: `{"404"}`), `test_bug_report_untrusted_text_documented` | AC1, AC4, AC6 |

Test data: insert `BugReport` rows directly (as in `tests/test_bug_reports_submit.py:86`) across ≥ 2 apps from
`tests/rbac_factories.make_app`. Set `created_at` explicitly where order matters. Use staff from
`tests/admin_auth_factories.make_staff` / `make_client(admin_permissions=[...], admin_user=...)`.

## Approach
1. **Migration** (High): write the seed as above, then run `uv run alembic upgrade head`, `uv run alembic check` and the migration test.
2. **Actions**: add the three `AuditAction` members (`app/modules/audit/actions.py`, after line 80). `AuditTarget.BUG_REPORT`
   already exists (line 30). The audit CHECK `action_format` accepts the names (`audit/models.py:34`).
3. **text.py**: factor `clean_description` into `_clean_text(value, max_len)`, and add `clean_resolution_note`. The
   behaviour of `clean_description` stays the same, and the existing tests prove it.
4. **triage.py** (pure): `decide(current_status, current_note, update_fields) -> Decision` (`values: dict` for the
   UPDATE, without `handled_by`/`handled_at`, which the service adds; `status_change: tuple[from, to] | None`;
   `note_change: NoteChange | None`), and `audit_entries(decision) -> list[tuple[AuditAction, dict]]`. Changes are built
   by hand. **Do not use `app/modules/audit/diff`** (identity consult finding 4: it would copy the note into audit).
5. **Repository** (SQLAlchemy only, R1):
   - `page(filter, limit, offset) -> (list[BugReportView], total)`: explicit columns, `JOIN apps`, `JOIN users
     reporter`, `LEFT JOIN users handler` (aliased), `func.left(description, 200)`. One query plus one `count(*)` with the
     same WHERE. No ORM lazy loads (no N+1).
   - `get_view(id) -> BugReportView | None`: the same joins plus the full text fields. `populate_existing`.
   - `get_for_update(id) -> BugReport | None`: `select(BugReport).with_for_update(key_share=True)` (FOR NO KEY
     UPDATE, which does not block FK `KEY SHARE` from ADM-030 attachments), `populate_existing`.
   - `apply(id, values) -> None`: `update(BugReport).where(id).values(**values,
     updated_at=bumped_updated_at(BugReport.updated_at))`, a single statement, so status, `handled_by` and
     `handled_at` change together (the CHECKs are not deferrable). `bumped_updated_at` (`app/db/base.py:17`)
     instead of ORM `onupdate=now()`, so the ETag always moves, even inside one transaction (ADM-024 ABA note).
   - The views are frozen dataclasses (the `EntitlementView` pattern, `entitlements/repository.py:20-55`), so the router never
     touches the ORM.
6. **Staff service** (`staff_service.py`, raises `AppError` subclasses only):
   - `list(filter, limit, offset)`: if `from > to`, raise `UnprocessableError("from must not be after to",
     details=[{"field":"from","reason":"after_to"}])` (as in `audit/service.py:270-274`).
   - `get(id)`: `NotFoundError("Bug report not found")`.
   - `update(id, data, *, actor, ctx, precondition)`, in this order (ADM-024 / entitlements `service.py:109-154`):
     a. lock the row (`get_for_update`), 404 if absent.
     b. If-Match: when `precondition` is present and not `*`, and `report.updated_at` is not in `versions`, raise
        `PreconditionFailedError(report.updated_at)`. This applies **even when the request would be a no-op**.
     c. `decide(...)`. If nothing changes, return `get_view` (no UPDATE, no audit).
     d. On `done`, add `handled_by=actor.user_id, handled_at=func.now()`; on `open`, add both as `None`. Then call `apply()`.
     e. For each `audit_entries` item, call `AuditLogger.record(...)` in the same transaction (R4).
     f. `logger.info("bug_report_updated report_id=%s status=%s actor=%s", …)`, ids only (R5/R11).
     g. Return `get_view`.
   - The lock serializes concurrent PATCHes, so a second "done" sees `done` and is a no-op (Q7).
7. **Router** (`router.py`, HTTP only): list/detail/PATCH as in the contract. Use `_with_etag` like
   `entitlements/router.py:31-36` for the detail 200 and the PATCH 200, and `require_permission(WRITE, errors={404: …, 412: …})`.
   Set `response.headers["Cache-Control"] = "no-store"` and `ETag` on 2xx. Header `If-Match: list[str] | None` →
   `parse_if_match` (`app/core/etag.py:67`). The router is mounted in `app/api/router.py`. It stays separate from
   `auth_router.py` (cookie class), so each router has one auth class (R3).
8. **Tests** as listed. Then update the BEARER map, `_fill`, `AUDITED` + count, `ADMIN_LEAF_PERMISSIONS` and the OpenAPI contract.
9. **Docs**: ADR-0020 (Accepted on merge), the identity-architecture §4 row and D29, the deploy.md query 2a.
10. `make check` green.

Rules: R1 (layering above), R2 (reversible data migration, `alembic check`), R3 (class 1, `x-permission`),
R4 (same-transaction audit, no-op writes none), R5/R11 (no PII in logs/audit/errors; reporter filter is a UUID, never
an email in a query string), R6 (envelope; 412 via the existing `PreconditionFailedError`), R7 (no validation
tightening; see Q1), R8 (tests), R9 (paths, `Page[T]`, `PATCH`, `limit` ≤ 100).

Follow-ups to record in impl-log (not in this diff):
- New ticket: tighten `page_url` host grammar and the `app_version` charset (plus matching DB CHECKs), and optionally strip or flag
  Unicode `Cf` in `description`/`user_agent` (ADM-028 security review 🟡 and 💭). Pending Q1.
- admin-pan (other repo): render every report field as text, show bidi/invisible characters visibly, and build a link from
  `page_url` only after re-checking `http(s)://`, with `rel="noopener noreferrer"`.
- Global `X-Content-Type-Options: nosniff` on JSON responses (`app/core`, High path). Today only the CSV export sets it.
- A status-only list index if `bug_reports` grows past about 100k rows.

## Specialist input used
- **database-optimizer**: adopted no new index, sort `created_at DESC, id DESC` only, explicit-column list query with
  joins (rec. 2), one UPDATE for status and handler fields (rec. 3), done→done keeps the handler (rec. 4), row lock
  before the If-Match check (rec. 5), note max 5000 at the schema (rec. 6), `from` inclusive / `to` exclusive with 422 on
  `from > to` (rec. 7), status enum (rec. 8), no-store on all three (rec. 9), ids/status-only audit (rec. 10),
  data-only seed (rec. 11), the hyphen check (verified OK). Adapted: `updated_at` is bumped via `bumped_updated_at` rather than
  ORM `onupdate` (answers its ETag-precision risk).
- **api-platform-engineer**: adopted a separate admin router, the paths, `ETag` on detail and PATCH (none on list, matching
  entitlements), the filter set with `reporter_id` UUID only, `description_preview` (200), nullable org/handler,
  `attachments: []` always present, `extra="forbid"` plus at-least-one-field, `model_fields_set` for null-clears, the 412
  order, three audit actions, verbatim JSON with no server-side HTML escaping, documented untrusted text, the round-trip
  test, and the admin-pan follow-up. Not adopted here: its recommendation to keep reporter email out of the list. The ticket
  implies email in the list (`no-store` "reporter email is PII" on the list), so that is the default and it is raised as Q4.
  The `app_version` tightening is deferred to a follow-up (Q1), as the consult advised raising it rather than deciding it.
- **identity-access-engineer**: adopted the `require_permission` errors maps, the handler always taken from the principal, the DB-clock
  `handled_at`, no re-stamp on done→done (retention), reopen keeps the note, the row lock (FOR NO KEY UPDATE), the
  If-Match check before the no-op short-circuit, hand-built audit `changes` (no `diff`), the sentinel-text test, all
  listed catalog places (`ADMIN_LEAF_PERMISSIONS`, BEARER, `AUDITED`, deploy.md 2a, identity-architecture §4), and
  the migration up/down test. Audit `org_id = None` follows `bug_report.created` (Q3). Handler shown as id plus
  display name (Q5).

## Assumptions
- **Builds on ADM-028 (MERGED)**: `app/modules/bug_reports/` (model, CHECKs, indexes, `text.py`), migration
  `b3f7a9c1d452`, ADR-0019, `AuditTarget.BUG_REPORT`. No change to the submit route or its validation.
- **Follows the ADM-024 If-Match pattern** (`app/core/etag.py` `version_etag`/`parse_if_match`/`IF_MATCH_DESCRIPTION`/
  `ETAG_RESPONSE_HEADER`, `PreconditionFailedError`, `bumped_updated_at`, entitlements router/service). `If-Match` is
  optional; when absent, the update is unconditional. `*` matches any existing row. `If-Match` is already in the admin CORS
  allow-headers (identity consult finding 5).
- ADM-028's security review 🟡 (HTML metacharacters in the `page_url` host and `app_version`) is handled here only on the read side:
  values come back verbatim as JSON strings, with no server-side HTML escaping, which would double-escape and corrupt data for
  non-HTML clients. The OpenAPI descriptions mark the fields as untrusted plain text, and a round-trip test pins
  the contract. Tightening submit validation changes ADM-028's shipped write contract and needs a CHECK migration. That is
  out of scope (R7) and raised as Q1.
- The 200-character preview cuts by code point, so it can split a grapheme cluster. That is acceptable for a preview.
- PATCH works on reports of disabled apps. No app-status check, since staff must still be able to close them.
- Reopening can put a reporter above the "10 open per user per app" cap. The cap is enforced only on submit (ADR-0019 §6),
  which is acceptable.
- Staff reads are not audited (OQ28). The list and detail routes write nothing.
- `BugReportAttachmentRead` is a placeholder shape so the OpenAPI type is stable for admin-pan. ADM-030 may extend it.
- **Hotspots**: no other open ticket (IN_PROGRESS/REVIEW/DONE: ADM-007, ADM-001, USR-08) edits these files.
  ADM-030 (next batch) will edit `app/modules/bug_reports/{router,schemas,repository}.py`,
  `tests/test_route_auth_classes.py` and the OpenAPI contract test after this merges. Expect a rebase there, not here.
- The single Alembic head is `b3f7a9c1d452` today. Re-check at implement time.

## Blocking questions
None blocks implementation: each has a default, and the plan implements the defaults. Ming confirms or changes
them in the batch round.

1. **HTML metacharacters (ADM-028 security review 🟡).** Should ADM-029 also tighten submit validation (a `page_url` host
   grammar such as `[a-z0-9.-]{1,253}` or IPv6 literal, `app_version` limited to `[A-Za-z0-9._+\- ]`, plus a CHECK migration)?
   **Default: no.** ADM-029 documents the fields as untrusted plain text and adds the round-trip test. A separate
   follow-up ticket (P2, High: migration) does the tightening and the optional Unicode `Cf` stripping, and admin-pan renders
   everything as text. If Ming says "yes, now", ADM-029 grows by `text.py` rules, a second migration that changes CHECKs
   on `bug_reports`, and new 422 tests. Recommendation: still a separate ticket.
2. **Resolution note semantics.** **Default:** an absent field leaves the note unchanged. `null` clears it (audited as
   `note_updated` with `cleared: true`). `""` or whitespace only gives 422. The value is stripped, 1..5000 characters, with
   the same control-character rule as the description. A note is allowed while the report is `open`. Reopen **keeps** the note. Mark done needs no note.
   This differs from the entitlements convention (null = no change) because a note must be clearable.
3. **Audit action shape.** **Default:** three actions, `bug_report.marked_done`, `bug_report.reopened` and
   `bug_report.note_updated`, one row per change kind (a PATCH with status and note writes two rows). `changes` is
   `{"status": {"from", "to"}}` or `{"resolution_note": {"changed": true, "cleared": bool}}`, with `app_key` set and `org_id`
   `None`, as for `bug_report.created`. Alternative: one `bug_report.updated` row with both parts.
4. **Reporter email in the list.** **Default: yes.** The list item carries reporter `{id, email, display_name}`, as the
   ticket implies ("`Cache-Control: no-store` (reporter email is PII)" on the list), and staff with `:read` see it in the
   detail anyway. Alternative (api consult): the list shows only `{id, display_name}` and the email appears only in the detail.
5. **Handler display.** **Default:** `handler: {id, display_name}`, without the email, so staff emails are not exposed to every
   `:read` holder. It is `null` when not done, or when the handler account was deleted (FK SET NULL).
6. **Description in the list.** **Default:** `description_preview`, the first 200 characters. The full description and the
   resolution note appear only in the detail.
7. **Concurrent "done".** **Default:** a second "done" on an already-done report is a no-op 200. It keeps the original
   handler and `handled_at`, writes no audit row and leaves the ETag unchanged. Alternative: 409. A client that wants to detect this can send `If-Match`.

## Split proposal
None. Estimate: about 550 changed production lines (router ~130, staff service ~130, repository ~120, schemas ~110,
triage ~60, migration ~50, text/actions/router mount ~25) plus about 800 test lines and docs. That is one cycle, Size M.
If the implementer finds it over budget, fall back to two slices: (a) the seed migration plus list and detail, then (b) PATCH,
audit and concurrency.
