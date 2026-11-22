Status: READY

> Blocked only for owner choices (High-path ticket, stops at PLAN anyway). Every question below has a
> default; "all defaults" makes this plan READY with no other edit.

# Plan — ADM-042: Usage consume, refund and current-usage read for app backends

## Scope summary
Spec `docs/spec-v1.md` §5.14 (line 284, OQ66):
> "The app backend calls an **atomic consume** (metric, amount, idempotency key; `usage:write`-scoped key) **before** the AI action; over the limit → **409 `usage_limit_exceeded`**, nothing consumed; a retried idempotency key returns the first result. Duration metrics consume an estimate first and settle the actual amount after. A failed AI action is **refunded** by the app (same idempotency key). [...] Disabled users and non-members of the app are refused at consume (**403**). [...] The extra call per AI action is accepted; its latency target is set at PLAN."

§5.14 line 283 (OQ65): "the app backend reads the user's limits and usage with a `usage:read`-scoped key". Line 276 (OQ60): "the app **and** this API enforce limits". Line 287 (OQ69): "Per-use events kept **90 days** (purge job); per-period totals per user + app + metric kept indefinitely in v1." Line 310: "Usage reports are high-volume and are not audit entries".

ADR-0022 (Accepted):
- §1: "`usage:read`: the per-user limits / used / remaining read. `usage:write`: consume, settle and refund, plus the same per-user current-usage read [...] All three are refused for the `admin` app at issuance."
- §3: "Runtime scopes do not take `lock_app`".
- §5: "`idempotency_key` body field: at most 128 printable ASCII characters, unique per (app, idempotency_key) [...] Each key is bound to a fingerprint (user, metric, operation, amount). Same fingerprint replays the first result; a different payload gives 409 `idempotency_key_reused`. State machine per key: consume, at most one settle, at most one refund. Settle and refund are idempotent and charged to the period of the original consume. A refund never credits more than was charged. A settle above the estimate is accepted even past the limit [...] never answered with 409. Amounts are positive integers with a per-call maximum (config, value at ADM-042); zero or negative: 422. Idempotency records are kept at least as long as events (90 days)."
- §6: "An app may apply the grace only on transport errors, timeouts and 5xx, never on 401, 403, 409, 422 or 429." (ADM-042 documents this; the grace itself is ADM-046.)
- §7: "Three buckets: a generous per-IP bucket before auth, a stricter per-IP bucket for failed auth, and a per-key bucket after auth. Values live in `app/core/config.py` (R5) and are set at ADM-042. [...] The consume latency target [...] is also set at ADM-042."
- §9: "`usage:read` returns only that app's metrics for a member of that app, by user id, with no email (R11). Consume and refund are not audit entries."
- §10: "ADM-042 extends the `app_credentials.scope` CHECK [...] with the three scopes and adds a uniqueness constraint on (app_id, idempotency_key)."
- Consequences: "Left to implementing tickets: exact routes, [...] numeric rate limits, amount maximum, latency target".

ADR-0024 §1 "An archived metric refuses consume (ADM-042)"; §4 "ADM-042 keys totals by `(metric, user, period, period_start)`, so old buckets are not reused". ADR-0025 §1 "ADM-042 recreates the CHECK from this list". Spec OQ5 (line 111): a member is "a distinct user with at least one non-expired role assignment on the app (app scope or any org); only users with status `active` count (unverified email still counts)".

`work/decisions.md`: nothing on usage.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1a | Consume within limit, at limit, over limit (409) | `tests/test_usage_consume_api.py` › `test_consume_within_limit`, `test_consume_last_unit_then_409`, `test_consume_over_limit_409_nothing_consumed`, `test_consume_amount_above_limit_on_fresh_counter_409`, `test_limit_zero_refuses` |
| 1b | Parallel consumes never exceed the limit | `tests/test_usage_concurrency.py` › `test_parallel_consumes_on_last_unit`, `test_parallel_first_consumes_fresh_counter`, `test_parallel_same_key_charges_once` |
| 2a | Idempotent replay returns the original result | `test_usage_consume_api.py` › `test_replay_returns_first_result`, `test_replay_after_limit_reached_still_200` |
| 2b | Different payload, same key → 409 | `test_replay_changed_amount_409`, `test_replay_changed_user_409`, `test_replay_changed_metric_409`; `test_usage_settle_refund_api.py` › `test_settle_changed_amount_409`, `test_settle_refund_mismatched_user_or_metric_409` |
| 3a | Refund restores the amount, never below 0 | `test_usage_settle_refund_api.py` › `test_refund_restores_amount`, `test_refund_after_settle_credits_settled_amount`, `test_double_refund_credits_once`, `test_refund_never_below_zero` |
| 3b | Settle adjusts an estimate | `test_settle_up_charges_delta_past_limit_200`, `test_settle_down_credits_delta`, `test_settle_replay_200`, `test_settle_after_refund_409`, `test_settle_charged_to_original_period` |
| 4 | Wrong scope 403, other app 403, admin JWT 401 | `tests/test_usage_auth.py` › `test_wrong_scope_403` (manifest, releases, org-members:read keys on all 4 routes; `usage:read` on the 3 POSTs), `test_other_app_key_403`, `test_admin_jwt_401`, `test_usage_write_key_reads`; registry sweep in `tests/test_route_auth_classes.py` (`test_admin_jwt_on_app_credential_route_401`, `test_app_credential_route_no_cors`, `test_app_credential_allow_list_is_exact`) |
| scope | Disabled user / non-member 403; unknown/archived metric 404; per-key rate limit; no CORS; no per-use audit row | `test_usage_consume_api.py` › `test_user_denied_one_code` (unknown, non-member, disabled, expired assignment), `test_unknown_metric_404`, `test_archived_metric_404`, `test_other_apps_metric_404`; `test_usage_auth.py` › `test_per_key_429`, `test_per_ip_429`, `test_failed_auth_ip_429`; `test_audit_coverage.py` › `test_usage_routes_write_no_audit_row_on_success` |
| scope | Scope CHECK extended (migration) | `tests/test_app_credential_scope_migration.py` › `test_usage_scopes_round_trip`; `uv run alembic check` clean |
| all | | `make check` green |

## Ming verification after merge
- After deploy: issue a `usage:write` key for one app (e.g. Ricotdin) in staging/production, call `POST /api/v1/apps/{key}/usage/consume` from that backend and confirm 200, then 409 at the limit. Observe consume latency in the app's own logs against the p95 < 100 ms target (Q10).
- Confirm `limits:write` issuance is refused in the admin UI until ADM-044 (Q11).

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `migrations/versions/20261122_<rev>_adm042_usage_counters_events.py` | new: `usage_counters`, `usage_events`, scope CHECK widened; downgrade | R2; ADR-0022 §10 | **High** |
| `app/db/models.py` | import `UsageCounter`, `UsageEvent` | R2 registry | **High** |
| `app/modules/usage/models.py` | add `UsageCounter`, `UsageEvent`, `UsageEventState`, `IDEMPOTENCY_KEY_RE` | tables | no |
| `app/modules/usage/metering.py` | new, pure: `remaining`, `charged`, `replay_or_conflict`, `settle_delta`, `window_open`, `counter_period` | rule 6, pure logic first | no |
| `app/modules/usage/meter_repository.py` | new: event insert/lookup, conditional counter upsert, settle/refund counter updates, read query, `set_lock_timeout`, `db_now` | R1 repository | no |
| `app/modules/usage/meter_service.py` | new: `UsageMeterService.read/consume/settle/refund` | R1 service | no |
| `app/modules/usage/meter_schemas.py` | new: `UsageConsume`, `UsageSettle`, `UsageRefund` (all `extra="forbid"`), `UsageEventResult`, `UserUsageRead`, `UserMetricUsage` | API contract | no |
| `app/modules/usage/app_router.py` | new: the 4 routes (copy `organizations/app_router.py:21-68`; body via `read_json_body`, `openapi_extra` via `inline_schema` as the release route does) | R1 router | no |
| `app/modules/usage/errors.py` | add 7 error classes (see API contract) | R6 codes | no |
| `app/api/router.py` | include `usage_app_router` | mount | no |
| `app/modules/app_credentials/models.py` | enum + `RUNTIME_SCOPES` + CHECK text (`:17-27`, `:37-40`) | ADR-0022 §1/§10 | **High** |
| `app/modules/app_credentials/deps.py` | `RequireAppCredential` takes one scope or a frozenset; `scopes` attr; optional `failed_auth_scope`; new `UsageReadCredentialDep`, `UsageWriteCredentialDep` | scope set; failed-auth bucket | **High** |
| `app/modules/app_credentials/service.py` | `authenticate` accepts a scope set (`:153-200`); `issue` refuses `limits:write` (next to `:123`) | scope set; Q11 | **High** |
| `app/modules/rbac/repository.py` | add `is_active_app_member(user_id, app_id)` next to `member_counts` (`:894`), same OQ5 predicate | target-user check | **High** |
| `app/modules/audit/actions.py` | add denied-only actions `usage.read`, `usage.consumed`, `usage.settled`, `usage.refunded` | `denied_action` of the deps (wrong scope / other app rows) | no |
| `app/core/rate_limit.py` | scope `usage` (per IP), subject scope `usage_key`, new `FailureScope = Literal["usage_auth_failed"]` + `enforce_failure_limit(request, scope)` | ADR-0022 §7 | **High** |
| `app/core/config.py` | 10 settings (see Approach step 2) | R5 | **High** |
| `.env.example` | the 10 keys, no values | R5 | **High** |
| `CLAUDE.md` | R3 class 4 (a): concrete usage routes; "the usage read also accepts `usage:write`"; R9 exceptions | R3/R9 text | no |
| `docs/identity-architecture.md` | decision-log row D36 | record of the numbers/choices | no |
| `docs/consumers.md` | new section "Metering AI usage from your backend (ADM-042, ADR-0022)" before `## Email verification` (`:284`) | consumer contract | no |
| `tests/usage_factories.py` | new: `make_metric`, `make_member`, `usage_key` helpers | tests | no |
| `tests/test_usage_metering.py` | new, pure unit tests | rule 6 | no |
| `tests/test_usage_consume_api.py` | new | AC1, AC2 | no |
| `tests/test_usage_settle_refund_api.py` | new | AC2, AC3 | no |
| `tests/test_usage_read_api.py` | new | read route | no |
| `tests/test_usage_auth.py` | new | AC4, rate limits | no |
| `tests/test_usage_concurrency.py` | new (committed data + cleanup, pattern `tests/test_releases_write_concurrency.py`) | AC1b | no |
| `tests/test_usage_ledger_db.py` | new: CHECKs, unique, composite FK | schema | no |
| `tests/test_usage_ledger_migration.py` | new: up/down round trip | R2 | no |
| `tests/test_app_credential_scope_migration.py` | `:98` uses `'usage:read'` as the "unknown" scope → change to `'bogus:scope'`; add `test_usage_scopes_round_trip` | CHECK widened | no |
| `tests/test_app_credentials_api.py` | issue `usage:read`/`usage:write` 201; `limits:write` 409; admin app refuses all three | ADR-0022 §1, Q11 | no |
| `tests/test_app_credential_auth.py` | scope-set acceptance; `RequireAppCredential` refuses `lock=True` with any runtime scope and mixed sets (extend `:418-424`) | scope model | no |
| `tests/test_route_auth_classes.py` | `APP_CREDENTIAL` → route → frozenset of scopes (`:63-72`); `:306` compares sets; 4 new routes; `lock is False` for them | registry (R3) | no |
| `tests/test_audit_coverage.py` | 4 entries in `APP_CREDENTIAL_AUDITED` (`:984-991`); success writes no row | R4 classification | no |
| `tests/test_rate_limit.py` | `enforce_failure_limit` unit test | new hook | no |

No ADR file (Q13). `tests/test_config.py` checks `.env.example` against `Settings`; no edit expected there.

## Data model / migration
One migration, `down_revision = "a4c81e6d27f3"` today; **re-pointed at build onto ADM-038's revision** (ADM-038 builds and merges first in this batch and adds a migration). Model CHECK texts must match the migration text exactly (Alembic does not compare CHECKs).

**`usage_counters`** (per-period totals; never purged, OQ69)
| Column | Type | Null | Default / constraint |
|---|---|---|---|
| `id` | uuid | no | PK (`UUIDPkMixin`) |
| `app_id` | uuid | no | composite FK `(metric_id, app_id)` → `usage_metrics(id, app_id)` `ON DELETE RESTRICT` (`fk_usage_counters_metric_id_app_id_usage_metrics`) |
| `user_id` | uuid | no | FK `users.id` `ON DELETE CASCADE` |
| `metric_id` | uuid | no | (composite FK above) |
| `period` | varchar(10) | no | `ck_usage_counters_period_valid`: `period IN ('day', 'week', 'month')` |
| `period_start` | timestamptz | no | from `periods.py` only |
| `used` | bigint | no | `0`; `ck_usage_counters_used_range`: `used BETWEEN 0 AND 9007199254740991` (`MAX_LIMIT`) |
| `created_at`, `updated_at` | timestamptz | no | `TimestampMixin` |
- `uq_usage_counters_user_id_metric_id_period_period_start` UNIQUE `(user_id, metric_id, period, period_start)`: the upsert target; the period kind is part of the key (ADR-0024 §4). It leads with `user_id`, so it also serves the user FK cascade.

**`usage_events`** (ledger + idempotency record; 90-day purge is ADM-045)
| Column | Type | Null | Default / constraint |
|---|---|---|---|
| `id` | uuid | no | PK |
| `app_id` | uuid | no | composite FK `(metric_id, app_id)` → `usage_metrics(id, app_id)` RESTRICT |
| `user_id` | uuid | no | FK `users.id` CASCADE |
| `metric_id` | uuid | no | |
| `idempotency_key` | varchar(128) | no | `ck_usage_events_idempotency_key_format`: `idempotency_key ~ '^[!-~]{1,128}$'` (0x21–0x7e, Q8) |
| `period` | varchar(10) | no | CHECK as above: the **charged** period kind |
| `period_start` | timestamptz | no | the charged period; settle/refund never recompute it |
| `amount` | bigint | no | `ck_usage_events_amount_positive`: `amount > 0` (original consume amount; fingerprint part) |
| `settled_amount` | bigint | yes | `ck_usage_events_settled_amount_positive`: `settled_amount IS NULL OR settled_amount > 0` |
| `state` | varchar(10) | no | `'consumed'`; `ck_usage_events_state_valid`: `state IN ('consumed', 'settled', 'refunded')` |
| `created_at` | timestamptz | no | `now()` |
| `settled_at`, `refunded_at` | timestamptz | yes | |
- `ck_usage_events_state_consistent`: `(state = 'consumed' AND settled_at IS NULL AND settled_amount IS NULL AND refunded_at IS NULL) OR (state = 'settled' AND settled_at IS NOT NULL AND settled_amount IS NOT NULL AND refunded_at IS NULL) OR (state = 'refunded' AND refunded_at IS NOT NULL AND (settled_at IS NULL) = (settled_amount IS NULL))`.
- `uq_usage_events_app_id_idempotency_key` UNIQUE `(app_id, idempotency_key)` (ADR-0022 §10; the hot lookup).
- `ix_usage_events_created_at` `(created_at)` (retention purge, OQ69).
- `ix_usage_events_user_id_metric_id_created_at` `(user_id, metric_id, created_at)` (user FK cascade; staff history in ADM-043).
- The fingerprint is the stored columns `(user_id, metric_id, amount)` (operation = consume), plus `settled_amount` for settle and `state` for refund. No hash column.

**`app_credentials`**: drop/recreate `ck_app_credentials_scope_valid` as `scope IN ('manifest', 'releases', 'org-members:read', 'usage:read', 'usage:write', 'limits:write')` (pattern: `a4c81e6d27f3:27-32`).

**Downgrade**: drop `usage_events`, then `usage_counters`; `DELETE FROM app_credentials WHERE scope IN ('usage:read', 'usage:write', 'limits:write')` (revocation by removal); recreate the USR-29 CHECK `scope IN ('manifest', 'releases', 'org-members:read')`.

The existing user/app membership check uses `uq_role_assignments_user_role_org` (leads with `user_id`) joined to `roles` by PK: no new index on `role_assignments`.

## API contract
All four: R3 class 4 (a) app credential, `Authorization: Bearer mmak_...`, no CORS, `Cache-Control: no-store`, per-IP `rate_limit("usage")` → auth (failed-auth hook) → `enforce_subject_limit(request, "usage_key", caller.key_id)` → body. POST bodies are read with `read_json_body(request, Model, cap=settings.usage_max_bytes, timeout_s=settings.usage_read_timeout_seconds)` after auth (no Pydantic body parameter). Metric is addressed by its immutable `key`, user by UUID; no email anywhere.

| Method | Path | Request | Response | Codes | Scope (no `admin:*` permission) |
|---|---|---|---|---|---|
| GET | `/api/v1/apps/{key}/usage/users/{user_id}` | query `metric` (optional, metric-key pattern) | 200 `UserUsageRead` `{user_id, metrics: [UserMetricUsage]}`; `UserMetricUsage` = `{metric, display_name, unit, period, period_start, period_end, limit, used, remaining}`, active metrics only, ordered by `metric` | 200, 401, 403 (`app_credential_*`, `app_disabled`, `usage_user_denied`), 404 `usage_metric_not_found` (filter: unknown/archived), 422, 429 | `usage:read` **or** `usage:write` |
| POST | `/api/v1/apps/{key}/usage/consume` | `UsageConsume` `{user_id, metric, amount, idempotency_key}` | 200 `UsageEventResult` (first call and replay) | 200, 401, 403 (+`usage_user_denied`), 404 `usage_metric_not_found`, 409 `usage_limit_exceeded` / `idempotency_key_reused`, 411, 413, 415, 422, 429 | `usage:write` |
| POST | `/api/v1/apps/{key}/usage/settle` | `UsageSettle` `{user_id, metric, idempotency_key, amount}` (actual amount) | 200 `UsageEventResult` | 200, 401, 403, 404 `usage_event_not_found`, 409 `idempotency_key_reused` / `usage_event_state_conflict` / `usage_adjust_window_closed`, 411, 413, 415, 422, 429 | `usage:write` |
| POST | `/api/v1/apps/{key}/usage/refund` | `UsageRefund` `{user_id, metric, idempotency_key}` (no amount: full refund) | 200 `UsageEventResult` | as settle | `usage:write` |

`UsageEventResult` = `{user_id, metric, amount, state, charged, period, period_start, period_end, limit, used, remaining, replayed}`: `amount` = original consume amount; `state` = `consumed|settled|refunded` (current); `charged` = current net charge (`amount`, `settled_amount`, or 0); `limit`/`used`/`remaining` = live values of the event's charged period (`remaining = max(0, limit - used)`); on replay these are current values, not a snapshot (documented); `replayed` = true when nothing changed.

Field rules: `amount` int, 1 ≤ amount ≤ `settings.usage_max_amount_per_call` (schema `gt=0, le=2**31-1`; service checks the config value → 422 `{field: amount, reason: too_large}`); `idempotency_key` pattern `^[\x21-\x7e]{1,128}$`; `metric` pattern `METRIC_KEY_RE`; `extra="forbid"` (so `on_behalf_of`, `occurred_at` → 422).

Error classes (`app/modules/usage/errors.py`): `UsageMetricNotFoundError` (404 `usage_metric_not_found`), `UsageEventNotFoundError` (404 `usage_event_not_found`), `UsageUserDeniedError` (403 `usage_user_denied`), `UsageLimitExceededError` (409 `usage_limit_exceeded`, `details=[{"field": "amount", "reason": "over_limit", "limit": L, "used": U, "remaining": R}]`), `IdempotencyKeyReusedError` (409 `idempotency_key_reused`), `UsageEventStateError` (409 `usage_event_state_conflict`), `UsageAdjustWindowClosedError` (409 `usage_adjust_window_closed`). No new status codes (R6).

State machine (per `(app, idempotency_key)`):
- consume: absent → `consumed` (or 409 limit, nothing stored). Existing + same `(user, metric, amount)` → replay 200 (any state, limit **not** re-checked); different → 409 `idempotency_key_reused`.
- settle: absent → 404. User/metric mismatch → 409 `idempotency_key_reused`. `settled` + same amount → replay; `settled` + other amount → 409 `idempotency_key_reused`; `refunded` → 409 `usage_event_state_conflict`; `consumed` past the window → 409 `usage_adjust_window_closed`; else delta = new − amount applied to the original counter (`GREATEST(0, used + delta)`, no limit guard; above `MAX_LIMIT` → 422 `{field: amount, reason: out_of_range}`), state `settled`.
- refund: absent → 404; mismatch → 409 `idempotency_key_reused`; `refunded` → replay 200 (no credit); past the window → 409 `usage_adjust_window_closed`; else credit `charged` to the original counter with `GREATEST(0, used - charged)`, state `refunded`.
- Settle and refund do not re-check the user (a refund after disable must work) or the metric status (refund on an archived metric works).

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `test_usage_metering.py` › `test_remaining_clamps_at_zero`, `test_charged_per_state`, `test_replay_or_conflict_{same,amount,user,metric}`, `test_settle_delta_up_down`, `test_window_open_boundaries` (exactly 24 h = closed), `test_counter_period_uses_metric_kind_and_db_now` (week starting on the 1st and month → different keys, ADR-0024 §4) | pure logic, Domain focus |
| `test_usage_consume_api.py` › `test_consume_within_limit`, `test_consume_last_unit_then_409`, `test_consume_over_limit_409_nothing_consumed` (counter unchanged, no event row), `test_consume_amount_above_limit_on_fresh_counter_409`, `test_limit_zero_refuses`, `test_override_raises_limit`, `test_expired_override_ignored`, `test_refused_consume_retry_succeeds_after_grant` | AC1 |
| same › `test_replay_returns_first_result` (`replayed: true`, counter charged once), `test_replay_after_limit_reached_still_200`, `test_replay_changed_amount_409`, `test_replay_changed_user_409`, `test_replay_changed_metric_409`, `test_same_key_two_apps_independent` | AC2 |
| same › `test_user_denied_one_code` (unknown id, non-member, disabled, only expired assignment → identical 403 body), `test_org_scoped_member_allowed`, `test_unverified_member_allowed`, `test_unknown_metric_404`, `test_archived_metric_404`, `test_other_apps_metric_404` | scope |
| same › `test_amount_zero_negative_422`, `test_amount_over_config_max_422`, `test_idempotency_key_space_or_129_chars_422`, `test_on_behalf_of_field_422`, `test_body_411_413_415`, `test_period_change_new_bucket`, `test_cache_control_no_store`, `test_idempotency_key_never_logged` (caplog), `test_lock_timeout_set_local` (SHOW lock_timeout inside the txn via repo spy) | validation, R11 |
| `test_usage_settle_refund_api.py` › `test_settle_up_charges_delta_past_limit_200`, `test_settle_down_credits_delta`, `test_settle_replay_200`, `test_settle_changed_amount_409`, `test_settle_after_refund_409`, `test_settle_unknown_key_404`, `test_settle_other_apps_key_404`, `test_settle_refund_mismatched_user_or_metric_409`, `test_settle_after_window_409`, `test_settle_charged_to_original_period` (metric period changed between consume and settle), `test_settle_above_max_limit_422` | AC3, AC2 |
| same › `test_refund_restores_amount`, `test_refund_after_settle_credits_settled_amount`, `test_double_refund_credits_once`, `test_refund_never_below_zero` (counter lowered by SQL first), `test_refund_disabled_user_works`, `test_refund_archived_metric_works`, `test_refund_after_window_409`, `test_refund_replay_after_window_200`, `test_consume_replay_after_refund_200_state_refunded`, `test_refund_writes_info_log_without_key` | AC3 |
| `test_usage_read_api.py` › `test_read_all_active_metrics`, `test_read_archived_metric_omitted`, `test_read_metric_filter`, `test_read_unknown_metric_404`, `test_read_no_counter_used_zero`, `test_read_override_limit`, `test_read_remaining_clamped_after_overage`, `test_read_non_member_403`, `test_read_invalid_user_id_422` | read route |
| `test_usage_auth.py` › `test_wrong_scope_403`, `test_other_app_key_403`, `test_admin_jwt_401`, `test_disabled_app_403`, `test_usage_write_key_reads`, `test_usage_read_key_cannot_consume_403`, `test_per_key_429`, `test_per_ip_429`, `test_failed_auth_ip_429` (21st failure → 429, success path unaffected), `test_wrong_scope_writes_denied_row` | AC4, ADR-0022 §7 |
| `test_usage_concurrency.py` › `test_parallel_consumes_on_last_unit` (limit 5, used 4, 8 parallel consumes of 1 → exactly 1 accepted, `used == 5`), `test_parallel_first_consumes_fresh_counter` (limit 3, 10 parallel, no row yet → 3 accepted), `test_parallel_same_key_charges_once` | AC1b |
| `test_usage_ledger_db.py` › `test_used_range_check`, `test_amount_positive_check`, `test_idempotency_key_format_check`, `test_state_consistency_check`, `test_unique_app_key`, `test_composite_fk_rejects_other_apps_metric`, `test_counter_unique_includes_period_kind` | schema |
| `test_usage_ledger_migration.py` › `test_tables_round_trip` (down to previous revision drops both tables; up recreates) | R2 |
| `test_app_credential_scope_migration.py` › `test_usage_scopes_round_trip` (insert each new scope; downgrade deletes them, old CHECK rejects; upgrade accepts); fix `:98` literal | ADR-0022 §10 |
| `test_app_credentials_api.py` › `test_issue_usage_read_and_write`, `test_issue_limits_write_refused_409`, `test_admin_app_refuses_usage_scopes` | Q11, ADR-0022 §1 |
| `test_app_credential_auth.py` › `test_scope_set_accepts_any_member`, `test_scope_set_wrong_scope_403`, `test_require_app_credential_refuses_lock_for_runtime_set`, `test_require_app_credential_refuses_mixed_set` | scope model |
| `test_route_auth_classes.py` › existing tests, updated registry | R3 |
| `test_audit_coverage.py` › `test_app_credential_routes_are_classified` (updated), `test_usage_routes_write_no_audit_row_on_success` | R4 / ADR-0022 §9 |
| `test_rate_limit.py` › `test_enforce_failure_limit` | new hook |

## Approach
1. **Pure module** `metering.py` + tests first (rule 6).
2. **Config (R5)**, `app/core/config.py` after `:127`, mirrored in `.env.example` without values: `usage_rate_limit=1200`, `usage_rate_window_seconds=60`, `usage_auth_failed_rate_limit=20`, `usage_auth_failed_rate_window_seconds=60`, `usage_key_rate_limit=600`, `usage_key_rate_window_seconds=60`, `usage_max_amount_per_call=1_000_000` (`ge=1, le=2**31-1`), `usage_max_bytes=2048` (`ge=512`), `usage_read_timeout_seconds=5`, `usage_adjust_window_seconds=86400` (`ge=60`). Values per Q7/Q9/Q4.
3. **Rate limits** (`app/core/rate_limit.py`): add `"usage"` to `Scope`/`_LIMITS`, `"usage_key"` to `SubjectScope`/`_SUBJECT_LIMITS`; add `FailureScope = Literal["usage_auth_failed"]` and `enforce_failure_limit(request, scope)` which hits the per-IP bucket and raises `RateLimitedError` when over (no Protocol change). In-process, N workers × setting (ADR-0002).
4. **Scopes** (`app_credentials/models.py`): enum `USAGE_READ`, `USAGE_WRITE`, `LIMITS_WRITE`; `RUNTIME_SCOPES` gets all four runtime scopes; CHECK text; remove the ADM-042 TODO comments.
5. **Auth** (`service.py`, `deps.py`): `authenticate(..., scope: AppCredentialScope | frozenset[AppCredentialScope], ...)` normalised to a frozenset; the check order stays (401s, disabled, other app, wrong scope); `row.scope not in {s.value for s in scopes}` → 403 `app_credential_scope`; runtime path when the row's scope is runtime. `RequireAppCredential(scope_or_scopes, denied_action, *, lock=True, failed_auth_scope=None)`: stores `scopes`; refuses `lock=True` with any runtime scope and refuses mixing runtime and CI scopes (`ValueError`); takes `request: Request`; when `failed_auth_scope` is set, catches `UnauthorizedError`/`PermissionDeniedError` from `authenticate`, calls `enforce_failure_limit` (429 replaces the error when over), else re-raises. Existing deps unchanged in behaviour. New: `UsageReadCredentialDep` (`{USAGE_READ, USAGE_WRITE}`, `AuditAction.USAGE_READ`), and three write deps (`{USAGE_WRITE}`, `USAGE_CONSUMED` / `USAGE_SETTLED` / `USAGE_REFUNDED`), all `lock=False, failed_auth_scope="usage_auth_failed"`. `issue`: `limits:write` → 409 `_refusal(..., "scope_not_available")` until ADM-044; the admin allow-list at `:123` already refuses usage scopes (test only).
6. **Models + migration** as in Data model; register in `app/db/models.py`; `make migration`, hand-edit names, CHECK texts and `downgrade()`; `uv run alembic check`.
7. **Membership** `RbacRepository.is_active_app_member(user_id, app_id) -> bool`: `EXISTS` over `role_assignments ⋈ roles (app_id) ⋈ users (status active)`, non-expired (`expires_at IS NULL OR > now()`), app or org scope; same predicate as `member_counts` (factor a shared private expression). No verified-email check (Q3).
8. **Repository** `meter_repository.py`: `db_now()`; `set_lock_timeout()` (`SET LOCAL lock_timeout = '2s'`, module constant); `find_active_metric(app_id, key)`; `insert_event(...)` `ON CONFLICT ON CONSTRAINT uq_usage_events_app_id_idempotency_key DO NOTHING RETURNING`; `get_event(app_id, key, for_update)`; `consume_counter(...)`: one statement `INSERT … SELECT … WHERE :amount <= (<effective limit scalar subquery>) ON CONFLICT (user_id, metric_id, period, period_start) DO UPDATE SET used = usage_counters.used + EXCLUDED.used, updated_at = now() WHERE usage_counters.used + EXCLUDED.used <= (<limit subquery>) RETURNING used, <limit>` (the effective limit reuses `UsageLimitOverrideRepository.effective_limit`'s expression); `adjust_counter(..., delta)` with `GREATEST(0, used + :delta)` and `WHERE used + :delta <= MAX_LIMIT`; `read_user_usage(app_id, user_id, starts_by_kind, metric_key)`: one query, active metrics LEFT JOIN active override LEFT JOIN counter on `period = m.period AND period_start = CASE m.period …` (no N+1). No `lock_app`, no `FOR UPDATE` on metric/app rows (ADR-0022 §3).
9. **Service** `meter_service.py` (R1, raises `AppError` subclasses only): `consume`: lock timeout → metric (app-scoped; unknown/archived 404) → `is_active_app_member` (else 403, log `usage_denied key_id user_id reason=user`) → amount ≤ config max → `period_bounds(metric.period, db_now)` → insert event → on conflict load and `replay_or_conflict` (replay never re-checks the limit) → counter upsert; zero rows → read current `used` and raise 409 (session rollback removes the event: refusals are not stored, Q6). `settle`/`refund` per the state machine (event `FOR UPDATE`, then counter; same lock order everywhere, so no deadlock cycle). `refund` logs `usage_refund key_id=… user_id=… metric=… amount=…` at INFO (refund-rate monitoring); nothing logs the idempotency key or an email. No audit rows (ADR-0022 §9). Commit happens in `get_session` before the response, so no 2xx before commit.
10. **Router** `usage/app_router.py`: copy `organizations/app_router.py:21-68`; responses map; `openapi_extra` requestBody via `inline_schema`; `Cache-Control: no-store`. Mount in `app/api/router.py`.
11. **Registry/tests**: `APP_CREDENTIAL` becomes `dict[route, frozenset[str]]`; add the 4 routes; `test_app_credential_allow_list_is_exact` compares `{s.value for s in c.scopes}`; `APP_CREDENTIAL_AUDITED` gets the 4 denied-only actions.
12. **Docs**: CLAUDE.md R3 (a) scope list gets the routes: `usage:read` — `GET /api/v1/apps/{key}/usage/users/{user_id}`; `usage:write` — `POST /api/v1/apps/{key}/usage/consume|settle|refund` and the usage read; `limits:write` stays "(ADM-044)". R9 exceptions: `/usage/users/{user_id}` is a single document, not `Page[T]`; the three action POSTs are verb routes returning 200 on first call and replay (not 201). D36 row in `docs/identity-architecture.md`: routes, codes, numbers, window, charset, latency target. `docs/consumers.md` section: routes and bodies, status table, `MMLABS_USAGE_CREDENTIAL` / `MMLABS_USAGE_READ_CREDENTIAL` backend-only, flow (consume before the action → settle for durations → refund on failure), one stable idempotency key per user action reused on retries, fail-closed default with grace only on transport errors/timeouts/5xx and never on 401/403/409/422/429 (ADR-0022 §6; grace setting is ADM-046), latency target p95 < 100 ms server-side, client timeout 2 s with one retry on the same key, limits are N workers × setting.

Rules: R1 (layers above), R2 (step 6), R3 (scope sets in registry, one class), R4 (no audit for uses per ADR-0022 §9; denied rows kept), R5 (step 2), R6 (codes above, no new status), R7 (no staff reads, purge, grace, `limits:write` routes), R8 (tests), R9 (exceptions recorded), R10 (counters only), R11 (no email, key never logged).

## Specialist input used
- **api-platform-engineer**: adopted routes, metric by key, single read document with `?metric=`, `read_json_body` 2 KiB cap / 5 s, replay 200 + `replayed`, fingerprint compare before the limit, consumers section, R9 list. Rejected: separate 403 codes for non-member/disabled (identity + security: existence oracle); `operation: estimate` field (no behavioural difference; any consume may be settled once); stored snapshot of limit/used for replay (live values of the charged period are returned instead; the decisive part, "accepted, amount", is identical and no extra columns are needed); settle/refund body without user/metric (security: the cross-check catches key collisions); amount max 2^31-1 as default (kept as the ceiling, default 1,000,000).
- **database-optimizer**: adopted counter key with period kind, composite same-app FKs, insert-event-first + conditional upsert, refused consumes rolled back, GREATEST(0,…), all three scopes in one CHECK, created_at index. Changed: no `fingerprint bytea` (the stored columns are the fingerprint); `idempotency_key` charset narrowed per Q8.
- **security-architect**: adopted live limit read, no `lock_app`/`FOR UPDATE` on metric/app, `lock_timeout`, `extra="forbid"`, refund full-only at most once, stored charged period, used CHECK 0..MAX_LIMIT with 422, one 403 code, settle/refund must match user/metric, key never logged, refund-rate log, failed-auth bucket, adjust window (Q4). Rate defaults 1200/20/600 adopted. "Settling to 0 counts as a refund": rejected (ADR-0022 §5: amount 0 → 422; use refund).
- **identity-access-engineer**: adopted scope-set model, all three scopes in CHECK/`RUNTIME_SCOPES`, `limits:write` not issuable, live OQ5 membership in the same transaction, no verified-email check, no user re-check on settle/refund, app-scoped key lookup, no audit for uses, D36 only (no ADR). Code name `usage_user_denied` chosen over security's `usage_subject_not_allowed` ("subject" is ambiguous next to JWT `sub` and the credential). Rate defaults 600/300 not adopted (shared egress IP risk the same consult names).

## Assumptions
- **Migration chain**: ADM-038 (same batch) builds and merges first and adds a migration; this migration's `down_revision` (and the `PREVIOUS` constant in `test_usage_ledger_migration.py`) is re-pointed onto ADM-038's revision at build. Head today: `a4c81e6d27f3`.
- Numbering: D36 is used; ADR-0027 stays unused by this ticket (Q13). ADM-038 keeps its reserved ADR-0028/D37, or may take ADR-0027 if the batch prefers contiguous numbers.
- Hotspots with ADM-038: possibly `app/core/config.py`, `.env.example`, `CLAUDE.md`, `docs/identity-architecture.md` (D37 row), `app/db/models.py`: expect a rebase.
- Read route lists every active metric of the app (staff-defined, a handful per app); no pagination (R9 exception). If metric counts grow, a follow-up adds a cap.
- `limit`/`used`/`remaining` in a replayed result are current values of the event's period (documented in consumers.md).
- A lock timeout surfaces as the generic 500 (a 5xx the client treats as a server error); no new 503 code.
- The denied rows for wrong scope / other app (existing `_denied`) stay; the per-IP and failed-auth buckets cap their volume.
- `read_json_body` is imported from `app/modules/releases/body.py` (moving it to `core` is a refactor outside R7; follow-up).
- Settle is allowed on any consume (no estimate flag), at most once.

## Blocking questions
1. **Routes and codes**: `GET …/usage/users/{user_id}` (single document, optional `?metric=`), `POST …/usage/consume|settle|refund`, metric by key, user by UUID; settle/refund bodies carry `user_id` + `metric` (mismatch → 409 `idempotency_key_reused`); first call and replay both **200** with `replayed`; R9 exceptions added. *Default: yes.*
2. **Replay semantics**: replay re-checks metric and user but never the limit; returns live `limit/used/remaining`, not a snapshot. *Default: yes.*
3. **Target user**: one 403 `usage_user_denied` for unknown, non-member (OQ5) and disabled users; no verified-email check; settle/refund do not re-check user or metric status. *Default: yes.*
4. **Adjust window** (not in ADR-0022; an added restriction): settle and refund only within 24 h of the consume (`USAGE_ADJUST_WINDOW_SECONDS`), else 409 `usage_adjust_window_closed`; replays still 200. *Default: yes, 24 h.*
5. **Refund/settle shape**: refund is full only, at most once; settle at most once, amount ≥ 1, overage accepted (never 409), to the original period. *Default: yes.*
6. **Refused consumes** are not stored (rolled back); a retry is re-evaluated. *Default: yes.*
7. **Amount max per call**: `USAGE_MAX_AMOUNT_PER_CALL` default **1,000,000**, ceiling 2^31-1. *Default: 1,000,000.*
8. **Idempotency key**: 1–128 chars of 0x21–0x7e (no space; ADR says "printable ASCII", this narrows it), never logged or echoed. *Default: 0x21–0x7e.*
9. **Rate limits** (per 60 s): per-IP **1200**, failed-auth per-IP **20** (new hook; over the limit a 401/403 becomes 429), per-key **600** shared across the 4 routes. *Default: these.*
10. **Latency target**: consume/settle/refund p95 < **100 ms** server-side, documented (D36, consumers.md), not enforced in CI; client guidance 2 s timeout, one retry on the same key. *Default: yes.*
11. **Scopes**: `usage:read`, `usage:write`, `limits:write` added to the CHECK and `RUNTIME_SCOPES` now; `limits:write` issuance refused (409 `scope_not_available`) until ADM-044; admin app refuses all three. *Default: yes.*
12. **Audit/logs**: no audit rows for read/consume/settle/refund; denied rows for wrong scope/other app stay; logs carry `key_id`, `user_id`, metric key only; one INFO line per refund. *Default: yes.*
13. **Record form**: decision-log row D36 only, no ADR-0027 (ADR-0022 is Accepted and leaves these values to ADM-042). *Default: D36 only.* (If Ming rejects Q4 or Q8's narrowing, still D36; an ADR only if Ming wants the window to bind ADM-046 too.)

## Split proposal
Estimated ~750 changed product/migration/doc lines plus ~900 test lines, above the ~400-line guide. Recommended: **no split**, because the migration, the scope-set auth change and the registry are shared by every AC, and the read route is about 80 lines. If Ming wants two PRs: slice A = migration + scopes + auth + consume/settle/refund (all four ACs); slice B = the read route, its R9 text and its consumers.md rows.
