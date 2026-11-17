## Attempt 1

# Security review of USR-06b (org invitations), attempt 1

I read the diff `origin/main...HEAD` on branch `claude/blissful-wozniak-fx4lm9`, the plan and the security consult. As asked, I did not run `make check` or pytest.

There are no blockers. Two issues should be fixed, and there are a few notes.

## Findings

### 🟡 S-1. A throttled re-invite returns the new role but the old invitation stays live
**Where:** `/home/user/org-admin-api/app/modules/invitations/service.py:186-197` (the silent return happens before `supersede_pending` at :200).

**Risk:**
- An inviter who corrects a mistake within 60 s gets 201 with `role` set to the new role. Example: they invite as owner, then re-invite the same address as viewer.
- Nothing is written in that case. The earlier invitation, with the higher-privileged role, stays pending and accepts normally.
- The same happens once the target's 3/24 h budget is used up. That budget is shared across all orgs, so an outsider can use it up.
- So the response misstates what the invitee will receive, and an attempt to downgrade fails silently in the more permissive direction.

**Fix:** in the target-limit and cap branches, still revoke this org's own pending invitations before returning the silent response.

```python
if <target_limit or cap reason>:
    for old_id in await self.repo.supersede_pending(org_id, app.id, email_hmac):
        await self.audit.record(AuditAction.ORGANIZATION_INVITATION_REVOKED, ..., changes={"reason": "superseded"})
    return await self._silent(org_id, role, reason), None
```

- This reveals nothing about other orgs: the rows belong to the caller's own org, and the response stays identical.
- It fails safe: the invitee holds no live link, and the inviter retries later.
- Add a regression test: invite as owner, re-invite as viewer within 60 s, then the owner token gives 422 `token_invalid`.

### 🟡 S-2. Hitting the global invite share is silent, so two accounts can turn invitations off for every app for 24 h without anyone noticing
**Where:**
- `/home/user/org-admin-api/app/modules/invitations/service.py:195-197`
- `/home/user/org-admin-api/app/modules/auth/email_throttle.py:14` (`INVITE_CAP_PERCENT = 10`, which is 30 per day at the default cap of 300)
- `/home/user/org-admin-api/app/core/config.py` (`invitation_max_per_inviter_per_day = 20`)

**Risk:**
- Any verified user of an open app can create an org (they become owner, holding `org:invite`) and send 20 invites a day. Two such accounts use up the whole invite share for every app.
- After that, every legitimate invite returns 201 and no mail is sent. Only the `invitation_not_sent` log line shows it.
- Verify and reset are not affected (I checked the floors), so this is an availability and integrity-of-response problem only.

**Fix:** the global cap (`daily_cap` / `purpose_cap`) says nothing about the target, so it does not need to be silent.
- Answer 429 when the cap is hit, using the existing pattern: `DAILY_CAP_RETRY_SECONDS`, `Retry-After`, code `invitations_unavailable` or `rate_limited`.
- Keep silence only for the per-target limit.
- Also lower the per-inviter default (for example 5) so that one cheap account cannot take a third of the share.

If the owner wants to keep the current behaviour, record it in ADR-0014 §9 as an accepted risk.

### 💭 N-1. A time-limited org role can create permanent access
**Where:** `service.py:305-312`, which grants with `expires_at=None`.

A user holding an expiring org role that includes `org:invite` can invite a second address they control, using a role within their permissions. They can also invite their own address with a different role that has a subset of their permissions. Either way, the result is a permanent assignment. The check at accept time only confirms that the inviter's authority is still live at that moment.

This is inherent to delegation, but it does bypass staff-set expiry.

**Follow-up:** cap the invited assignment's `expires_at` at the latest expiry among the inviter's assignments that grant `org:invite`. Alternatively, do not count expiring assignments as authority to invite.

### 💭 N-2. The S1 guard assumes the owner role holds every org-level permission
**Where:** `service.py:124-141`.

If staff configure a sparse owner role, for example only `{org:create, org:invite}`, the overlap check passes even when `<app>:member` carries `projects:delete`. A "viewer" invitee then gets those permissions in the org token. The guard fails open in that one configuration.

**Fix:** document the precondition "the owner role is a superset of all org-level permissions" in consumers.md (staff configuration) and in ADR-0014 §11. The real fix stays in USR-06c.

### 💭 N-3. Weak side channels on create (cross-org information only, not account existence)
- **Timing:** the silent path skips 2–3 inserts, the audit write and the org fetch, so it is measurably faster.
- **Counter:** silent invites do not count towards `count_created_by_24h`.

Either can tell an inviter that someone else invited this address in the last minute, or 3 times in the last 24 h. They say nothing about whether the account exists or is already a member. Plan A7 accepts this.

The per-target budget is also a griefing vector: 3 invites a day from any org block real invites to a victim. S-1 limits the damage.

### 💭 N-4. Any member with `org:invite` can revoke another member's pending invite to the same address
**Where:** `supersede_pending` in `repository.py:37-53`.

A lower-privileged member can kill an owner's pending invite to the same address by re-inviting it with a lesser role. The effect stays inside the org. Mention it in consumers.md or ADR-0014.

### 💭 N-5. `safe_org_name` can be bypassed in a few ways
**Where:** `/home/user/org-admin-api/app/modules/invitations/rules.py:234`.

`_DOMAIN_LIKE` only catches ASCII dot plus an ASCII TLD. It does not catch:
- `evil . com` (spaces);
- `evil。com` and `evil．com` (U+3002 and U+FF0E dots);
- non-Latin TLDs such as `.рф`.

Gmail may turn some of these into links. Low risk, since the subject is fixed and the name appears inside quotes. **Fix:** normalize with NFKC first, then match `\w\s*[.。．｡]\s*\w{2,}`. This can also go to the `AuthOrgCreate.name` follow-up.

### 💭 N-6. The migration docstring is wrong about lock time
**Where:** `/home/user/org-admin-api/migrations/versions/20261117_c5e91d3a7b24_usr06b_org_invitations.py:11-12`.

The docstring says "NOT VALID then VALIDATE … holds no long lock". `DROP CONSTRAINT` takes ACCESS EXCLUSIVE, and that lock is held until commit. `VALIDATE` runs in the same transaction, so the split saves nothing. `email_send_log` is small, so there is no real impact. Fix the docstring wording.

### 💭 N-7. A race on the partial unique index would give 500, not 409
**Where:** `repository.py:55-78` (`add`).

The plan says an `IntegrityError` on `uq_org_invitations_pending` maps to 409, but `add` does not catch it. The race cannot happen today: `lock_app` (FOR NO KEY UPDATE) serializes every create and accept for an app, and the per-email advisory lock adds to that. If the app lock is ever removed, this would surface as a 500. Either map it to `ConflictError`, or add a comment that the app lock is what prevents it.

### 💭 N-8. Email match uses `!=`
**Where:** `service.py:280` (the HMAC comparison).

The values are fixed-length HMACs that the caller cannot pick freely, so there is no practical timing issue. `hmac.compare_digest` would be more idiomatic.

## Verified OK
- **Org-exact checks:**
  - `org_permission_keys` (`/home/user/org-admin-api/app/modules/rbac/repository.py:331-357`) filters by `RoleAssignment.org_id == :org` only, the org being `active`, DB-clock expiry, and `Permission.app_id == App.id`.
  - Create uses it both for the `<app>:org:invite` check and for the subset check (`service.py:161-175`). Accept uses it for the inviter re-check (`:354-357`). Neither path uses `effective_permissions`.
  - Tests cover permission held only at app scope, permission held in another org, and the subset rule ignoring app-scope permissions.
- **Invitable roles:**
  - Role lookup is limited to the calling app's `app.id`.
  - Roles holding `is_admin` are refused.
  - The `admin` app is refused before anything else, through `get_link_origin` → `MailLinkAppContextDep`.
  - `org_mode != required` gives 422.
  - The composite FK `(role_id, app_id)` → `roles(id, app_id)` enforces same-app at the DB level.
  - `_guard_privileged` inside `_grant_locked` is a second backstop (`SYSTEM_ORG_INVITE` is non-privileged).
- **S1 guard:** checks org_mode, open join, both roles set and present, an admin-free default role, and overlap other than `org:create`. It runs on create and on accept. On accept it runs before join and before consume, so nothing is consumed on refusal (tested).
- **No membership or account oracle on create:**
  - There is no `users` lookup by email (and a grep-level test enforces it).
  - The 201 has the same shape for known, unknown, member and capped targets.
  - The silent path writes, queues and audits nothing.
  - Caps on the caller (per inviter, per org) answer 403, which is fine.
- **Token handling:**
  - `secrets.token_urlsafe(32)`, stored only as SHA-256 (`token_hash`, unique, length CHECK 32).
  - The link is `{validated Origin}/accept-invitation#token=` (in the fragment).
  - The token travels only in the request body (`max_length=256`).
  - It never appears in logs, audit rows or errors. `__repr__` leaves out the hashes, and the validation handler drops `input`.
  - The mail is queued through `BackgroundTasks` after commit.
- **Accept:**
  - Unverified is checked before any token lookup.
  - The lookup is `FOR UPDATE` and filters on `app_id`, pending state and DB-clock expiry.
  - Unknown, used, expired, superseded and other-app tokens all get one 422 `token_invalid` with `details={}` (the test compares full bodies).
  - An `email_hmac` mismatch gives 403 and does not consume the invitation (accepted in A4).
  - Re-checks: role admin-free; inviter exists, is active, and still holds `org:invite` plus a superset of the role's permissions. The latter fails for a suspended org.
  - The consume is a conditional UPDATE … RETURNING. `get_session` rolls back on any later error, so the invitation stays pending.
  - Concurrent accepts serialize on the app lock and then the row lock; one real-connection test covers this.
- **B1 join on accept:**
  - Join happens only when `effective_roles(user, app)` returns no app-scope roles (the `org_id IS NULL` branch).
  - `join_default_role` refuses closed or approval apps before consume.
  - The `app.joined` audit has the same shape as a normal join.
- **Lock order:** create takes the app lock, then the per-email advisory lock, then `lock_global`. Verify and reset take the email lock then the global lock, with no app lock, and no path takes `lock_global` before `lock_app`. Accept takes the app lock, then the invitation row, then org KEY SHARE. I found no lock cycle.
- **Email cap shares:**
  - The shares are 70/40/10.
  - `total_24h` includes invite send-log rows, and the reset count is `total − verify − invite`, so reset keeps at least 20 % and verify at least 50 %.
  - The counts are taken under `lock_global`.
  - Per target: 1/min and 3/24 h across orgs, using `email_send_log` with purpose `invite`.
- **Email content:** fixed subject, no inviter email, the app name is staff data, the org name goes through `safe_org_name` (the control, format and bidi set comes from audit), and `EmailStr` blocks CRLF in `to`. The dispatcher logs only purpose and `user_id` (the inviter's id, as recommended in F7).
- **Audit:**
  - Written in the same transaction.
  - `invitation_created` records `{role_key, expires_at}`.
  - `invitation_revoked` records `{reason}`.
  - `invitation_accepted` records `{role_key, invited_by, granted}`.
  - `role_assignment.granted` and `app.joined` are written as well.
  - No email, HMAC or token is stored (tested).
  - Refusals write nothing.
  - Both routes are registered in `IDENTITY_MULTI_ROW_AUDITED`.
- **Cookie class:** both routes are in the `COOKIE` sweep and use `AppContextDep` (Origin plus `X-MM-App`), `DeviceDep`, `Cache-Control: no-store` and no `x-permission`. Per-IP rate limits `invitation_create` and `invitation_accept` are read from env and listed in `.env.example` without values.
- **Migration:** the CHECK is widened to `('verify','reset','invite')`. The downgrade deletes invite rows before restoring the narrow CHECK. The FKs are: org CASCADE, role CASCADE, `invited_by` and `accepted_by` SET NULL (a null inviter fails the accept re-check). The model and migration constraint names match. There is a roundtrip test.
- **Docs:** ADR-0014 §1–11 match the code, as do the amendments to ADR-0007 and ADR-0013 and the updates to identity-architecture §4/D10, consumers.md and the deploy.md log table. The only exception is the global-cap behaviour, which needs updating if S-2 is changed.

## Follow-ups (out of scope)
- USR-06c before USR-05: a scope marker or `org_roles` claim, `build_default_role` skipping org-level permissions, and the `set_default_role_if_null` fix using `is_distinct_from(org_owner_role_id)`. Also cover N-2.
- Cap invited assignment expiry at the inviter's expiry (N-1).
- List and revoke endpoints for pending invitations, plus member management. The org pending cap of 50 can currently be filled by members, with no way to revoke.
- A retention purge for `org_invitations`.
- Safe-name validation on `AuthOrgCreate.name`, using the NFKC-hardened version from N-5.
- Make `_CONTROL_CHARS` public instead of importing a private name across modules.
- Add "invitations" to the cookie-class list in CLAUDE.md R3 (plan A14).

## Verdict
**PASS with should-fix.** No 🔴. S-1 (silent re-invite leaves the higher-privileged invitation live) should be fixed in this ticket because it is a small change. S-2 should be fixed, or accepted explicitly by the owner and recorded in ADR-0014.
