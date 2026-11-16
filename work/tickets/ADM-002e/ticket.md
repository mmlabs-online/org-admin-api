# ADM-002e: Close the APP_ENV ?host= bypass and fix the admin runbook trap

| Field | Value |
|---|---|
| State | DONE |
| Priority | P1 |
| Areas | auth, security |
| Attempts | 2 |
| Created | 2026-11-15 |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/36 |

## Goal
Fix the two should-fix items from the ADM-002d security review, plus small follow-ups.

## Scope
From `work/tickets/ADM-002d/security-review.md` and `review.md`.
1. 🟡 `app/core/config.py` APP_ENV gate: a `?host=` (or `hostaddr`) query parameter overrides the netloc in the asyncpg dialect, so `postgresql+asyncpg://u:p@localhost/db?host=prod-db.internal` passes the gate but connects remotely. Treat any `host`/`hostaddr` query key as non-loopback (or resolve hosts the way the engine does); add `?host=` cases (remote host, socket path) to `tests/test_config.py`.
2. 🟡 `docs/deploy.md` admin-token runbook: the EXIT trap runs under `set -eu`; a failing `stty` (no tty, e.g. SIGHUP after an SSH drop) aborts the trap before logout and cleanup. Start the trap with `set +e`, use `stty echo 2>/dev/null || true`, initialise `jar= body= hdr=` before the trap and create the temp files after it.
3. 💭 `resend` verification: take a share lock on the user (like forgot) so a concurrent disable's `invalidate_all` cannot miss a freshly issued VERIFY token.
4. 💭 Test that after `upgrade head` every `admin`-app permission row has `is_admin = true` (guards future data migrations).
5. ~~Exclude `work/**/*.md` from `ruff format`~~ — done in solo 2026-11-16 (`extend-exclude = ["work"]`).
6. `.env.example` holds values; R5 says keys only — strip values (keep comments).

## Acceptance criteria
- [ ] `?host=` / `hostaddr` DSNs without explicit APP_ENV refuse startup (tests)
- [ ] Runbook block passes `sh -n`/`dash -n`; trap completes logout + cleanup with stdin not a tty (reviewer dry-run)
- [ ] Resend/disable race closed (test or lock-order evidence)
- [ ] `make check` green

## Dependencies
ADM-002d.

## Notes / answers from owner
- 2026-11-16 (orchestrator): plan READY; implementation paused so ADM-012 (admin CORS, owner priority) runs first; resume after ADM-012.
