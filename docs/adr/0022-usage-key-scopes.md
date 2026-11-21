# ADR-0022: Usage key scopes (runtime app credentials)

Status: Accepted (2026-11-20)
Amended by ADR-0025 (§1 and §10: `org-members:read` is a runtime scope and the first user of the runtime path)
Date: 2026-11-20

Amends ADR-0021 §1 (scope set and auth path), §3 (rate limits) and §12 (R3 text).

## Context
Spec §5.14 (AI usage limits, OQ56/61/65/66/67/69): app backends meter AI usage through this API at
runtime. Spec §5.14 requires new scopes on the same scoped-key mechanism and an amendment before any
usage route ships. ADR-0021 §1 defines app credentials with one scope per key (`manifest`,
`releases`), at most 2 active keys per (app, scope), and takes `lock_app` on every authentication.
ADR-0017 §5 keeps token issuance ungated. The owner approved the defaults of this ADR at the plan
gate (batch 2026-11-20-1, questions 1 and 21-28, all defaults).

## Decision

### 1. Scope set (amends ADR-0021 §1)
Three new scopes: `usage:read`, `usage:write`, `limits:write`. Still exactly one scope per key and at
most 2 active keys per (app, scope).
- `usage:read`: the per-user limits / used / remaining read.
- `usage:write`: consume, settle and refund, plus the same per-user current-usage read, so a backend
  that only meters holds one key.
- `limits:write`: the app-admin actions of ADM-044 only.

All three are refused for the `admin` app at issuance (no AI metrics). Error codes are unchanged:
wrong scope gives 403 `app_credential_scope`, another app's key 403 `app_credential_mismatch`, a
disabled app 403 `app_disabled`, a key of the wrong type or an admin JWT the single 401 of ADR-0016 §4.

### 2. Runtime-key rule
The usage scopes are runtime keys. They live only in the app backend's deploy environment
(`MMLABS_USAGE_CREDENTIAL` for `usage:write`, `MMLABS_USAGE_READ_CREDENTIAL`,
`MMLABS_LIMITS_CREDENTIAL`), never in CI secrets, browser or mobile bundles. No CORS. A runtime key
never shares a value with a CI scope (`manifest`, `releases`). Apps without a backend cannot take part.

### 3. Auth hot path (amends ADR-0021 §1, last bullet)
Runtime scopes do not take `lock_app`; that path stays for `manifest` and `releases` only.
`last_used_at` is written by a conditional
`UPDATE ... WHERE revoked_at IS NULL AND (last_used_at IS NULL OR last_used_at < now() - 60 s)`.
Revocation and app disable stay immediate because every request reads the key and app rows (no
caching). A request already in flight during a revoke may complete; this is accepted.

### 4. `on_behalf_of` (limits:write)
- Required on every `limits:write` call (missing: 422). Any other scope sending it: 422.
- Proof of the user: the app admin's own access token, sent in the request (header or field is
  ADM-044's choice). It must have a valid signature (`kid`), `aud` = the key's app, not be expired,
  and `sub` is the acting user. This deviates from the bare user id in spec OQ61; the spec carries a
  dated note.
- Live DB checks on every call, never from claims: the user is active with a verified email; holds
  `<app>:usage:manage` through an assignment with `org_id IS NULL` on the key's app (org-scoped
  assignments do not count); the target user is a member of that app.
- Self-grant refused: acting user = target user on a per-user grant gives 403. App defaults and
  periods stay allowed. Staff (`admin:usage:write`) can still adjust anyone.
- Reason 1-500 chars on every adjustment (OQ67).
- Audit (R4): actor is the user; `changes` holds `via_credential = <key_id>` and the reason. A denied
  `on_behalf_of` writes a `denied` row with `SystemActor("app:{key}/{key_id}")`.
- Failed proof or failed checks: 403 (code names are ADM-044's).

### 5. Idempotency (OQ66, OQ69)
- Every consume carries an `idempotency_key` body field: at most 128 printable ASCII characters,
  unique per (app, idempotency_key). The scope is the app, not the key id, so a refund works after a
  key rotation.
- Each key is bound to a fingerprint (user, metric, operation, amount). Same fingerprint replays the
  first result; a different payload gives 409 `idempotency_key_reused`.
- State machine per key: consume, at most one settle, at most one refund. Settle and refund are
  idempotent and charged to the period of the original consume. A refund never credits more than was
  charged. A settle above the estimate is accepted even past the limit (the cost is incurred); it is
  recorded, never answered with 409.
- Amounts are positive integers with a per-call maximum (config, value at ADM-042); zero or negative: 422.
- Idempotency records are kept at least as long as events (90 days).

### 6. Fail-closed default and fail-open grace
Fail-closed is the default. Fail-open grace is a per-app setting that only staff with
`admin:usage:write` can change; it is audited and `limits:write` cannot change it. An app may apply
the grace only on transport errors, timeouts and 5xx, never on 401, 403, 409, 422 or 429. Late
reports are accepted only while the setting is on, reuse the same idempotency keys, are dated at most
24 h back and counted in the original period, may record overage (never 409), and are subject to a
per-user grace cap (value at ADM-046). Enforcement during the grace is client-side only.

### 7. Rate limits (amends ADR-0021 §3 for runtime scopes)
Three buckets: a generous per-IP bucket before auth, a stricter per-IP bucket for failed auth, and a
per-key bucket after auth. Values live in `app/core/config.py` (R5) and are set at ADM-042. Per
ADR-0002 limits are in-process, so the effective limit is N workers x the setting. The consume latency
target required by spec §5.14 / §6 is also set at ADM-042.

### 8. Token issuance stays ungated
ADR-0017 §5 is not reopened. Limits are per metric and per period and change mid-period, which a
claim cannot express; claims can be up to 12 h stale (`ACCESS_TOKEN_TTL_SECONDS` <= 43200, ADR-0005
amendment) while consume is atomic and current; gating login would turn a quota problem into an auth
outage. Enforcement is the consume call.

### 9. Data exposure
`usage:read` returns only that app's metrics for a member of that app, by user id, with no email
(R11). Consume and refund are not audit entries (spec §5.14 "Volume and audit"); staff and app-admin
changes are. Usage counters only: no billing (R10).

### 10. R3 text and registry
The R3 class-4 text in `CLAUDE.md` is rewritten scope-generic by ADM-041 and absorbs the ADR-0021 §12
text. ADM-034, ADM-042 and ADM-044 extend the registry in `tests/test_route_auth_classes.py`
(a scope mapping per route). ADM-042 extends the `app_credentials.scope` CHECK (column added by
ADM-034) with the three scopes and adds a uniqueness constraint on (app_id, idempotency_key).
No usage route ships before this ADR is accepted (it is).

## Rejected alternatives
- Editing ADR-0021 in place: accepted ADRs keep their content; changes are new records with a pointer.
- Multi-scope runtime keys: larger blast radius per leaked key.
- Bare-UUID `on_behalf_of`: a leaked `limits:write` key plus any app admin's UUID would act as that
  admin across the whole app.
- OAuth client credentials (ADR-0016 §2).
- Gating token issuance (§8).
- Fail-open on any error (§6).

## Consequences
- Blast radius: a leaked `usage:write` key can burn or refund any member's quota; a leaked
  `usage:read` key exposes all members' usage; a leaked `limits:write` key is useless without a live
  app-admin token.
- Availability coupling: apps depend on this API for AI actions; fail-closed by default, staff-only grace.
- Left to implementing tickets: exact routes, header vs field for the `on_behalf_of` token, numeric
  rate limits, amount maximum, latency target, `on_behalf_of` error-code names.
- Tickets that change the route-class registry: ADM-034, ADM-042, ADM-044.
