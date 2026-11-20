# Consult — api-platform-engineer — ADM-040 (2026-11-20)

FINDINGS
- ADM-039 already gives the pattern. Router prefix is `/apps/{key}/usage-metrics` with `current_admin` on the router, per-route `require_permission(READ|WRITE)`, a `_with_etag` helper, and `PATCH` with optional `If-Match` (app/modules/usage/router.py:19-24, 31-36, 105-132). Metrics are addressed by UUID `metric_id`, and the 404 for "unknown app, or metric not of it" is one shared text (router.py:27).
- `Reason` and `Limit` are reusable: reason is trimmed, 1-500, NFC and bidi-checked (schemas.py:39-45, with the comment "Reused by ADM-040/044"). `Limit` is a strict int 0..2^53-1 (schemas.py:52). Models use `extra="forbid"`.
- ADR-0024 §5-6 puts `reason` in `changes.reason` next to `{from, to}` diffs, never returned and never logged. It adds `UNIQUE(id, app_id)` on metrics for a composite FK (ADR-0024 §1, models.py:35). An archived metric refuses consume (§1), but nothing says whether overrides on it are allowed.
- Existing user-scoped routes are `/users/{user_id}/role-assignments` (POST/DELETE with an `assignment_id`) in tests/test_route_auth_classes.py:101-103. The auth-class registry must get the new routes (the file already lists the usage-metrics routes at lines 140-143).
- Spec: OQ62 says a user's limit may be set below the default, including 0, with an optional "until a date". OQ67 requires a reason on every adjustment. The ticket's acceptance criteria add: expiry is judged by the DB clock, expired rows fall back to the default, and the reason is required (422 without).

RECOMMENDATIONS
1. Route shape (R9: plural nouns, nested under the existing metric path). Use the metric UUID, as ADM-039 does, and the user UUID. One row per (user, metric), so the natural key is the address.
   - `PUT /api/v1/apps/{key}/usage-metrics/{metric_id}/overrides/{user_id}`, permission `admin:usage:write`. It is idempotent and covers both set and replace. A POST would force a 409-or-upsert decision.
   - `DELETE` on the same path returns 204.
   - Do not nest under `/users/{id}/…`: the app and metric are the parent resources, and one route set keeps the registry and the 404 logic simple. An optional reverse read `GET /users/{user_id}/usage-overrides` can come later; it is not required by the ticket.
2. Reads (the ticket scope lists only write routes, but staff need to see what they set, and the criteria need an observable "expired falls back"). Add both, `admin:usage:read`:
   - `GET .../overrides` returns `Page[UsageLimitOverrideRead]` (R9: limit ≤ 100, offset). Order by `created_at, id`. Optional filter `include_expired=false` (default: all).
   - `GET .../overrides/{user_id}` returns the single row, with ETag, and 404 if none exists.
   - The effective limit goes in the read schema as computed fields, not in a separate endpoint. `UsageLimitOverrideRead` fields: `metric_id, user_id, limit, expires_at, effective` (bool: unexpired by DB `now()`), `created_by`, `created_at`, `updated_at`. The reason is never returned (ADR-0024 §5). A full "effective limit for user" read belongs with ADM-042/044 (usage and balance), so defer it and say so.
3. PUT body is `{limit: Limit, expires_at: datetime|None = None, reason: Reason}` with `extra="forbid"`. `expires_at` must be timezone-aware (reject naive, 422) and strictly in the future, checked against the DB clock (`now()`), not the app clock, to match the "DB clock" criterion. A past value gives 422 (code e.g. `expires_at_in_past`); do not silently create a dead row. `limit` 0 is allowed (OQ62). Omitted or null `expires_at` means permanent.
4. Status codes (R6). PUT returns 201 on insert and 200 on replace, with the ETag header. A single PUT route needs `status_code` set per response, and FastAPI supports this by setting `response.status_code`. Document both in `responses`. DELETE returns 204 and 404 if no override exists (not idempotent-204, to stay consistent with `revoke_entitlement`; entitlements router.py:131-136). 404 also covers unknown or disabled app, the `admin` app, a metric not of the app, and an unknown user. Reuse `_APP_404`/`_METRIC_404`. Resolve the metric through `(metric_id, app_key)` and add a composite FK `(metric_id, app_id)` in the new table, per ADR-0024 §1.
5. Archived metric: allow PUT and DELETE (staff may pre-stage or clean up, and archiving is reversible), but include the metric `status` in 404/409 only if Ming prefers strictness. Default: allowed, no error.
6. User must exist (404). Do not require app membership: the user may join later, and OQ53 gives the default on join. Flag as an open question.
7. Concurrency: support optional `If-Match` on PUT (replace) and DELETE with `version_etag(updated_at)`, as ADM-024/039 do. Without a precondition, last write wins. A replace on a missing row with `If-Match` gives 412.
8. DELETE needs a reason (OQ67, R4) but DELETE bodies are fragile through proxies. Recommend a JSON body `{reason}` on DELETE, accepted by FastAPI and httpx tests. Do not use a query param (free text lands in access logs; ADR-0024 §5 says never logged). Check how `revoke_entitlement` carries its reason before copying it.
9. Audit (R4, same transaction): add `AuditTarget.USAGE_LIMIT_OVERRIDE` and actions `usage_override.set`, `.replaced`, `.removed` (actions.py:92-95 has the style). Diffs `{limit, expires_at}` from/to, plus `changes.reason`. Target id is the override id; carry `user_id` and `metric_id` in the diff or details (ids only, no email). A PUT that changes nothing writes nothing (the ADM-039 rule, ADR §6) and returns 200.
10. Expiry: no purge job needed in v1. Expired rows stay (history) and are excluded by `expires_at IS NULL OR expires_at > now()` in the effective-limit query. Expose that query as a repository function for ADM-042 to reuse; the criterion "expired falls back" needs a test that sets `expires_at` shortly in the future then moves the DB clock or inserts directly in the past.
11. Tests (R8): happy PUT 201 then 200, GET, DELETE 204, 404 matrix (app, `admin` app, metric, user, no override), 422 (no reason, blank reason, past or naive `expires_at`, negative limit, extra field), 401/403 (admin:usage:read cannot write), 412, audit row with reason, and the new routes in tests/test_route_auth_classes.py.

RISKS
- ADR-0024 is still "Proposed" until ADM-039 merges; ADM-040 depends on it, so the plan must follow its audit and reason rules exactly.
- A migration touches High paths, so the PLAN gate applies (CLAUDE.md workflow).
- If DELETE-with-body is rejected by a deployment proxy (Cloudflare tunnel), clients cannot remove overrides. Mitigation: `POST .../overrides/{user_id}:remove` is non-REST, so prefer to test the body first.
- Lowering a limit below current usage: ADM-042 must treat it as exhausted (never negative remaining). Note that in the ADR.
- Enum or error-code drift: keep new audit actions in actions.py only.

OPEN QUESTIONS FOR MING (suggested defaults)
1. Should reads of overrides be in ADM-040 (default: yes, list and single, `admin:usage:read`) or deferred?
2. PUT shape (default: PUT idempotent, 201 or 200) versus POST?
3. Allow overrides on archived metrics (default: yes)?
4. Must the user be a member of the app (default: user must exist, membership not required)?
5. Past `expires_at` (default: 422)?
6. DELETE removes with reason in the body (default), and 404 when no override exists (default)?
7. Should an unchanged PUT write an audit row (default: no, matching ADR-0024 §6)?
