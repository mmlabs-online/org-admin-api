# Consult — identity-access-engineer — USR-31 (2026-11-24)

**Findings**
- Each key has exactly one scope, and ADR-0025 §1 is the one-scope-per-key rule (`docs/adr/0025-org-members-read-scope.md:17`). If invitations got their own scope, simap would need a second key and deploy secret for data that is less sensitive than the member list. The member read already returns emails (§6, §9). The invitation read returns only ids, role keys and timestamps.
- `RequireAppCredential` ties the denied audit action to the dependency (`app/modules/app_credentials/deps.py:34,84-93`). `OrgMembersCredentialDep` therefore records `ORGANIZATION_MEMBERS_READ` on denial, so the new route needs its own dependency instance.
- The rate limiter counts per scope name (`app/core/rate_limit.py:156,180`). If both routes use `rate_limit("app_org_members")` and `enforce_subject_limit(..., "app_org_members_key", ...)`, they share one budget, and no new config is needed (`app/core/config.py:124-127`).
- `org_invitations` stores an HMAC of the invitee email (`docs/adr/0014-org-invitations.md:18`). That HMAC is deterministic, so anyone holding it can test guessed addresses. It must be excluded like the token hash is.

**Recommendations**
1. Reuse `org-members:read` and add a new route `GET /api/v1/apps/{key}/orgs/{org_id}/invitations` returning `Page[T]`. Don't add an opt-in field to the members route: a separate route gets its own audit action and pagination and keeps the USR-29 contract unchanged.
2. R3 (a): change the `org-members:read` entry to "`GET …/orgs/{org_id}/members` and `GET …/orgs/{org_id}/invitations`". Add both routes to `tests/test_route_auth_classes.py`.
3. ADR form: add a dated "Amendment (USR-31)" section to ADR-0025 (§2 route, §8 audit, a new fields item) and a D38 row. Don't use ADR-0029 for this; the decision only extends an existing scope.
4. Add a new dependency `OrgInvitationsCredentialDep` (`lock=False`) and a new action `organization.invitations_read`. Record one row per call (org_id, returned/total counts) and a denied row on 404. Never include the email HMAC, token hash or inviter email. Test this.
5. Share both rate-limit buckets with the members route.
6. Reuse the USR-29 relation query, and filter `app_id = {key}`, pending, `expires_at > now()`.

**Risks**
- A leaked key now also reveals planned membership and invitation timing. This is small next to the emails the key already exposes.
- With shared buckets, a client that reads members and invitations on every page view uses up the 60/min per-key limit twice as fast.
- `docs/spec-v1.md` §8 Q4 ("no app-credential widening") needs a new dated line, so this is a spec conflict to flag.

**Open questions for Ming (defaults)**
- Separate scope? Default: no, reuse `org-members:read`.
- Should invitations share the members rate-limit buckets? Default: yes.
- Amendment to ADR-0025 plus D38, or a new ADR-0029? Default: amendment plus D38, leaving ADR-0029 unused.
- Show the inviter's `user_id` in each item? Default: no, keep fields minimal.
