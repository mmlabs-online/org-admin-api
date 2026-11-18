Status: READY

# Plan — USR-18: Filters and sort for the staff apps list

## Spec summary
- spec §5.3: "Apps are registered; each app declares namespaced permissions ... Every admin `/api/v1` endpoint declares the `admin:*` permission it needs". Membership paragraph: "each app has a `join_policy` (`open` | `approval` | `closed`; default `open`, `admin` = `closed`) ... an `org_mode` (`none` | `required`; ...)".
- spec §5.9 (context): "**Per-app member count** — the "Members" column of the Apps screen: an additive `member_count` field on the existing staff apps list and app detail (no new endpoint, no breaking change in v1)." Permission: "`member_count` uses the existing `admin:apps:read`."
- CLAUDE.md R9: "lists return `Page[T]` (`limit` ≤ 100, `offset`) ... No breaking changes inside v1. Exceptions (D18, D23): apps are addressed by their immutable `key` in `/api/v1/apps/{key}/…` paths".
- Ticket AC4: "Same design (param names, empty-`q` rule, escaping helper) as ADM-020." ADM-020 (merged) built `app/core/listing.py` (`escape_like`, `ilike_any`, `SearchTerm`, `sort_clauses`) and the organizations list filters. This plan reuses them **unchanged**. `SearchTerm` is the variant ADM-020 actually shipped (ADM-020 impl-log: the fallback, in which a before-validator does NFC + `strip()` and then `min_length=1`/`max_length=100` apply). No change to `listing.py` is needed.

The apps list lives in `app/modules/rbac/` (router `list_apps`, `AppService.list_apps`, `RbacRepository.list_apps`). There is no `app/modules/apps/`.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Optional filters: `status` (`active` \| `disabled`), `join_policy` (`open` \| `approval` \| `closed`) and `q`. `q` is trimmed, then must be 1..100 chars. It is a case-insensitive substring match on `key` OR `name`, with LIKE wildcards escaped. **Plus `org_mode` (`none` \| `required`) as an additive filter.** This one needs the owner's decision (non-blocking; see Assumptions). | `tests/test_rbac_apps_api.py`: `test_list_apps_filter_status`, `test_list_apps_filter_join_policy`, `test_list_apps_filter_org_mode`, `test_list_apps_filter_q_key_or_name_case_insensitive`, `test_list_apps_filter_q_is_trimmed`, `test_list_apps_filter_q_escapes_like_wildcards[...]`, `test_list_apps_filters_combined`. |
| 2 | Optional `sort` from the allow-list `key`, `-key`, `name`, `-name`, `created_at`, `-created_at`. **The default is `key`, which is today's order (`ORDER BY key`). Do not copy the org default `-created_at`.** `id` ascending is always the tie-breaker. | `test_list_apps_sort_orders[...]` (all six values, exact order), `test_list_apps_sort_tie_breaker_is_id_asc[...]`, `test_list_apps_default_order_unchanged`, `test_apps_sort_allow_list_matches_repository_mapping`. The existing `test_list_apps_paginated_and_sorted` passes unchanged. |
| 3 | `total` is the filtered count. `member_count` (ADM-016) is still present and still comes from one batched query per page. Invalid values of these parameters give 422. No breaking change (R9). **AC wording amended (see Assumptions): "unknown or invalid values *of these parameters*" → 422. Unknown parameter *names* are still ignored, as decided in ADM-020.** | `test_list_apps_total_is_filtered_count`, `test_list_apps_member_count_on_filtered_page`, `test_list_apps_query_count_with_filters`, `test_list_apps_rejects_bad_query[...]`, `test_list_apps_ignores_unknown_params`. The existing `test_list_apps_member_count`, `test_list_apps_query_count` and `APP_READ_KEYS` tests pass unchanged. |
| 4 | Same design as ADM-020: param names `status`/`q`/`sort`, the empty-`q` → 422 rule, the `escape_like`/`ilike_any` helper, the `SearchTerm` type and `sort_clauses`. The description wording matches the org list. | Code review: imports come from `app.core.listing`, and there is no new LIKE or sort helper. `tests/test_openapi_contract.py`: `test_apps_list_query_params` (enums, `q` min/max length, `"422"` in the `q` description, `sort` enum and default `"key"`) and `test_apps_list_422_is_envelope`. |
| 5 | Tests cover each filter alone and combined, escaping, sort order and tie-breaker, 422 cases, and 403 without `admin:apps:read`. | Everything under "Tests to write", plus `test_list_apps_without_permission_403`. `make check` is green. |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/rbac/schemas.py` | Add `Literal` to the `typing` import. Add `AppSort = Literal["key", "-key", "name", "-name", "created_at", "-created_at"]` and `DEFAULT_APP_SORT: AppSort = "key"`. | AC2. The allow-list type is shared by router, service and repository (same placement as `OrganizationSort` in ADM-020). |
| `app/modules/rbac/repository.py` | Add a module-level `_APP_SORT_COLUMNS = {"key": App.key, "name": App.name, "created_at": App.created_at}`. Change `list_apps` to `list_apps(*, status, join_policy, org_mode, q, sort, limit, offset)`, with one `conditions` list for count and page, and `order_by(*sort_clauses(sort, _APP_SORT_COLUMNS, App.id))`. Import `ilike_any`, `sort_clauses` from `app.core.listing` and `ColumnElement` if it is not already imported. | AC1–AC3 (R1: SQL only here). |
| `app/modules/rbac/service.py` | `AppService.list_apps(*, status, join_policy, org_mode, q, sort, limit, offset)` passes the arguments through. | R1 layering. |
| `app/modules/rbac/router.py` | `list_apps` gets the `status`, `join_policy`, `org_mode`, `q` and `sort` query params with descriptions, and passes them to the service. The `member_counts(items)` call and the `Page(...)` build are unchanged. | AC1–AC4. |
| `tests/test_rbac_apps_api.py` | New section "list filters and sort (USR-18)": a local `_seed_app` helper and the API tests listed below. | AC1–AC3, AC5, R8. |
| `tests/test_openapi_contract.py` | Add `test_apps_list_query_params` and `test_apps_list_422_is_envelope`, modelled on the organizations ones (reuse `_schema`, `_param`, `_flat`, `REF`). | AC4. |
| `docs/identity-architecture.md` | Row "Admin: `/api/v1/apps`, ...": add "list `?status=&join_policy=&org_mode=&q=&sort=` (USR-18)" in the style of the ADM-020 organizations row. | The endpoint catalogue stays accurate. |
| `docs/spec-v1.md` | Add one dated note under §5.3, after the Membership paragraph: "> 2026-11-xx (USR-18): staff apps list filters `status`, `join_policy`, `org_mode`, `q` (key or name, trimmed 1..100, empty → 422) and `sort` (allow-list, default `key`, `id` tie-breaker)." | §5.3 records shipped behaviour, as ADM-020 did for §5.1. |

No other files. No migration. `app/core/listing.py` is **not** modified. `README.md` is not touched: its endpoint table has no apps row today, and adding the apps catalogue there is a follow-up (R7).

## Tests to write
Fixture: the existing `rbac_client` (`admin:*`). Helper in `tests/test_rbac_apps_api.py`. `make_app` cannot be used, because it hard-codes `name=key.title()` and passing `name=` would raise `TypeError`. The helper is:

```python
T0 = datetime(2026, 1, 1, tzinfo=UTC)

async def _seed_app(db_session, key, name, *, status="active", join_policy="open",
                    org_mode="none", created_at=T0) -> App:
    app = App(key=key, name=name, status=status, join_policy=join_policy,
              org_mode=org_mode, created_at=created_at)
    db_session.add(app); await db_session.flush(); await db_session.refresh(app)
    return app
```

Constraints the tests must respect:
- **The seeded `admin` app always exists**: key `admin`, name `mmlabs Admin`, `active`, `closed`, `none`, and a migration-time `created_at`.
  - Filter tests include it in the expectations where it matches (`status=active`, `join_policy=closed`, `org_mode=none`).
  - Order tests drop it before asserting (`[k for k in keys if k != "admin"]`).
  - `q` terms must not match `admin`/`mmlabs Admin` unless intended.
- **App keys match `^[a-z][a-z0-9-]{1,31}$`.** They cannot contain `%`, `_` or `\`, so the escaping tests put the wildcards in `name`. This corrects the consult, which said `_` is common in keys. Use plain keys without `-`, and ASCII single-word names with the same capitalisation (collation caveat from ADM-020).
- `now()` is frozen inside the test transaction, so `created_at` order tests set `created_at` explicitly.

| Test (file › name) | Covers |
|---|---|
| `tests/test_rbac_apps_api.py › test_apps_sort_allow_list_matches_repository_mapping` | Every value of `typing.get_args(AppSort)` resolves through `sort_clauses(value, _APP_SORT_COLUMNS, App.id)`, so the Literal and the mapping cannot drift. |
| `› test_list_apps_filter_status` | Seed `alpha` (active) and `bravo` (disabled). `status=disabled` returns `["bravo"]`, total 1. `status=active` returns `["admin","alpha"]`, total 2. Disabled apps stay visible to staff. |
| `› test_list_apps_filter_join_policy` | Seed `po` (open), `pa` (approval) and `pc` (closed). `join_policy=approval` returns `["pa"]`. `join_policy=closed` returns `["admin","pc"]`. Totals match. |
| `› test_list_apps_filter_org_mode` | Seed `mn` (none) and `mr` (required). `org_mode=required` returns `["mr"]`, total 1. |
| `› test_list_apps_filter_q_key_or_name_case_insensitive` | Seed `one`/"Alpha", `bravo`/"Other" and `three`/"Charlie". `q=ALP` returns `["one"]` (name). `q=BRAV` returns `["bravo"]` (key, upper-case query). |
| `› test_list_apps_filter_q_is_trimmed` | `q=" alpha "` matches name `Alpha`. |
| `› test_list_apps_filter_q_escapes_like_wildcards` (parametrized) | Names `100% Pure` (key `pure`), `100 Pure` (`plain`), `a_b` (`ab`), `axb` (`axb`) and `back\slash` (`back`). `%` → `["pure"]`, `a_b` → `["ab"]`, `_` → `["ab"]`, `\` → `["back"]`. Same shape as the org test. |
| `› test_list_apps_filters_combined` | `status=disabled&join_policy=approval&q=alph` returns only the disabled+approval app matching `alph`. These are excluded: an active+approval `alph…` app, a disabled+open `alph…` app, and a disabled+approval non-matching app. `total == 1`. |
| `› test_list_apps_total_is_filtered_count` | Three `match…` apps and two `other…` apps, with `q=match&limit=1`. `total == 3`, `len(items) == 1`, `limit == 1`. |
| `› test_list_apps_sort_orders` (parametrized over all six values) | Keys `ka`/"Bravo"/T0+3d, `kb`/"Charlie"/T0+1d, `kc`/"Alpha"/T0+2d. After dropping `admin`, the expected orders are: `key` → ka,kb,kc; `-key` → kc,kb,ka; `name` → kc,ka,kb; `-name` → kb,ka,kc; `created_at` → kb,kc,ka; `-created_at` → ka,kc,kb. All six are distinct. |
| `› test_list_apps_sort_tie_breaker_is_id_asc` (parametrized: `name`, `-name`, `created_at`, `-created_at`) | Two apps with name `Same` and `created_at=T0`, plus one other (`Other`, T0+1d). After dropping `admin` and the other app, the tied pair comes back in `id` ASC order in both directions. `key` is unique, so it is not tested for ties. |
| `› test_list_apps_default_order_unchanged` | With no `sort`, the items are equal to `sort=key` and keys are ascending (including `admin`). |
| `› test_list_apps_member_count_on_filtered_page` | `mca` (1 member) and `mcb` (2 members). `q=mcb` returns one item with `member_count == 2`. `status=active&sort=-key` still reports `mca` → 1 and `mcb` → 2. |
| `› test_list_apps_query_count_with_filters` | Uses the existing `_app_statements` with path `f"{BASE}?status=active&q=qa-&sort=-name"`, with 1 and then 5 apps that have members (`_app_with_member`). Both runs issue exactly 3 statements (count, page, member counts), and exactly one of them touches `role_assignments`. |
| `› test_list_apps_rejects_bad_query` (parametrized) | `status=deleted`, `join_policy=x`, `org_mode=x`, `sort=bogus`, `sort=id`, `sort=KEY`, `sort=member_count`, `q=` (empty), `q="   "` and `q="x"*101` each return 422 with `error.code == "validation_error"`. |
| `› test_list_apps_ignores_unknown_params` | `?foo=bar` returns 200 and the same items as no params. This documents the AC3 reading. |
| `› test_list_apps_without_permission_403` | `make_staff(db_session)` with no permission, the `client` fixture and `bearer(mint(staff))`, as in `tests/test_organizations.py::test_list_without_permission_403`. Both `q=acme&sort=name` and `sort=bogus` return 403 `permission_denied` with `details == {"required": "admin:apps:read"}`. 403 comes before 422. |
| `tests/test_openapi_contract.py › test_apps_list_query_params` | `GET /api/v1/apps` has `limit`, `offset`, `status`, `join_policy`, `org_mode`, `q` and `sort`, all `in: query` and not required. The new ones have non-empty descriptions. The enums are `status == ["active","disabled"]`, `join_policy == ["open","approval","closed"]` and `org_mode == ["none","required"]` (resolved via `_flat` and `$ref`). `q` has `(minLength, maxLength) == (1, 100)` and `"422"` in its description. `sort` has enum `["key","-key","name","-name","created_at","-created_at"]` and `default == "key"`. |
| `tests/test_openapi_contract.py › test_apps_list_422_is_envelope` | `GET /api/v1/apps` has a `422` referencing `REF`, and its description contains `validation_error`. |

Existing tests that must stay green unchanged include the whole of `tests/test_rbac_apps_api.py` (notably `test_list_apps_paginated_and_sorted`, `test_list_apps_member_count`, `test_list_apps_query_count` and `test_member_count_matches_dashboard`), `tests/test_openapi_contract.py` (incl. `test_every_error_response_references_envelope` and `test_schema_generation_does_not_mutate_shared_dicts`), `tests/test_route_auth_classes.py`, `tests/test_cli_openapi.py`, `tests/test_listing.py` and `tests/test_organizations.py`.

## Approach
1. **`rbac/schemas.py`**: add the `AppSort` Literal and `DEFAULT_APP_SORT = "key"`.
2. **`rbac/repository.py`**:
   - `_APP_SORT_COLUMNS` mapping (module level, next to the imports).
   - Then:
     ```python
     async def list_apps(self, *, status: AppStatus | None, join_policy: JoinPolicy | None,
                         org_mode: OrgMode | None, q: str | None, sort: AppSort,
                         limit: int, offset: int) -> tuple[list[App], int]:
         conditions: list[ColumnElement[bool]] = []
         if status is not None: conditions.append(App.status == status.value)
         if join_policy is not None: conditions.append(App.join_policy == join_policy.value)
         if org_mode is not None: conditions.append(App.org_mode == org_mode.value)
         if q is not None: conditions.append(ilike_any(q, App.key, App.name))
         total = await self.session.scalar(select(func.count()).select_from(App).where(*conditions))
         result = await self.session.scalars(
             select(App).where(*conditions)
             .order_by(*sort_clauses(sort, _APP_SORT_COLUMNS, App.id))
             .limit(limit).offset(offset))
         return list(result), total or 0
     ```
   - The default `key` gives `key ASC, id ASC`, which returns the same rows as today, because `key` is unique.
3. **`rbac/service.py`**: pass-through, no business rule.
4. **`rbac/router.py`**: `list_apps` signature, with wording copied from the org list:
   ```python
   status: Annotated[AppStatus | None, Query(description="Only apps with this status.")] = None,
   join_policy: Annotated[JoinPolicy | None, Query(description="Only apps with this join policy.")] = None,
   org_mode: Annotated[OrgMode | None, Query(description="Only apps with this org mode.")] = None,
   q: Annotated[SearchTerm | None, Query(description=(
       "Case-insensitive substring of key or name; trimmed, 1..100 chars after "
       "trimming; empty or whitespace-only is 422 (omit the parameter instead)."))] = None,
   sort: Annotated[AppSort, Query(description=(
       "One of key, -key, name, -name, created_at, -created_at ('-' = descending); "
       "id ascending breaks ties. name follows the database collation."))] = DEFAULT_APP_SORT,
   ```
   - The parameter `status` shadows the `fastapi.status` import only inside this function, as in the organizations router. `status.HTTP_201_CREATED` is used only in decorators, so nothing breaks.
   - Keep `**require_permission("admin:apps:read")` (the 422 response is already declared by it). Keep `counts = await svc.member_counts(items)` exactly once.
5. Update `docs/identity-architecture.md` and `docs/spec-v1.md` as listed.
6. Run `make check`. `alembic check` reports no drift.

Rules:
- **R1**: the router declares params, the service passes through, and SQL stays in the repository. `core/listing.py` is reused, not changed.
- **R2**: no schema change.
- **R3**: unchanged. The route stays Bearer + `admin:apps:read`, and the permission dependency runs before param validation, so 403 comes before 422.
- **R4**: read-only, so there is no audit entry.
- **R5**: no config.
- **R6**: 422/403 go through the existing envelope.
- **R7**: `member_count` sort, unknown-param rejection, README apps rows and indexes are follow-ups.
- **R8**: tests as listed.
- **R9**: every param is optional and additive, the default order is unchanged, the response is still `Page[AppRead]`, and `member_count` is unchanged.
- **R10**: not affected.
- **R11**: `q` is not logged.

## Specialist input used
- **api-platform-engineer** (`consult-api-platform-engineer.md`):
  - #1 param contract (`AppStatus`/`JoinPolicy`/`OrgMode` enums, `SearchTerm`, `AppSort` Literal with default `key`, `_APP_SORT_COLUMNS` and a drift test): adopted.
  - #2 one `conditions` list, `ilike_any(q, App.key, App.name)`, `sort_clauses(..., App.id)`: adopted. Corrected one point: keys cannot contain `_` (CHECK `^[a-z][a-z0-9-]{1,31}$`), so the escaping tests use names.
  - #3 `id` ASC tie-breaker in both directions, tested only on `name`/`created_at`, with explicit `created_at`: adopted.
  - #4 no `member_count` sort: adopted (follow-up).
  - #5 ignore unknown param names, and read the AC as invalid values of the declared params: adopted, with the AC wording amended (see Assumptions) and pinned by `test_list_apps_ignores_unknown_params`.
  - #6 403 before 422: adopted.
  - #7 OpenAPI contract tests: adopted.
  - #8 tests, including the `admin`/disabled visibility and the SQL statement counter: adopted.
  - #9 docs: adopted for `identity-architecture.md` and the spec. The README is skipped because it has no apps row to update.
  - Risks:
    - Default `key`, not `-created_at`: adopted (AC2 and `test_list_apps_default_order_unchanged`).
    - Wording drift: the descriptions are copied from the org router.
    - Same `conditions` for count and page: adopted.
    - The ADM-020 `SearchTerm` variant is settled (fallback shipped), and it is reused as is.

## Assumptions
- **AC3 wording amended.** The ticket's "unknown or invalid values → 422" is read as "unknown or invalid values **of these parameters** → 422". Unknown parameter **names** stay ignored (the FastAPI default), consistent with ADM-020 follow-up 1. Rejecting them would be a global change across list routes. This plan cannot edit `ticket.md`. **The orchestrator should change the AC3 text in `work/tickets/USR-18/ticket.md` to "unknown or invalid values of these parameters → 422" and add a note.**
- The `admin` app and disabled apps remain visible in the staff list under every filter (current behaviour, spec §5.9: "the `admin` app shows its count").
- App names are NFC-normalized on write (`test_app_name_stored_nfc`), and `q` is NFC-normalized by `SearchTerm`, so the Vietnamese-name mismatch caveat from ADM-020 does not apply here.
- `name` sorts under the DB collation, as for orgs. The ADM-020 production `lc_collate` follow-up covers this too. `key` is lowercase ASCII and collation-safe in practice. Test keys avoid `-`.
- Offset pagination over a mutable `name` can skip or duplicate a row between pages. This is acceptable for staff tooling.
- No index: the apps table is small (tens of rows).
- Estimated diff: about 250 lines, mostly tests. One cycle is enough.

### Needs owner's decision (non-blocking)
- **`org_mode` filter (additive, beyond the ticket's AC1).** The plan includes it because it is cheap, it uses the same pattern, admin-pan's Apps screen shows `org_mode`, and adding it is not a breaking change. If the owner says no, drop the `org_mode` param, its condition, `test_list_apps_filter_org_mode`, the `org_mode=x` 422 case and the contract-test line. Nothing else depends on it.
- **No `member_count` sort** (consult #4). It would need a grouped-subquery join inside the filtered count and page query. It is left as a follow-up until admin-pan asks.

## Blocking questions
None.

## Split proposal
None.

## Data model / migration
None. No schema change and no index.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/apps` | Query: `limit` (1..100, default 20), `offset` (≥0, default 0). **New:** `status` (`active`\|`disabled`, optional), `join_policy` (`open`\|`approval`\|`closed`, optional), `org_mode` (`none`\|`required`, optional), `q` (string, NFC + trimmed, 1..100 after trim, optional; case-insensitive substring of `key` or `name`, wildcards literal; empty/whitespace → 422), `sort` (`key`\|`-key`\|`name`\|`-name`\|`created_at`\|`-created_at`, default `key`; tie-breaker `id` ASC). Unknown query params are ignored (unchanged). | `Page[AppRead]` (unchanged, incl. `member_count`); `total` = filtered count | 200, 401, 403, 422 | `admin:apps:read` |

## Follow-ups (not in this diff, R7)
1. `member_count` as a sort key, if admin-pan asks. Use a LEFT JOIN on a grouped subquery that reuses the OQ5 member definition, without duplicating it.
2. Reject unknown query params on all list routes (shared with ADM-020 follow-up 1).
3. README endpoints table: add the apps (and other shipped) routes.
4. Production collation check for the `name` sort (shared with ADM-020 follow-up 5).
