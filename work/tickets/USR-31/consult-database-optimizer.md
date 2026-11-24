# Consult — database-optimizer — USR-31 (2026-11-24)

**Findings (db)**
- Table `org_invitations` (app/modules/invitations/models.py:33-58). Indexes: partial unique `uq_org_invitations_pending (org_id, app_id, email_hmac) WHERE accepted_at IS NULL AND revoked_at IS NULL`; `ix_org_invitations_org_id_created_at (org_id, created_at)`; `ix_org_invitations_role_id_app_id`. There is no email, token or hash in the selected columns unless you add them. The role FK is composite (role_id, app_id) -> roles, so the role is always of the same app (models.py:35-40).
- Existing pending predicate on the DB clock: `accepted_at IS NULL AND revoked_at IS NULL AND expires_at > func.now()` (invitations/repository.py:44-53, same shape at :105-113). `status_expr()` (:133-141) is the single status definition, and `page_for_org` (:143-176) is the USR-23 staff read. It joins App and Role, orders `created_at DESC, id`, and returns count plus rows in two statements. It selects no email, token or hash.
- Each invitation has exactly one role (`role_id`), so "role key(s)" is a single `role_key`. This is not a list.
- The USR-29 visibility check is `RbacRepository.org_related_to_app(org_id, app_id)` (rbac/repository.py:644-661). It needs an active org and at least one non-expired role assignment of the app. It returns False for unknown, unrelated and suspended orgs alike. The service flow is in organizations/app_service.py:39-53: load app, 422 `_org_not_supported` unless `org_mode == REQUIRED`, then deny with a DENIED audit row and `OrgNotVisibleToAppError`.

**Recommendations**
1. A migration is not needed. The query is `WHERE org_id=:o AND app_id=:a AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now()`. It uses `ix_org_invitations_org_id_created_at`. An org has few pending invitations, and `count_pending_for_org` already caps them, so a new index (`org_id, app_id, created_at`) is not worth it. Alembic head stays c91e4b7a3d58.
2. Add `InvitationRepository.page_pending_for_app(org_id, app_id, limit, offset)`. Select only `OrgInvitation.id, Role.key, created_at, expires_at`, joined to Role on `(Role.id, Role.app_id) = (role_id, app_id)`. Filter by `app_id` directly, with no App join. Order by `created_at DESC, id` (the same as USR-23, :172) so pages are stable. `id` is the tiebreaker. Use `select(func.count())` with the same filters. Do both statements in the same transaction. Use `func.now()` throughout, which is transaction-start time and consistent between count and rows. Do not use Python time.
3. Do not reuse `status_expr()` for the filter. Use the explicit predicates, which are index-friendly and match :44-53. Do not select the entity (this follows the "explicit columns" rule at rbac/repository.py:673-674).
4. In the service, copy the USR-29 guard block (app_service.py:39-53) as a shared helper, or call the same sequence. Keep one code path so the 404 body and audit row stay identical to the member read. Use an audit action for the invitations read, with ids and counts only (returned, total, limit, offset).
5. Add a test that dumps the response and the audit `changes` and checks there is no `email_hmac`, `token_hash` or email. Add an expiry test using a DB-side `expires_at` in the past (never `datetime.now()`).

**Risks**
- `org_related_to_app` requires an active member holding a role of the app. An org whose only simap members have left, but which still has pending invitations, returns 404 and no invitations. This is consistent with USR-29 and probably acceptable.
- Offset paging on a live list can skip or duplicate rows when invitations are created, accepted or expire between calls. The same is true of USR-23 and is acceptable at this size.
- `expires_at > now()` means an item can expire between the read and the app's use. Document that the data is a snapshot.

**Open questions for Ming (defaults)**
1. New route or opt-in field on the member read? Default: a new route `GET /api/v1/apps/{key}/orgs/{org_id}/invitations` returning `Page[T]`. The member read sorts by user and has different pagination and audit semantics.
2. A separate scope? Default: no, reuse `org-members:read`. It is the same data domain (membership) and the same runtime key.
3. Include `invited_by` or the role key? Default: include the role key. Omit `invited_by` and everything else (minimal fields).
