# ADM-004b: Audit identity events, denied privileged attempts and retention

| Field | Value |
|---|---|
| State | REVIEW |
| Priority | P1 |
| Areas | db, api, security, auth |
| Attempts | 0 |
| Created | 2026-11-15 |
| PR | — |

## Goal
Complete the audit log: security-relevant identity events and denied privileged attempts are recorded, using one failure-path write mechanism that never changes the intended error.

## Scope
Split from ADM-004 (see `work/tickets/ADM-004/plan.md` and consults).
- Identity events: `auth.login_succeeded`, `auth.login_failed` (credential-checked failures only; never 429 or Turnstile refusals; unknown email → `actor_kind=anonymous`, no email, no HMAC), `auth.logout`, `auth.logout_all`, `device_session.reuse_revoked`, `auth.password_reset_completed`, `auth.email_verified`.
- Denied privileged attempts: ADM-002b guard 403s and the `last_super_admin` 409, `outcome=denied`. Plain `require_permission` 403s are not recorded.
- Additive migration: `outcome` column (`success|failure|denied`, default `success`) + `outcome` filter on `GET /api/v1/audit-logs`.
- One failure-path mechanism: `AuditLogger.record_failure` on the request session (rollback → add row → commit → caller raises the intended error), wrapped so an audit write error is only logged (`audit_write_failed`, no PII) and the intended 401/403/409 is always returned. Where code already commits before raising (login failure reservation, `commit_on_reuse`), add the row before that commit. Must work with the `create_savepoint` test harness; no separate engine.
- Retention purge (trigger bypass flag or monthly partitions); period per spec Q6 (owner).

## Acceptance criteria
- [ ] One row per covered identity event; none for 429/Turnstile refusals
- [ ] Denied privileged attempts recorded with `outcome=denied`; the HTTP error is unchanged
- [ ] An audit write failure on a failure path never turns the response into a 500 (test)
- [ ] No password, token, cookie, hash or email of an unknown account in any row (test)
- [ ] `make check` green

## Dependencies
ADM-004.

## Notes / answers from owner
- Carried from ADM-004 review/security review: decide retention + IP truncation (spec Q6) before widening `admin:audit:read`; scope the `email_send_log`/`email_tokens` key exemption to `email_retention.purged` with integer values; consider a cleanup summary row per table (batches commit before the summary); reject scoped IPv6 (`%zone`) before insert; document that `X-Request-ID` is client-supplied (or accept it only from trusted proxies); optionally strip C0 control chars from user agent.
- 2026-11-15 (planner): retention purge split to ADM-004c. Per consults, the single failure-path mechanism is replaced by per-path mechanisms (ADR-0009 amendment); logout actions renamed `auth.logged_out` / `auth.logged_out_all`.
