# ADM-018: Service read of org entitlements (`ent` token claim)

| Field | Value |
|---|---|
| State | IN_PROGRESS |
| Priority | P3 |
| Areas | architecture, auth, security, api |
| Attempts | 0 |
| Created | 2026-11-17 |
| PR | — |

## Goal
mmlabs services can read an org's active entitlements for their own app (spec §5.6), once the owner answers spec §8 Q4.

## Spec reference
§5.6 Org product entitlements; §8 Q4; ADR-0016; ADM-006 plan.md (follow-ups)

## Acceptance criteria
- [ ] Owner decides Q4 first (pull with an app credential, an `ent` token claim, or both; push is §5.7 Later); decision recorded in a new ADR ("Entitlement consumption"), amending R3/ADR-0016 if auth class 4 is widened
- [ ] If pull: `GET /api/v1/apps/{key}/entitlements/{org_id}` with the app credential, own app only (another app → 403); single document `{org_id, app_key, active, plan, limits}` built from the ADM-006 active definition; unknown, suspended and not-entitled orgs give one indistinguishable answer; per-IP rate limit; no CORS
- [ ] Decide whether `mmak_` keys need a scope (`manifest` | `entitlements:read`)
- [ ] If token claim: additive `ent` claim only when the `org` claim is present (12 h staleness accepted)
- [ ] R3 class-4 list and `test_app_credential_allow_list_is_exact` updated; audit/rate-limit tests; `docs/consumers.md` section

## Dependencies
ADM-006.

## Notes / answers from owner
- 2026-11-17: created by the orchestrator from the ADM-006 plan follow-ups. Owner decided ADM-006 v1 ships staff endpoints only; this ticket needs the Q4 answer before it can run (planner should return BLOCKED otherwise).
- 2026-11-17 (owner) **§8 Q4**: services consume entitlements through an additive `ent` claim (`{plan, limits}`) in the access token, added only when the `org` claim is present; staleness up to the token TTL (12 h) is accepted. No pull endpoint and no app-credential widening for now (R3 class 4 unchanged). Tick spec §8 Q4 with this answer.
- 2026-11-18 (owner): when the token carries `org` but the org has no active entitlement for the app (none / disabled / app disabled), emit `"ent": null` (the key is always present with `org`; object when entitled). Token issuance is never gated on entitlements.
- 2026-11-18 (owner): a `limits` key the app needs but the entitlement lacks means the app's own default (recommended 0 / deny), never "unlimited" — document in consumers.md.
