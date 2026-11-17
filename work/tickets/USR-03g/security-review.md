## Attempt 1

## Security review: USR-03g (join approval flow)

I read the code and git only (`git diff origin/main...HEAD`, commit 14b4956). I did not run `make check` or pytest. The implementer's log says `make check` passed (2180 tests) and that `alembic heads` shows a single head, `a9d4c27e6b13`. I checked the down_revision chain myself and there is one head: only the new file revises `f3a8d51c9e27`.

### Findings

**No 🔴 blockers.**

🟡 **S1 — approve/reject responses return the email without `Cache-Control: no-store`.**
- Where: `app/modules/join_requests/router.py:51-59` and `:67-74`
- Risk: `JoinRequestRead` includes `email` and `display_name`. The list route sets `no-store` (`router.py:39`, "rows carry emails"), but the two decision routes do not. An intermediary or the browser cache could keep the response. The rule is applied unevenly.
- Fix: add `response: Response` to both handlers and set `response.headers["Cache-Control"] = "no-store"` before returning. Assert the header in `test_approve_grants_default_role` and `test_reject_records_decision`.

💭 **N1 — approve/reject show the email under `admin:roles:assign` only.**
- The list needs `admin:users:read`, but approve/reject return the same PII to a caller who may lack it.
- Exposure is small: the request_id is a random UUID that can only be found through the list.
- Option: either document that `admin:roles:assign` implies seeing the subject's email, or drop `email`/`display_name` from the decision responses (the UI already has them from the list).

💭 **N2 — the approve audit row does not link to the role assignment it creates.**
- Where: `app/modules/join_requests/service.py:111-123`
- The grant at `:113` writes no `role_assignment.granted` row, and the `app.join_request_approved` row carries only `role_key`.
- A later `role_assignment.revoked` (target = assignment id) cannot be traced back to this approval without a join on time and user.
- R4 is still met (one row per action, same transaction).
- Suggestion: add `"role_assignment_id": change(None, ra.id)` to `changes` when a grant happens.

💭 **N3 — `join_available` returns true when the default role is not usable.**
- Where: `app/modules/membership/service.py:77-92`
- For `approval` (and `open`, which already behaved this way) it checks only `default_role_id is not None`. If that role holds an `is_admin` permission, `/token` says `join_available: true` but join answers 403 `join_unavailable`. This is a UX mismatch, not a security issue.
- Matches the existing follow-up "`role_has_org_permission` defense-in-depth in `usable_default_role`". The approve path likewise checks only `is_admin`, not org-scope permissions, on the default role. Org tokens use only roles held in exactly that org, so an app-scope default role does not widen org access. The follow-up is enough.

💭 **N4 — a sentence in `docs/consumers.md:353` is incomplete.**
- It says "`join_available` is true for `approval` apps with a default role" without "outside the rejection cooldown". The paragraph just above says it correctly.
- Fix: append "…unless a rejection is inside the cooldown".

💭 **N5 — no index on `join_requests.decided_by`.**
- `ON DELETE SET NULL` from `users` will scan the whole table when a user is deleted. This is rare and the table is small, so it is performance only.
- Add an index later if user deletes become routine (database-optimizer's call).

### Focus areas with no problem found

- **Authz:**
  - All three staff routes are Bearer: router-level `current_admin` plus exactly one `require_permission` each (list: `admin:users:read`; approve/reject: `admin:roles:assign`). This is the same split as `rbac/assignments_router.py`.
  - `_with_409` changes only a fresh copy of the responses dict, so no shared state is mutated.
  - The `admin` key returns 404 before any read or lock (`service.py:66,73`).
  - A request id from another app returns the same 404, because `get_in_app` and `get_in_app_for_update` both filter on `app_id`.
  - The 401/403 parametrized test and `test_route_auth_classes` cover the routes.
- **Approve escalation:**
  - `usable_default_role` refuses a NULL or missing role and one with an `is_admin` permission (409 `default_role_unusable`). `_guard_privileged` still runs inside `_grant_locked`, as defense in depth.
  - The grant is at app scope only (`org_id=None`, no expiry).
  - `granted_by` and `decided_by` come from `actor_user_id(principal)`.
  - Approving your own request gives nothing beyond what `admin:roles:assign` already allows through the assignments route.
  - If the user is already a member, no grant is made.
- **Lock order:** approve locks app (`FOR NO KEY UPDATE`), then the user (`FOR SHARE`), then the request (`FOR UPDATE`, `populate_existing`). Reject locks app, then request. Join locks app, then inserts.
  - Every writer that changes default role, policy, app status or role permissions goes through `lock_app` (`rbac/service.py:403` and others), so approve sees committed values and checks them again under the lock.
  - User disable (`set_status`) locks `admin` app, then the user `FOR NO KEY UPDATE`. It conflicts with the approve `FOR SHARE` only on the user row, so there is no lock cycle.
  - Two concurrent approves are serialized by the app lock; the second gets 409 `not_pending` (tested).
  - A duplicate pending insert is serialized by the app lock. The partial unique index plus the savepoint is a backstop that turns any violation into a 409.
- **Cooldown:**
  - It uses the database clock (`decided_at > now() - interval`); any rejected row inside the window blocks.
  - The invitation path cannot bypass it: `_guard` requires `join_policy = open` (`invitations/service.py:136`). The CLI still refuses approval.
  - A new account with a different email is an inherent limit. The pending cap and deny-list are already follow-ups.
- **Idempotent 202:** a replay returns the existing pending id before the cooldown check, with no write and no audit row (tested). The checks for active/verified user and member status still run first.
- **Enumeration:** join and `/token` only describe the caller's own signed-in account. `join_rejected` reveals no reason, decider or date.
- **PII:** audit rows hold only `status` and `role_key` (target `join_request`, actor id). Logs hold only ids and the app key. `display_name` is flagged as untrusted text.
- **Migration:**
  - Both CHECKs are present (status enum; pending iff `decided_at` is NULL).
  - The partial unique index's `WHERE` text is byte-identical in model and migration.
  - FKs: app and user cascade; `decided_by` is SET NULL, which the CHECK allows.
  - `downgrade()` drops the indexes and the table and is documented as lossy.
  - The constraint tests cover drift that `alembic check` cannot see.
- **Docs:** the claim that `join_approval_unavailable` is gone from HTTP is correct (only the CLI raises it now). Apart from N4, the identity-architecture §2/§3/§4/S8 updates match the code.

### Follow-ups (out of scope)
- Cancel pending requests when a user is disabled; a pending cap per app; expiry of stale requests; retention purge that keeps the newest rejection for the cooldown (already in plan.md).
- Lock the user in the open-join path with `get_for_share` (already listed, security F5).
- Make `join_available` and `usable_default_role` agree on usability, including the org-scope permission check (N3).

**Verdict: PASS with notes.** No exploitable issue was found. S1 is a small should-fix and can go into this ticket or a follow-up.

Files reviewed:
- /home/user/org-admin-api/app/modules/join_requests/{service,router,repository,models,schemas}.py
- /home/user/org-admin-api/app/modules/membership/service.py
- /home/user/org-admin-api/migrations/versions/20261117_a9d4c27e6b13_usr03g_join_requests.py
- /home/user/org-admin-api/docs/consumers.md
