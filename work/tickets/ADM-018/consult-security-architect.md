## Security consult: ADM-018 (`ent` {plan, limits} claim)

I read these files: the ticket, spec §5.6/§8 Q4, `docs/identity-architecture.md` (D7, S7, §5), `app/modules/tokens/service.py`, `app/modules/entitlements/{models,schemas,repository}.py`, `app/modules/rbac/authz.py` and `docs/consumers.md`.

### Findings
1. **Integrity is fine.** The claim is signed with ES256 and has a `kid`. `TokenService.issue` builds `claims` on the server, and `org_id` is the only input from the request, which `org_role_keys` already checks against the exact org, unexpired assignments and an active org. `ent` is not a registered JWT claim name, so there is no collision. The one thing to check: `TokenRequest` must keep `extra="forbid"` so a client can never send `ent`.
2. **Exposure (Low/Informational).** The token sits in the browser SPA, and JS can decode it. Every org member who gets a token for that app will see the org's `plan` slug and `limits`, including low-privilege members. This includes the developer tokens that people copy into scripts (consumers.md:123). It only exposes that org's own entitlement, which is usually fine. The risk is commercial: staff might put sensitive labels into `plan` (for example `acme-discount-50`) or internal-only limit keys. The browser app's JS can also read it.
3. **Cross-app and cross-org leakage is safe by construction, if the plan holds to it.** Look up the claim only by `(org_id = claim org, app_id = token aud app)`, and only after the `org_roles` check passes. Never add entitlements for other apps or other orgs. Admin tokens can never carry `org`, because the admin app has `org_mode=none` and `OrgNotSupportedError` fires, so they never carry `ent` either. A test should pin this.
4. **Staleness window (Medium business risk, accepted by the owner).** A disable or downgrade reaches the app only when the token expires: up to 12 h plus 30 s leeway in production (S7). A member can mint a token just before a downgrade and keep the higher limits for up to 12 h. Backends cannot refresh on their own, because `/auth/token` uses the cookie flow, and there is no pull endpoint. An org suspension takes effect at the next token, since `org_role_keys` refuses it. A token that is already issued still runs to `exp`, the same as roles today.
5. **"Active" must use the single definition.** `repository.ACTIVE` = enabled AND org active AND app active. If the claim used `enabled` alone, it would drift from the staff view.
6. **Size and DoS (Low).** The limit of 32 keys, the 64-character keys and the `le=2**53-1` values are enforced only by Pydantic (`schemas.Limits`). The DB CHECK only requires a JSON object, so a CLI job or manual SQL could store more. At the worst case the API allows (32 × ~85 bytes plus a 63-character plan), `ent` is about 2.8 KB of JSON, or about 3.8 KB after base64url. On top of `roles` and `org_roles` that makes a token of roughly 5–6 KB, which is close to the per-header limits common in proxies and app servers (often around 8 KB). It is bounded, but nothing protects against values that arrive outside the API.
7. **Absent claim vs. not entitled.** If `ent` is simply left out when the org has no active entitlement, consumers cannot tell "not entitled" apart from "token issued before ADM-018 deployed" or "consumer misread the token". That ambiguity can make an app fail open.

### Recommendations
- **R-1 (AC "additive `ent` only when `org` present"; R3 unchanged).** When `org` is present, always emit `ent`: either `{"plan", "limits"}` for an active entitlement, or `null` for none, disabled, or an inactive app. Never emit `ent` without `org`. Do not include `id`, `enabled`, `active` or timestamps; `null` alone hides why there is no entitlement. Reuse `ACTIVE`, through a repository method such as `active_for(org_id, app_id)`, to keep the R1 layering. Do not refuse to issue the token when there is no entitlement; that stays the app's decision (see Open questions).
- **R-2 (data minimisation, R11).** Do not put `ent`, plan or limits in the `token_issued` log line. Add a note to the staff-facing description in the entitlements docs: "`plan` and `limits` are visible to every member of the org and to the app's browser code; do not put confidential commercial terms in them."
- **R-3 (DoS, defense in depth; R7: no schema change in this ticket).** When the claim is built, refuse to emit an out-of-contract `limits` (more than 32 keys, a bad key pattern, or a value outside 0..2^53-1). Log the entitlement id only and fail closed (`ent: null`), or raise. Propose a DB CHECK on the key count as a follow-up, not as part of this diff.
- **R-4 (tests, R8).** Add tests for:
  - an active entitlement, so the claim equals the DB values;
  - disabled, missing and app-disabled cases all giving `ent: null`, the same shape for each;
  - no `org`, so no `ent` key;
  - an org token for app A when the org is entitled only to app B, giving `ent: null` (no cross-app leak);
  - an admin token never carrying `ent`;
  - a request body containing `ent`, which gets 422;
  - a worst-case `limits` (32 × 64-character keys with maximum values), checking token size stays under about 8 KB.
- **R-5 (`docs/consumers.md` section, AC5).** It should say:
  - `ent` is authoritative only after full verification on the backend (signature, `aud` = your app, `iss`, `exp`, `typ`), and only together with the `org` claim. Scope all enforcement to `org`.
  - The browser may decode `ent` for display or UX hints only. Never trust plan or limits that the client sends back as a value.
  - Enforce limits on the server against your own usage counters; the claim is a ceiling, not a counter.
  - `ent: null` or a missing `ent` on an org token means not entitled. Fail closed.
  - Changes reach tokens only at `exp` (up to 12 h in production). Use `iat` for the snapshot age. For high-value actions, require a token younger than N minutes, or keep the operation reversible.
  - Limit values are integers ≤ 2^53-1, so they are safe in JS.
  - Unknown limit keys must be ignored, and a missing key must be treated per app policy (recommended: deny or 0).
  - Add a follow-up for `mmlabs-auth`: an `ent` accessor that returns nothing unless `org` is present.
- **R-6 (AC1).** Record the decision in the new ADR, including the accepted 12 h window, that the plan is visible to org members, and that R3 class 4 stays unchanged. Tick spec §8 Q4.

### Risks
- **Downgrade or disable abuse for up to 12 h** (residual, accepted by the owner). Impact depends on what the limits gate. If a limit gates paid resources, the cost exposure is up to 12 h per org. A pull endpoint or push (§5.7) is the only real fix.
- **Commercial or internal information shown to all org members** through plan slugs and limit keys. This depends on staff discipline (R-2).
- **Consumers enforcing in the browser, or failing open** when `ent` is missing. This is mitigated only by documentation and the `mmlabs-auth` helper.
- **Token growth** if the 32-key limit is bypassed outside the API. This could break the `Authorization` header at proxies, which would be a self-inflicted outage for that org.

### Open questions
1. Should the `ent` value for an inactive entitlement be `null` (recommended) or should the claim be left out? This needs an owner or planner call, because consumers.md will specify it as a contract.
2. Should token issuance be refused when an `org_mode=required` app has no active entitlement for the org (fail closed at the identity layer), or is that left to each app? I lean towards leaving it to the app, to keep the change additive and keep v1 non-breaking.
3. Are `plan` and `limits` considered non-confidential for org members? If not, `ent` should carry only the limits the app needs, or the pull design should be revisited.
4. Should high-impact apps get a shorter TTL for org tokens to narrow the 12 h window? The TTL is global today, so that would be a separate ticket.
