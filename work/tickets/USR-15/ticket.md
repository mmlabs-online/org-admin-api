# USR-15: Join requests hardening

| Field | Value |
|---|---|
| State | MERGED |
| Priority | P3 |
| Areas | api, auth, security, db |
| Attempts | 0 |
| Created | 2026-11-17 |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/57 |

## Goal
Keep the join-request queue (USR-03g) clean and bounded.

## Spec reference
§5.3; USR-03g plan/security-review follow-ups

## Acceptance criteria
- [ ] Disabling a user cancels their pending join requests (in the same transaction as the disable, lock order as `set_status`)
- [ ] A per-app cap on pending requests (from env) → join answers a defined error when the cap is reached
- [ ] Retention: cleanup CLI purges decided requests older than a configurable age, keeping the latest rejection per (app, user) while the cooldown applies
- [ ] Open join (`join_policy=open`) reads the user with `get_for_share` (lock) like approve does
- [ ] Tests for each, plus audit rows where state changes

## Dependencies
USR-03g.

## Notes / answers from owner
- 2026-11-17 (owner): create from the USR-03g follow-ups.
- 2026-11-18 (orchestrator): appsec 🟡 (cleanup must use the API's JOIN_* env) checked read-only in `mmlabs-online/deployment` `apps/org-admin-api/compose.yml`: `org-admin-api` and `migrate` (used for `cleanup`) share the same `x-app` anchor (same `env_file` + `environment`), so the cooldown/retention values match. No change made in the deployment repo.
