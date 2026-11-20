# ADM-044: App-admin usage actions through limits:write with on_behalf_of

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P3 |
| Size | M |
| Areas | auth, api, security |
| Gate | plan-approval |
| Depends on | ADM-040, ADM-041, ADM-042 |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-20 |
| Claimed by | — |
| PR | — |

## Goal
Each app's own admins adjust their users' limits and their app's defaults from inside the app, without admin-pan.

## Scope reference
`docs/spec-v1.md` §5.14 Usage limits and credits per user and app (owner answers 2026-11-18 and 2026-11-20; OQ51–OQ69). OQ52 (who adjusts), OQ61.

## Scope
- App-credential routes with a `limits:write` key and `on_behalf_of: <user_id>`; the API re-checks that this user holds `<app>:usage:manage` through an app-scope assignment (org-scoped admins refused).
- Allowed: that app's metric defaults/periods and per-user overrides (reason required); audited as `SystemActor("app:{key}/{key_id}")` with `on_behalf_of`.

## Acceptance criteria
- [ ] Allowed with the permission, 403 without it, 403 for an org-scoped holder, 403 for another app's metrics.
- [ ] Audit rows name the app key and on-behalf-of user id.

## Notes / answers from Ming

## Handoff
