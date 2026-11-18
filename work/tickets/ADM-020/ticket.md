# ADM-020: Filters and sort for the staff organizations list

| Field | Value |
|---|---|
| State | REVIEW |
| Priority | P1 |
| Areas | api, db |
| Attempts | 0 |
| Created | 2026-11-17 |
| PR | — |

## Goal
Staff can find an organization by name/slug and status, sorted server-side, instead of paging through `GET /api/v1/organizations` (today `limit`/`offset` only, fixed order).

## Spec reference
§5.1 Organizations ("Paginated list"); §5.9 context (admin-pan Organizations screen, PAN-014).

## Acceptance criteria
- [ ] Optional `status` (`active` | `suspended`) and `q` (trimmed, 1..100 chars; case-insensitive substring on `name` OR `slug`, LIKE wildcards escaped like the users `email` filter). Empty `q` behaviour (422 or absent) is decided at PLAN and documented.
- [ ] Optional `sort` from an allow-list: `created_at`, `-created_at` (default), `name`, `-name`, `slug`, `-slug`; `id` always the tie-breaker; unknown value → 422.
- [ ] Response stays `Page[OrganizationRead]`; `total` is the filtered count. No breaking change (R9).
- [ ] OpenAPI documents the new params and declares the existing 404 (get/patch/delete) and 409 (duplicate slug, `code: conflict`) responses of the organizations routes.
- [ ] Tests: each filter alone and combined, `%`/`_` escaping, sort order and tie-breaker, 422 for bad `status`/`sort`/empty `q` (if 422 chosen), 403 without `admin:organizations:read`.

## Dependencies
ADM-002b, ADM-013. Shares the filter/sort design with USR-18 (apps list); plan them together or reuse the first one's helper.

## Notes / answers from owner
- Optional per request, to decide at PLAN (else follow-up): indexes on `lower(name)`/`slug` (table is small today); rejecting unknown query params on list routes; the same `sort` param on `GET /api/v1/users` (admin-pan PAN-013 disabled column sorting).
