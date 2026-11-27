# ADM-026: Organizations and apps lists — name sort collation and `member_count` sort

| Field | Value |
|---|---|
| State | PLANNING |
| Priority | P3 |
| Areas | api, db |
| Attempts | 0 |
| Claimed by | claude@2026-11-27T03:59Z |
| Created | 2026-11-19 |
| PR | — |

## Goal
`sort=name` orders Vietnamese and mixed-case names as staff expect (today it follows the database collation: uppercase before lowercase, accented letters last), and apps can be sorted by `member_count` (422 today).

## Spec reference
§5.1 Organizations, §5.3 apps registry, §5.9 (member_count, OQ5). Follows ADM-020 and USR-18 (shared listing helper `app/core/listing.py`).

## Acceptance criteria
- [ ] `name` / `-name` sort on organizations and apps uses a case-insensitive, locale-aware order (e.g. an ICU collation such as `und-u-ks-level2`, or `lower(name)` with a Vietnamese-friendly collation), keeps the `id` tie-break, and is documented. A migration (with `downgrade()`, R2) only if a collation/index is created.
- [ ] Apps `sort` accepts `member_count` / `-member_count` if it can be done without a per-row count (else documented as not supported and left out).
- [ ] Existing sort values and defaults unchanged (R9).
- [ ] Tests: mixed-case and accented names in the expected order; `member_count` sort with ties broken by `id`.

## Dependencies
ADM-020, USR-18, ADM-016. Consumer: admin-pan PAN-029. Requested by admin-pan (batch 2026-11-19).

## Notes / answers from owner
