# Security review — ADM-042

## Attempt 1
Verdict: PASS

Static review only (git diff origin/main...8c14fb8, grep, reads). I did not run pytest or make check.

**Findings**

🟡 **The failed-auth bucket does not cap denied-audit writes or auth work. It only changes the status code.** `app/modules/app_credentials/deps.py:68-70`, together with `app/modules/app_credentials/service.py:189,194` (`_denied` → `record_failure`, which commits on its own).
- **Risk:** the bucket is hit only after `authenticate` has failed. For mismatch and wrong-scope failures, `record_failure` has already rolled back and committed a `denied` audit row before `enforce_failure_limit` raises 429. Once an IP is over the failed-auth limit, every further attempt still does a key lookup, a hash compare and, with a valid key of any app, one committed audit insert. Only the 1200/min per-IP bucket (per worker) caps it. So plan.md:204 ("the per-IP and failed-auth buckets cap their volume") does not hold for the failed-auth bucket. Exploiting this needs a valid key of some app, so it is not a blocker.
- **Fix:** check the bucket before authenticating. Add a non-counting `RateLimiter.blocked(scope, key, limit) -> int | None` that returns retry-after when `count >= limit` in a live window. In `RequireAppCredential.__call__`, when `failed_auth_scope` is set, call it first and raise `RateLimitedError` if it is set. Keep the existing post-failure `hit` for counting. Add a test that the over-limit attempt writes no audit row: wrong-scope key with limit=1, second call → 429, `AuditLog` count unchanged.

💭 **The runtime `last_used_at` touch runs before `SET LOCAL lock_timeout`.** `app/modules/app_credentials/service.py:199` vs `app/modules/usage/meter_service.py:139,209,242`. The throttled UPDATE on the credential row can wait on a revoke that is in flight, with no timeout. The wait is short, because revoke holds the row only for its own transaction. Optional fix: set the lock timeout in the usage deps before `authenticate`, or inside `touch_last_used_throttled`.

💭 **Settle can return the wrong 422 when the counter row is missing.** `app/modules/usage/meter_service.py:225-234`. `adjust_counter` returns False both for "would exceed MAX_LIMIT" and for "counter row missing". The second cannot happen today: a consume always creates the row, and a user delete cascades to both tables. If retention or a counter reset ever deletes counters, a settle would get 422 `out_of_range` instead of a clear error. Note it for ADM-045.

💭 **A replay of a consume after the user was removed from the app, or after the metric was archived, returns 403/404 instead of the first result.** Membership and metric status are checked before the idempotency lookup (`meter_service.py:140-141`). This matches "membership live" and plan Q3, and fails closed. Document it for consumers if it is not already in `docs/consumers.md`.

**Verified, no finding:**
- **Scope set:** `RequireAppCredential` refuses `lock=True` with runtime scopes and refuses runtime/CI mixes (`deps.py:40-42`). `authenticate` keeps the check order: 401s → disabled → mismatch → scope (`service.py:169-195`). Membership of the set is checked against `row.scope`, and the runtime path is decided from the verified `row.scope`. The read route accepts exactly {usage:read, usage:write}; the three POSTs accept only usage:write. The registry in `tests/test_route_auth_classes.py` matches.
- **Issuance:** the allow-list refuses every non-`releases` scope for `admin` (`service.py:123`). `limits:write` gets 409 `scope_not_available` (`service.py:127`).
- **Rate-limit order:** per-IP `rate_limit("usage")` (route dependency) → auth with failure hook → per-key `usage_key` keyed on `key_id`, shared across the 4 routes → `read_json_body`. The failure bucket uses the same trusted-proxy `client_ip` keying. A missing header reaches `authenticate` (`auto_error=False`) and is counted.
- **Target user:** one 403 `usage_user_denied` covers unknown, non-member, disabled and expired-only users. It is checked live in the request transaction (`rbac/repository.py:894`, OQ5 predicate). On read, the metric 404 is app-scoped, so it is no user oracle.
- **Idempotency:**
  - The lookup is scoped by `(app_id, idempotency_key)`.
  - The fingerprint compares user, metric and amount; the operation is implicit because settle and refund cross-check user/metric on the consume event.
  - Key format `^[!-~]{1,128}$` is enforced in both the schema and a DB CHECK.
  - The key is never logged: usage logs carry only key_id, user_id, metric and amount.
  - A replay never re-checks the limit.
- **Atomic consume:** `INSERT … SELECT WHERE amount <= limit ON CONFLICT DO UPDATE … WHERE used+amount <= limit` re-evaluates the WHERE on the locked latest row version under READ COMMITTED, so it cannot overshoot. There is no `lock_app` and no `FOR UPDATE` on metric or app rows. `SET LOCAL lock_timeout '2s'`; a timeout surfaces as 500, which the plan accepts. Lock order is event then counter everywhere.
- **State machine:**
  - Settle once: a repeat with the same amount replays, a different amount gets 409; settle after refund gets 409.
  - Refund is full-only, at most once, and idempotent.
  - The 24 h window is measured from `created_at` on the DB clock.
  - The charge goes to the stored `period/period_start`.
  - `GREATEST(0,…)` keeps `used` at zero or above.
  - `used + delta <= MAX_LIMIT` gives 422, never a CHECK 500.
  - A settle above the estimate may exceed the limit.
- **Input and output:**
  - Amount: strict int, > 0, at most 2^31-1, plus a config max (422 `too_large`, no value echo).
  - `extra="forbid"`.
  - Body cap, 411, 413 and 415 come from `read_json_body`; its 422 strips input and context.
  - `Cache-Control: no-store` is set.
  - Responses contain no email or key.
  - No audit row is written on successful use.
- **Migration:** widens the CHECK. Downgrade drops events, then counters, deletes keys with the new scopes, then restores the USR-29 CHECK. No FK points at `app_credentials`, so the delete is safe.

**Follow-ups (out of scope)**
- ADM-045: retention or purge of `usage_events` must not orphan counters that a later settle relies on (see the third note).
- Consider a dedicated 503 for `lock_timeout` (SQLSTATE 55P03) on the usage routes, so clients can tell contention from a real 5xx. Today the fail-open grace treats both as 5xx, which is acceptable.

**Files:** `app/modules/app_credentials/deps.py`, `app/modules/app_credentials/service.py`, `app/core/rate_limit.py`, `app/modules/usage/meter_service.py`, `app/modules/usage/meter_repository.py`, `app/modules/usage/app_router.py`, `migrations/versions/` (c91e4b7a3d58).
