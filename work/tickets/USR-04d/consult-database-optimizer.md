# consult-database-optimizer — USR-04d

**Findings**
- `email_send_log` already has `ix_email_send_log_sent_at`, so no new index is needed there. `email_tokens` has no index on `created_at`, and `count_issued_24h` does a sequential scan of it (USR-04b N3). One new index serves both the cap count and the cleanup.
- The longest token TTL is 24 h (verify; reset is 30 min). Any token with `created_at` older than 7 d is therefore already expired, so "used or expired, older than 7 d" reduces to `created_at < now() - 7 d`. That also keeps every row the 24 h cap counts.
- `TokenRepository.invalidate` updates `used_at IS NULL` rows without checking expiry, so it can touch old expired rows that cleanup is deleting.
- Head revision is `91a78d0496f9`. The ops workflow's environment `org-admin-api-ops` needs the owner's approval, so a scheduled run of it would wait for approval every time.
- Changing `get_for_update` to `with_for_update(key_share=True)` keeps the conflict with login's `FOR SHARE` and with user DELETE. It stops blocking the foreign-key `KEY SHARE` taken by inserts into `email_tokens` and `account_sessions`, which removes the S1 cycle. Cleanup deletes only child rows and takes no lock on `users`, so the two do not interact.

**Recommendations**
- **Migration (R2):** add `ix_email_tokens_created_at` to the model and the migration (`down_revision="91a78d0496f9"`), with `downgrade()` dropping it. A plain `CREATE INDEX` is fine because the table is small.
- **Repository, R1:** add `purge_send_log(cutoff, batch)` and `purge_tokens(cutoff, batch)`, each shaped as `DELETE … WHERE id IN (SELECT id … WHERE <ts> < :cutoff ORDER BY <ts> LIMIT :batch FOR UPDATE SKIP LOCKED)`. `SKIP LOCKED` means cleanup never waits on, or deadlocks with, invalidate/issue.
  - Commit after each batch (1000 rows) and loop until a batch deletes fewer rows than the limit.
  - Use `SET LOCAL lock_timeout='2s'`.
  - Take the cutoffs from the DB clock and enforce minimums in code: tokens 7 d (never below 24 h), send log 48 h.
- **CLI:** `python -m app.cli cleanup [--dry-run]`.
  - Print only fixed-format counts (R11).
  - Exit 1 with a fixed message on failure.
- **Schedule:** a daily cron in `mmlabs-online/deployment`, in a separate workflow or job that can only run `cleanup`.
  - It needs its own environment without a required reviewer.
  - Document it in `docs/deploy.md`.
- **Scope (R7):** leave `login_failures` and session cleanup out and list them as follow-ups.
  - Sessions need a decision: `account_sessions.id` is the `account_id` apps hold (ADR-0004), and `device_sessions` deletes cascade to it.
  - Design `cleanup` so new targets plug in easily.

**Risks**
- A cleanup query with a bad predicate could delete tokens still inside the 24 h window, which loosens the S2 cap. The enforced minimum cutoff guards this; add a test asserting a 25 h-old used token survives.
- With the ops environment's approval gate, a scheduled run would hang. Without the gate, it bypasses the owner's approval control, so it must be limited to `cleanup`.
- On a first run against a large backlog, batching with `SKIP LOCKED` may leave locked rows behind; the next run picks them up.

**Open questions**
- Does the owner accept a scheduled cleanup run without approval, in its own environment, or should it stay manual through the ops workflow?
- Should `login_failures` (`last_failed_at` older than 24 h and not blocked) be added now, given ADR-0002 flags it? It is cheap and no index is needed.
