USR-06b consult (api-platform-engineer, read-only). I read the ticket, the USR-06 plan and API consult, organizations/auth_router.py, auth/deps.py, the link/path constants (VERIFY_PATH, RESET_PATH) and docs/consumers.md. I did not read tests/test_audit_coverage.py, the rate_limit internals, or the email dispatcher and hook code in full. I did not check the actual `SYSTEM_JOIN` or `AppContextService` internals; verify them when planning.

## Findings
1. The 06a pattern to copy is `organizations/auth_router.py`. The handler declares `app: AppContextDep`, `_cookie: DeviceCookieDoc`, the body, then `device: DeviceDep`, and has `dependencies=[rate_limit(...)]`. AppContext failures (400 and 403) and Device failures (401) are raised inside dependencies, so they win over body 422s. The `{}` sweep in `test_cookie_routes_not_bearer_guarded` (line ~308) therefore works for POST routes as long as the new handlers keep this order.
2. The link mechanics exist. `get_link_origin` = `MailLinkAppContextDep` plus the normalized request `Origin`, and it refuses `admin` with `origin_not_allowed`. `VerificationEmailHook` shows how mail is scheduled through `BackgroundTasks` after the commit, with `email_hasher` and `email_daily_cap`. Fixed paths are `VERIFY_PATH` and `RESET_PATH`, each followed by `#token=`.
3. docs/consumers.md already has an Organizations section (lines 132-148) and an `org_access_denied` row (128). Line 44 says `credentials: "omit"` for the public routes, so accept must be documented as `credentials: "include"` (cookie class). identity-architecture.md line 157 still lists the path-token accept route, and D10 and ADR-0013 line 14 mention `<app>:org:invite` only in passing.
4. 06a already set the error-code convention: a single 403 `org_access_denied` for unknown org, no role, or suspended. 06b should mirror it.

## Recommendations

### Endpoint: create invitation
`POST /api/v1/auth/orgs/{org_id}/invitations`, cookie class.
- Body: `{account_id, email, role}`, `extra=forbid`. `role` is a role key of the calling app. Reuse the email validator and normalization used by signup.
- Dependencies: `rate_limit("invitation_create")`, `AppContextDep`, `DeviceCookieDoc`, body, `DeviceDep`, `AuditContextDep`. Link origin comes from `get_link_origin`, which gives `admin` refusal and an allow-listed origin for free. It must not come from a header or the body.
- Order inside the service:
  1. Account belongs to device and email is verified (`email_unverified`).
  2. Org is active and the inviter holds `<app>:org:invite` org-exact for the calling app. Otherwise one 403 `org_invite_forbidden`, the same answer for unknown org, not a member, or no permission. Do not use 404 here (see Open questions on R8's 404 test). 06a's `org_access_denied` is the model.
  3. Role validation: same app, not `admin`, no `is_admin` permission, permissions are a subset of the inviter's org-exact permissions (the union with app scope is not allowed). Failure gives 422 `role_not_invitable` with `details` naming the field `role`.
  4. Caps and replacement of a pending invitation for the same org and email HMAC.
- Response: **201** `InvitationCreated {id, org_id, role, expires_at}`. No email echo, no status, no token, and **always the same body** whether the email has an account, is already a member, has a pending invitation, or hit the per-email cap. The id is generated for every call.
  - Already a member or a pending duplicate: still 201. Replace (revoke the old, issue new) for pending. For "already holds role in org", skip the mail silently. Run that branch in the background step so timing and body match.
  - Per-email caps (1/min, 3/day) and the Gmail daily cap: swallow silently (201, no mail). Per-inviter and per-org pending caps are independent of the target, so they may answer 403 `invitation_limit_reached` (R6-compatible; document it) or 429 via `rate_limit`. Use 429 for the rate-based ones and 403 for the pending-cap one.
- Store only `token_hash` (reuse the existing token hashing helper) and `email_hmac` (the existing email HMAC from `get_email_hasher`). The plaintext email lives only in the scheduled `OutgoingEmail`, created after the commit.
- Audit in the same transaction: `invitation.created` with `{invitation_id, org_id, role_key, expires_at}` and no email or token. Actor is `EndUser` with `app_key`. Register the action in `actions.py` and in `test_audit_coverage`.

### Endpoint: accept
`POST /api/v1/auth/invitations/accept`, cookie class. Update identity-architecture §4 and the ticket's "path" text to the body form (B3).
- Body: `{account_id, token}`, `extra=forbid`. Token in the body only, never in the path or query, and never logged. Check that the access-log and `AuditContext` do not capture request bodies.
- Response: **200** `InvitationAccepted {org_id, roles: [str]}`. R6 lists 201/204 for create and delete only, so 200 for an action is consistent with `/auth/token`.
- Atomic consume in a single statement:
  `UPDATE invitations SET accepted_at=now(), accepted_by=:user WHERE token_hash=:h AND email_hmac=:caller_hmac AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now() RETURNING ...`
  Because the caller's own verified-email HMAC is part of the WHERE clause, a mismatched account matches zero rows and the invitation is **not consumed**. Concurrent accepts: exactly one row is returned.
- Error mapping:
  - Caller account unverified: 403 `email_unverified`. That is the caller's own state and reveals nothing about the token. It is checked before the token lookup.
  - Unknown, expired, used, revoked, wrong-email (another verified account): **one** 422 `token_invalid` with identical message, `details`, and timing. Fold the mismatch in.
  - Also treat as `token_invalid`: org suspended or deleted, and role deleted or newly `is_admin`. Re-validate the role eligibility at accept time (not only at create). Do not re-require the inviter's current membership (a departing inviter should not void invites). Note this as a decision for the planner.
- B1: after consuming, perform the app join per `join_policy` in the same transaction. A `closed` app refuses with the existing join-closed error and the whole transaction rolls back, so the invitation stays pending. For the role grant, use a new `SYSTEM_ORG_INVITE` system actor (non-privileged, like `SYSTEM_JOIN` and `SYSTEM_ORG_CREATE`) via `_grant_locked`. It is idempotent: if the user already holds the role, still consume and return 200.
- Audit: `invitation.accepted` and `role_assignment.granted` (`org_id`, `role_key`) in one transaction, with no email or token.
- Rate limit `invitation_accept` per IP and per account (R11 brute-force posture), with config keys in `.env.example` (R5).

### Cookie-class wiring (R3)
- Add both routes to `COOKIE` in `tests/test_route_auth_classes.py`:
  - `("POST", f"{V1}/auth/orgs/{{org_id}}/invitations")`
  - `("POST", f"{V1}/auth/invitations/accept")`
- Confirm `_fill` substitutes `org_id`. It does for the 06a `/orgs` routes, but verify the sweep's URL builder at line ~308 handles `{org_id}`; it only replaces `{key}` visibly, so it may need extending.
- Neither goes in `SELF_SCOPED` or `BEARER`. No `x-permission` in OpenAPI. The `<app>:org:invite` check lives in the service. Add the sweep expectations: 400 without `X-MM-App`, 401 with header only.
- CORS needs no change, since both fall under `/api/v1/auth/`. Add them to the unregistered-Origin test.

### Invite link target (B4)
- `{validated Origin}{INVITE_PATH}#token=<raw>` with `INVITE_PATH = "/accept-invitation"` as a constant next to `VERIFY_PATH`. The fragment keeps the token out of server logs and Referer. Pages must send `Referrer-Policy: no-referrer`.
- Document in consumers.md: every `org_mode=required` app hosts that fixed page. The page reads the fragment, clears it with `history.replaceState`, requires sign-in (preserving the fragment through login and signup/verify), then calls accept with `credentials: "include"`, `X-MM-App`, `{account_id, token}`. On 200, call `getToken(audience, {orgId})`. On 422 `token_invalid`, show "ask the inviter to send a new invitation". On 403 `email_unverified`, route to verification. Warn that the signed-in email must match the invited one (the 422 is deliberately uninformative, so the page copy should say "signed in with the invited email address").
- Mail body: new `invitation_message(link, org_name)` in `email/messages.py`. The subject must be fixed (no org name, to avoid header injection and phishing text). In the body, sanitize the org name (strip control characters and newlines, truncate), do not include the inviter's email, and send plain text.

### Schema and R9 surface
- Naming (avoid clashes with the admin `Organization*` schemas): `InvitationCreate`, `InvitationCreated`, `InvitationAccept`, `InvitationAccepted`. IDs are UUIDs, dates `timestamptz`, snake_case.
- Document responses on each route: create 201/401/403/422/429; accept 200/401/403/422/429. Add the `X-MM-App` header doc and `DeviceCookieDoc`.
- `Cache-Control: no-store` on both (use `_finish`). Device cookie rotation handled via `_finish` like 06a.
- No list or revoke endpoint in this ticket (R7); record both as follow-ups. Admin-side revoke of pending invitations on org delete: the new FK from `invitations.org_id` should be `ON DELETE CASCADE`, since this is a new table and the admin delete is already 409 when assignments exist, so no R9 change.

### Docs to update
- docs/consumers.md: add an invitations subsection to "Organizations" (flow above, page path, error table: `org_invite_forbidden`, `role_not_invitable`, `invitation_limit_reached`, `token_invalid`, `email_unverified`, 429). Fix line 44 area to list accept as credentials-included. Note the 7-day TTL and that invitations are single-use.
- docs/identity-architecture.md §4 (line 157): body-token accept path; D10 sentence. ADR-0013: replace the `<app>:org:invite` forward reference with the decision (or a short ADR-0014 for the invitation design; planner's call). Spec §5.8: one line on invitations.
- `<app>:org:invite` needs to be in the app role catalog (seed or staff data). Document that staff must put it in the org owner role and may add it to other org roles. It must not be in the join default role, per ADR-0013 line 14.

## Risks
- **Email and membership oracles** on create: any difference in status, body, timing or mail-sent behaviour between known, unknown, already-member, duplicate and capped emails. Mitigation: 201 uniform, work moved to the post-commit background step, constant response shape.
- **Token validity oracle** on accept: the mismatch-vs-invalid split. Mitigation: HMAC in the WHERE clause, one 422.
- **Privilege escalation via invited role**: check the subset against the inviter's org-exact permissions, re-check at accept, and block `is_admin` and `admin`-app roles. A role edited after invite creation is the TOCTOU case.
- **Join side effect** (B1): accepting joins the user to the app and could bypass a `join_policy` approval gate if one exists. Refusal must roll back and keep the invitation pending.
- **Mail abuse**: invitation emails are an open spam vector (attacker-controlled org name and recipient). Caps, fixed subject, sanitized org name, rate limits, and counting in the Gmail daily cap mitigate it.
- **Race on replace-pending**: needs a partial unique index on `(org_id, email_hmac) WHERE accepted_at IS NULL AND revoked_at IS NULL`, and the replace must run in one transaction.
- **Body logging**: confirm no middleware logs request bodies (accept carries the raw token).
- **Admin app in link origin**: use `get_link_origin`, not the resend variant.
- **Existing test sweep** may not substitute `{org_id}` in the cookie sweep. Check before relying on it.

## Open questions for the planner
1. Create: 403 `org_invite_forbidden` for all of unknown org, not a member, and no permission (recommended, no oracle), or 404 for the unknown/non-member case? R8 lists a 404 test, but R8 says "404 where it applies"; confirm that this endpoint's 403-only behavior is acceptable.
2. Create status: 201 (recommended, R6) versus 202. Is `InvitationCreated.id` useful given there is no list or revoke endpoint yet?
3. Does the accept re-check require the inviter to still hold `<app>:org:invite`? Recommended: no, only the role's eligibility.
4. Pending-cap and per-inviter-cap error shape: 403 `invitation_limit_reached` versus 429 only.
5. Are the invite page path (`/accept-invitation`) and the new rate-limit and TTL config keys confirmed (consumers must host the page, so this is a consumer-facing contract and frozen once calechip ships)?
6. One ADR (0014) for invitations, or extend ADR-0013?
