# Consult — security-architect — USR-31 (2026-11-24)

**Findings**
- A leaked `org-members:read` key already exposes every member's email (ADR-0025 §9, `docs/adr/0025-org-members-read-scope.md:49`). Adding invitations gives an attacker little more: how many people are invited to each org (at most 50 pending, ADR-0014 §9), which roles they get, and when. That is planned-membership business intel with no identities. The extra risk is Low, and the key still cannot write anything.
- The existing staff query `InvitationRepository.page_for_org` (`app/modules/invitations/repository.py:143-176`) has **no `app_id` filter**. It also returns `invited_by` and `accepted_by`, which are user ids. Reusing it as-is would leak other apps' invitations and these ids.
- `OrgInvitationRead` (`app/modules/invitations/schemas.py:44-56`) carries `invited_by` and `revoked_at`, so it is the wrong schema for this route.
- The model stores `email_hmac` and `token_hash` (`app/modules/invitations/models.py:68-69`). Its `__repr__` is already safe (`:83`).

**Recommendations**
1. Add a new repository method that selects explicit columns only: id, role key, created_at, expires_at. Do not select the ORM entity, so the hash columns are never loaded (the same pattern as `page_app_org_members`, `app/modules/rbac/repository.py:673`). Filter on `org_id` and `app_id = caller.app_id`, plus the pending conditions `accepted_at IS NULL AND revoked_at IS NULL AND expires_at > func.now()`. Use the database clock, as in `repository.py:136-140`. Filtering on the server is required by AC1.
2. Add a dedicated response schema with only `id`, `role` (the full role key), `created_at` and `expires_at`. **Leave out `invited_by`.** Inviter ids are not needed for seat planning, they expose who invited whom inside the org, and a removed or disabled inviter's id would leak. Also leave out `email_hmac`: it is a stable pseudonym that could link the same person across orgs.
3. Use a new route, not an opt-in field on the member read. It gets its own audit action and its own field-minimisation test, and the USR-29 response stays unchanged. Order the guards as in `app_service.py:39-53`: `org_mode` check, then `org_related_to_app`, then a denied audit row and the 404. Run the visibility check before any invitation query, so a suspended org that has pending invitations still answers 404.
4. Audit: new action (e.g. `organization.invitations_read`) with actor `app:{key}/{key_id}` and changes `returned`, `total`, `limit`, `offset` only. No invitation ids or role keys. Logs carry `key_id` and `org_id` only.
5. Use the same per-IP and per-key rate-limit buckets as the member read, so one key's limit covers both routes rather than doubling.

**Risks**
- An app could match invitation timing to new member `joined_at` values and guess who an invitation was for. Low risk; accept it and document it in ADR-0025.
- If an org loses all its app roles, its pending invitations become invisible (404). Accept this.

**Open questions for Ming (defaults)**
- Q1: Use the existing `org-members:read` scope, or add a new one? Default: reuse it, since this data is less sensitive than the member emails the scope already grants.
- Q2: Include the inviter `user_id`? Default: no.
- Q3: Expose `role` as one key per invitation? Each invitation row holds exactly one role, so default: yes, a single key.
