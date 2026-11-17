# ADM-006: Org product entitlements

| Field | Value |
|---|---|
| State | PLANNING |
| Priority | P2 |
| Areas | architecture, db, api |
| Attempts | 0 |
| Created | 2026-11-10 |
| PR | — |

## Goal
Admins can enable/disable a product for an org with a plan and limits; mmlabs services can read an org's entitlements.

## Spec reference
§5.6 Org product entitlements (depends on USR-02 apps registry; consumption model depends on §8 Q4)

## Acceptance criteria
- [ ] Entitlements table + migration (org, app, plan, limits, enabled), unique per (org, app); apps come from the USR-02 registry
- [ ] Endpoints to grant, update, revoke and list an org's entitlements
- [ ] Read endpoint suitable for services: an org's active entitlements
- [ ] Tests: grant/revoke, duplicate 409, unknown org/product 404, suspended org behaviour as defined in the plan

## Notes / answers from owner
- 2026-11-17 (owner): v1 ships staff (Bearer) endpoints only. The service read path (spec §8 Q4: pull with service keys / push / both) is deferred to a follow-up ticket; the AC "read endpoint suitable for services" is met in v1 by a staff read of an org's active entitlements.
