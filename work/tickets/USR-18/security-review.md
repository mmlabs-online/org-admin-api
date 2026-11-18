## Attempt 1

Security review, USR-18 (filters and sort on GET /api/v1/apps)

Scope: I read `git diff origin/main...HEAD` (the app/modules/rbac/* changes and the new tests) and `app/core/listing.py`. I did not run `make check` or pytest, as you asked.

**Findings**

No 🔴 blockers. No 🟡 should-fix items.

💭 Notes (all on your focus list, all confirmed OK):
- **Sort, no SQL from strings:** `sort` is typed `Literal[...]` with six values (`app/modules/rbac/schemas.py:29`), so anything else gets a 422 at the boundary. The repository then maps it through the fixed dict `_APP_SORT_COLUMNS` (`app/modules/rbac/repository.py:40`) using `sort_clauses()` (`app/core/listing.py`). That helper raises on any unknown key and adds `App.id ASC` as the tie-breaker. The user string never becomes SQL; only the column objects reach `ORDER BY`. The test `test_apps_sort_allow_list_matches_repository_mapping` keeps the Literal and the dict in sync.
- **LIKE escaping:** `q` goes only through the shared `ilike_any()` (`repository.py:110`). It escapes `\` first, then `%` and `_`, and binds the pattern with `escape="\\"`. Tests cover `%`, `_`, `a_b` and `\`.
- **q limits:** the shared `SearchTerm` type applies NFC, then strip, then a length of 1–100 (`app/modules/rbac/router.py:70`). Empty or whitespace-only input gets a 422, which is tested. The query is bounded and the pattern is a bind parameter.
- **status / join_policy / org_mode:** these are Enum-typed query params, compared by `.value` as bind parameters. They are safe.
- **Authz unchanged:** the route still declares `require_permission("admin:apps:read")` (`router.py:61`), and `test_list_apps_without_permission_403` covers it. No new auth class or route was added.
- **member_count:** it is still one batched query, `member_counts([ids on the page])` (`router.py:99`). It is limited to the filtered page IDs, so it adds no N+1 and exposes nothing outside the page; a query-count test covers this. The member definition (active users, non-expired assignments) is unchanged.
- **Admin and disabled apps:** visibility is unchanged. The list still returns every app, including `admin` and disabled ones, unless the caller passes `status`; this matches what `main` did before. The new filters only narrow the list and cannot show more.
- **Totals:** `total` is counted with the same conditions as the page, so it reveals nothing beyond the result set.

**Follow-ups (out of scope)**
- 💭 `app/modules/rbac/repository.py:37` now imports `AppSort` from `rbac.schemas`. This is a minor R1 layering smell (the repository depends on a Pydantic-schema module). It has no security impact; the type alias could move to a neutral module later.

Verdict: PASS (security): no blockers and no should-fix items.
