# ADM-026: Organizations and apps lists — name sort collation and `member_count` sort

| Field | Value |
|---|---|
| State | DONE |
| Priority | P3 |
| Areas | api, db |
| Attempts | 1 |
| Claimed by | — |
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
- 2026-11-27 (Ming, chat): "duyệt hết; câu 1–24 theo mặc định; cho merge USR-28".
- Plan approved (2026-11-27, batch 2026-11-27-1); all blocking questions answered with the plan defaults.

## Handoff

### 2026-11-27 — claude — DONE (batch 2026-11-27-1, attempt 1)
- Agent calls: 6, tokens ~272k total (api-platform-engineer 26k, database-optimizer 30k, planner opus 59k, implementer sonnet 63k, reviewer opus 55k, appsec-engineer 39k).
- Done: organizations and apps `name`/`-name` sort = `lower(name COLLATE "und-x-icu")` then `id` ascending (case/accent folded, Vietnamese-friendly); apps `member_count`/`-member_count` via one grouped LEFT JOIN with `coalesce` (shared `member_count_select()`, also used by `member_counts`); other sorts/defaults unchanged; app/core and migrations untouched; ICU guard test. Review PASS (4586 passed; ruff/mypy/alembic clean; tier high); appsec PASS (🟡 deploy prerequisite).
- **Merge gate:** M-10 — Ming confirms on prod (read-only) that `und-x-icu` exists and `server_encoding` = UTF8; without ICU every name sort would 500.
- Follow-ups: remove or generalise `app/core/listing.py::sort_clauses` (High); reuse `name_sort_key` for other name-sorted lists; `is_active_app_member` reuses the shared member rule; catch-all `Exception` handler returning the R6 envelope (High); remove or guard unused `app_debug`.

