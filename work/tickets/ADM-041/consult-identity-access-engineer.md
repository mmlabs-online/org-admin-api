# Consult — identity-access-engineer — ADM-041 (2026-11-20)

## Findings
1. **Numbering and sequencing.** "ADR-0018" points at two files, and the D-log is already inconsistent. ADR-0018 §12 says "D28 records this ADR", but D28 in `docs/identity-architecture.md` is bug-report submit, and no D-row for ADR-0018 exists (the last row is D29). `CLAUDE.md` R3 class 4 still describes the manifest key only, because ADM-034 and ADM-035, which apply the ADR-0018 §12 text, are not merged yet. There is no `scope` column in code yet.
2. **One scope per key** (ADR-0018 §1) means one app backend holds 3 keys. Unlike the manifest/releases split, all 3 sit in the same runtime secret store, so separate keys for `usage:read` and `usage:write` isolate little.
3. **The auth path takes a row lock.** ADR-0018 §1 says app-credential auth takes `lock_app` before writing `last_used_at`. On a per-AI-action route, that queues every consume call of an app behind one row lock.
4. **The per-IP limit doesn't fit.** ADR-0018 §3 sets a per-IP limit before auth, 30/min per route. An app backend sends all its users' traffic from one IP, so with the fail-closed default it would block AI.
5. **`on_behalf_of = <user id>` takes the backend's word for who the user is.** Anyone with a leaked `limits:write` key who knows an app admin's UUID (UUIDs show up in admin UI and logs) can change any limit in that app.
6. **Leaked usage keys.** A leaked `usage:write` key can use up any member's quota (DoS). A leaked `usage:read` key exposes usage of all the app's users. ADR-0018's Consequences section lists no blast radius for these.

## Recommendations
- **R-1 (AC1, R7):** Write this as a new **ADR-0021** that amends ADR-0018 §1 and §3, plus a new D-log row **D30**. "ADR-0018 amendment" is ambiguous with two 0018 files. Put the D28 mislabel and the duplicate 0018 number on the follow-up list. Then choose one:
  - write the R3 class 4 text once, covering ADR-0018 and ADR-0021 together, and note the routes are "pending ADM-034/035/042/044"; or
  - keep R3 untouched and have the ADR give the final text.

  Ming should pick. Writing R3 twice in parallel tickets will cause a merge conflict.
- **R-2 (scopes):** Keep "exactly one scope per key" and the limit of 2 active keys per (app, scope). Add `usage:read`, `usage:write` and `limits:write` to the CHECK set. Let the `usage:write` route set include the current-usage read for the same user, so a normal backend needs 2 keys rather than 3. Refuse all 3 scopes for the `admin` app (as `manifest` is). Mark them **runtime** keys: held in the backend's deploy environment (e.g. `MMLABS_USAGE_CREDENTIAL`), never in CI secrets, never sent to browser code. No CORS.
- **R-3 (hot path):** Runtime scopes must not take `lock_app`. Update `last_used_at` only when it is older than about 60 s, with no lock. Revocation and app disable stay immediate because every request still reads the database.
- **R-4 (rate limit):** Use three buckets:
  - a generous per-IP bucket before auth, to stop key guessing;
  - a per-IP bucket for failed auth only;
  - a **per-key** bucket after auth, with values in config.

  Note that ADR-0002 limits are in-process, so they apply per worker.
- **R-5 (fail-open):** Grace only triggers on transport errors, timeouts and 5xx. **Never on 4xx**: 401, 403 (`app_credential_scope`, disabled user, non-member), 409 and 429 must stay closed. Otherwise a revoked key or an attacker-induced 4xx turns into free usage. Only staff (`admin:usage:write`) can turn the setting on, it is audited, and `limits:write` cannot change it. Late reports: same idempotency keys, a bounded age (e.g. ≤ 24 h, counted against the original period), overage recorded and never 409.
- **R-6 (idempotency):**
  - Scope keys per (app, key) and bind each one to a fingerprint (user, metric, amount, operation). The same key with a different payload returns 409 `idempotency_key_reused`.
  - Allow at most one refund per consume, and one or more settles, each idempotent. A refund can't exceed the amount consumed and is credited to the original period.
  - Cap the key length (≤ 128 chars, printable ASCII).
  - Keep idempotency records at least as long as the 90-day event retention.
- **R-7 (`on_behalf_of`):**
  - On every call, check live in the DB (not from token claims): the user is active and verified, and holds `<app>:usage:manage` through an assignment with `org_id IS NULL`, in the same app as the key.
  - The target user must be a member of that app.
  - `limits:write` without `on_behalf_of` → 422. The key alone must never act with staff power.
  - Audit actor = the user; put the credential `key_id` in the audit details.
  - Better: send the admin's **app access token** (aud=`<app>`) as the subject instead of a raw UUID. This is proof the admin is present, it is standard (the RFC 8693 subject-token idea, without a token endpoint), and it stops "leaked key + UUID" attacks. It differs from the spec wording, so Ming must decide.
- **R-8 (ungated issuance):** Record why issuance stays ungated:
  - ADR-0017 §5: issuance is never gated on entitlements;
  - limits are per metric and per period, and a token can't express that;
  - claims can be up to a token lifetime stale, while consume is atomic and current;
  - gating login would make a quota problem an auth outage.
- **R-9 (threat model):** Add the blast radius of each new scope (Finding 6) and the mitigations from R-3 to R-7 to Consequences.

## Risks
- If fail-closed apps hit the per-key limit or a slow DB, AI stops in every app. ADM-042 needs a latency target and load test.
- Any app without a backend (pure client or mobile) can't take part without exposing a key.
- If `on_behalf_of` stays a bare UUID, `limits:write` is effectively an app-wide staff key.
- Can an app admin grant to themselves? Not specified (OQ61).

## Open questions (Ming)
1. ADR-0021 or an in-place edit of ADR-0018? And who writes the R3 text: ADM-041 or the first implementing ticket?
2. Should `on_behalf_of` be the user's app access token instead of a user id (a deviation from the spec)?
3. Two keys (`usage:write` also allows reads) or three?
4. Should app admins be blocked from granting to themselves?
5. Is fail-open grace staff-only to enable? What is the maximum age of a late report?
