# USR-02e: Role catalog with ETag (flag-gated)

| Field | Value |
|---|---|
| State | PLANNING |
| Priority | P1 |
| Areas | api, security |
| Attempts | 0 |
| Created | 2026-11-11 |
| PR | — |

## Goal
Apps (and staff) can fetch an app's role → permissions catalog cheaply with ETag / 304, without making it public before the owner decides.

## Spec reference
§5.3; ADR-0001 §5; split from USR-02b (`work/tickets/USR-02b/plan.md` → "Split proposal" → slice 02b-3)

## Acceptance criteria
- [ ] `GET /api/v1/apps/{key}/roles` in its own router, **always mounted and public** (D17, R3 class 3), with a per-IP rate limit; no `x-permission` declaration (public allow-list)
- [ ] Canonical sorted document; strong ETag = hash of the exact response bytes; `If-None-Match` → 304 (lists, `W/`, `*`), empty body, same ETag; `Cache-Control: no-cache`
- [ ] 404 for unknown, disabled and `admin` apps
- [ ] Tests: ETag stable, changes after a permission/role change, 304 variants, 404 cases, flag off → 404

## Notes / answers from owner
- 2026-11-11 (orchestrator): per D17 the catalog is **public and always mounted** — the "(flag-gated)" in the title, "without making it public" in the Goal and "flag off → 404" in the tests list are superseded (they predate the owner's Q1 answer). Test instead that the catalog is reachable with the RBAC flag off.
- 2026-11-11 (owner): **Q1 yes** — the catalog is public (D17). **Q2 yes** — `{key}` in paths and a non-`Page` document are R9 exceptions (D18).
- 2026-11-11: created by the orchestrator from the USR-02b planner's split (slice 02b-3). Can merge into USR-02d if Q1 (public catalog) is answered first.
- Depends on USR-02d.
- Carry-over from the USR-02d security review: catalog uses its own minimal schema (not `RoleRead`); before role names go public, extend `_NO_CONTROL` to reject C1 (`\x80-\x9f`), bidi overrides/isolates (`\u202a-\u202e`, `\u2066-\u2069`) and `\u2028/\u2029`, and apply it to `AppCreate.name`/`AppUpdate.name` too (NUL currently → 500); optionally refuse origins under `.api.mmlabs.online`.
