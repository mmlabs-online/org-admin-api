# ADM-002d: Admin auth hardening and runbook fixes

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P2 |
| Areas | auth, security |
| Attempts | 0 |
| Created | 2026-11-15 |
| PR | — |

## Goal
Close the non-gating follow-ups from the ADM-002 / ADM-002c security reviews.

## Scope
Split from ADM-002b (see `work/tickets/ADM-002b/plan.md`).
1. `strict_aud: True` in `app/core/access_token.py`, with list-`aud` tests (`["admin"]`, `["stoktok","admin"]`); the same in `docs/consumers.md` and the mmlabs-auth note.
2. New data migration: `is_admin = true` on all admin-app permissions (documented no-op `downgrade()`, R2 exception). Optionally force `is_admin=true` on API-created admin-app permissions.
3. `set-admin-origins --clear` works on a drifted admin row: skip only the drift check when origins are empty; still lock; write only origins; test + ADR-0008/deploy.md wording.
4. `docs/deploy.md` runbook: Bearer token out of shell history and `ps` (variable, `-H @file` 0600, unset/rm); subshell with `trap` cleanup ending in `/auth/logout`; note that `JWT_PUBLIC_KEYS_EXTRA` holds only this service's own keys.
5. Refuse signup and forgot-password with `X-MM-App: admin`.

## Acceptance criteria
- [ ] Each item above has a test (docs items: reviewer checks the runbook)
- [ ] `make check` green

## Dependencies
ADM-002b.

## Notes / answers from owner
