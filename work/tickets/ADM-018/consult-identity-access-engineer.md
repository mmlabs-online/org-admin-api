## ADM-018 consult (identity-access-engineer): `ent` token claim

### Findings
1. **No gating, confirmed.** ADM-006 `plan.md` lines 180 and 196 say: "No entitlement gate on token issuance or join in v1". The ADM-006 architect consult (line 62) gives the reason: no rows exist yet, so a gate would lock calechip orgs out. The owner's Q4 answer in the ADM-018 ticket notes (2026-11-17) adds a claim only. It does not add a pull endpoint or widen R3 class 4. So ADM-018 adds information to the token and must not add a new refusal.
2. **Issuance path** (`/home/user/org-admin-api/app/modules/tokens/service.py:89-97`). The `org` block already refuses an unknown, suspended or foreign org with one 403 `org_access_denied`. That works because `org_role_keys` is org-exact and requires org `active` (`app/modules/rbac/authz.py:45-49`). When the `ent` lookup runs, the org is already known to be active. The natural place for it is inside the same `if data.org_id is not None:` block, right after `org_roles`.
3. **The "active" definition lives in one place:** the `ACTIVE` expression in `/home/user/org-admin-api/app/modules/entitlements/repository.py:14` (`enabled AND org active AND app active`). The repository has `find(org_id, app_id)`, but it ignores `ACTIVE`, so it would wrongly include rows where `enabled=false`.
4. **Claim size is bounded.** Plan matches `^[a-z0-9][a-z0-9_-]{0,62}$`. `limits` has at most 32 keys, each matching `^[a-z][a-z0-9_]{0,63}$`, with integer values from 0 to 2^53-1 (`entitlements/schemas.py`). That is about 3 KB in the worst case, which is fine for a Bearer header.
5. **TTL:** `access_token_ttl_seconds` defaults to 900 and is capped at 43200 (`app/core/config.py:93`). Production uses 12 h. `consumers.md` items 11–14 (lines 394-406) already describe revocation lag and freshness through `iat`.
6. **Admin app:** entitlements are refused for `admin` (`service.py:69`), and `org_mode=none` apps cannot receive an `org_id` token. So `ent` can only appear on tokens for `org_mode=required` apps. Calechip is currently the only such app.
7. **The `mmlabs-auth` gap already exists.** `consumers.md:420-422` lists an `org_roles` accessor as a follow-up in another repo. `ent` needs the same treatment.

### Recommendations
- **R-1 (AC4, R7).** Add one repository read that reuses `ACTIVE`, for example `EntitlementRepository.active_for(org_id, app_id) -> (plan, limits) | None`. Call it from `TokenService` only when `data.org_id` is set and only after the `org_roles` check. Do not copy the active predicate into the token module. Calling the repository from the service follows R1, and `TokenService` already uses repositories this way.
- **R-2 (AC4, no-gate decision).** Claim shape: `"ent": {"plan": str, "limits": {str: int}}`. Include it only if the row is active. Otherwise leave the claim out, and never return an error. Every existing issuance outcome must stay byte-for-byte the same, apart from the extra claim.
- **R-3 (AC5, `consumers.md` semantics).** Document these consumer rules:
  - `ent` present: the org was entitled at `iat`, with this plan and these limits.
  - `org` present and `ent` absent: the org was **not entitled** at `iat`. The cause could be no row, a disabled row or a disabled app. Treat it as a denial, not as "unknown". Do not add a rule that rejects the token when `ent` is missing, unlike the `org_roles` rule. The token stays valid for identity and roles.
  - No `org`: `ent` does not apply. Never reuse an `ent` from a different token or org.
  - Unknown `limits` keys must be ignored (additive).
  - A missing limit key falls back to the app's own default, never to "unlimited". This needs owner confirmation (Q2).
- **R-4 (AC5, R11 spirit).** Add these "must not" rules to `consumers.md`:
  - Do not enforce limits in the browser. The SPA may show plan/limits as a UI hint only. Every check runs server-side on every request against the product's own data. The token supplies the cap, not the usage count.
  - Do not cache `ent` beyond the request, and do not extend it into a longer app session (same as item 12).
  - Do not authorize anything from `ent` alone. It is org-level, not user-level, so access still comes from `org_roles` through the catalog.
  - For sensitive or quota-consuming actions, products may require a fresh token (`now - iat > N` → 401 → silent re-mint), as item 13 already allows. Without a pull endpoint, this is the only way to shorten the 12 h staleness.
- **R-5 (AC1).** The ADR "Entitlement consumption" is still required. It should record:
  - the claim, not pull and not push;
  - no gating;
  - absent = not entitled;
  - 12 h staleness and its mitigation (re-mint);
  - R3 class 4 and ADR-0016 unchanged.

  Also tick spec §8 Q4 and add a note to §5.6.
- **R-6 (AC5, R8).** Tests:
  - an org token with an active entitlement carries the exact `ent`;
  - a disabled row produces no `ent`;
  - no row still issues a token (no-gate regression);
  - a token without `org_id` has no `ent`, even if the org is entitled;
  - a suspended org still gets 403 `org_access_denied` (unchanged);
  - after a staff update or revoke, the next mint reflects it;
  - an empty `limits` gives `{}`.

  Mark the AC5 items "class-4 allow-list / audit / rate-limit tests" as N/A under the owner's decision. `test_app_credential_allow_list_is_exact` should stay unchanged, and it is a useful guard that class 4 was not widened. Minting writes no audit entry (R4 covers mutations only).
- **R-7 (R5/R11 logging).** In `token_issued`, at most log `ent=present|absent`, not the plan or limits.
- **R-8 (R7, follow-up).** Add `mmlabs-auth` support in its own repo: an `ent` accessor that returns `None` when the claim is absent, plus a `require_entitled()` / limit helper that runs server-side. Record it next to the existing `org_roles` follow-up line in `consumers.md`. A release is needed before calechip relies on it.

### Risks
- **Rollout ambiguity.** For up to one TTL (12 h) after deploy, live tokens were minted without `ent`. A consumer that enforces "absent = not entitled" right away will deny entitled orgs. `consumers.md` must say to turn on enforcement only after one full TTL has passed since deploy, or to enforce only on tokens with `iat` at or after the release time. Because calechip has no rows yet, it also needs staff to grant entitlements before it enforces anything, or it locks itself out.
- **Staleness.** A revoke or disable takes up to 12 h to reach a token, and there is no server-side fallback (no pull). This is accepted by the owner, but it should be stated in the ADR.
- **Disclosure.** Every org member holding a token can read the org's plan and limits, because a JWT is readable by its holder. This is low sensitivity (no billing data), but limit keys must never carry secrets or customer-confidential terms.
- **Drift with RBAC remains.** A user can hold `org_roles` in an org that is not entitled. That is by design, and products must combine both checks.

### Open questions (for owner)
1. Absent vs explicit `"ent": null` when not entitled. `null` would tell "issuer checked, not entitled" apart from "minted by an older release" and remove the rollout ambiguity. Default recommendation: keep it absent, as the owner worded it, and document the rollout window.
2. Does a missing limit key mean "app default" or "unlimited"? This must be fixed in `consumers.md` before calechip enforces anything.
3. Should the 12 h production TTL be shortened for `org_mode=required` apps if entitlement revocation latency starts to matter? This is outside this ticket; it is a possible follow-up.
