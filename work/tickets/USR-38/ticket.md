# USR-38: Designated per-app super-admin role for org apps (requested by simap SIM-043)

| Field | Value |
|---|---|
| State | IN_PROGRESS |
| Priority | P2 |
| Size | M |
| Areas | architecture, db, api, auth, security |
| Gate | plan-approval |
| Depends on | — |
| Needs Ming | OQ80, OQ81, OQ82, OQ83, OQ90 (spec §8; answer at the plan gate) |
| Attempts | 0 |
| Created | 2026-11-26 |
| Claimed by | claude@2026-11-26T04:25Z |
| PR | — |

## Goal
Staff can designate one app-scope role of an `org_mode = required` app (first: `simap:super-admin`) as that app's super-admin role and grant it only to mmlabs staff super-admins, audited — the foundation for staff access to every org of the app (USR-39, USR-40).

## Scope reference
- `docs/spec-v1.md` §5.16 (owner, 2026-11-26): decided — per-app role of the target app, never `admin:super-admin` in an app token; generic for `org_mode = required` apps. Designation and grant rules are proposals (OQ80–OQ83).
- Cross-repo requester: simap SIM-043 (simap D73).
- Needs ADR-0030 + decision-log D42 (amends ADR-0008 privileged-role rules and ADR-0013 / ADR-0015 org-token rule); written in this plan and covering USR-39 / USR-40.

## Scope
- Designation (proposed, OQ80): nullable `apps.super_admin_role_id` (role of the same app; only `org_mode = required`; never the `admin` app; distinct from default and org-owner roles); set by staff with `admin:*`; migration with working `downgrade()`.
- Manifest can neither declare, change, delete the designated role nor change its permissions or the designation; deleting the designated role is refused; changing `org_mode` away from `required` while designated is refused.
- The designated role is privileged because of the designation (not because of its permission content): granting needs `admin:*`.
- Grant rules (proposed, OQ81): app scope only (`org_id` → 422); recipient must hold an active app-scope `admin:super-admin` at grant time (422, reason set at PLAN); self-assignment forbidden; refused by the USR-36 bulk route at request level; audited as `role_assignment.granted`.
- Lifecycle (OQ82, proposed: mint-time re-check in USR-39, no cascade revoke) and expiry (OQ83, proposed optional) settled at PLAN.

## Out of scope
- Token minting (USR-39), org picker (USR-40), `admin:super-admin` in any app token, impersonation.

## Acceptance criteria
- [ ] Staff with `admin:*` can set and clear an app's super-admin role designation; refused for the `admin` app, for `org_mode != required`, for a role of another app, and without `admin:*` (403); change is audited.
- [ ] Granting the designated role to an account without active `admin:super-admin` is refused; granting it to a staff super-admin succeeds and writes `role_assignment.granted`; org-scoped grant and self-assignment are refused.
- [ ] The manifest sync cannot create, alter, delete or re-permission the designated role, and cannot change the designation.
- [ ] The designated role cannot be deleted; `org_mode` cannot leave `required` while designated.
- [ ] The bulk route (USR-36, if merged) refuses the designated role at request level.
- [ ] Migration up/down works; `alembic check` clean; ADR-0030 and D42 written.

## Technical notes
- Files: apps / rbac models, service (`_guard_privileged`, `role_is_privileged`, `user_has_privileged_assignment`, `_grant_locked`), apps PATCH, manifest sync, migration, ADR, tests.
- Architect consult (2026-11-26): column modelled on `org_owner_role_id` (composite FK to same app + CHECKs); keep `is_system` false; check `admin:super-admin` with a plain read under the app lock — never take the `admin` lock from the grant path (lock-order / deadlock, ADR-0003 §10).
- **Spec vs consult conflict for the plan gate:** the spec proposes the role holds all the app's permissions (org-scope included); the architect recommends refusing `scope = org` permissions on it so they never leak through the `roles` union (ADR-0015 risk) and consumers grant powers from the opt-in claim instead. Ming decides at PLAN (OQ80 / OQ88).
- Aligns with USR-25 (wider self-assignment ban) and USR-36 (bulk refusal).

## Test plan
- API tests per criterion against the Postgres test DB; migration up/down.

## Notes / answers from Ming
- 2026-11-26 (Ming, chat): "duyệt hết; câu 1–32 theo mặc định; cho merge USR-40".
- Plan approved (2026-11-26, batch 2026-11-26-1); all blocking questions answered with the plan defaults.

## Handoff
