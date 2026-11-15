# ADM-002b: Enforce admin RBAC guards and mount the users/RBAC APIs

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P1 |
| Areas | auth, security, api |
| Attempts | 0 |
| Created | 2026-11-15 |
| PR | — |

## Goal
Make every `# ADM-002 guard` site real, close the known escalation paths, then remove the two admin API flags so the users and RBAC staff APIs are available in production behind Bearer auth.

## Scope
Split from ADM-002 (see `work/tickets/ADM-002/plan.md` and its consults).
- Pass the actor and its effective admin permissions from routers into services (R1).
- `admin:*` required for: anything on the `admin` app (including origins), creating or flipping `is_admin` / `<app>:*` permissions, putting an `is_admin` permission into a role, granting or revoking a role that holds an `is_admin` permission.
- No self-grant / self-revoke of admin-app roles; `granted_by` = actor.
- 409 for an `is_admin` grant onto a role that already has assignments (USR-02d member-role escalation).
- `admin:*` allowed only on `admin:super-admin`.
- User disable: last-super-admin guard under `lock_app("admin")`, refuse self-disable, revoke the user's device sessions (USR-02c F2).
- API create/update of apps refuses origins another app holds, including `admin` (USR-03b F4).
- Remove `ADMIN_USERS_API_ENABLED`, `ADMIN_RBAC_API_ENABLED` and `_admin_apis_gate`; mount the routers unconditionally.
- Read-only audit of `admin:` rows and admin-app assignments created while the flags were on (USR-02c F3); output in impl-log.
- Owner decision on `/docs` / `/openapi.json` in production.

## Acceptance criteria
- [ ] Each guard site has a test for the allowed and refused path
- [ ] Self-grant, `is_admin` 409, `admin:*`-only-on-super-admin, last-super-admin and self-disable tests
- [ ] Flags removed; route-enumeration test from ADM-002 still green with all routers mounted
- [ ] The escalation guards (`rbac/service.py` guard sites) land with tests in the **same change** that removes the flags (ADM-002 security review 🟡3: today a holder of `admin:roles:assign`/`admin:roles:manage` could grant themselves super-admin if the routers were mounted)
- [ ] `make check` green

## Dependencies
ADM-002.

## Notes / answers from owner
- Carried from ADM-002 security review: 🟡1 `strict_aud: True` in `app/core/access_token.py` (+ list-`aud` test cases, and the same in `docs/consumers.md` / mmlabs-auth); 🟡2 seed migration should `DO UPDATE SET is_admin = true` on existing rows (new migration, local DBs only); note in deploy.md that `JWT_PUBLIC_KEYS_EXTRA` must hold only this service's retired/next keys (they can verify admin tokens).
