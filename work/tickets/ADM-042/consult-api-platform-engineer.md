# Consult — api-platform-engineer — ADM-042 (2026-11-22)

## Findings

- ADR-0022 fixes: scopes `usage:read` (read only) and `usage:write` (consume/settle/refund + the same read) (ADR-0022 §1). The idempotency key is a body field ("`idempotency_key` body field", <=128 printable ASCII, unique per app, §5). A different payload gives 409 `idempotency_key_reused`. A settle above the estimate is accepted past the limit, never 409 (§5). Amount is a positive integer with a config max, zero or negative gives 422. Routes, rate-limit numbers, amount max and latency target are left to ADM-042 (ADR-0022 "Left to implementing tickets", §7).
- Spec §5.14 OQ66 (spec-v1.md:284): over limit gives 409 `usage_limit_exceeded`, nothing consumed. Non-member or disabled user gives 403. Unknown or archived metric gives 404 (ticket scope; ADR-0024 §1: an archived metric refuses consume, `admin` app has no metrics so 404).
- Existing metric routes are `/apps/{key}/usage-metrics[/{metric_id}]` for staff (test_route_auth_classes.py:149-165). Metrics are addressed by UUID `id` there. Runtime callers only know the immutable metric `key`, so the runtime routes should use the metric key.
- Pattern to copy: `app/modules/organizations/app_router.py:32-68` (a `rate_limit("...")` dependency, then `enforce_subject_limit(request, "..._key", caller.key_id)`, `Cache-Control: no-store`, a `_RESPONSES` map). `app/modules/releases/body.py:61-81` `read_json_body(request, model, cap=, timeout_s=)` requires Content-Length (411), JSON content type (415) and a cap (413). It is reusable for the small POST bodies. Routes must be added to `APP_CREDENTIAL` in tests/test_route_auth_classes.py:67-71. That map is one scope per route, so the read route needs two scopes, or `usage:write` must be accepted via a dependency allowing a scope set. This is a registry-shape change to flag.
- consumers.md has only the org-members section (:215-250), a good template: status table, "server-to-server only", and personal-data notes. There is no fail-closed or latency text yet.

## Recommendations

Routes (user addressed by user UUID in the path, metric by `key`; no email, per ADR-0022 §9):
1. `GET /api/v1/apps/{key}/usage/users/{user_id}` returns a single document `{user_id, metrics:[{metric, unit, period, period_start, period_end, limit, used, remaining}]}`. Add an optional `?metric=<key>` filter. It is not `Page[T]`, so it needs an R9 exception. Scopes: `usage:read` or `usage:write`. Send `Cache-Control: no-store`.
2. `POST /api/v1/apps/{key}/usage/consume`. Body: `{user_id, metric, amount, idempotency_key, operation?: "consume"|"estimate"}`. Keep `idempotency_key` in the body, as the ADR says. Do not add an Idempotency-Key header. Response 200 `{idempotency_key, metric, amount, status, limit, used, remaining, period_start, period_end, replayed}`. Use 200 for both first call and replay (resource not addressable, replay must be byte-identical in substance). 201 on first call and 200 on replay would let callers tell them apart, but it is more to document. I recommend 200 and a `replayed: bool`.
3. `POST /api/v1/apps/{key}/usage/settle`. Body: `{idempotency_key, amount}` (the actual amount). The user and metric come from the ledger row, so there is no payload mismatch surface. Return the same shape plus `delta`. A second identical settle replays; a different amount gives 409 `idempotency_key_reused`.
4. `POST /api/v1/apps/{key}/usage/refund`. Body: `{idempotency_key}`. Return the same shape. A refund with no prior consume gives 404 `usage_event_not_found`. Settle-after-refund and refund-after-refund (replay) follow the §5 state machine. An illegal transition gives 409 `usage_state_conflict`.
   - Alternative: one resource `/usage/events/{idempotency_key}` with PUT/POST subactions. I rejected it because the key is client-chosen and the 4-route set is clearer.
5. Status codes: 409 `usage_limit_exceeded` (not 429, which is reserved for rate limiting and which the ADR §6 says clients must not fail-open on... 409 likewise). Its `details` should carry `{limit, used, remaining}` and no other user data. 403: `usage_user_not_member` and `usage_user_disabled` (separate codes, same status). 404: `usage_metric_not_found`, used for unknown and archived metrics. An archived metric on read should still be listed or 404; I recommend consume 404 and read omitting archived metrics. Unknown `user_id` that is not a member gives 403, not 404, to avoid user enumeration. 422 for amount <= 0 or > max, a bad key charset, or an `on_behalf_of` field. 401, `app_credential_*` and 429 are unchanged.
6. R9 exceptions to add to CLAUDE.md R9 and the ADR: `usage/users/{user_id}` and the three action POSTs are singular or verb-style documents, not `/<plural-noun>`; the read is not `Page[T]`. List all four.
7. Registry: add these 4 routes to `APP_CREDENTIAL`; consume, settle, refund map to `usage:write`; the GET maps to `usage:read` (plus `usage:write`). Add explicit tests for wrong scope 403, other app 403, admin JWT 401, and a mismatch on both the CI scopes.
8. Body cap: reuse `read_json_body` with a small cap (about 2 KiB) and a 5 s timeout. Add settings in `app/core/config.py` (R5): per-IP pre-auth, per-IP failed-auth, per-key bucket (consume should be higher than org-members), and the amount max. Suggested amount max 2^31-1; this fits in JS-safe ints.
9. Replay: store the original response fields (status, amounts, limit/used/remaining at the time) in the ledger row. Return the stored values on replay with `replayed: true`. Never recompute the limit on replay. Compare the fingerprint before any limit check, so a replay of an accepted consume succeeds even if the user has since hit the limit.
10. docs/consumers.md new section "Metering AI usage from your backend (ADM-042, ADR-0022)", containing:
    - the routes, bodies and status table;
    - the key-in-backend-only rule (`MMLABS_USAGE_CREDENTIAL`);
    - the call-before-action flow (consume, then settle for durations, refund on failure);
    - a stable idempotency key per user action, reused across retries;
    - fail-closed by default; grace only on transport error, timeout and 5xx, never on 401/403/409/422/429 (ADR-0022 §6);
    - a latency target. Propose p95 < 100 ms server-side for consume and a 2 s client timeout with 1 retry on the same key.

## Risks
- The scope-set requirement on the read route changes how `OrgMembersCredentialDep`-style deps and the registry test work; plan it explicitly.
- Replay vs. rate limits: retries count against the per-key bucket; set generously.
- Returning different 403 codes for non-member vs disabled leaks membership to a holder of a `usage:read` key; acceptable since that key already sees all members (ADR-0022 consequences).
- In-process rate limits multiply by workers (ADR-0002).

## Open questions for Ming (default in brackets)
1. Replay status 200 with `replayed:true`? [yes]
2. Runtime metric addressed by key (not UUID) in the body? [yes]
3. Amount max? [2^31-1]
4. Consume latency target? [p95 < 100 ms]
5. Non-member of an unknown user id gives 403 (not 404)? [yes]
6. Estimate/settle as separate routes with the `operation` field only on consume? [yes]
