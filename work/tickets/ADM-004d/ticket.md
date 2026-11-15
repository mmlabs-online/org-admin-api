# ADM-004d: Audit log DB hardening

| Field | Value |
|---|---|
| State | REVIEW |
| Priority | P2 |
| Areas | db, security |
| Attempts | 0 |
| Created | 2026-11-15 |
| PR | — |

## Goal
Move the remaining audit-log guarantees from app code into the database.

## Scope
From the ADM-004c reviews (`work/tickets/ADM-004c/security-review.md`, `review.md`).
1. Trigger: add `AND OLD.action <> 'audit_log.purged'` to the DELETE branch so the purge trail is kept by the DB, not only by `_purgeable()`.
2. `BEFORE INSERT` trigger forcing `NEW.occurred_at := now()` (no backdated rows from raw SQL).
3. `c3f1a9d27b60.downgrade()` refuses while `audit_log` has rows (and in offline mode), like `af3eb84d2d5e`.
4. `docs/deploy.md`: "any DB role can set the flag" (not only the owner); never put `-c audit.allow_purge=on` in `DATABASE_URL`.
5. Strip Unicode bidi / line-separator format characters (e.g. U+202E, U+2028) from `user_agent`.
6. Comment in the purge batch loop: `SKIP LOCKED` may end a run early; re-run is safe.

## Acceptance criteria
- [ ] Tests for each DB guard (purge-trail DELETE refused even with the flag; inserted `occurred_at` overridden; downgrade refusal)
- [ ] Migration up/down/up clean; `make check` green

## Dependencies
ADM-004c.

## Notes / answers from owner
