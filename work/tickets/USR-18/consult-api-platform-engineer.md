USR-18 consult (api-platform-engineer, read-only). Reads: ticket, app/core/listing.py, ADM-020 plan, rbac router/service/repository/models. The apps list lives in app/modules/rbac/, not app/modules/apps/.

## Findings
- Current apps list:
  - Router: rbac/router.py:58 `list_apps(session, page)`.
  - Service: rbac/service.py:335 passes through to the repo.
  - Repo: rbac/repository.py:86 is a count plus `ORDER BY key LIMIT/OFFSET`.
  - Default order is `key ASC`. `key` is unique, so no tie-breaker was needed until now.
- member_count is already batched. The router calls `svc.member_counts(items)` once per page, which maps to `RbacRepository.member_counts(app_ids)` (one grouped query, `Role.app_id.in_(ids)`). Filtering and sorting only change which `items` are on the page, so it stays one extra query per page.
- App columns: `key` String(32), `name` String(255), `status` (CHECK active/disabled), `join_policy` (CHECK open/approval/closed), `org_mode` (CHECK none/required), plus `created_at` from TimestampMixin. The enums `AppStatus`, `JoinPolicy` and `OrgMode` are in rbac/models.py. `AppStatus`, `JoinPolicy` and `OrgMode` are already used in rbac/schemas.py, so Pydantic enum query params fit directly.
- app/core/listing.py is generic enough for reuse: `SearchTerm`, `ilike_any(q, App.key, App.name)`, and `sort_clauses(sort, columns, tiebreaker)`. The tie-breaker is always ascending, which the ticket's "id always the tie-breaker" allows.
- ADM-020 explicitly defers rejecting unknown query params (its follow-up 1) and says USR-18's planner must reconcile that with this ticket's "unknown or invalid values → 422".

## Recommendations
1. Param contract (AC1, AC4):
   - `status: AppStatus | None`.
   - `join_policy: JoinPolicy | None`.
   - `org_mode: OrgMode | None`. Add it as an additive filter. admin-pan can use it, it is cheap, and it matches the ticket's "App model fields" (see Open questions).
   - `q: SearchTerm | None`, with the same description wording as ADM-020: empty or whitespace-only gives 422, and admin-pan omits `q` when the box is blank.
   - `sort: AppSort`, `Literal["key","-key","name","-name","created_at","-created_at"]`, with `DEFAULT_APP_SORT = "key"`. This is the current order, so R9 holds.
   - Define `AppSort` and the default in rbac/schemas.py (or beside the other app schemas). Keep an `_APP_SORT_COLUMNS` dict in the repo.
   - Add the same allow-list vs mapping drift test as ADM-020 (`test_sort_allow_list_matches_repository_mapping`).
2. Repo (R1): `list_apps(*, status, join_policy, org_mode, q, sort, limit, offset)`. Use one `conditions` list for both count and page, and `order_by(*sort_clauses(sort, _APP_SORT_COLUMNS, App.id))`.
   - Match `q` on `ilike_any(q, App.key, App.name)`.
   - `key` is already lowercase by CHECK. ILIKE is still correct.
   - Escaping matters for `_`, which is common in keys.
3. Tie-breaker: `id ASC` in both directions, as in ADM-020. `key` is unique, so for `key`/`-key` the tie-breaker never fires. Test it only on `name` and `created_at`. As in ADM-020, set `created_at` explicitly in tests, since `now()` is frozen in the test transaction.
4. `member_count` as a sort key: do not add it in v1 of this ticket.
   - It lives in a separate aggregate. Sorting would need a join/subquery inside the filtered count and page query, and it would break the "member_count batched per page" design.
   - The ticket's allow-list does not include it, and adding a key later is additive and safe.
   - Record it as a follow-up if admin-pan asks.
   - If the owner does want it, use a LEFT JOIN on a grouped subquery that reuses the same member definition (do not duplicate OQ5). Keep the batched call for the response, or read the counts from the joined rows.
5. Unknown params vs the ticket's 422 line. Recommend ADM-020's decision: ignore unknown query params (FastAPI default, unchanged behaviour).
   - Read "unknown or invalid values → 422" as: unknown/invalid values of the declared params (`status=deleted`, `sort=bogus`, `sort=id`, `sort=KEY`, `join_policy=x`, empty `q`) give 422 `validation_error` via the envelope.
   - Rejecting unknown param names would be a global behaviour change across list routes. It would break clients that send extra params, and it is a follow-up in ADM-020. Edit the AC wording in ticket.md to say "unknown or invalid values of these parameters".
6. Permission order: keep `require_permission` first, so 403 comes before 422. Test `sort=bogus` without `admin:apps:read` and expect 403, as in ADM-020.
7. OpenAPI contract tests in tests/test_openapi_contract.py, modelled on ADM-020:
   - `test_apps_list_query_params`: `limit`, `offset`, `status` enum `["active","disabled"]`, `join_policy` enum `["open","approval","closed"]`, `org_mode` enum, `q` minLength 1 / maxLength 100, and `sort` enum of exactly the six values with `default == "key"`. All must be `in: query` and not required, with non-empty descriptions.
   - `test_apps_list_422_is_envelope`: 422 present and referencing `ErrorEnvelope`.
   - Existing `test_every_error_response_references_envelope` must stay green.
   - Confirm that `Page[AppRead]` and `member_count` are unchanged in the schema.
8. Other tests (AC5, R8):
   - Each filter alone and combined, `%`/`_`/`\` escaping, and a total that equals the filtered count with `limit=1`.
   - Default order equals `sort=key`.
   - All six sort values with the exact id order.
   - Use ASCII single-word names, and keys without `-` (collation caveat from ADM-020). App keys do allow `-`, so use distinct plain keys for ordering tests.
   - `member_count` present and correct on a filtered page. Count queries per page with a SQL counter: the page issues exactly one `member_counts` query regardless of the number of items.
   - Disabled apps (and the `admin` app) appear in the staff list. Make sure that is covered when filtering `status=disabled`.
9. Docs: update the README endpoints row, the docs/identity-architecture.md apps row, and a dated note in spec §5.3, as ADM-020 did.

## Risks
- Sequencing: USR-18 depends on app/core/listing.py from ADM-020. Plan after ADM-020 merges, or stack the branch. The ADM-020 `SearchTerm` fallback variant (if FastAPI does not apply Annotated metadata to query params) must be settled before USR-18 copies the pattern. Check ADM-020's impl-log.
- Default sort is `key`, not `-created_at` as for orgs. Do not copy the org constant blindly, or the order changes (a silent R9 break).
- `name` sort uses the DB collation (the production `lc_collate` follow-up from ADM-020 applies here too). `key` is lowercase ASCII, so it is collation-safe.
- Offset pagination over mutable sort keys (`name`) can skip or duplicate a row. Acceptable for staff tooling, as in ADM-020.
- Wording drift: the ticket says "Same design (param names, empty-`q` rule…)". Make sure `q`, `sort` and the 422 text are identical to the org description wording, or admin-pan will see two behaviours.
- Count query now carries the filters: both queries must use the same `conditions`, or `total` will be wrong.

## Open questions
1. Add `org_mode` as a filter? The ticket's AC lists only `status` and `join_policy`, but the lead's brief lists `org_mode`. I recommend yes, as an additive filter, with the AC updated. If it is out of scope, put it in the follow-ups (R7). Owner/planner to confirm.
2. Do you want `member_count` sort now (my recommendation is no, follow-up only)?
3. Confirm the unknown-param reading in Recommendation 5 and amend the AC text accordingly.
4. Does the `admin` app stay visible in the list when filtering (current behaviour: yes)? Assumed unchanged.
