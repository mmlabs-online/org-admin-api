# Impl log — ADM-041

## Attempt 1
Files touched (docs only):
- docs/adr/0022-usage-key-scopes.md (new) — required: ticket scope, AC1/AC2
- docs/adr/0021-release-log-and-deployer-credential.md — one "Amended by ADR-0022" line
- CLAUDE.md — R3 class (4) rewritten scope-generic (plan step 4)
- docs/identity-architecture.md — D31 row after D29
- docs/spec-v1.md — dated note on OQ61 (§5.14 and §8), Q3 default accepted

Tests added: none (docs-only).
Checks: `ruff check` all passed; `make typecheck` no issues in 167 files; `tests/test_route_auth_classes.py` 171 passed (Postgres started).
Out-of-plan files: none.
Follow-ups: D30 (release-log row) is absent from identity-architecture.md §8 although ADR-0021 refers to D30; plan says not to add it, so D31 follows D29. CLAUDE.md R3 class 4 cites "D31" only (not D30) for the same reason.
Scope self-check: only plan files changed; no code.
