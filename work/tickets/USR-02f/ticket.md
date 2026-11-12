# USR-02f: Bootstrap super-admin CLI (grant-super-admin) with masked output and runbook

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P1 |
| Areas | auth, security |
| Attempts | 0 |
| Created | 2026-11-12 |
| PR | — |

## Goal
Grant the first super-admin from the server's command line — never over HTTP, with the RBAC flag off.

## Spec reference
§5.3; ADR-0001; identity-architecture.md §4; split from USR-02c (`work/tickets/USR-02c/plan.md` → "Split").

## Acceptance criteria
- [ ] `python -m app.cli grant-super-admin --email <email>` (argparse): in one transaction locks the `admin` app, loads the seeded `admin:super-admin` role, finds the user by normalized email and reuses `AssignmentService._grant_locked`; app scope, `granted_by` and `expires_at` NULL
- [ ] Idempotent: "granted" / "already held" both exit 0; an expired row is replaced in place
- [ ] Exit codes: 0 success, 2 unknown email, 3 disabled user, 1 unexpected error, 64 usage error; failures print one generic line, no traceback, no DSN
- [ ] Output: user id, masked email (helper in `app/core/text.py`), `created_at`, `email_verified: yes/no`; the full email never appears, even on a DB error (engine `hide_parameters`; `DB_ECHO` refused for the CLI)
- [ ] Not reachable over HTTP (not imported from `app.main`); needs no flag; creates no users, takes no password, no "mark verified" path (D16)
- [ ] `make grant-super-admin email=...` and a `docs/deploy.md` runbook entry: `docker compose run --rm --no-deps`, required env, sign up and log in yourself before granting (pre-registration hijack), password reset after USR-04
- [ ] Tests: every exit code, idempotency, no full email in captured stdout/stderr, masking helper

## Notes / answers from owner
- 2026-11-12: created by the orchestrator from the USR-02c planner's split.
- Depends on USR-02c.
