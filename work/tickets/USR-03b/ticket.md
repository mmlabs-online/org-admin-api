# USR-03b: Accounts list and join flow (API)

| Field | Value |
|---|---|
| State | DONE |
| Priority | P1 |
| Areas | api, auth, security, db |
| Attempts | 1 |
| Created | 2026-11-11 |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/24 |

## Goal
Apps can list the accounts signed in on the device and let a user join the app ("Continue as <account>"), so a verified user can get an app token. The client libraries moved to USR-08 (separate repo).

## Scope
`GET /api/v1/auth/accounts`; `POST /api/v1/auth/apps/{key}/join` with `join_policy` (open / approval → `join_requests` / closed); integration docs for apps calling the API directly. Libraries → USR-08.

## Spec reference
§5.3 Membership, §5.8; identity-architecture.md §2, §4, §6; decisions D4, D8, D9

## Acceptance criteria
- [ ] `GET /api/v1/auth/accounts` (cookie) lists the device's active account sessions: account_id, email, email_verified, display name; never another device's
- [ ] `POST /api/v1/auth/apps/{key}/join {account_id}` (cookie + `X-MM-App` + Origin check): verified email required (403 `email_unverified`); `open` → assigns the app's default role at app scope (`org_id NULL`), idempotent; `approval` → creates a pending `join_requests` row (202) + staff endpoint to approve/reject; `closed` → 403; audit event emitted (recorded via an interface until ADM-004 lands)
- [ ] `docs/consumers.md`: accounts list + join UX ("Continue as <account>" = consent), with plain `fetch` examples (no library yet)
- [ ] Tests: accounts list (own device only, prev-value cutoff), join per policy, idempotent join, unverified → 403, closed → 403, approval → 202 + staff approve/reject

## Dependencies
USR-03, USR-03c (token, JWKS), USR-03e (browser CORS) and USR-02.

## Notes / answers from owner
- 2026-11-13 (USR-07 security review S1): `register-app` adding a permission can set a NULL `default_role_id` back to `<key>:member` (via `_build_for_app` → `set_default_role_if_null`) silently; docs say the default role never changes. Before join (this ticket) relies on `default_role_id`, either keep a NULL default (restore after rebuild) or print a `~ default_role` change line and fix the docstring and `docs/deploy.md`.
- 2026-11-12 (USR-03c security review S1/S2): `mmlabs-auth` must use a flushable JWKS cache (no `cache_keys=True`), refetch on unknown `kid` at most once a minute (`cooldown_duration=60`), and the emergency key-rotation runbook must include flushing every consumer's JWKS cache.
- 2026-11-12 (USR-02c security review F4): the join flow writes `role_assignments`, so it must take `lock_app` first.
- New ticket (2026-11-11).
- From the USR-02d security review: the join must read `apps.default_role_id` under `FOR SHARE`/`FOR KEY SHARE` on the app row (or a single `INSERT … SELECT default_role_id FROM apps …`), otherwise a concurrent default-role switch + PUT can grant admin permissions through join. Where the libraries live (this repo `clients/` vs separate repos) and how they are published (npm/PyPI private?) — decide in the plan; may need the owner (BLOCK if it changes repo layout).
- This ticket is large; the planner should propose a split (e.g. API part / TS client / Python package) if over ~400 lines.
- 2026-11-12 (USR-03 security review N1): `GET /accounts` must apply `accounts_created_before` when the previous cookie value is used.
- 2026-11-13 (owner): do the join API first; the TS and Python libraries move to **USR-08** and will live in a **separate repo** for the `@mmlabs` libraries (owner creates it).
- 2026-11-13 (planner): approval flow split to USR-03g (plan O1, owner to confirm); `approval` apps answer 403 `join_approval_unavailable` until then.
