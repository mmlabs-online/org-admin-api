## Attempt 1
Verdict: PASS

Reviewed 2026-11-22, commit 8c14fb8 vs origin/main (ADM-038 included); batch bookkeeping under `work/` ignored.

### Checks (run by reviewer)
- `make check`: exit 2, but only from `ruff check .` on `.github/scripts/risk_tier.py` (13 known kit errors). `git diff origin/main...HEAD -- .github/scripts/risk_tier.py` is empty, so that file is unchanged.
- `ruff check` + `ruff format --check` on the 32 changed .py files: "All checks passed!" / "32 files already formatted". `ruff check . --exclude .github`: all passed.
- `mypy app`: "Success: no issues found in 209 source files".
- `uv run alembic check`: "No new upgrade operations detected."
- Full `pytest -q`: **4044 passed, 1 warning in 705.22s**. This matches impl-log (4044 passed).
- Committed-row leakage: `tests/test_usage_concurrency.py` alone gave 3 passed. Concurrency + the API/DB usage files gave 121 passed. The API/DB files + concurrency gave 121 passed. Concurrency + both migration tests + API files gave 126 passed. Afterwards `org_admin_test` held 0 rows in `usage_events`, `usage_counters` and `usage_metrics`, and 0 `race-%` apps.
- Migration round trip on the dev DB: `alembic downgrade -1` (c91e4b7a3d58 -> b7d3f1a9c542) then `upgrade head` both succeeded, with a single head. `test_usage_ledger_migration.py` and `test_usage_scopes_round_trip` also pass.

### Acceptance criteria
- [x] Consume within / at / over limit (409) — `test_usage_consume_api.py` `test_consume_within_limit`, `test_consume_last_unit_then_409`, `test_consume_over_limit_409_nothing_consumed`, `test_limit_zero_refuses` (all passing). The atomic check is one conditional upsert, `meter_repository.py:39-43` (`INSERT … WHERE amount <= limit … ON CONFLICT DO UPDATE … WHERE used + amount <= limit`).
- [x] Parallel consumes never exceed the limit — `test_usage_concurrency.py` `test_parallel_consumes_on_last_unit` (exactly 1 accepted, used==5), `test_parallel_first_consumes_fresh_counter` (3 of 10), `test_parallel_same_key_charges_once`. All pass alone and in both orders.
- [x] Idempotent replay returns the original result; a different payload with the same key gives 409 — `meter_service.py:155-163` (fingerprint user/metric/amount, limit not re-checked). Tests: `test_replay_returns_first_result`, `test_replay_after_limit_reached_still_200`, `test_replay_changed_{amount,user,metric}_409`, `test_settle_changed_amount_409`, `test_settle_refund_mismatched_user_or_metric_409`.
- [x] Refund restores the amount, never below 0; settle adjusts the estimate — `meter_repository.py:49` `GREATEST(0, used + delta)`. Refund credits only the current charge (`meter_service.py:250-256`). Tests: `test_refund_restores_amount`, `test_refund_after_settle_credits_settled_amount`, `test_double_refund_credits_once`, `test_refund_never_below_zero`, `test_settle_up_charges_delta_past_limit_200`, `test_settle_down_credits_delta`, `test_settle_charged_to_original_period`.
- [x] Wrong scope 403, other app 403, admin JWT 401 — `test_usage_auth.py` `test_wrong_scope_403`, `test_other_app_key_403`, `test_admin_jwt_401`. Registry: `tests/test_route_auth_classes.py:63-79`, including `lock is False` for the 4 routes.
- [x] Scope bullets — disabled/non-member user gets one 403 `usage_user_denied` (`test_user_denied_one_code`). Unknown/archived metric gets 404. Per-key, per-IP and failed-auth 429 (`test_per_key_429`, `test_per_ip_429`, `test_failed_auth_ip_429`). No audit row on success (`test_audit_coverage.py:1139`). The scope CHECK is extended in the migration.

ADR-0022 and the approved defaults:
- Idempotency is unique on (app, key) (migration `uq_usage_events_app_id_idempotency_key`). The key charset is 0x21–0x7e, 1–128 chars, enforced in both the schema and a DB CHECK (Q8).
- State machine: consume, then at most one settle (`meter_service.py:214-221`; a settle after a refund gives 409 `usage_event_state_conflict`), then at most one refund (a repeated refund replays). Both are charged to the stored period. The 24 h adjust window is checked only on the first transition, so replays still return 200 (Q4).
- Fail-closed policy for 401/403/409/422/429 is documented at `docs/consumers.md:325`.
- No audit row per use. Only the denied-only actions are added (`audit/actions.py`).
- `limits:write` is refused at issuance (`app_credentials/service.py:127`). Refused consumes are not stored (`meter_service.py:173` plus the rollback in `get_session`).
- Config defaults 1200/20/600/1,000,000/2048/5/86400 are at `config.py:132-141`, with matching keys in `.env.example`.
- No `lock_app` or `FOR UPDATE` on metric or app rows. `SET LOCAL lock_timeout` is at `meter_repository.py:79`.

### Issues
None.

### Rules (CLAUDE.md)
No violations found. R1: router is HTTP only, the service raises `AppError` subclasses, the repository holds the SQL. R2: hand-reviewed migration, working `downgrade()` (`migration:153-162`), models registered. R3: CLAUDE.md is updated and the registry is exact. R5: no hard-coded values. R6: no new status codes. R7: every file is in the plan. R11: idempotency keys and emails are never logged, and 422 errors do not echo inputs (`releases/body.py:79`).

### Blockers from review agents
None.

### Nits (do not block)
- `rbac/repository.py:894` duplicates the OQ5 predicate of `member_counts` instead of sharing a private expression as plan step 7 said. The predicate is identical.
- `tests/test_usage_concurrency.py`: `_setup(prefill=…)` runs outside `try/finally`, so a failing prefill would leave committed rows behind.
- `meter_service.py:150`: the `assert` relies on the conflicting row being visible. A same-key insert that gives up after the lock timeout surfaces as a 500, which matches the plan's assumption.

### Follow-ups (out of scope)
- Move `read_json_body`/`inline_schema` from `releases/body.py` to `app/core`.
- ADM-045 purge must keep events for at least 90 days. ADM-044 enables `limits:write`.

### Ming verification after merge
- Issue a `usage:write` key for one app. Call `POST /api/v1/apps/{key}/usage/consume` and confirm 200, then 409 at the limit. Observe p95 latency against the < 100 ms target.
- Confirm `limits:write` issuance is refused in the admin UI until ADM-044.

### Review agents (attempt 1)
- appsec-engineer: `Verdict: PASS` — no 🔴; 1 🟡 (failed-auth bucket checked after authenticate, so over-limit attempts still write denied audit rows), 3 💭 (see `security-review.md`). No blockers from review agents.
