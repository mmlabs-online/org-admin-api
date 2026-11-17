## Attempt 1 — 2026-11-17

### Files touched
| File | Required because |
|---|---|
| app/core/config.py | `le=3600` -> `le=43200` plus comment (AC1) |
| .env.example | TTL comment bounds (AC1) |
| app/cli.py | `MSG_ADMIN_CLOSED` no longer implies a short tail (plan) |
| tests/test_config.py, test_auth_token.py, test_access_token.py, test_admin_auth.py | Plan test list (AC1, 2, 2a, 2b) |
| docs/adr/0005, 0001, 0007, 0008, 0011 | Amendment and stale lifetime statements (AC3, 3a) |
| docs/consumers.md, docs/deploy.md | New lifetime, checklist, rotation wait, incident runbook, owner post-merge note (AC3) |
| docs/identity-architecture.md, docs/spec-v1.md | D7 / §5.8 / §5.10 / OQ8, tagged "(owner, 2026-11-17)" (AC3a) |

### Tests added / changed
- tests/test_config.py › test_access_token_ttl_bounds (59, 43201), test_access_token_ttl_accepted, test_access_token_ttl_from_env
- tests/test_auth_token.py › test_token_ttl_43200
- tests/test_access_token.py › test_long_lived_token_accepted
- tests/test_admin_auth.py › test_long_lived_token_role_revoked_403, test_long_lived_token_disabled_401

### Checks (actual output summary)
- make check (ruff, format, mypy, pytest): exit 0; pytest "1997 passed, 1 warning in 153.51s"
- alembic check: not run separately (no schema change)
- rg for stale lifetime wording: remaining hits are "default 15 min / 900 s" statements, the historical ADR-0005 §6 line and the ADR-0008 §7 "default 900 s" (default unchanged)

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- As listed in plan.md Assumptions (per-audience admin TTL, tokens_valid_after/sid, cap exp at assignment expiry, verify consumer libs before production 43200).

### Scope self-check
- Could the diff be smaller? No; docs edits are the plan's list.
- Diff size: see `git diff --shortstat` of the commit.
