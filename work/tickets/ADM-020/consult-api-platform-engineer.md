**Findings**
- GET /organizations (app/modules/organizations/router.py:25) takes only PageDep. The service passes through. The repo (repository.py:23) counts all rows and orders `created_at desc, id`. `list_for_user_app` uses a different order (name, id) and must stay untouched.
- Users list (app/modules/users/router.py:26, repository.py:71) has `email: Query(min_length=1, max_length=254)` and `status: UserStatus`. It does not trim. `escape_like` lives in `users/repository.py` and is used as `col.ilike(f"%{escape_like(x)}%", escape="\\")`. It is not shared, so USR-18 would have to import across modules.
- The `status` enum is already `OrganizationStatus` (a StrEnum). An invalid value gives 422 and OpenAPI gets an enum for free.
- PageDep (core/pagination.py) is a Depends function returning PageParams. Sort and filters can follow the same shape.
- `_with_404` and `_with_409` exist only as private copies in rbac/router.py (also `_with_409` in entitlements, join_requests and app_credentials). Each adds to `require_permission(key)["responses"]` (a fresh dict copy) with just a description. `openapi.py` `_postprocess` then attaches the ErrorEnvelope schema to every response with code >= 400. Organizations routes declare no 404 or 409 today.
- test_openapi_contract.py:
  - `test_every_error_response_references_envelope` covers new declarations automatically.
  - `test_422_is_envelope_validation_error` only checks POST. A GET /organizations 422 assertion would have to be new.
  - There is no assertion on query-param schema.
- FastAPI auto-adds a 422 response to any route that has parameters.
- Unknown query params are silently ignored today on every list route. Only body models use `extra="forbid"`.
- `Organization` has no index on name or slug beyond whatever the unique constraint on slug provides. I did not verify that constraint.

**Recommendations**
1. **Params (AC1, R9).**
   - `status: OrganizationStatus | None = None`.
   - `q: Annotated[str | None, Query(max_length=100)]`, with trimming done in a dependency.
   - Trim before validation. `Query(min_length=1)` runs on the raw value, so `"  "` would pass.
   - Use a shared `q_param` dependency (or a `StringConstraints(strip_whitespace=True, min_length=1, max_length=100)` annotation) so that whitespace-only and empty both give 422 `validation_error`.
   - Run NFC on `q` with `nfc_if_str` from core/text.py, to match how names are stored (ADR-0003 §11).
2. **Empty `q`: choose 422, not "ignored".** `?q=` is almost always a client bug, such as an unset form field. Ignoring it silently returns everything. It also matches the existing users `email` (min_length=1), so USR-18 gets the same rule. Document it in the param description. The admin-pan UI should omit `q` when the input is blank.
3. **Sort type.**
   - Use `OrgSort = Literal["created_at","-created_at","name","-name","slug","-slug"]`, or a StrEnum with those values. Both render as an OpenAPI `enum` with `default: "-created_at"`, and both give a 422 on an unknown value.
   - Prefer the Literal. A StrEnum cannot name members like `-name` idiomatically, so it needs a mapping anyway.
   - Keep a `dict[OrgSort, ColumnElement]` mapping in the repository. Never build SQL from the string.
   - OpenAPI cannot express "field plus direction" other than as a flat enum, so list the values in the param description.
4. **Sort semantics.**
   - `name` ordering uses the DB collation. For case-insensitive ordering use `func.lower(Organization.name)`.
   - Decide this at PLAN and document it. I suggest lower(name) for the UX.
   - Always append `, Organization.id` as the tie-breaker (AC2). Keep id ascending regardless of direction.
5. **Reusable helper.**
   - Create `app/core/listing.py` (core/ is shared, like pagination.py). It would hold `escape_like`, `ilike_any(cols, q)` returning a `ColumnElement[bool]`, a `q_param` Annotated type, and `order_by(sort, mapping, tiebreaker)`.
   - Move `escape_like` there. Re-export it from users/repository.py, or update its import and test import, so nothing breaks.
   - R1 holds: the core helper is pure SQLAlchemy/Pydantic with no HTTP. Routers declare the params, services pass typed values, and repositories apply the helper.
   - Do not put a Query-parameter dependency class in core that imports module enums. Each module owns its own `Literal` and mapping, and shares only the primitives.
6. **Service and repository signature.**
   - `list(*, status, q, sort, limit, offset)`. Build one `conditions` list and reuse it for `count()` and the page query, as users does. `total` is then the filtered count.
7. **404 and 409 in OpenAPI (AC4).**
   - Hoist `_with_404` and `_with_409` into one shared helper. A good place is `app/modules/admin_auth/deps.py`, next to `require_permission`, as `require_permission(key, errors={404: "...", 409: "..."})`, or a small `with_errors` function.
   - The rbac copy is a tempting refactor, but it is outside the ticket (R7). Add the helper and use it in organizations only. Log migrating rbac, entitlements and the others as a follow-up. If that is too large, a local copy in organizations/router.py is the minimal diff.
   - Declare 404 on get, patch and delete, and 409 on post (duplicate slug, `code: conflict`).
   - Also declare 409 on delete, since `OrgHasAssignmentsError` and `OrgHasEntitlementsError` are already raised there. The ticket text says only the duplicate slug, but delete's 409 is real. Declare it to be accurate, with the `details[0].reason` values.
   - PATCH has no 409 today.
8. **Unknown query params: ignore for now, follow-up.**
   - Rejecting them needs a router-wide dependency that diffs `request.query_params` against the declared names.
   - It would also change behaviour on existing routes (R9 risk) and clients that add cache-busters.
   - It is useful because a typo like `?statuss=` silently returns everything. Do it as its own ticket across all list routes.
   - Optionally implement it for the new params only. That is inconsistent, so I would not.
9. **`sort` on GET /users: follow-up (R7).** The ticket defers it ("else follow-up"), and PAN-013 needs it. With the shared helper it is about 10 lines. Create a ticket, for example ADM-0xx. Adding it now means a users test-suite diff and a new allow-list decision (email, created_at, status), which was not planned. If the owner wants it at PLAN, keep it to additive `created_at`, `-created_at`, `email`, `-email`.
10. **Indexes.**
   - Skip in this ticket. `ILIKE '%x%'` cannot use a btree index, and the table is small.
   - If wanted later, use pg_trgm GIN on `lower(name)` and `slug`. That is a separate migration with a downgrade (R2).
   - Sort by `slug` is already index-assisted if slug is unique.
11. **Tests (AC5, R8).**
   - Each filter alone and combined.
   - `%`, `_` and `\` literal matches.
   - Case-insensitive match on name OR slug.
   - Whitespace-only `q` returns 422, and `q` of 101 characters returns 422.
   - `q` is trimmed (`" acme "` matches).
   - Each sort value in both directions.
   - A tie-breaker test with equal `created_at` or equal names.
   - Bad `status`, bad `sort` and empty `q` each return 422 with the envelope.
   - 403 without `admin:organizations:read`.
   - `total` is the filtered count while `limit` stays at its value.
   - Add contract tests in test_openapi_contract.py:
     - `parameters` for GET /organizations include `status` (enum), `q` (maxLength 100) and `sort` (enum, default `-created_at`).
     - get, patch and delete have a 404 response, and post has a 409 response, each referencing the envelope.
     - A GET /organizations 422 references the envelope.

**Risks**
- Moving `escape_like` can break existing tests or imports of `users.repository.escape_like`. Grep before the move, and re-export if anything depends on it.
- Declaring 404 and 409 is additive in docs and is not a breaking change. The `test_no_5xx_documented` test is unaffected.
- The users `email` filter does not trim, but `q` does. That is a small inconsistency. Do not change `email` (R9), and note it in the plan.
- If `q` trimming is done in the repository instead of at the boundary, an empty-after-trim value could turn into `%%` and match everything. Trim and validate at the boundary only.
- Default sort `-created_at` plus `id` ascending must stay identical to today's order, so there is no behaviour change when params are absent.
- Offset pagination plus a mutable sort column (`name`) can show duplicates or skips between pages. This is acceptable for an admin UI, but note it.

**Open questions**
1. Empty or whitespace-only `q`: confirm 422 (recommended).
2. Is `name` sort case-insensitive (`lower(name)`)? Suggested yes. If so, the `slug` filter and sort stay as they are, since slugs are lowercase.
3. Should the 409 on delete (org has assignments or entitlements) be declared in this ticket? Recommended yes.
4. Is `sort` on /users a follow-up ticket (recommended), or in scope now?
5. Should rejecting unknown query params be a separate cross-route ticket? Recommended yes.
6. Where should `app/core/listing.py` go, and should the `_with_404`/`_with_409` helper be shared now or left local to organizations?
7. Should `escape_like` stay re-exported from users/repository.py?
8. Should USR-18 be planned in the same PLAN pass, with ADM-020 landing first and USR-18 reusing the helper? Recommended yes.

I read the ticket files, the organizations and users routers and repositories, core/pagination.py, core/openapi.py, core/text.py, rbac/router.py (the `_with_40x` helpers), `require_permission` in admin_auth/deps.py, and tests/test_openapi_contract.py. I did not read the apps list service or repository, so USR-18 specifics such as `member_count` and the `join_policy` enum are unchecked. I did not check the organizations model for the slug unique constraint.
