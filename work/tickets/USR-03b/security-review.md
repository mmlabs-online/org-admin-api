## Attempt 1

# Security review: USR-03b, attempt 1 (appsec-engineer)

**Verdict: PASS. No 🔴 blockers.** The cookie-class binding, the path key matching the header, the admin refusal, the lock ordering, the admin-permission guard on the default role and the N1 cutoff are all correct and covered by tests. There are four 🟡 follow-ups and four 💭 notes.

I read the diff (`git diff origin/main...HEAD`), the ticket, the plan, the security-architect and identity-access-engineer consults, and the code paths it touches: `auth/deps.py`, `sessions/service.py`, `rbac/service.py` (`_grant_locked`, `register`, `update`), `rbac/repository.py`, `db/session.py`, and FastAPI 0.162.2 `routing.py` (exit-stack behaviour). I ran no tests, to avoid clashing with the reviewer's run on the shared DB (`test_join_error_after_rotation_rolls_back` commits).

## What I checked and found sound
- **R3 cookie class on both endpoints** (`app/modules/auth/router.py:208-271`). The order is `rate_limit`, then `AppContextDep` (X-MM-App plus a registered Origin, active app), then `DeviceDep`. A bad app context returns 403 before any device lookup or rotation, so a top-level cross-site GET navigation to `/accounts`, which cannot set `X-MM-App`, never reaches the cookie. CORS tests cover preflight for registered and foreign origins (`tests/test_auth_cors.py`).
- **Path key must equal the header** (`app/modules/membership/service.py:48`). This is an exact string comparison against the DB key, and it runs before the admin check and the lock, so app A cannot enroll users into app B. Tested.
- **Admin refusal** (`membership/service.py:50`). It runs before any policy read, so it holds even if `admin` were PATCHed to `open` (tested in `test_join_admin_refused_even_if_open`).
- **Lock ordering and the default-role race** (`membership/service.py:53, 77-87`):
  - `lock_app` (`FOR NO KEY UPDATE` with `populate_existing`) runs before the account, role and assignment reads. `default_role_id` is read from the locked row.
  - `role_has_admin_permission` is rechecked under the lock (defence in depth on top of the `rbac/service.py:259-268` and `:642` guards).
  - The statement-order test asserts the lock comes first.
  - Lock order: device row, then apps (`NO KEY UPDATE`), then users (FK `KEY SHARE`). This agrees with ADR-0003 §10. I found no `FOR UPDATE` on users anywhere, so it cannot deadlock against existing paths.
- **Default-role escalation via the CLI (USR-07 S1) is fixed** (`rbac/service.py:183-208, 414-415`). A NULL default stays NULL on a permission add, and only `create()` sets `<key>:member`. Tested in `test_null_default_role_kept_after_permission_add`.
- **N1 cutoff.** Both `list_active_accounts` (`sessions/repository.py:150-167`) and join's `get_active_account(created_before=…)` apply `accounts_created_before`. Tested for both endpoints.
- **Cross-device access.** Account lookups are scoped by `device_session_id`, and revoked accounts and disabled users are excluded or answer 401. `user_id` is never returned. Tested.
- **Cookie rotation and rollback.** `Set-Cookie` is sent only on success. Any error, including the 422 from body validation, is raised inside FastAPI's function exit stack, so `get_session` rolls the rotation back and the client's old value stays valid. A test covers this for a service error.
- **no-store** is set on both 200 responses (`router.py:231, 269`) and tested. Rate limits `accounts` (60/60 s) and `join` (20/60 s) are configured from env and listed in `.env.example` without values (R5).
- **Mass assignment.** `JoinRequest` uses `extra="forbid"` and accepts only `account_id`; `role_id` in the body returns 422 (tested).
- **`/token` `join_available`** (`tokens/service.py:79-83`) is now `open AND default_role_id IS NOT NULL`, so it no longer advertises a join that would fail.
- **Audit and logging** (`audit/recorder.py`, `membership/service.py:86, 90-97`). Only ids and the app key are logged, never emails, tokens or cookies. A test asserts neither the email nor the raw cookie appears in the logs.
- **Docs examples** (`docs/consumers.md`, `docs/identity-architecture.md`). They contain no secrets, real emails or tokens. They state that consent is the click and that the path key must equal `X-MM-App`.

## Findings
- 🟡 **Rate limit for join is per IP only** (`router.py:248`, `core/rate_limit.py:92`). The security-architect consult recommended per IP and per account. A compromised or malicious registered app origin can silently join every account on the device into itself: the server cannot check the "click = consent" step in D8, and `/accounts` hands it every `account_id`. Each enrolment then allows `/token`.
  - Fix (follow-up): add a per-`(device_session_id)` or per-`user_id` join limit, and consider listing app memberships per user so people can see where they have joined.
- 🟡 **Unmasked emails in `/accounts` for every account on the browser** (`router.py:233-243`, `auth/schemas.py:86-90`). XSS in any registered app exposes the emails of all accounts on the device, including accounts never used with that app. The plan defers this to the owner (O3) with "unmasked" as the default. It must stay an explicit owner decision. If the owner wants it, the fix is to mask emails for accounts with no membership in the calling app.
- 🟡 **A revived expired assignment becomes permanent** (`membership/service.py:89` through `rbac/service.py:791-793`). If staff granted the default role with an expiry and it lapsed, a re-join under `open` writes `expires_at=None`. This is documented in identity-architecture §7 and is acceptable under `open` semantics. A deny-list or ban for staff-removed users remains an open follow-up (consult open question).
- 🟡 **Origin exclusivity is enforced only in `register`**, not in admin `PATCH /apps` (pre-existing; the security-architect raised it). The app_mismatch check relies on Origin-to-app binding. This check is still sound, because the path key is compared with `X-MM-App`, which the binding already ties to the Origin. But two apps sharing an origin would let either one act as the other. Follow-up: enforce exclusivity on PATCH too.
- 💭 **The audit line is written before commit** (`membership/service.py:90`). If the commit fails, the log claims a join that did not happen. This is acceptable for the placeholder; ADM-004's in-transaction insert fixes it.
- 💭 **A default role whose key lacks the `<app>:` prefix** would be filtered out by `effective_roles` (`rbac/authz.py:42`). A second join would then hit `_grant_locked` and answer 409 `ConflictError` instead of an idempotent 200. This fails closed and grants nothing extra; it is only a data-hygiene case.
- 💭 **No test for 422 with a rotated cookie.** The mechanism is verified in FastAPI's code, but a regression test would pin it down.
- 💭 **CLAUDE.md R11 still describes the cookie as `mm_device; Path=/api/v1/auth`**, while D20 and the code use `__Host-mm_device; Path=/`. This predates the ticket. Follow-up: update CLAUDE.md.

## Follow-ups (out of scope)
- Per-account or per-device join throttle, and a membership view the user can see.
- Owner decision O3 on masking emails in `/accounts`.
- Origin exclusivity on admin `PATCH /apps`.
- A ban or deny-list for re-joins after staff removal (USR-03g or later).
- Sync CLAUDE.md R11's cookie name and path with D20.
