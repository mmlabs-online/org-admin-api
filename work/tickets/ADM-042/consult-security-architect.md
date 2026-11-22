# Consult — security-architect — ADM-042 (2026-11-22)

### Findings
1. **What a leaked `usage:write` key can do** (ADR-0022 Consequences, lines 115-117). Anyone holding the key can burn any member's quota, which denies that user AI. Worse, they can refund real consumes, which gives selected users (including themselves, if a user gets the key) unlimited AI. The only controls today are revocation and keeping 2 keys for rotation. Nothing detects refund abuse.
2. **The failed-auth per-IP bucket is missing.** ADR-0022 §7 (line 82) requires three buckets. I only found the pre-auth per-IP bucket and the post-auth per-key bucket (`app/core/rate_limit.py:71-94` and `:162-179`). Nothing throttles failed auth separately: `_reject` at `app_credentials/service.py:168-180` only raises a 401.
3. **Wrong-scope and wrong-app attempts write an audit row on every request** (`app_credentials/service.py:185-191`). On a hot path, a misconfigured backend produces one row per request. Only the rate limit caps this.
4. **The cap and the counter don't line up yet.** `MAX_LIMIT = 2**53-1` (`usage/models.py:26`) caps limits but not `used`. A settle above the estimate is allowed past the limit (ADR-0022 §5, line 68), so many settles could push `used` past `MAX_LIMIT` (BIGINT itself is safe; JS clients are not).
5. **Changing a metric's period means a new bucket** (ADR-0024 §4). So a refund or settle that recomputes "the current period" would credit the wrong bucket.
6. **Existing services return 404 for an unknown user** (`usage/service.py:204-205`). Copying that onto consume would let a key holder learn which user ids have an mmlabs account at all, even outside its app.

### Recommendations
- **Refund and settle rules** (AC3, ADR-0022 §5):
  - The event row stores `period`, `period_start`, `charged` and a state (`consumed`, `settled`, `refunded`). Refund and settle read these values and never recompute them.
  - Refund has no amount field. It reverses exactly `charged`, at most once.
  - Settle sets `charged` to the new value, at most once. Settling to 0 counts as a refund.
  - Add a refund/settle window (proposed 24 h after consume). This limits how much past usage a leaked key can reverse.
  - Add a DB `CHECK (used >= 0 AND used <= MAX_LIMIT)`. If a settle would break it, return 422, not 500.
- **Idempotency** (AC2):
  - Pydantic pattern `^[\x21-\x7e]{1,128}$` plus a DB CHECK on the column.
  - Fingerprint the request on (user, metric, operation, amount), as ADR-0022 §5 says.
  - Settle or refund must match the consume's user and metric, else 409 `idempotency_key_reused`.
  - Insert the event first with `ON CONFLICT (app_id, idempotency_key) DO NOTHING`. A concurrent request with the same key then waits on the unique index and replays the result.
  - Never log the idempotency key, because apps may put user content in it.
- **Race to exceed the limit** (AC1): do the whole consume in one conditional statement, `INSERT … SELECT … WHERE :amt <= :limit ON CONFLICT … DO UPDATE SET used = used + :amt WHERE used + :amt <= :limit RETURNING used`. Zero rows returned means 409, and the event is rolled back. The first insert must be guarded too, for the case amount > limit. Read the limit live (override, else default). Do not `SELECT … FOR UPDATE` the metric or app rows, and do not take `lock_app` (ADR-0022 §3).
- **Amounts:** add a config value `usage_max_amount_per_call` (R5) and refuse zero or negative with 422. In responses, clamp `remaining` at 0 or more.
- **Subject checks:** check the metric first: unknown or archived gives 404 (scoped to the app). Then the user: unknown, disabled, non-member and unverified all give the same 403 with one code (e.g. `usage_subject_not_allowed`). Check membership on every request, never from claims.
- **Fail-closed:**
  - Never return 2xx before commit. DB errors return 5xx.
  - Use `extra="forbid"` so `occurred_at` and late-report fields are refused until ADM-046.
  - Set `SET LOCAL lock_timeout` (around 2 s) on consume so a hot row can't hold up workers.
- **Data exposure and audit:** responses and logs carry only the user id, metric key, key_id and amounts: no email, no reason, no idempotency key. No audit row per use. Add a structured log or metric for refund rate per key_id, so a leak can be spotted.
- **Rate limits:** add new scopes `usage` (per IP), `usage_auth_failed` (per IP, strict) and `usage_key` (per key). Size the per-key bucket by app throughput, since one key serves every user of an app. Backends share egress IPs, so the per-IP bucket must stay generous. The effective limit is N workers times the setting (ADR-0002).
- **Tests:** a concurrency test with N parallel consumes on the last unit, a replay with a changed amount (409), a second refund, a refund after the window, and settle overflow.

### Risks
- A leaked key in a user's hands means unlimited AI for that user (through refunds) until someone revokes the key. The refund window and monitoring reduce this; they don't remove it.
- Fail-closed couples every app's AI features to this API's uptime.
- Rejected consumes kept in the ledger would let a caller fill the table with 409 rows.

### Open questions for Ming (proposed defaults)
1. Per-call max amount? Default **1,000,000**.
2. Refund/settle window? Default **24 h** after consume; after that, 409 `usage_refund_window_closed`.
3. Store rejected (409) consumes in the ledger? Default **no**. A retry is re-evaluated, so it can succeed after a grant.
4. Refunds always full, never partial? Default **full only**. Partial adjustments go through settle.
5. Rate limits? Default per-IP **1200/60 s**, failed-auth per-IP **20/60 s**, per-key **600/60 s**.
6. Consume latency target? Default **p95 < 100 ms** server-side.
7. Unknown user id gives 403, not 404? Default **403**, to stop account enumeration.
