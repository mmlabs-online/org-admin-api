# USR-04d: Email hardening: dispatcher shutdown, retention cleanup, daily-cap alert

| Field | Value |
|---|---|
| State | REVIEW |
| Priority | P1 |
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
- [ ] (USR-04b security S1, priority) `UserRepository.get_for_update` uses `FOR NO KEY UPDATE` (`with_for_update(key_share=True)`) so reset/verify cannot deadlock with a concurrent resend; real-Postgres concurrency test resend vs reset and resend vs verify
- [ ] (USR-04b security S2) per-purpose ceiling so reset cannot starve verify (e.g. reset ≤ 50 % of `EMAIL_DAILY_CAP`); retention keeps `email_tokens` ≥ 24 h (the cap counts them)
- [ ] Tests for each item

## Dependencies
USR-04b.

## Notes / answers from owner
- 2026-11-13: created by the orchestrator from the USR-04b planner's split.
- 2026-11-13: raised to P1 for the S1 deadlock fix (500 on a rare reset/verify vs resend race).
