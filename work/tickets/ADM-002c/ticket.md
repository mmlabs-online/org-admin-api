# ADM-002c: Set admin app origins from the CLI

| Field | Value |
|---|---|
| State | DONE |
| Priority | P1 |
| Areas | auth, security |
| Attempts | 1 |
| Created | 2026-11-15 |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/26 |

## Goal
Staff can obtain an `aud=admin` token in production: the `admin` app needs a registered Origin for `/api/v1/auth/login` and `/api/v1/auth/token`.

## Scope
Split from ADM-002. New CLI command `set-admin-origins` via a new `AppService.set_admin_origins`: follows D19, refuses loopback in staging/production, refuses origins held by another app, never changes `join_policy`, `org_mode`, status or roles of `admin`. Runbook in `docs/deploy.md`; the ops workflow input in `mmlabs-online/deployment` is a separate PR (owner). Non-credentialed admin CORS only if an admin UI exists by then.

## Acceptance criteria
- [ ] `set-admin-origins --origin …` replaces `apps[admin].origins`; dry-run supported; masked, fixed-line errors like `register-app`
- [ ] Tests: happy path, loopback refused in production, origin held by another app refused, re-run unchanged
- [ ] deploy.md runbook: deploy → sign up/verify → grant-super-admin → set-admin-origins → login with `X-MM-App: admin` → token

## Dependencies
ADM-002.

## Notes / answers from owner
