# Consult — identity-access-engineer — ADM-042 (2026-11-22)

**Findings**
- The scope check accepts exactly one scope. `service.py:189` does `row.scope != scope.value`, `RequireAppCredential` takes a single scope (`deps.py:27-34`), and the registry maps each route to one scope (`tests/test_route_auth_classes.py:63-70`, asserted at `:306`). ADR-0022 §1 (`docs/adr/0022-usage-key-scopes.md:23-24`) says `usage:write` must also work on the per-user read route, so this model has to change.
- `RUNTIME_SCOPES` (`models.py:27`) and the scope CHECK (`models.py:38-40`) list only `org-members:read`. ADR-0022 §10 (`:101-102`) says ADM-042 adds all three new scopes to the CHECK, `limits:write` included.
- The admin-app refusal already works. `service.py:122-124` lets the admin app have only `releases` keys, so all three new scopes are refused there. The only gap is a test.
- ADR-0022 §7 (`:81-85`) asks for three rate-limit buckets: per-IP before auth, a stricter per-IP bucket for failed auth, and per-key. Only the first and last exist (`rate_limit.py:144-179`; USR-29 config at `config.py:124-127`). There is no failed-auth bucket yet.
- Rules for the target user. The spec (OQ66, `spec-v1.md:284`) asks only for "disabled users and non-members → 403". The member definition (OQ5, `spec-v1.md:111`) is an active user with any non-expired role on the app, and unverified email still counts. R11 already blocks unverified users from getting an app token, so a verified-email check at consume adds almost nothing.
- Audit. ADR-0022 §9 (`:95`) says consume and refund are not audit entries. Settle falls under the same reasoning. The existing denied-audit rows for a wrong app or wrong scope (`service.py:187,191`) stay.
- Numbering. ADR-0026 and D35 are taken (USR-30). Next free are ADR-0027 and D36.

**Recommendations**
1. Let `RequireAppCredential` and `authenticate` accept a frozenset of scopes, and keep the current check order. The read route accepts `{usage:read, usage:write}`; consume, settle and refund accept `{usage:write}`. Change `deps.py:30` to refuse `lock=True` when any accepted scope is a runtime scope. Change the registry to route → set of scopes. (AC "wrong scope 403"; R3.)
2. Add all three scopes to `RUNTIME_SCOPES` and to one CHECK migration with a downgrade, as ADR-0022 §10 says. Keep `limits:write` non-issuable until ADM-044, with a one-line refusal next to `service.py:123`, so no unused secrets get handed out. (R2, R7.)
3. Check the target user live on every consume and read. Do it in the same transaction as the conditional upsert, one indexed query: the user exists, is active, and holds a non-expired assignment on the key's app (app scope or any org, the OQ5 definition). Do not check verified email.
   - Unknown, non-member and disabled users all get the same 403 (for example `usage_user_denied`). A distinct "disabled" code would let any app key learn whether a user ID exists anywhere in mmlabs.
   - Settle and refund do not re-check the user. A refund after the user was disabled must still work, and settle records a cost already incurred (ADR-0022 §5).
4. Look up idempotency keys by `(caller.app_id, key)` only. A refund or settle with another app's key gives 404, never the other app's event. The fingerprint includes `user_id`.
5. Rate limits.
   - Per-IP before auth, shared by all usage routes: generous, default 600 per 60 s.
   - Failed-auth per-IP bucket: new, default 20 per 60 s, counted on every 401/403 from `authenticate`.
   - Per-key after auth, one bucket for all usage routes: default 300 per 60 s.
   - All values in config (R5). This needs a new `SubjectScope` and a new failure-path hook.
6. No audit rows for consume, settle, refund, read, or a 403 on the target user. Log `key_id`, `user_id` and the reason only. Never log the idempotency key body or emails (R11).
7. R3 text: list the concrete usage routes under their scopes, add "the usage read route also accepts `usage:write`", and keep "`limits:write` (ADM-044)". Update the registry.
8. ADR form: no new ADR. ADR-0022 is Accepted and leaves routes, numbers, amount max and latency target to this ticket (`:119-120`). Record them as D36. Write ADR-0027 only if Ming rejects recommendation 5's failed-auth bucket (that deviates from §7) or wants a different rule for target users.

**Risks**
- Shared egress IP. If several app backends run on the Mac runner, they share one per-IP bucket. A 429 on consume is fail-closed, and ADR-0022 §6 forbids fail-open on 429, so a tight IP limit turns into an AI outage across apps. Size it generously and make the per-key bucket the real limit.
- In-process limiter: the effective limit is N workers × the setting (ADR-0002).
- A leaked `usage:write` key can burn or refund any member's quota (ADR-0022 Consequences). Recommendation 3 limits it to members of that app; revoke-and-reissue is the only fix.
- The membership query sits on the consume hot path and needs an index on `role_assignments(user_id, app_id)` plus an expiry filter. Database consultant to confirm.

**Open questions for Ming (defaults)**
1. Add `limits:write` to the CHECK now but refuse issuing it until ADM-044? Default: yes.
2. Check verified email at consume? Default: no (OQ5 counts unverified members; R11 already blocks their tokens).
3. One 403 code for unknown, non-member and disabled users? Default: yes, `usage_user_denied`.
4. Add the failed-auth bucket now (ADR-0022 §7)? Default: yes. Rate defaults: 600 per IP, 20 failed per IP, 300 per key, each per 60 s.
5. Audit rows for refunds? Default: no (ADR-0022 §9; the ledger is the record).
6. ADR form? Default: D36 only, no ADR-0027.
