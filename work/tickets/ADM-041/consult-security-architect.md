# Consult — security-architect — ADM-041 (2026-11-20)

### Findings
1. **Hot-path locking.** `AppCredentialService.authenticate` (`app/modules/app_credentials/service.py:177-180`) takes `rbac.lock_app()` (`FOR NO KEY UPDATE` on the app row) and writes `last_used_at` on every call, and ADR-0018 §1 keeps that for both scopes. Doing the same on every runtime `usage:write` consume call would push every AI action of an app through one row lock. Those calls would also queue behind RBAC writers and manifest syncs. This is a fail-closed outage risk under OQ66.
2. **Per-IP limits don't fit runtime keys.** ADR-0018 §3 sets per-IP pre-auth limits sized for CI. An app backend sends all its users' traffic from one IP, so the ticket's per-key limit is needed. The limiter is in-process (ADR-0002), so with N workers the effective limit is N times the setting.
3. **`on_behalf_of` is only an assertion.** As written in OQ61, the app backend sends a bare user id. Anyone with a leaked `limits:write` key and the user id of any `<app>:usage:manage` holder can change limits for every user of that app, and the API has no proof that user signed in.
4. **D-log numbering clash.** ADR-0018 release log §12 says its decision-log row is **D28**, but D28 (bug-report submit) and D29 are already taken in `docs/identity-architecture.md:320-321`. The §12 R3 class-4 text has also not been applied to `CLAUDE.md` yet (R3(4) still covers only the manifest route).
5. **Two files are numbered 0018.** An "ADR-0018 amendment" is ambiguous unless the full filename is cited.

### Recommendations
- **ADR form.** Write it as a new **ADR-0021** ("amends `0018-release-log-and-deployer-credential.md` §1/§3/§12"), and add a back-link in 0018. The D-log row is **D30**. Fix the release ADR's stale "D28" in the same PR, or flag it as a follow-up (R7). This covers the "ADR accepted… D-log updated" criterion.
- **Scope model.** Add `usage:read`, `usage:write` and `limits:write` as **runtime** scopes:
  - Keep one scope per key, so ADR-0018's invariant is unchanged and each scope can be revoked on its own.
  - Allow at most 2 active keys per (app, scope).
  - Refuse all three for the `admin` app (it has no AI metrics), like the manifest refusal.
  - Never combine CI scopes (`manifest`, `releases`) and runtime scopes.
  - Wrong scope gives 403 `app_credential_scope`; wrong key type gives the uniform 401.
- **Authentication path for runtime scopes** (R3, R11):
  - No `lock_app`.
  - Throttle the `last_used_at` write (for example at most once a minute, as a conditional UPDATE).
  - Revocation still takes effect immediately because every request reads the key row. Do not add caching, or cap it at 5 s or less and document that.
- **`on_behalf_of` rules.** It is required on every `limits:write` call; no machine-only limit changes. Check these live from the DB, not from token claims:
  - the actor is active and has a verified email;
  - the actor holds `<app>:usage:manage` through an **app-scope** assignment on *this* app;
  - the target user is an active member of this app;
  - a reason of 1–500 chars is present.

  Audit with both `SystemActor("app:{key}/{key_id}")` and the user id. A denied `on_behalf_of` writes a `denied` row. Strongly consider **binding** `on_behalf_of` to the app admin's own access token (`aud=<app>`, `sub` must equal `on_behalf_of`, not expired) instead of a bare id. That deviates from the OQ61 wording, so it goes to Ming (see Open questions).
- **Idempotency.**
  - Keys are unique per (app, idempotency_key).
  - The same key with a different payload returns 409 (for example `idempotency_key_conflict`) and never silently gives the first result.
  - Keep keys at least as long as events (90 days, OQ69).
  - consume → settle → refund is a state machine on one key. Settle and refund can never credit more than was consumed and are charged to the period of the original consume. The amount is a positive integer with a per-call maximum; negative amounts return 422.
- **Fail-open grace.**
  - Only `admin:usage:write` can turn it on; app admins cannot. It is audited.
  - Usage reported late is accepted only while the app has the setting on.
  - Limit how far back a report can be dated (for example 24 h) and add a per-user grace cap.
  - Say plainly that enforcement during the grace is client-side.
- **Rate limits.** Keep a per-IP pre-auth bucket against key brute force, plus a post-auth per-key bucket sized for runtime traffic. Values go in `app/core/config.py` (R5), final numbers are PLAN's, and the ADR should note the per-worker multiplication.
- **Token issuance stays ungated** (ADR-0017 §5, spec §5.14). The ADR should give the reasons: limits are per metric and change within a period, while a claim can be up to 12 h stale; consume is the exact enforcement point; and gating login would tie sign-in availability to metering.
- **R3(4) wording.** Rewrite it in one pass: the ADR-0018 §12 text plus a sub-item (c) for the runtime scopes. It must say these keys are for server use only and never go to the browser; "no CORS" does not stop a leaked key being used from curl. Update `tests/test_route_auth_classes.py` in the first implementing ticket, not this docs-only one.
- **Data exposure.** `usage:read` returns only that app's metrics for a member of that app, by user id, with no email (R11).

### Risks
- **Blast radius of a leaked runtime key.** Unlike CI keys, these live in a long-running backend's env.
  - `usage:write`: burn or refund any user's quota in that app.
  - `limits:write`: escalate to app-admin-level limit changes if `on_behalf_of` isn't bound.

  Mitigations: separate keys per scope, `last_used_at`, revoke, per-key limits, audit.
- **Availability.** With fail-closed, any slowness here blocks AI in every app. Spec §6's p95 < 200 ms target was set for admin reads, so a separate target is needed.
- **Monthly lock contention** on per-period counters if consume uses row locks. Prefer an atomic `UPDATE … WHERE used + amount <= limit RETURNING`.
- **Self-grant.** An app admin can raise their own limit.

### Open questions (for Ming)
1. Should `on_behalf_of` be bound to the app admin's access token, or stay a bare user id as OQ61 says?
2. May an app admin change their own limits?
3. One scope per key (three secrets per backend), or a multi-scope runtime key?
4. Should ADM-041 also apply the ADR-0018 §12 text to R3 now, or leave that to the releases implementing ticket? Doing both risks overlapping edits.
5. Is fail-open grace capped per user and per outage, and is it staff-only to turn on?
