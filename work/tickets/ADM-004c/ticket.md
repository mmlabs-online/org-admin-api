# ADM-004c: Audit retention purge

| Field | Value |
|---|---|
| State | REVIEW |
| Priority | P1 |
| Areas | db, security |
| Attempts | 0 |
| Created | 2026-11-15 |
| PR | — |

## Goal
Audit rows can be purged after the retention period, only by an operator, with a DB-enforced floor so recent evidence can never be erased.

## Scope
Split from ADM-004b (see `work/tickets/ADM-004b/plan.md` and consults).
- Migration replacing `audit_log_immutable()`: UPDATE and TRUNCATE always refused; DELETE allowed only with `SET LOCAL audit.allow_purge='on'` AND `OLD.occurred_at < now() - interval '90 days'` (hard-coded floor). Downgrade restores the original function text verbatim.
- CLI `audit-purge --older-than-days N [--dry-run]`: refuses missing N or N < 90; batches (`lock_timeout`, `FOR UPDATE SKIP LOCKED`, `ix_audit_log_occurred_at_id`); one `audit_log.purged` summary row (`{"cutoff", "deleted": int}`) per batch transaction; never an HTTP route.
- The bypass statement lives only in a CLI-only module; a grep test fails if `audit.allow_purge` appears anywhere else in `app/` (besides the migration).
- Email cleanup: summary row per batch transaction (no deletion without its row).
- Ops workflow allowlist entry in `mmlabs-online/deployment` (own approval line) + deploy.md runbook.

## Acceptance criteria
- [ ] DELETE without the flag, or with the flag on a row newer than 90 days, raises; UPDATE/TRUNCATE always raise (tests)
- [ ] `audit-purge` refuses N < 90 / missing; dry-run changes nothing; each batch writes its summary row
- [ ] Grep test for the bypass statement
- [ ] Migration up/down/up clean; downgrade restores the original function
- [ ] `make check` green

## Dependencies
ADM-004b.

## Notes / answers from owner
- Defaults pending owner confirmation (spec Q6): keep 365 days, full IP, 90-day floor; read-time IP masking before widening `admin:audit:read`. Open: does Vietnam Decree 13/2023 require a shorter period?
- Carried from ADM-004b security review: 🟡 `af3eb84d2d5e` `downgrade()` must refuse while `audit_log` has `outcome <> 'success'` rows (dropping `outcome` makes denied rows read as successes); add `af3eb84d2d5e` to the "never downgrade production past" line in deploy.md. 💭 use `CF-Ray` (or always generate) for the stored request id; strip C1 (`\x80-\x9f`) from user agent; add `type(exc).__name__` to `audit_write_failed`; large-table migration pattern (NOT VALID + VALIDATE, CONCURRENTLY) for future audit DDL; admin UI must show `outcome`.
