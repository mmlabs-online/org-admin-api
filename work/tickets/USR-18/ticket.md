# USR-18: Filters and sort for the staff apps list

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P1 |
| Areas | api |
| Attempts | 0 |
| Created | 2026-11-17 |
| PR | — |

## Goal
Staff can search and filter apps on the admin-pan Apps screen (PAN-015); `GET /api/v1/apps` today accepts only `limit`/`offset`.

## Spec reference
§5.3 Authorization (apps registry); §5.9 context (admin-pan Apps screen).

## Acceptance criteria
- [ ] Optional filters: `status` (`active` | `disabled`), `join_policy` (one value of the existing enum), `q` (trimmed, 1..100 chars; case-insensitive substring on `key` OR `name`, LIKE wildcards escaped).
- [ ] Optional `sort` from an allow-list: `key`, `-key`, `name`, `-name`, `created_at`, `-created_at` (default = today's order); `id` always the tie-breaker.
- [ ] `total` is the filtered count; `member_count` (ADM-016) still present; unknown or invalid values → 422. No breaking change (R9).
- [ ] Same design (param names, empty-`q` rule, escaping helper) as ADM-020.
- [ ] Tests: each filter alone and combined, escaping, sort order and tie-breaker, 422 cases, 403 without `admin:apps:read`.

## Dependencies
USR-02b, ADM-016. Shares design with ADM-020 (plan together or after it). Requested by admin-pan (batch 2026-11-17).

## Notes / answers from owner
