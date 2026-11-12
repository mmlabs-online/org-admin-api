# USR-03f: Session hardening: commit before response, atomic login throttle, cookie-tossing defence

| Field | Value |
|---|---|
| State | IN_PROGRESS |
| Priority | P1 |
| Areas | auth, security, api |
| Attempts | 0 |
| Created | 2026-11-12 |
| PR | — |

## Goal
Close the three 🟡 findings from the USR-03 security review before tokens (USR-03c) build on device sessions.

## Spec reference
§5.8; D6, D11, D13; R11; `work/tickets/USR-03/security-review.md` (Attempt 1: S1, S2, S3, N1).

## Acceptance criteria
- [ ] S2: the DB transaction commits **before** the response (and its `Set-Cookie`) is sent for login, logout and any rotating request (e.g. `get_session` with `scope="function"` repo-wide, or explicit commits); a failed commit sends no `Set-Cookie`; test with the strict client
- [ ] S1: per-email backoff/Turnstile decision and the failure increment are atomic (e.g. `pg_advisory_xact_lock` on the email HMAC, or reserve-then-decide); concurrency test: two parallel attempts at `failures=2` → exactly one 401 `invalid_credentials` and one `turnstile_required`, counter ends at 3 (corrected per plan D-1)
- [ ] S3: defence against cookie tossing from sibling `*.mmlabs.online` subdomains per the owner's decision — either `__Host-mm_device` with `Path=/` (spec/R11 change) or keep `Path=/api/v1/auth` and clear tossed `Domain=` cookies / record the risk in ADR-0004
- [ ] N1: logout `{account_id}` honours `accounts_created_before` when the previous cookie value is used
- [ ] Tests for each item; ADR-0004 updated

## Dependencies
USR-03.

## Notes / answers from owner
- 2026-11-12 (owner): S3 → use `__Host-mm_device` with `Path=/` (spec D13/R11 cookie path changes from `/api/v1/auth` to `/`; update spec, identity-architecture.md and ADR-0004 accordingly; CLAUDE.md R11 wording is for the owner to update).
- 2026-11-12: created by the orchestrator from the USR-03 security review (no 🔴, three 🟡). Needs an owner decision on S3 (`__Host-` prefix and `Path=/` vs `Path=/api/v1/auth`).
