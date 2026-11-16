## Attempt 1 — 2026-11-16

### Files touched
| File | Required because |
|---|---|
| docs/deploy.md | AC 1: host switched to admin-portal in runbook, examples and outputs; Host policy rewritten; l.482 wording; ADM-012 host-move sentence qualified |
| docs/adr/0008-admin-api-authentication.md | AC 2: old host marked superseded, dated ADM-014 note added |
| docs/consumers.md | AC 3: include for login and cookie-class calls, omit for signup/forgot/verify-email/reset; examples updated |
| Makefile | AC 2: `##` help comment host only (plan A1) |

### Tests added / changed
- None (docs only, per plan).

### Checks (actual output summary)
- Scoped grep `grep -rnE '(^|[^-[:alnum:].])admin\.mmlabs\.online' docs README.md CLAUDE.md Makefile`: only "not used / must not exist" sentences remain (deploy.md ADM-012 sentence and Host policy, ADR-0008 note).
- `grep 'so CORS answers the preflight' docs/consumers.md`: no hits.
- `sh -n` on the admin-token runbook block: exit 0. `bash -n` on the set-admin-origins bash blocks: exit 0.
- `grep set-admin-origins: Makefile` shows the new host (no `help` target exists).
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: 1736 passed, 1 warning (ruff, format, mypy green).
- alembic check: n/a (no migrations).

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- Plan F-1 to F-5 stand (production origins state, wildcard DNS, auth-client audit, identity-architecture diagram, deployment repo TURNSTILE_ALLOWED_HOSTNAMES).

### Scope self-check
- Could the diff be smaller? No.
- Diff size: see `git show --stat`.

## Attempt 2 — 2026-11-16

### Files touched
| File | Required because |
|---|---|
| docs/consumers.md | Review Issue 1: l.42 now "Only login creates the cookie; every cookie-class call may rotate it (R11), and logout or a 401 clears it."; sign-up paragraph now "neither reads nor sets the cookie; only login creates it" |
| docs/deploy.md | Review nits (Host policy): catch-all rule `http_status:404`, concrete `dig +short` check, dry run always + wait 60 s + 15 min token TTL note |

### Tests added / changed
- None (docs only).

### Checks (actual output summary)
- Scoped grep: only "not used / must not exist" hits (deploy.md 209, 559, 560, 566; ADR-0008 56, 59).
- `sh -n` / `bash -n` on all sh/bash fences in docs/deploy.md: no failures.
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: first run 1 failed, 1735 passed (`tests/test_cli_audit_purge.py::test_purge_batches_share_cutoff`, timing-sensitive, unrelated to docs; passes alone, 25 passed). Re-run: 1736 passed, 1 warning (ruff, format, mypy green).

### Out-of-plan files
None.

### Follow-ups (noticed, not done)
- `test_purge_batches_share_cutoff` looks flaky under load.
- Reflowed line widths in consumers.md (review nit) left as is.

### Scope self-check
- Could the diff be smaller? No.
