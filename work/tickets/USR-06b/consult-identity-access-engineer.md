USR-06b consult (identity-access-engineer, read-only)

Ticket: /home/user/org-admin-api/work/tickets/USR-06b/ticket.md

## Findings

F1. **No org-exact permission read exists yet.** `RbacRepository.org_role_keys` (/home/user/org-admin-api/app/modules/rbac/repository.py:309) returns roles only. The only permission read is `effective_permission_keys` (:331), which goes through `_active_assignments` (:278). With an org, that function returns the union `org_id IS NULL OR org_id = X`.
- If the planner uses `effective_permissions(user, app, org_id)` for `<app>:org:invite` or the subset rule, every calechip member passes. `build_default_role` (/home/user/org-admin-api/app/modules/rbac/service.py:252-289) re-widens `<app>:member` to all non-admin permissions on seed and on every manifest push.
- The result would be cross-tenant invitation, and any member could grant any non-admin role.

F2. **Org tokens require an app-scope role.** `TokenService.issue` (/home/user/org-admin-api/app/modules/tokens/service.py:74-82) raises `not_member` before it looks at `org_id`.
- Without B1 (implicit join on accept), an invitee who holds only an org role cannot get any token.
- So B1 is a functional requirement, not just a convenience.

F3. **S1 becomes systematic in 06b.** The org token `roles` claim is the union (service.py:90; D10). Combine that with B1 and the re-widened default role: **every invitee holds `<app>:member` at app scope, so every org token carries member permissions regardless of the invited role.**
- A role like "viewer" means nothing in the token.
- Today (06a) this needs a staff-made lesser org role. After 06b it happens to every invitee by default.

F4. **`_grant_locked` details** (/home/user/org-admin-api/app/modules/rbac/service.py:1237):
- It raises `ConflictError` when the role is already held. Accept must treat "already holds this role in the org" as success.
- `org_exists_key_share` does not check org `status`, so accept must check that the org is active itself.
- `_guard_privileged` with a non-privileged system actor already refuses `is_admin` roles. That is a good second layer, but it gives a 403, not the uniform error.

F5. **Reusable pieces:**
- `lock_joinable_app` and `join_default_role` (/home/user/org-admin-api/app/modules/membership/service.py:40-63) already encode the join_policy refusals (closed, approval, missing or admin default role).
- `OrgSelfService._user` (/home/user/org-admin-api/app/modules/organizations/auth_service.py:62) does the account, active and verified checks.
- Token hashing is SHA-256 of the raw token (/home/user/org-admin-api/app/modules/auth/email_verification.py:46).

F6. **`email_send_log` / `email_tokens` have `CHECK purpose IN ('verify','reset')`** (/home/user/org-admin-api/app/modules/auth/models.py:50,83). `cap_refusal` only splits out a verify share. Adding `invite` needs a migration (`String(10)` fits) and its own capped share, so user-triggered invites cannot use up the Gmail quota that verify and reset need.

F7. **Doc basis and conflict.**
- Spec §5.8 never mentions invitations. They rest on D10 and identity-architecture §4 (/home/user/org-admin-api/docs/identity-architecture.md:157). §4 shows the token in the path; B3 moves it to the body, so §4 needs an amendment.
- The spec's claims list (spec-v1.md:67) is `roles, org`. Any new claim needs a spec line, and the spec wins.

## Recommendations

**R-1. S1 / the scope question. Fix it within USR-06b, as a gating first step that lands before the accept endpoint.**
- Add an additive `org_roles` claim: org-exact role keys from `org_role_keys`, present only when `org` is present.
- Keep `roles` unchanged (the D10 union), so this is not a breaking change under R9.
- Amend ADR-0013 §6, D10, spec §5.8 claims and consumers.md: "org-scoped authorization MUST use `org_roles`; `roles` is for app-level features only."
- Reviewer gate: no PASS on 06b without it.

Why this option:
- It is the smallest correct fix. One extra read is already done in `issue()`, and there is no RBAC model change.
- Server-side decisions are already org-exact (R-2), so the remaining exposure is only in consumers. calechip is the only `org_mode = required` app and has not shipped against org tokens yet, so the contract can be set correctly now.
- A role/permission `scope` marker (`app|org`) is the better long-term model, because it also lets `build_default_role` stop re-widening org permissions. It touches the catalog, the manifest (USR-05) and the admin API, though. Keep it as a separate follow-up ticket; it does not need to block.
- Do not ship invitations with only the consumers.md advice ("keep the default role minimal"). The manifest flow silently undoes it.
- Also add a follow-up so `mmlabs-auth` (D4) exposes `org_roles`.

**R-2. AC4, org-exact `<app>:org:invite`.**
- Add `RbacRepository.org_permission_keys(user, app_key, org_id)`. It mirrors `org_role_keys`: `RoleAssignment.org_id == :org` only, the org is active, unexpired by the DB clock, joins `Permission.app_id == App.id`. Add an `AuthzService` passthrough with the prefix filter.
- Check order: app has `org_mode = required` (else 422) → `lock_app` → user (active, verified) → `org_role_keys` empty → 403 `org_access_denied` (one answer for unknown, suspended and non-member, same as tokens) → `permits(org_perms, f"{app}:org:invite")` fails → 403 `org_invite_forbidden`.
- Never call `effective_permissions` with an `org_id` for a decision.
- Required tests:
  - holding `org:invite` at app scope only (including via the re-widened default role) cannot invite;
  - holding `org:invite` in org A cannot invite into org B.

**R-3. AC4/AC7, invitable-role rule (B2).** Address the role by key in the body and resolve it only within the calling app. Unknown keys and other apps' keys get the same 422 `unknown_role`, with no cross-app lookup. Refuse when:
- the role is not this app's;
- the app is `admin` (unreachable anyway, since `org_mode` is `none`);
- `role_has_admin_permission(role)`;
- any key in `role_permission_keys(role)` is not `permits(inviter_org_exact_perms, key)`.

Re-check at accept, inside the lock: the role still has no admin permission, `invited_by` still holds org-exact `org:invite`, and the role is still a subset of the inviter's org-exact permissions. Otherwise return the uniform invalid error.
- This gives an implicit revocation path, since there is no revoke endpoint in v1: removing the inviter's org role kills their pending invites.
- `invited_by ON DELETE SET NULL` means the invitation becomes invalid.
- Use `role_id` FK `ON DELETE CASCADE` for invitations.
- Test: a "member" inviter cannot invite an "owner".

**R-4. AC4, storage and creation.**
- Table `org_invitations`:
  - `id`, `org_id` (FK CASCADE), `app_id`, `role_id` (composite `(role_id, app_id)` → `roles`);
  - `email_hmac bytea` (32 bytes, `normalize_email` + IDENTITY_HMAC_KEY, the same function signup and login use), `token_hash bytea UNIQUE` (SHA-256 of 32 random bytes);
  - `invited_by` (SET NULL), `expires_at` (7 days from the DB clock), `accepted_at`/`accepted_by`, `revoked_at`, `created_at`.
- Partial unique index on `(org_id, app_id, email_hmac) WHERE accepted_at IS NULL AND revoked_at IS NULL`. Re-inviting sets `revoked_at` on the old row, then inserts the new one (the superseded token gets the uniform error).
- The response is identical whether or not the email has an account, already belongs to the org, or was silently throttled per target. Use 201 `{id, role, expires_at}` and do not echo the email.
- Per-inviter and per-org caps may be visible (403/429), because they are about the caller.
- Link: the validated request `Origin` (one of `apps.origins`) + a fixed path + `#token=`.
- Emails sent from mmlabs contain the end-user org name. Render it as plain text and truncate it. Do not include the inviter's email.

**R-5. AC4/AC7, accept, email binding and account switching (B3).** `POST /api/v1/auth/invitations/accept {account_id, token}`, cookie class. Order:
1. `rate_limit` → AppContext (Origin + `X-MM-App`) → Device.
2. `lock_app(calling app)` first (ADR-0003 §10).
3. The account is on this device, active and verified. Unverified → 403 `email_unverified`, not consumed, and the token is not looked up.
4. `SELECT … WHERE token_hash = :h FOR UPDATE`. Unknown, expired, accepted, revoked, wrong app, inactive org, or failed R-3 re-check → **one** 422 `invitation_invalid` (like reset's `token_invalid`), with `details={}`.
5. `email_hmac(user.email) != row.email_hmac` → 403 `invitation_email_mismatch`, **not consumed**. No hint is possible or desirable, since only the HMAC is stored.
6. B1 pre-check: if the user has no app-scope role, run `join_default_role(app)`. Closed, approval or unavailable → its existing error, not consumed.
7. Conditional consume: `UPDATE … SET accepted_at = now(), accepted_by = :u WHERE id = :id AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now() RETURNING`. Zero rows → `invitation_invalid`.
8. Grant the org role with `_grant_locked(..., org.id, actor=SYSTEM_ORG_INVITE)` (a new non-privileged system actor). An existing unexpired org role counts as success.
9. Grant the default role at app scope if needed.
10. Audit, all in the same transaction.

The server never picks an account. On a mismatch the SPA offers the other accounts on the device (`GET /auth/accounts`), or sign-in or sign-up. It keeps the token in sessionStorage across sign-up and verify-email, so document this in consumers.md.

Tests:
- another verified account → mismatch, and the invitation can still be accepted afterwards by the right account;
- unverified → refused, not consumed;
- replayed, expired and superseded tokens all give the identical body;
- two concurrent accepts → exactly one 200 (separate sessions);
- closed app → refused and the invitation stays pending;
- org suspended after invite → invalid.

**R-6. AC6, audit.**
- Create writes `invitation.created`: target = invitation, `org_id`, `app_key`, `changes` = `role_key`, `expires_at`, and the superseded id if any. Never email, HMAC or token.
- Accept writes `invitation.accepted`, `role_assignment.granted` (with `org_id`), and `app.joined` when B1 joined the user.
- Register both routes in `IDENTITY_MULTI_ROW_AUDITED` / the coverage harness (the 06a S2 pattern) and add both to the `COOKIE` route sweep.
- Logs carry UUIDs only (R11).

**R-7. Limits (B4, R5).**
- Per inviter about 20 per day, per org about 50 pending, per target email HMAC 1 per minute and 3 per day.
- A new `invite` share of the global daily cap, smaller than verify's, via the purpose CHECK migration.
- A per-IP accept limit.
- All values come from env and are listed in `.env.example`.

## Risks
- **Highest:** using a union read anywhere in invite, subset or accept checks is cross-tenant escalation (F1). Shipping 06b without R-1 leaves the invited role meaningless in tokens (F3).
- `org_roles` relies on consumers adopting it. Until the scope marker exists, `roles` still mixes scopes, and the manifest keeps re-widening `<app>:member`.
- Spam or phishing relay through invite emails: open sign-up → join → create org → invite arbitrary addresses, with an attacker-chosen org name in an mmlabs-branded mail.
- Without a revoke endpoint, a mis-sent invite lives up to 7 days. It can only be killed by superseding it or by the R-3 inviter re-check.
- Revocation lag of up to the token TTL (about 15 minutes) after org-role removal or suspension.
- Deadlock risk if accept takes locks in a different order from join or org create. Keep app → invitation row → org (KEY SHARE).

## Open questions (owner)
1. Approve the additive `org_roles` claim inside USR-06b (amends spec §5.8 claims, D10, ADR-0013 §6), with the role/permission scope marker as a separate follow-up? Alternative: change `roles` in org tokens to org-exact only. That is cleaner, but it is a semantic break of D10 and the 06a contract. Recommended: additive `org_roles`.
2. Confirm that accept re-checks the inviter's current org-exact `org:invite` and subset (implicit revocation). Recommended: yes.
3. Should member-only status be hidden from the inviter? If the invited email already belongs to the org, the email is still sent and the response is unchanged. Recommended: yes.
4. Invitations rest on D10 and §4, not on a spec §5.8 line. Should a one-line spec addition land with the ticket?
5. Can an invitee holding the invited verified email accept on an app where they were never joined and the default role is unset (join unavailable)? Per B1 the answer is refuse and keep the invitation pending. Confirm.
