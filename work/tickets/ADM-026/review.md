## Attempt 1 — 2026-11-27
Verdict: PASS

Tier: **High** (`python3 -I .github/scripts/risk_tier.py --base origin/main` → `high`; trigger `app/modules/rbac/**` on
`rbac/repository.py`, `rbac/router.py`, `rbac/schemas.py`). Not auto-mergeable. Merge is gated on Ming's read-only
prod check M-10 (`und-x-icu` present, `server_encoding = UTF8`).

### Checks (run by reviewer)
- `uv run ruff check app tests migrations`: All checks passed.
- `uv run ruff format --check app tests migrations`: 451 files already formatted.
- `uv run mypy app`: Success, no issues found in 216 source files.
- `uv run pytest -q` (full suite, local PG 16.15, `org_admin_test`): **4586 passed**, 2 warnings (pre-existing: a SAWarning in
  `tests/test_app_org_invitations.py:153` and a pty DeprecationWarning), 760.95 s. This covers the final state after the
  implementer's last fix to `tests/test_openapi_contract.py`. The implementer had not re-run the full suite after that
  fix; this run confirms it (4585 passed + 1 fixed = 4586).
- `uv run alembic check` (local dev DB `org_admin`): No new upgrade operations detected.
- `git diff b54e59f 856d348 --stat -- migrations app/core`: empty. No migration, and `app/core` is untouched.
- `python3 -I .github/scripts/risk_tier.py --base origin/main`: `high`.

### Acceptance criteria
- [x] **AC1: `name`/`-name` is case-insensitive and locale-aware on orgs and apps, keeps the `id` tie-break, and is documented. No migration.**
  - Sort key: `app/modules/rbac/repository.py:80` sets `NAME_SORT_COLLATION: Final = "und-x-icu"`, a literal constant.
    `:83-89` defines `name_sort_key`, which returns `func.lower(column.collate(...))`, so the collation sits inside
    `lower()` as the plan specified.
  - Apps: `:92-96` maps `"name"` to `name_sort_key(App.name)`. `:240-242` orders by `primary.asc()/desc()`, then
    `App.id.asc()` in both directions.
  - Orgs: `app/modules/organizations/repository.py:18-22` builds `_SORT_EXPRESSIONS`. `:25-27` defines `_order_by`,
    which puts `Organization.id.asc()` last in both directions, and `:61` uses it.
  - Tests: `tests/test_name_sort.py` has `test_{orgs,apps}_name_sort_case_and_accents[name,-name]` and
    `test_{orgs,apps}_name_case_only_tie_broken_by_id[name,-name]`. All are green.
  - Docs: the router descriptions are at `app/modules/organizations/router.py:78-80` and
    `app/modules/rbac/router.py:98-101`. The spec notes are at `docs/spec-v1.md:54` and `:78`.
  - Migration: none, and `alembic check` is clean.
- [x] **AC2: apps sort by `member_count`/`-member_count` without a per-row count.**
  - `app/modules/rbac/repository.py:233-237` builds `member_count_select().subquery("mc")` and LEFT JOINs it on
    `mc.app_id = apps.id`. The primary key is `coalesce(mc.member_count, 0)`, and `:241` adds the `App.id.asc()`
    tie-break.
  - I compiled the SQL myself. The output is `FROM apps LEFT OUTER JOIN (SELECT roles.app_id, count(distinct(role_assignments.user_id)) ... GROUP BY roles.app_id) AS mc ON mc.app_id = apps.id ORDER BY coalesce(mc.member_count, 0) DESC, apps.id ASC`.
    That is one grouped, uncorrelated subquery.
  - `total` is still counted without the join (`:230`, unchanged).
  - Tests: `test_list_apps_sort_member_count[x2]`, `..._ties_by_id[x2]`, `..._excludes_expired_and_inactive`,
    `..._with_filters_and_paging` (asserts `total == 3` with paging) and `..._query_count` (3 statements for both
    1 app and 5 apps; `role_assignments` appears in 2 statements).
- [x] **Shared `member_count_select()`, no predicate duplication.** `app/modules/rbac/repository.py:99-116` holds the
  OQ5 query that used to sit inside `member_counts`. `member_counts` (`:1135`) now calls it and adds only its `in_`
  filter. Its `tests/test_rbac_member_counts.py` stays green.
- [x] **AC3: existing values and defaults unchanged (R9).**
  - `AppSort` (`app/modules/rbac/schemas.py:30-39`) keeps all six old values and adds two.
  - The defaults are unchanged: `DEFAULT_APP_SORT = "key"` (`:40`) and `DEFAULT_ORGANIZATION_SORT = "-created_at"`
    (`app/modules/organizations/schemas.py:13`).
  - `OrganizationSort` is unchanged. Orgs `sort=member_count` returns 422 (`tests/test_organizations.py`, bad-query
    parametrize).
  - The existing default-order and sort-order tests pass without edits.
- [x] **AC4: tests for mixed case, accents and `member_count` ties.** See AC1 and AC2. The ICU guard
  `test_icu_collation_available` asserts the collation row and UTF8, with the plan's message. It fails loudly and is
  not skipped.
- [x] **AC5: `make check` is green.** See Checks above.

### Deviations judged
1. **Expected order (`đức` before `Dung`): accepted, and the implementer's correction is right.**
   - My reasoning: under the CLDR root collation, `đ` (U+0111) has d's primary weight plus a secondary (diacritic)
     difference. "đức" and "dung" are therefore equal at the primary level on `d`. Comparing the strings at the primary
     level, `ư` has u's primary weight, so it ties with `u`. The next pair is `c` against `n`, and `c < n` decides it
     at the primary level, before any secondary difference counts. So `đức < Dung`.
   - The local DB agrees. `ORDER BY lower(n COLLATE "und-x-icu"), n` on `{Beta, alpha, Ánh, Zed, đức, Dung, Đức, dung}`
     returns `alpha, Ánh, Beta, Đức, đức, Dung, dung, Zed`. That matches `EXPECTED_ASC` in
     `tests/test_name_sort.py:18`.
   - The tests are still real regressions: byte order (`COLLATE "C"`) gives `Beta, Dung, Zed, alpha, Ánh, đức`.
   - I also checked that the local DB default is `C.UTF-8` with the libc provider. There, `lower('Đ' COLLATE "C")`
     returns `Đ`, but `lower('Đ' COLLATE "und-x-icu")` returns `đ`. This confirms that putting the collation inside
     `lower()` matters, and the `Ân`/`ân` tie test exercises it.
2. **Typing: accepted.**
   - `Select[uuid.UUID, int]` matches the installed SQLAlchemy stubs.
   - `dict[str, ColumnElement[Any] | QueryableAttribute[Any]]` is accurate and passes `mypy --strict`.
   - The code has no `cast` or `type: ignore` in `app/`. The two `type: ignore`s in the tests follow existing patterns
     (an async-generator fixture, and an `_app_row` Optional).
3. **Out-of-plan edit to `tests/test_openapi_contract.py`: accepted.** It is justified under "Out-of-plan files" in the
   impl-log. The existing contract test pins the apps `sort` enum, and the edit only appends the two new values (R7
   satisfied).
4. **`app/core/listing.py::sort_clauses` has no callers left in `app/`: accepted.** Its only remaining callers are in
   `tests/test_listing.py`. Removing it is a follow-up (High area), not a change for this ticket.

Other diff entries vs `origin/main` (`work/backlog.md`, `work/batches/2026-11-27-1.md`, `work/tickets/USR-28/ticket.md`,
`ticket.md`) are orchestrator bookkeeping commits (b54e59f, f890d44), not implementer scope.

### Issues
None.

### Rules (CLAUDE.md)
No violations found.
- R1: ordering stays in the repositories, and the routers change only their description text.
- R2: no schema change, and `alembic check` is clean.
- R3, R4, R8: no new endpoint, no auth change, no mutation. The existing 401/403 tests cover the list.
- R5: the collation is a constant, never input.
- R7: the only out-of-plan file is justified.
- R9: additive values and unchanged defaults. The order change inside `name` is the fix Ming approved (Q1) and is noted
  in the spec.

### Blockers from review agents
None — appsec-engineer `security-review.md` Attempt 1: PASS, no 🔴; one 🟡 without code change (missing ICU on prod → 500 on name sort; gated by Ming todo M-10 before merge).

### Nits (do not block)
- 💭 The `member_count_select` docstring (`app/modules/rbac/repository.py:100-103`) says it is "the single definition".
  However, `is_active_app_member` (`:1107-1124`, pre-existing and not changed here) repeats the same OQ5 predicate for
  one user. The docstring overstates this slightly.
- 💭 `tests/test_name_sort.py:49`: `_mine(resp_json: dict, ...)` uses a bare `dict`. This is fine for tests, which are
  outside the `mypy app` scope.

### Follow-ups (out of scope)
- Remove `app/core/listing.py::sort_clauses` and `tests/test_listing.py::test_sort_clauses_*`, or generalise it to
  `ColumnElement` (High, `app/core`).
- Consider `name_sort_key` for other name-sorted lists (roles, users).
- Make `is_active_app_member` reuse the OQ5 predicate from `member_count_select` (for example, a shared
  `_active_member_conditions()`), so the definition really lives in one place.
- Ming's pre-merge check M-10 on prod: `SELECT 1 FROM pg_collation WHERE collname = 'und-x-icu'` and `SHOW server_encoding`.
  If the row is missing, do not merge, because `sort=name` would answer 500.

### Ming verification after merge (carry into PR)
- **Before merge, read-only on prod:** run the M-10 queries above. Both must succeed: one row, and `UTF8`.
- **After deploy:** in admin-pan, sort Organizations and Apps by name. Check that lowercase and accented names (for
  example "Ánh…" and "đ…") sort among their base letters. Then sort Apps by Members in both directions.
