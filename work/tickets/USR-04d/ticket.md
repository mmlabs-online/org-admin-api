# USR-04d: Email hardening: dispatcher shutdown, retention cleanup, daily-cap alert

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P2 |
| Areas | auth, db |
| Attempts | 0 |
| Created | 2026-11-13 |
| PR | — |

## Goal
Operational hardening of the email subsystem left over from USR-04 / USR-04b reviews.

## Spec reference
§5.8; ADR-0007; split from USR-04b (`work/tickets/USR-04b/plan.md` → "Split").

## Acceptance criteria
- [ ] `EmailDispatcher.aclose()` is wired into the app lifespan (clean shutdown drains or drops the queue with a log line)
- [ ] A retention CLI (via the ops workflow) deletes `email_send_log` rows older than 48 h and used or expired `email_tokens` older than 7 days; add an index on `email_tokens(created_at)` with a migration if needed
- [ ] Runbook: alert on `email_not_sent reason=daily_cap`, `email_quota_exceeded`, `email_auth_failed`
- [ ] Route-level `AppContextDep` on `/auth/verify-email/resend` for consistency
- [ ] Tests for each item

## Dependencies
USR-04b.

## Notes / answers from owner
- 2026-11-13: created by the orchestrator from the USR-04b planner's split.
