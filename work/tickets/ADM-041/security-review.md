# Security review — ADM-041 (appsec-engineer)

## Attempt 1 (commit 3ab03e0, docs only)

**Verdict: PASS, no blockers. Five should-fix items.** The design is sound. Every risk the consults raised is handled in the ADR: one scope per key, no `lock_app` on the hot path, `on_behalf_of` bound to a token, live DB checks, a per-key rate-limit bucket, fail-open grace only on 5xx and transport errors, and reasons for keeping token issuance ungated. The should-fix items are gaps that ADM-042, ADM-044 and ADM-046 could implement unsafely if the ADR does not pin them down. Each one is a one- or two-line ADR edit, best made in this PR. Otherwise they become binding constraints on those tickets.

### Findings

**🟡 1. The `on_behalf_of` token check list is incomplete, and it is unclear where the acting user comes from.** `docs/adr/0022-usage-key-scopes.md:44-47`
- **Risk:** The ADR lists signature/`kid`, `aud`, expiry and `sub`. It leaves out `typ`, `alg` pinning, `iss`, strict string `aud`, `email_verified` and the required claims.
  - The ADR also does not say whether the acting user is taken from `sub` or from a separate `on_behalf_of` id. The ticket text still reads `on_behalf_of: <user_id>` (`work/tickets/ADM-044/ticket.md:24`).
  - An implementer could check that the token is valid but then act as a body-supplied id. That brings back the "leaked key + any admin's UUID" attack the ADR rejects (`:107-108`).
- **Fix:** Add to §4:
  - "Verified with `app.core.access_token.verify_access_token(token, key_set, audience=<key's app key>, issuer=settings.jwt_issuer)`, the same checks as `docs/consumers.md`."
  - "The acting user is `sub` only. If a separate `on_behalf_of` id is also sent and differs from `sub`, the call gets 403."
  - "The token is never logged, stored or put into audit `changes` (R11). Audit may record its `jti`."

**🟡 2. A logged-out or stolen app-admin token stays usable until it expires.** `docs/adr/0022-usage-key-scopes.md:44-50,116`
- **Risk:** App tokens carry no `sid`. Only admin tokens are session-bound (`app/modules/tokens/service.py:117-122`, `app/modules/admin_auth/deps.py:84`).
  - So a token captured from the app backend (logs, a compromised process) keeps working for its whole TTL, up to 12 h (`app/core/config.py:109`, `le=43200`), even after the admin logs out or the device session is revoked.
  - The live DB checks catch a disabled user or a removed permission, but not logout.
  - The app backend sees the admin's token on every request, so the Consequences line "useless without a live app-admin token" (`:116`) overstates the protection against a compromised backend.
- **Fix:** Either add to §4 "the `on_behalf_of` token's `iat` must be at most N minutes old (config, value at ADM-044)", or record it as accepted risk in Consequences: "a compromised backend that holds both the key and an admin's token can act as that admin until the token expires (≤ `ACCESS_TOKEN_TTL_SECONDS`); logout does not stop it."

**🟡 3. The throttled `last_used_at` update must no longer be read as "revoked".** `docs/adr/0022-usage-key-scopes.md:37-41`
- **Risk:** Today, if the touch updates no rows the request is rejected as revoked (`app/modules/app_credentials/service.py:178-180`: `if not await self.repo.touch_last_used(row.id): raise _reject("revoked", ...)`).
  - With the new `... AND (last_used_at IS NULL OR last_used_at < now() - 60 s)` condition, "0 rows" also means "touched less than 60 s ago".
  - If the runtime path reuses that check, almost every consume returns 401. Because fail-closed is the default and the grace never applies to 401, that is a full AI outage for the app.
- **Fix:** Add to §3: "For runtime scopes the result of the conditional UPDATE is ignored. Revocation and app status are decided only by the key and app rows read earlier in the request." ADM-042 should add a test that two consumes within 60 s both succeed.

**🟡 4. The per-IP buckets are probably shared by every app backend, so one app can block all the others.** `docs/adr/0022-usage-key-scopes.md:80-84`
- **Risk:** Production is the Mac runner behind Cloudflare Tunnel, and the client IP is `CF-Connecting-IP` or the peer address (`app/core/client_ip.py:18-30`).
  - If the mmlabs app backends run on the same host, they share one egress IP, so the "generous per-IP" and "stricter failed-auth per-IP" buckets are global across apps.
  - A single app with a revoked or rotated key still deployed on one instance fills the failed-auth bucket. Every app's consume then gets 429. The grace never applies to 429 (`:75`), so AI stops across the ecosystem.
- **Fix:** In §7, key the failed-auth bucket by (IP, presented `key_id`) and keep only a much higher plain per-IP ceiling against brute force. Have ADM-042 size the pre-auth per-IP bucket for the combined traffic of all apps from one host. Add a Consequences line that the failed-auth bucket must not let one app's misconfiguration starve the others.

**🟡 5. Fail-open grace has no time limit, and the late-report date comes from the client.** `docs/adr/0022-usage-key-scopes.md:73-78`
- **Risk:** While the setting is on, any backend may send late reports that "may record overage, never 409". The only limits are 24 h of backdating and a per-user cap. There is no link to an actual outage, and nothing turns the setting off.
  - The report date is client-supplied and is "counted in the original period". A report sent just after a daily or weekly reset can be dated into the previous, already-closed period, which moves usage out of the current period's limit.
  - Low impact today because backends are trusted, but it is a quiet way around OQ60 "both sides enforce" that stays open if staff forget to switch the setting off.
- **Fix:** Add to §6:
  - The grace setting has a mandatory expiry: staff set an end time, at most N hours, value at ADM-046, and it is audited.
  - A late report may not be dated before the time the grace was turned on, nor before the start of the previous period.
  - The per-user grace cap counts against the period the report is dated into, so backdating gains nothing.

**💭 6. The settle amount needs an upper bound.** `docs/adr/0022-usage-key-scopes.md:67-69`
- "A settle above the estimate is accepted even past the limit" plus "never 409" leaves settle bounded only by the per-call maximum, and only if that maximum applies to settle as well.
- **Fix:** State that the per-call maximum applies to the consume, settle and refund amounts.

**💭 7. The self-grant ban is narrow. That is acceptable, but say so.** `docs/adr/0022-usage-key-scopes.md:52-53`
- Two app admins can grant to each other, and an admin benefits from raising app defaults. Both are audited with the acting user and `via_credential`.
- **Fix:** Add one Consequences line: "self-grant ban is per-request; collusion between app admins is detected through audit, not prevented."
- Also say "target user is an **active** member" (`:51`) to match the consume rule in OQ66.

**💭 8. Refuse the `admin` app at authentication too, not only at issuance.** `docs/adr/0022-usage-key-scopes.md:26`
- As defense in depth, usage routes for `apps/admin/...` should also refuse at auth or route level, so a key created by a bad data fix or an older migration can never meter `admin`.
- The `on_behalf_of` side is already closed: `aud` must equal the key's app, so an `aud=admin` token cannot be used.

**💭 9. "Issuance stays ungated" in D31 is ambiguous.** `docs/identity-architecture.md:322`
- Next to the key-issuance rules it can be read as "key issuance needs no permission".
- **Fix:** Write "access-token issuance stays ungated (ADR-0017 §5)".

**🟢 10. The R3 rewrite does not weaken any existing guarantee.** `CLAUDE.md:55`
- Compared with the old class 4 text, everything is kept: issuance by `admin:*` only, SHA-256 hash only, no CORS, own app only (another app → 403), never on any other route, per-IP pre-auth limit, no refresh tokens.
- It adds "server-side only", the 401 for a wrong key type or an admin JWT, a per-key limit, and a pointer to the route registry.
- The only change of authority is that route lists move from CLAUDE.md to the ADRs and `tests/test_route_auth_classes.py`. This is acceptable because the registry test enforces it. Today the registry still holds only the manifest route, which matches "manifest … (live)".
- "Deployer credential … for any registered app" comes from ADR-0021 §2, already accepted, and is marked pending ADM-036.
- Minor: R3 cites "D25, D31" but not D30, because the release-log D-row is missing (impl-log already lists this as a follow-up). This does not affect security.

**🟢 11. Scopes and blast radius.**
- One scope per key, at most 2 active keys per (app, scope), and runtime and CI values never shared (`:19-20,34`).
- Bare-id `on_behalf_of` rejected (`:107`).
- Per-scope blast radius is stated honestly (`:114-116`), including that a leaked `usage:write` key can refund to restore quota.
- Idempotency is scoped per app with a payload fingerprint and a 409 on reuse, the refund is capped at the amount charged, and retention is at least 90 days (`:60-70`). This closes replay and double-refund.
- The fail-open triggers correctly exclude 401, 403, 409, 422 and 429 (`:75`), so a revoked key or an attacker-induced 4xx cannot turn into free usage.

### Follow-ups (out of scope)
- ADM-044's ticket text (`work/tickets/ADM-044/ticket.md:24-25`) still says `on_behalf_of: <user_id>` and makes `SystemActor` the audit actor. Update it to ADR-0022 §4: token-bound, actor = the user, `via_credential`.
- D30 row for ADR-0021 is missing from `docs/identity-architecture.md` §8 (already in impl-log).
- ADM-042 needs the regression test from finding 3, plus a test that a key of the wrong scope on usage routes gets 403 `app_credential_scope` and is audited as `denied`.
