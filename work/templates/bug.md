# BUG-XXX: <short description of the wrong behaviour>

| Field | Value |
|---|---|
| Type | bug |
| State | BACKLOG |
| Severity | S1 / S2 / S3 |
| Priority | P1 |
| Areas | <architecture, db, api, auth, security> |
| Found in | vX.Y.Z (production `api.mmlabs.online` / local / CI) / unknown |
| Introduced in | vX.Y.Z (<ticket or commit>) / unknown |
| Attempts | 0 |
| Created | YYYY-MM-DD |
| PR | — |

## Report
<What the owner (or a consumer app) saw, quoted, with the date. Logs or screenshots: describe them; never copy passwords, tokens, cookies, signing keys or full emails (R5, R11).>

## Environment
- Instance: production / local (`make dev`) / CI · Version or commit · Calling app (`X-MM-App`) and auth class (Bearer / cookie / public, R3) · Request (method + path)

## Steps to reproduce
1. …

## Expected
- …

## Actual
- … (status code, error `code` from the envelope, observable effect)

## Root cause
<file:line and why, once known; "unknown — planner investigates" otherwise. Mark it **confirmed** (reproduced) or **suspected**.>

## Scope
- Fix the cause above; nothing else (R7).

## Out of scope
- …

## Acceptance criteria
- [ ] A regression test reproduces the bug: it fails on `main` before the fix and passes after (name the test).
- [ ] The expected behaviour above holds.
- [ ] `make check` passes (and `uv run alembic check` if a migration changed).

## Technical notes
- Files: …
- Test level: a pytest test against the Postgres test DB (`client` / `make_client` fixtures, fakes in `tests/fakes.py`) wherever it can reproduce the bug. Only when pytest cannot see it (Docker image, deploy, CI, real SMTP or Cloudflare): a scripted reproduction outside the repo whose command and output are recorded in impl-log (ADR-0012).

## Dependencies
<Tickets this fix needs first, or "None".>

## Notes / answers from owner
