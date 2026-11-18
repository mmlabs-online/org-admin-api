Status: READY

# Plan — ADM-021: Audit log — org filter and single-entry read

## Spec summary
`docs/spec-v1.md` §5.4 Audit log:
> Append-only record of every mutating admin action: actor, action, target type/id, before/after (or diff), timestamp, request id. Read-only, filterable listing for auditors.

`docs/spec-v1.md` §6:
> p95 latency < 200 ms for reads at expected admin load.

This ticket adds one more filter (`org_id`) to the existing listing and a read-only single-entry route. Both are additive within v1 (R9). No new permission: both routes use the existing `admin:audit:read` (ADR-0009 §8).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | `GET /api/v1/audit-logs` accepts an optional `org_id`. It matches rows whose `target_type`/`target_id` is that organization OR whose `org_id` column equals it, and it combines with the existing filters. | `tests/test_audit_logs_api.py` › `test_filter_org_id_target_or_column`, `test_filter_org_id_row_matching_both_once`, `test_filter_org_id_with_other_filters`, `test_filter_org_id_other_target_type_not_matched`, `test_unknown_org_id_empty_page`; `org_id` case added to `test_422_validation`. |
| 2 | `GET /api/v1/audit-logs/{id}` with `admin:audit:read` returns one `AuditLogRead`, with the same `user_agent` handling as the list. An unknown id returns 404. | `test_get_audit_log_matches_list_item` (full JSON equality with the list item, including `user_agent`, `ip`, `changes`), `test_get_audit_log_404`, `test_get_audit_log_422_malformed_id`. |
| 3 | The `org_id` filter is index-backed, or the planner shows it meets §6 (p95 < 200 ms). | New partial index `ix_audit_log_org_id`. Test `tests/test_audit_schema.py` › `test_org_filter_uses_indexes` checks that the plan uses `ix_audit_log_org_id` and `ix_audit_log_target` and has no `Seq Scan on audit_log`. Manual evidence: `EXPLAIN (ANALYZE, BUFFERS)` of the page query and the count query on a table seeded with at least 100k rows after `ANALYZE`, pasted into `impl-log.md` with execution times. `alembic check` is clean. |
| 4 | Tests: `org_id` alone and with other filters (target match and column match), single read happy path, 404, 401/403 on both routes. | The tests in rows 1 and 2, plus `test_get_audit_log_401`, `test_get_audit_log_403_without_permission`, the existing `test_401_without_token`/`test_403_without_permission` for the list, and the new route in `BEARER` of `tests/test_route_auth_classes.py` (which runs the parametrized 401 tests). `make check` is green. |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/audit/repository.py` | Add `org_id: uuid.UUID \| None = None` to `AuditLogFilter`. Add the OR clause in `list`. Add `async def get(self, audit_log_id) -> AuditLog \| None` (`session.get`). Update the module docstring, which today says "`add` and `list` are the only row operations", to list `get` as a read. | AC1, AC2 (R1: SQLAlchemy only in the repository) |
| `app/modules/audit/service.py` | `AuditLogService.get(audit_log_id)` raises `NotFoundError("Audit log entry not found")` when the row is `None`. | AC2 (R1: the service raises `AppError` subclasses) |
| `app/modules/audit/router.py` | Add the `org_id` query param (with an OpenAPI description) to `list_audit_logs` and pass it into the filter. Add `GET /{audit_log_id}`, `response_model=AuditLogRead`, `require_permission("admin:audit:read")`, with a declared 404 response. | AC1, AC2 (R3 class 1, R9) |
| `app/modules/audit/models.py` | Add `Index("ix_audit_log_org_id", "org_id", "occurred_at", "id", postgresql_where=text("org_id IS NOT NULL"))` to `__table_args__`. | AC3 (R2: model and migration agree, `alembic check` is clean) |
| `migrations/versions/20261118_<rev>_adm021_audit_log_org_id_index.py` (new) | `create_index` / `drop_index` for `ix_audit_log_org_id`. `down_revision = "c7a3e91f5d28"`. | AC3 (R2) |
| `tests/test_audit_logs_api.py` | New tests for the filter, the single read, 401, 403, 404 and 422. | AC4 (R8) |
| `tests/test_audit_schema.py` | `test_org_filter_uses_indexes` (EXPLAIN-based, same style as `test_outcome_partial_index_used`). | AC3 |
| `tests/test_audit_org_index_migration.py` (new) | Downgrade to `c7a3e91f5d28` removes the index; upgrade to head restores it with the right definition. | R2 (working `downgrade()`) |
| `tests/test_route_auth_classes.py` | Add `("GET", f"{V1}/audit-logs/{{audit_log_id}}"): "admin:audit:read"` to `BEARER`. Add `"audit_log_id"` to the `_fill` name list. | R3 (`test_every_route_is_classified` and `test_bearer_route_permission_map` fail without it; the parametrized 401 tests then cover the new route) |
| `docs/identity-architecture.md` | Route table (§4, the line for `GET /api/v1/audit-logs`): add `org_id` to the filter list and add a row for `GET /api/v1/audit-logs/{audit_log_id}` (ADM-021). | Keep the R3 route catalogue accurate |

No other files. `docs/spec-v1.md` is **not** edited, because §5.4 already covers a "filterable listing".

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_audit_logs_api.py` › `test_filter_org_id_target_or_column` | Org `O`. Row A: `target_type="organization", target_id=O, org_id=None`. Row B: `action="entitlement.created"` (or any valid action), `target_type="entitlement", target_id=<other>, org_id=O`. Row C: an unrelated org. `?org_id=O` returns exactly [B, A] (newest first) with `total == 2`. |
| › `test_filter_org_id_row_matching_both_once` | A row with `target_type="organization", target_id=O, org_id=O` appears once, with `total == 1`. |
| › `test_filter_org_id_other_target_type_not_matched` | A row with `target_type="user", target_id=O, org_id=None` is **not** returned for `?org_id=O`. The pair is checked together. |
| › `test_filter_org_id_with_other_filters` | `org_id` ANDed with `action`, with `outcome=denied`, and with `app_key`. Each case returns only the matching subset, and both a target-match row and a column-match row are exercised. |
| › `test_unknown_org_id_empty_page` | A random UUID returns 200 with `{"items": [], "total": 0, "limit": 20, "offset": 0}`. |
| › `test_422_validation` (extend the parametrize) | `{"org_id": "not-a-uuid"}` returns 422 `validation_error`. |
| › `test_get_audit_log_matches_list_item` | Insert a row with `ip`, `user_agent`, `changes`, `org_id` and a target. `GET /audit-logs/{id}` returns 200. The body equals the corresponding list item exactly, and `set(body) == READ_KEYS`. |
| › `test_get_audit_log_404` | A random UUID returns 404 with `error.code == "not_found"` (use the code `NotFoundError` emits). |
| › `test_get_audit_log_422_malformed_id` | `/audit-logs/not-a-uuid` returns 422 `validation_error`. |
| › `test_get_audit_log_401` | Unauthenticated client returns 401 `unauthorized`. |
| › `test_get_audit_log_403_without_permission` | `make_client(admin_permissions=["admin:users:read"])` returns 403 with `details.required == "admin:audit:read"`. With `["admin:audit:read"]` it returns 404 for a random id (the guard passes). |
| `tests/test_audit_schema.py` › `test_org_filter_uses_indexes` | `SET LOCAL enable_seqscan = off`, then EXPLAIN the **count** form of the filter: `SELECT count(*) FROM audit_log WHERE (target_type = 'organization' AND target_id = :o) OR org_id = :o`. Assert `ix_audit_log_org_id` and `ix_audit_log_target` both appear and `Seq Scan on audit_log` does not. Also call `AuditRepository(db_session).list(AuditLogFilter(org_id=uuid4()), limit=1, offset=0)` so the method itself runs. This mirrors `tests/test_stats_repository.py::test_login_count_uses_action_index`. |
| `tests/test_audit_org_index_migration.py` › `test_org_index_down_up` | Plain `def`, as in the other migration tests. `command.downgrade(cfg, "c7a3e91f5d28")`: `ix_audit_log_org_id` is absent from `pg_indexes`. `command.upgrade(cfg, "head")`: it is present and `indexdef` contains `WHERE (org_id IS NOT NULL)`. Use a `finally` that upgrades to head. Reuse the `_cfg()` shape of `tests/test_join_requests_cancelled_migration.py`. |
| `tests/test_route_auth_classes.py` (map entry) | The parametrized `test_every_bearer_route_401_without_token` / `_401_with_cookie_only` and `test_bearer_route_permission_map` now cover the new route. |

## Approach
1. **Model and migration (R2).**
   - Add `ix_audit_log_org_id` `(org_id, occurred_at, id) WHERE org_id IS NOT NULL` to `AuditLog.__table_args__`, next to `ix_audit_log_app_key` (same partial style).
   - Write the migration by hand (`make migration m="adm021 audit log org_id index"`, then edit it). Set `revision` to the generated id and `down_revision = "c7a3e91f5d28"`. The file chain is linear and its only head is `c7a3e91f5d28` (usr15, which revises `e2b7a94d1c58`). Confirm with `uv run alembic heads` before committing.
   - `upgrade()`: `op.create_index("ix_audit_log_org_id", "audit_log", ["org_id", "occurred_at", "id"], unique=False, postgresql_where=sa.text("org_id IS NOT NULL"))`.
   - `downgrade()`: `op.drop_index("ix_audit_log_org_id", table_name="audit_log")`. DDL is not blocked by the append-only trigger (UPDATE/DELETE/TRUNCATE only).
   - Use a **plain** `CREATE INDEX`, following both prior index migrations: audit's own `af3eb84d2d5e` (`ix_audit_log_outcome`) and `4d4b5d67dc44`. The docstring states why: the table is young and small in production (ADM-004 shipped 2026-11-15), and the build lock is brief. It cites the `docs/deploy.md` "Future audit DDL on a large table" note as the rule to switch to `CONCURRENTLY` later.
   - Run `uv run alembic check` and get no drift.
2. **Repository (R1).**
   - `AuditLogFilter.org_id`. In `list`, when it is set, append one element to `where`: `or_(and_(AuditLog.target_type == AuditTarget.ORGANIZATION.value, AuditLog.target_id == filters.org_id), AuditLog.org_id == filters.org_id)`. Import `AuditTarget` from `app.modules.audit.actions`.
   - A single OR cannot duplicate rows. The same `where` feeds both the count and the page query.
   - Add `get(audit_log_id)` returning `await self.session.get(AuditLog, audit_log_id)`.
   - Update the docstring: `add` is the only write; `list`, `get` and `count_actions_since` are reads.
3. **Service (R1).** `AuditLogService.get` raises `NotFoundError("Audit log entry not found")` on `None`. The message has no id echo and no PII (R5/R11). No validation that the org exists (see Assumptions).
4. **Router (R3, R9).**
   - Add `org_id: Annotated[uuid.UUID | None, Query(description=...)] = None` to the list route. The description says: "Rows about this organization: the target is this organization, or the row's `org_id` equals it. Combined with the other filters by AND (e.g. with `target_type=user`, only user-targeted rows whose `org_id` is this org)."
   - New route `@router.get("/{audit_log_id}", response_model=AuditLogRead, **<require_permission("admin:audit:read") + responses {404: "Audit log entry not found"}>)`. Its body is `AuditLogRead.model_validate(await AuditLogService(session).get(audit_log_id))`. This is the same read model as the list; there is no extra transformation, since `user_agent` is sanitized on write (`service.py:_clean_user_agent`).
   - Add the 404 declaration inline: copy the `_route` helper pattern from `organizations/router.py` locally, or set `kwargs["responses"][404]` directly. Do not move the helper into shared code (R7).
   - The router-level `current_admin` already makes this R3 class 1 (Bearer).
   - Not audited: read-only (R4 covers mutations only), the same as the list.
5. **Tests**, as listed above. Then the docs row in `docs/identity-architecture.md`.
6. **AC3 evidence.**
   - Locally, insert about 200k rows into the dev DB (e.g. `INSERT ... SELECT FROM generate_series`, with roughly 5% carrying an `org_id` and roughly 2% `target_type='organization'`, spread over a few hundred org UUIDs). Run `ANALYZE audit_log`.
   - `EXPLAIN (ANALYZE, BUFFERS)` the SQLAlchemy-rendered page query (`ORDER BY occurred_at DESC, id DESC LIMIT 20`) and the count query for one org.
   - Paste both plans and timings into `impl-log.md`. Expect a BitmapOr over `ix_audit_log_target` and `ix_audit_log_org_id`, and single-digit ms.
   - Do this on a scratch DB or roll back. Do not leave seeded rows: audit rows cannot be deleted without the purge latch, so use the test DB inside a transaction or a throwaway database.
7. Run `make check`. It must be green (R8).

Rules: R1 (layering, steps 2–4), R2 (step 1), R3 (step 4 and the auth-classes map), R4 (n/a, read-only), R5 (no new config), R6 (404/422 via `NotFoundError` and the validation envelope), R7 (file list above), R8 (tests), R9 (additive; `/audit-logs/{audit_log_id}`, `Page[T]` kept for the list), R10/R11 (n/a).

## Specialist input used
- **api-platform-engineer**
  - Adopted:
    - the `org_id` field on `AuditLogFilter` with one `or_` element, using `AuditTarget.ORGANIZATION.value`;
    - the documented AND semantics with `target_type`/`target_id`;
    - `get` in repository and service with `NotFoundError`;
    - a UUID path param (422 on malformed);
    - `AuditLogRead.model_validate` reused with no extra user-agent handling;
    - the test list (both arms, the row matching both, combinations, an empty page for an unknown org, full-equality single read, 401/403 on both routes);
    - the migration test;
    - the partial index;
    - the docstring update.
  - Adopted, adjusted: the route is "`/{id}`" in the ticket but is named `{audit_log_id}` to match the repo convention (`{org_id}`, `{user_id}`); the URL shape is unchanged.
  - Rejected: updating `docs/spec-v1.md` §5.4. The spec already says "filterable" and does not list filters; the route catalogue in `identity-architecture.md` is updated instead.
  - Deferred to follow-ups: the data-completeness note (identity-side rows such as join requests and `record_in_savepoint` rows carry `org_id=None`) and any backfill (R7).
- **database-optimizer**
  - Adopted:
    - a single `or_()`, not UNION;
    - `ix_audit_log_org_id (org_id, occurred_at, id) WHERE org_id IS NOT NULL` in model and migration;
    - `downgrade()` drops it;
    - `session.get` for the PK read;
    - EXPLAIN ANALYZE evidence on 100k+ seeded rows after `ANALYZE`;
    - an EXPLAIN test with `enable_seqscan=off` that checks index names.
  - Adjusted: the plan test runs on the **count** query, where a BitmapOr is the natural plan. A LIMIT page query on a near-empty test table may legitimately choose a backward scan of `ix_audit_log_occurred_at_id`, which would make the test flaky. The page-query plan goes into the impl-log evidence instead.
  - Resolved open question: `af3eb84d2d5e` used a plain `op.create_index` (no `CONCURRENTLY`, no `autocommit_block`), as did `4d4b5d67dc44`. This plan follows that.

## Assumptions
- An unknown or deleted `org_id` returns an empty page (200), not 404. There are no FKs by design (ADR-0009 §2), deleted orgs' history must stay reachable, and this matches `test_unknown_app_key_empty_page`.
- The target arm matches only when `target_type = 'organization'`. A row targeting a user/app/etc. with the same UUID value does not match through the target arm, though it still matches through its `org_id` column.
- `org_id` combined with explicit `target_type`/`target_id` is a plain AND (no special casing).
- The path param is named `audit_log_id`, and the URL is `/api/v1/audit-logs/{audit_log_id}`.
- The 404 message is `"Audit log entry not found"`, using the standard `not_found` code from `NotFoundError`.
- The migration's `down_revision` is `c7a3e91f5d28`, the only head in the file chain. If another ticket lands a migration first, the implementer rebases `down_revision` onto the new head and notes it in impl-log.
- A plain `CREATE INDEX` is acceptable now: the production `audit_log` is days old and small, and both prior audit/index migrations did the same.

## Needs owner's decision
These are non-blocking; the defaults above are used unless the owner says otherwise.
1. **Plain vs `CONCURRENTLY` index build.** The default is plain, as in `af3eb84d2d5e`. `docs/deploy.md` says future audit DDL on a *large* table should use `CREATE INDEX CONCURRENTLY`. If production `audit_log` is already large enough that a brief write lock during deploy matters, switch to `CONCURRENTLY` inside `op.get_context().autocommit_block()` (this needs the migration runner to allow it, and it would be the first such migration in the repo).
2. **Unknown org → empty page** (default) rather than 404.
3. **Data completeness for admin-pan.** The filter only finds rows that carry `org_id` or target the organization. Some rows tied to an org (e.g. writes through `record_in_savepoint`, which hard-codes `org_id=None`, and any identity-side action that records the org only in `changes`) will not appear. Should a follow-up populate `org_id` on those writes? Backfilling past rows is impossible anyway, because the log is append-only.

## Blocking questions
None.

## Split proposal
None. Estimated diff is about 250–300 lines, mostly tests, and covers one feature (§5.4 listing).

## Data model / migration
- Table `audit_log`: no column changes.
- New index `ix_audit_log_org_id` on `(org_id, occurred_at, id)`, partial `WHERE org_id IS NOT NULL`, non-unique, btree.
  - Declared in `AuditLog.__table_args__` with `postgresql_where=text("org_id IS NOT NULL")`.
  - The migration uses the identical text, so `alembic check` reports no drift.
- Migration `migrations/versions/20261118_<rev>_adm021_audit_log_org_id_index.py`:
  - `down_revision = "c7a3e91f5d28"`.
  - `upgrade()` runs `op.create_index(...)` (plain, see Assumptions).
  - `downgrade()` runs `op.drop_index("ix_audit_log_org_id", table_name="audit_log")`. It is lossless, so no guard is needed.
- Existing `ix_audit_log_target` `(target_type, target_id, occurred_at, id) WHERE target_id IS NOT NULL` serves the target arm unchanged: `target_id = :o` implies the partial predicate.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/audit-logs` | Existing query params plus the optional `org_id` (UUID): rows whose target is `organization`/`org_id` OR whose `org_id` column equals it, ANDed with the other filters. | `Page[AuditLogRead]` (unchanged) | 200, 401, 403, 422 (malformed `org_id`, existing validations) | `admin:audit:read` (Bearer, R3 class 1) |
| GET | `/api/v1/audit-logs/{audit_log_id}` | Path `audit_log_id` (UUID) | `AuditLogRead` (single document, the same model and fields as a list item) | 200, 401, 403, 404 (unknown id), 422 (malformed id) | `admin:audit:read` (Bearer, R3 class 1) |
