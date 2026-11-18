## Consult: software-architect, ADM-018 (`ent` claim)

Read-only consult. The owner's Q4 answer (in the ticket notes) settles the scope: we add an `ent` token claim, build no pull endpoint, and leave R3 class 4 as it is. Nothing here blocks planning.

### Findings
1. **Where the claim goes.** `TokenService.issue` (`/home/user/org-admin-api/app/modules/tokens/service.py`, around lines 88-97) builds `org` and `org_roles` only inside the `data.org_id is not None` branch, and only after the `org_access_denied` gate. That gate uses `org_role_keys`, which already requires the org to be `active`. So by the time `ent` is built, "org active" is guaranteed, and the `aud` app has passed the `X-MM-App`/Origin check (unknown or disabled app → 403). That makes this branch the natural home for `ent`.
2. **There is one active definition to reuse.** `ACTIVE` in `/home/user/org-admin-api/app/modules/entitlements/repository.py` (line 14) is `enabled AND org active AND app active`. `_select()` already joins App and Organization. There is no lookup yet by `(org_id, app_id)` that applies `ACTIVE`: `find()` returns the ORM row with no active filter.
3. **Layering (R1).** `TokenService` already calls repositories directly (`SessionRepository`, `UserRepository`). A service calling a repository is fine under R1. Going through `EntitlementService` would also construct `AuditLogger`, `OrganizationRepository` and `RbacRepository`, which this path doesn't need.
4. **Shape is bounded by ADM-006 validation.** `plan` matches `^[a-z0-9][a-z0-9_-]{0,62}$`. `limits` has at most 32 keys, each matching `^[a-z][a-z0-9_]{0,63}$`, with ints from 0 to 2^53-1, so values stay JS-safe. The worst case is about 2.8 KB of JSON, roughly 3.7 KB after base64url, which gives a total token of about 4.5-5 KB. A typical one (a plan and a few limits) adds under 200 bytes.
5. **The claims contract allows this.** ADR-0005 §5 says "Adding a claim is not breaking". Both mmlabs-auth Python files pass unknown claims through unchanged: `/home/user/mmlabs-auth/python/src/mmlabs_auth/access_token.py` and `/home/user/mmlabs-auth/python/src/mmlabs_auth/fastapi.py` (`Principal.claims`, kept out of repr). `REQUIRED_CLAIMS` doesn't change, so `tests/test_drift.py` in mmlabs-auth stays green. I found no strict claim schema on the TS side.
6. **The admin audience can't get `ent` today.** `grant()` refuses the admin app and any app with `org_mode != required`. An `org_id` on a `none` app is rejected with 422 `org_not_supported` before the claim is built. So `sid` (admin only) and `ent` (org apps only) are disjoint in practice, but nothing in the code states it.
7. **Docs to touch:**
   - `docs/consumers.md`: the claims list (line 408-411), revocation-lag item 11 (line 394) and the org-mode section (line 413-422).
   - ADR-0005: §5 and the risk-2 claim list.
   - Spec §5.6 note (line 59) and §8 Q4 (line 148, unticked).
   - `identity-architecture.md`, which lists claims (it mentions `org_roles`).
   - The next ADR number is 0017.

### Recommendations
1. **Repository method (R1, single active definition).** Add `EntitlementRepository.get_active_for(org_id, app_id) -> EntitlementClaim | None`. It runs `_select().where(OrgEntitlement.org_id == org_id, OrgEntitlement.app_id == app_id, ACTIVE)` and returns a small frozen dataclass `{plan, limits}` (or reuses `EntitlementView`). Don't write a second "active" predicate and don't shortcut to `enabled` only: the claim and `?active=true` must never disagree. `TokenService` builds `EntitlementRepository(session)` in `__init__`, like its other repositories.
2. **Placement in `issue()`.** Put it inside the `if data.org_id is not None:` block, after `org_roles` is set. Also guard with `app.key != ADMIN_APP_KEY` as an explicit invariant (cheap, and it survives any future `org_mode` change on the admin app). Then `ent` never sits next to `sid`.
3. **Shape: omit the claim, never send null.**
   - An active entitlement gives `"ent": {"plan": "<plan>", "limits": {...}}`. Always include `limits`, even as `{}`, so consumers get a stable shape.
   - No row, or an inactive one (disabled, app disabled), means the claim is left out.
   - This matches the `org`/`org_roles` precedent ("only with"): a missing claim means not entitled, and there is no third state to handle.
   - Don't add `active`, `enabled`, `app_key` or `org_id` inside `ent`. The claim's presence means active, `aud` already names the app and `org` already names the org.
4. **Don't block issuance on entitlements.** A member of an org without an active entitlement still gets the org token, just without `ent`. Enforcing entitlements is the product's job, and refusing would change a v1 `/token` behaviour (R9). Record this explicitly in the ADR (also open question 1).
5. **New ADR-0017 "Entitlement consumption".** It records:
   - the Q4 answer: claim only, push stays §5.7 Later, pull is rejected for now;
   - R3 class 4 and ADR-0016 unchanged, `mmak_` scopes not needed (so ticket AC 3 is answered "no");
   - the shape and omit rule;
   - staleness up to TTL plus leeway;
   - issuance not gated on entitlement;
   - no `ent` without `org`, and never on `aud=admin`.

   Also add a short "ADM-018" amendment to ADR-0005 §5 (new optional claim, verifiers must not require it) and add `ent` to the risk-2 frozen-claims list.
6. **Consumer guidance in `docs/consumers.md`:**
   - Read `ent` only together with `org`. It describes the `org` org for the `aud` app only.
   - Treat a missing `ent` as not entitled (fail closed).
   - Treat `limits` as ceilings and enforce them server-side.
   - A revoke, disable or downgrade reaches a live token only at `exp` (up to 12 h in production).
   - Rollout: tokens minted before the deploy have no `ent`, so consumers should start enforcing at least TTL plus leeway (about 12.5 h) after the release. Otherwise legitimately entitled users are refused.
   - Add `ent` to the claims list and to item 11.
   - mmlabs-auth needs no change. An `ent` accessor is optional and goes on the follow-ups list (R7).
7. **No audit (R4) and no rate-limit change.** Minting a token is not an admin mutation, and `/token` keeps its existing limits. Don't log `plan` or `limits`. If anything is added to the `token_issued` line, make it `ent=true/false` at most.
8. **Ticket AC 5 (`test_app_credential_allow_list_is_exact`).** The test must stay unchanged and green. Note this in the plan: there's nothing to update, which proves class 4 wasn't widened.
9. **Tests.** Extend `tests/test_auth_token.py`, decoding with the test key set:
   - an org token with an active entitlement has `ent == {"plan", "limits"}` with exact values;
   - `limits` `{}` is present as `{}`;
   - a token with no `org_id` has no `ent`, even when the org is entitled;
   - an org token with no entitlement row has no `ent` and still returns 200;
   - `enabled=false` gives no `ent`;
   - an app with `status` disabled at the DB level (only reachable through a direct repository or service test, since `/token` refuses it earlier) gives no `ent`;
   - a suspended org gives the existing 403 `org_access_denied` (no regression);
   - an entitlement of another app for the same org does not leak into this app's token;
   - an admin token never has `ent` and still has `sid`;
   - after a PATCH of `plan`/`limits`, a newly minted token reflects the change;
   - a worst-case size test (32 keys of 64 chars, values 2^53-1) asserts the token stays under a stated budget (e.g. 8 KB);
   - a repository unit test for `get_active_for` covering each `ACTIVE` leg.

### Risks
- **Staleness.** A revoked or downgraded entitlement stays usable for up to 12 h (accepted by the owner). Consumers counting usage against `limits` may overshoot for that long after a downgrade.
- **Token size.** The worst case is about 5 KB. That's fine as an `Authorization` header, but it breaks any consumer that keeps the token in a cookie (4 KB limit) or logs headers. Tightening the ADM-006 limits would be out of scope (R7), so document the bound and keep the size test.
- **Rollout fail-closed window.** A consumer that enforces from day one refuses entitled users who still hold pre-deploy tokens, for up to TTL.
- **Visibility.** `plan` and `limits` become readable by any client holding the token (the SPA, browser extensions). They are not secret today, but staff must not put sensitive data in limit keys.
- **One extra query per org token.** It hits the unique `(org_id, app_id)` index, so it's negligible.

### Open questions (for the owner, non-blocking if the defaults above are accepted)
1. Is the default right that a member of a non-entitled org still gets the token (without `ent`)? The alternative is a new 403 `not_entitled`. That would change `/token` behaviour and needs a v1-compatibility decision.
2. Should a missing `ent` mean "not entitled" in consumer guidance, or should products with no entitlement concept just ignore it? I recommend the first for org apps that sell plans.
3. Should mmlabs-auth get an `ent` accessor now (a follow-up in that repo) or later?

### Relevant files
- /home/user/org-admin-api/work/tickets/ADM-018/ticket.md
- /home/user/org-admin-api/app/modules/tokens/service.py
- /home/user/org-admin-api/app/modules/entitlements/repository.py
- /home/user/org-admin-api/app/modules/entitlements/schemas.py
- /home/user/org-admin-api/docs/adr/0005-access-token-signing.md
- /home/user/org-admin-api/docs/adr/0015-org-roles-claim-and-permission-scope.md
- /home/user/org-admin-api/docs/consumers.md
- /home/user/org-admin-api/docs/spec-v1.md
- /home/user/org-admin-api/tests/test_auth_token.py
- /home/user/mmlabs-auth/python/src/mmlabs_auth/access_token.py
- /home/user/mmlabs-auth/python/src/mmlabs_auth/fastapi.py
