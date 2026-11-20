# ADR-0017: Entitlement consumption

Status: Accepted (owner, 2026-11-17 and 2026-11-18)
Amended by ADR-0021 (§2: R3 class 4 widened for releases only; still no entitlement pull)
Date: 2026-11-18

## Context
Spec §5.6 says mmlabs services read which org may use which product, with plan and limits
(ADM-006 stores them). Spec §8 Q4 asked how products consume admin decisions: pull, push or both.

## Decision
1. **Claim only.** Products read entitlements from the access token. A pull endpoint is rejected
   for now; push stays spec §5.7 Later.
2. **R3 class 4 and ADR-0016 are unchanged.** `mmak_` keys stay manifest-only and need no scope.
3. **Shape.** `ent: {"plan": str, "limits": {str: int}}`. `limits` is always an object, possibly
   `{}`. No `id`, `enabled`, `active`, `app_key`, `org_id` or timestamps.
4. **Presence.** The key is present if and only if `org` is present. It is an object when the
   entitlement is active (the ADM-006 `ACTIVE` definition: enabled, org active, app active). It is
   `null` for no row, a disabled row or a disabled app, one value for every cause. It is never
   present without `org` and never on `aud=admin` (explicit guard in `TokenService`).
5. **Issuance is never gated on entitlements.**
6. **Staleness** is up to TTL plus leeway (12 h in production). Mitigation: the fresh-token rule
   (consumers.md item 13).
7. **Out-of-contract rows fail closed.** A row whose plan or limits break the ADM-006 contract
   (for example a manual SQL write) yields `null` and a warning without values.
8. **Visibility.** `plan` and `limits` are readable by every org member holding the token and by
   the app's browser code. Staff must not put confidential terms in them.
9. **Size bound.** About 3.8 KB worst case for the claim, fixed by the ADM-006 validation
   (32 keys of 64 characters, values up to 2^53-1, plan of 63 characters).
10. **A missing limit key** means the app's own default (recommended 0 or deny), never unlimited.

## Rejected alternatives
- Pull with an app credential: widens R3 class 4.
- Omitting the claim when not entitled: cannot tell "not entitled" from a pre-deploy token.
- Gating `/auth/token` on entitlements: changes v1 behaviour.

## Consequences
- Rollout window: tokens minted before the release have no `ent` (see consumers.md).
- Follow-ups: `mmlabs-auth` accessor; DB CHECK on the `limits` size; a shorter TTL for
  `org_mode = required` audiences if lag matters; pull or push if 12 h staleness is unacceptable.
