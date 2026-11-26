# Consult — identity-access-engineer — USR-29 (2026-11-21)

### Findings
- The auth order in the code matches what the brief says: 401s, then disabled, then mismatch, then scope, then an optional `confirm`. See `app/modules/app_credentials/service.py:152-194`. `confirm` takes `lock_app` and makes an unconditional `touch_last_used` call (`service.py:208-218`, `repository.py:77-86`). Both manifest and releases still use this path, because ADR-0021 §1 requires it (`docs/adr/0021-...md:53-54`).
- ADR-0022 §3 (`0022-usage-key-scopes.md:36-41`) defines the runtime-key path: no `lock_app`, and `last_used_at` is written by a conditional UPDATE at most every 60 s. None of it is in the code yet. The scope CHECK is still `('manifest','releases')` (`models.py:32`), and `touch_last_used` has no throttle. Whichever of ADM-042 or USR-29 ships first has to build this path.
- The issuance allow-list refuses the `admin` app for every scope except `releases` (`service.py:121-123`). A new scope is refused for `admin` automatically, and USR-29 should keep it that way. `admin` has no org-scoped roles, so there is nothing to read.
- Rate limits today are one per-IP bucket per route (`config.py:104-112`). The per-key bucket after auth is decided (ADR-0022 §7) but not built.
- The route registry maps each route to one scope (`tests/test_route_auth_classes.py:62-69, 303`), so the new route only needs a new entry there.
- Precedent for read volume: ADR-0022 §9 says high-frequency usage calls get no audit rows. R4 only requires audit for mutations. Audit rows are only removed by the operator `audit-purge` CLI, with a floor of 90 days or more (`docs/identity-architecture.md:248-249`).

### Recommendations
1. **Hot path: use the runtime-scope pattern (ADR-0022 §3), not `lock_app`.** A read changes nothing, so it has no reason to queue on the same app lock as RBAC writers and manifest syncs. Revocation and app disable still take effect immediately, because every request re-reads the key and app rows. Build the throttled touch as a separate repository method, for example `touch_last_used_throttled`, with `WHERE revoked_at IS NULL AND (last_used_at IS NULL OR last_used_at < now() - interval '60 s')`, and call `authenticate(..., lock=False)` with no `confirm`. Make "runtime scope" a property of the scope (a set in `models.py`) so ADM-042 can reuse it. Keep the existing `lock=False`+`confirm` path for releases unchanged.
2. **Scope name: `org-members:read`**, which matches the `usage:read` style. Exactly one scope per key, at most 2 active keys per (app, scope), refused for `admin` at issuance (keep the allow-list at `service.py:122` as it is). It is a runtime key: it lives only in the deploy environment as `MMLABS_ORG_MEMBERS_CREDENTIAL`, never in CI, and has no CORS.
3. **Order of checks after auth:** credential checks, then `org_mode != required` → 422 `org_not_supported` (reuse `organizations/auth_service.py:45`), then the org-exact check → 404 `org_access_denied` for unknown, unrelated and suspended orgs alike (same no-oracle pattern as `invitations/service.py:165`). Neither answer leaks anything, because the caller has already been proven to be that app. Disabled app → 403 `app_disabled`, as today.
4. **Rate limits:** a per-IP bucket before auth, then a per-key bucket after auth, both in `config.py` (R5). Suggested defaults: 120/min per IP, 60/min per key. Mention N workers × the setting (ADR-0002).
5. **Audit: keep one row per successful call.** This is a bulk read of PII (emails), unlike usage counters, and it is the only evidence of what a leaked key took. Store `org_id`, `count`, `offset`, `limit`, whether `updated_since` was set, and the `status` filter. Never store emails or the body. Add a new `AuditAction` (`org.members_read`). The per-key bucket caps the volume (60/min ≈ 86k rows per key per day in the worst case). Point simap at `updated_since` and larger pages (`limit=100`) rather than polling. Denied mismatch/scope rows already come from `_denied`.
6. **ADR form: write a new ADR-0025** ("Org-member read scope") that amends ADR-0016 §1 and ADR-0021 §1/§12/§3, and ADR-0022 §1/§10 (scope set, CHECK, registry). Add decision-log entry D34. Do not edit accepted ADRs in place (ADR-0022, "Rejected alternatives"). Ming must approve it at the plan gate before the route ships, as ADR-0022 §10 did.
7. **R3 class 4 (a):** add `org-members:read` — `GET /api/v1/apps/{key}/orgs/{org_id}/members` (runtime key, refused for the `admin` app), and add D34/ADR-0025 to the citation list. If ADM-041 has not rewritten R3 to be scope-generic yet, list the scope explicitly. R9 needs no exception because the route returns `Page[T]` under `/apps/{key}`, which is already covered.
8. **Tests:** cover revoke-then-call → 401 with no lock involved; disable app → 403; check that `last_used_at` is not rewritten within 60 s; per-key 429; an admin JWT, an `mmdk_` key and a `manifest` key → 401/401/403 `app_credential_scope`; issuing for `admin` → 409 `admin_app`.

### Risks
- A leaked key exposes the emails of every member, in every org of that app (PII, §8 Q6). It is read-only, but the reach is the whole app, not one org. Mitigations: per-key bucket, audit trail, quick revocation.
- Two tickets, ADM-042 and USR-29, are both adding the runtime path and extending the CHECK. Migrations could collide and the code could be duplicated. One of them should own the shared hot-path code.
- Without the lock, a request already running during a revoke can finish. ADR-0022 §3 accepts this, and ADR-0025 should say so again.
- Listing disabled users returns their emails too. That is acceptable but should be stated in the ADR.

### Open questions for Ming (defaults in brackets)
1. Scope name? [`org-members:read`]
2. Runtime-key path with no `lock_app` and a throttled `last_used_at`? [yes, ADR-0022 §3]
3. Audit one row per call, or log only? [one row per call, ids and counts only]
4. Rate limits? [120/min per IP, 60/min per key, set in config]
5. Who builds the runtime hot path, if ADM-042 has not merged? [USR-29 builds it, ADM-042 reuses it]
6. Issue for `admin`? [no, refused]
7. Include `email` in the response, or only `user_id`+`display_name`, with email behind a separate scope? [include it, as the ticket says; revisit if a second consumer appears]
