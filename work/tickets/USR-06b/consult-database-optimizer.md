DB consult for USR-06b (read-only; no files changed). Reviewed: ticket, migration b4d82f6a1c93, the create_rbac and create_email_tokens migrations, app/modules/auth/{models,repository,email_throttle,retention}.py, and ADR-0013.

## Findings (existing code that shapes the design)
1. `email_send_log.purpose` is `String(10)`. 'invite' (6 chars) fits, so only the CHECK `purpose IN ('verify','reset')` changes. It exists in both the migration (`ck_email_send_log_purpose_valid`) and `app/modules/auth/models.py:83`. Alembic does not compare CHECKs, so `alembic check` will NOT catch a mismatch. The model text and migration text must match by hand, and a test should assert it.
2. The global daily Gmail cap counts `email_tokens` rows created in 24 h (`count_issued_24h`, repository.py:178), not send-log rows. Invitations will not be in `email_tokens`, so they would bypass the cap. This is a real gap against B4 ("counted in the Gmail daily cap").
3. `cap_refusal` (email_throttle.py:43-46) treats every non-verify purpose as reset (`used = total - verify`). An invite purpose would eat the reset share and be capped by it.
4. Retention: `purge_send_log` is purpose-agnostic, so 'invite' rows are purged at 48 h automatically. `org_invitations` needs its own purge. Its rows must stay at least 24 h old if the cap counts them (same floor logic as tokens). The audit summary keys are allow-listed in `app/modules/audit/service.py:27` (`_ALLOWED_KEYS`), so `org_invitations` must be added there.
5. `users.email` is CITEXT, and the existing code already uses HMAC keyed by `IDENTITY_HMAC_KEY` (`email_hasher.hmac(normalized)`) for login failures and the send log. citext is only needed for a column that stores the email. B4 says store only the HMAC, so no citext column.

## Recommendations

### Table `org_invitations` (new, expand-only)
- `id` uuid pk (UUIDPkMixin).
- `org_id` uuid NOT NULL, FK organizations ON DELETE CASCADE. Not RESTRICT: pending invitations must not block admin org delete with 409, since they are not grants. Accepted grants are protected by the existing role_assignments RESTRICT.
- `app_id` uuid NOT NULL and `role_id` uuid NOT NULL, with composite FK `(role_id, app_id)` → `roles(id, app_id)`, named `fk_org_invitations_role_same_app`, ON DELETE CASCADE. It is the same pattern as `fk_role_permissions_role_same_app`. Do not use SET NULL here: it would try to null the NOT NULL `app_id`. Cascade on role delete silently drops pending invites, which is acceptable because they are transient. Alternative: a plain no-action FK, which makes role delete return 409 while any invite exists, including stale ones. Cascade is recommended. A direct FK on `app_id` is redundant; the composite FK cascades from apps through roles.
- `email_hmac` bytea NOT NULL, CHECK `octet_length = 32`. `token_hash` bytea NOT NULL UNIQUE, CHECK `octet_length = 32` (same as email_tokens).
- `invited_by` uuid NULL, FK users ON DELETE SET NULL.
- `expires_at` NOT NULL, `created_at` NOT NULL default now(), CHECK `expires_at > created_at` (same as email_tokens).
- `used_at` NULL and `used_by` uuid NULL (FK users SET NULL). `revoked_at` NULL.
- CHECK `NOT (used_at IS NOT NULL AND revoked_at IS NULL) OR ...` is optional. A simpler CHECK is `used_at IS NULL OR revoked_at IS NULL`: not both set.
- Use timestamps instead of a status enum. Single-use state is then one atomic UPDATE.
- The "role is same app, non-admin, subset of inviter" rules and "app is org_mode=required and not admin" cannot be expressed cheaply as constraints. Enforce them in the service at create AND re-check at accept (see below), with tests.

### email_hmac, not citext email
- Matches R11 (no full emails stored or logged) and B4. No citext dependency added; the users downgrade even drops the extension.
- Cost: staff cannot see who was invited, and resend needs the email again. Reissue (below) takes the email in the request, so this is fine.
- If admin UI needs a display, store an optional masked `email_hint` (e.g. `a***@gmail.com`). This is an open question.
- Normalize with the same function as login/send log (`strip().lower()`). Gmail dots and plus-tags are not folded; an invite to `a+x@gmail.com` only matches an account with that exact address. Document this.
- Rotating `IDENTITY_HMAC_KEY` invalidates every pending invitation (and the throttles). Note it in the runbook.

### Indexes
- `uq_org_invitations_token_hash` (unique, also serves accept).
- Partial unique `uq_org_invitations_pending_org_email` on `(org_id, email_hmac) WHERE used_at IS NULL AND revoked_at IS NULL`. `now()` is not allowed in an index predicate, so expiry cannot be part of it. Create therefore must supersede first (below).
- `ix_org_invitations_org_id_created_at` (per-org cap, FK scan, listing).
- `ix_org_invitations_invited_by_created_at` (per-inviter cap, FK for the SET NULL scan).
- `ix_org_invitations_created_at` (global cap count, retention).
- `ix_org_invitations_role_id_app_id`: the FK referencing side needs an index for role/app delete cascades. `(role_id, app_id)` can be covered by the `role_id` leading index; add `role_id` alone if the above are not enough. `used_by` is a SET NULL FK on user delete; an index is only needed if user deletes are common (they are rare, so skip it, but note it).
- Declare partial-index predicates in the model with the same `postgresql_where` text so `alembic check` shows no drift.

### Reissue semantics
Create, in one transaction:
1. Take `pg_advisory_xact_lock(hashtextextended('invite'||org_id||hex(email_hmac),0))`, the same style as `EmailSendLogRepository.lock`.
2. `UPDATE org_invitations SET revoked_at = now() WHERE org_id=:o AND email_hmac=:h AND used_at IS NULL AND revoked_at IS NULL` (this supersedes unexpired and expired pending rows).
3. INSERT the new row.
4. Insert the send-log row with purpose 'invite'.

The partial unique index is the race backstop; map its `IntegrityError` to a retryable error. Reissue may change the role, because the new invitation is validated afresh. The old token then falls into the same uniform "invalid/expired" error. An explicit revoke is the same UPDATE without the insert. Per-email limits (1 per 60 s, 5 per 24 h) use the existing send-log window keyed by `email_hmac` and purpose 'invite' across all orgs. This is the "per target email" cap.

Per-org and per-inviter caps come from counting `org_invitations.created_at` over 24 h (supersede counts as a new row), and optionally a pending count per org.

### Create must not reveal account existence
- Do not query `users` at all on create.
- Always return the same 201/202 body, send the same email for known and unknown addresses, and keep response time similar.
- A conflict such as "already a member" would leak; either skip that check or return the same shape. An unregistered address is fine, because the person signs up and then accepts.

### Cap integration
- Change `count_issued_24h` to also count `org_invitations` created in 24 h. Return per-purpose counts, e.g. `(total, verify, invite)`.
- Add an explicit invite share in `cap_refusal` and compute reset as `total - verify - invite`.
- Caution: the current invariant is "verify always keeps ≥50%, reset ≥20%". Adding an invite share of 30% would drop the verify guarantee to 20%. Choose shares so that reset + invite ≤ 50% combined (for example invite 25%, reset 25%, or keep reset 50% and cap the combined non-verify share). This is a decision for the planner and ADR-0007 should be updated.
- Hold `lock_global` during the check, like verify/reset.
- Extend `EmailRetentionRepository` with `count_invitations`/`purge_invitations`: delete rows that are used, revoked or expired AND have `created_at < now() - 7 d`, with a 24 h floor, batched with `FOR UPDATE SKIP LOCKED`. Register the table in `EmailRetentionService` and in the audit allow-list.

### Atomic single-use accept
Do the whole accept in one transaction at READ COMMITTED. First lock the account row `FOR SHARE` (see `users/repository.py:60`) and check status active and `email_verified_at IS NOT NULL`. The check on the verified email is in the service. Pre-checks that can refuse without consuming come first: app `join_policy != 'closed'`, org `status = 'active'`, and the role is still in the same app with no `is_admin` permission (re-check, because the role can change after the invite). Then:

```sql
UPDATE org_invitations
   SET used_at = now(), used_by = :account_id
 WHERE token_hash = :token_hash
   AND used_at IS NULL AND revoked_at IS NULL
   AND expires_at > now()
   AND email_hmac = :hmac_of_normalized_account_email
RETURNING id, org_id, app_id, role_id, invited_by;
```

- Putting `email_hmac` in the WHERE matters: another verified account, or one with the wrong email, matches 0 rows and does NOT burn the token. Otherwise a stolen link plus any account could invalidate the invitation as a denial of service.
- Two concurrent accepts: the second UPDATE waits on the row lock, re-evaluates `used_at IS NULL` under READ COMMITTED, and matches 0 rows. Exactly one succeeds (test with two sessions).
- 0 rows means one uniform error for unknown, replayed, expired, revoked or wrong-account.
- On 1 row, in the same transaction: join the app per B1 (join-policy mapping is an open question), then
  `INSERT INTO role_assignments (id,user_id,role_id,org_id,granted_by,expires_at) VALUES (…,:account,:role,:org,:invited_by,NULL) ON CONFLICT ON CONSTRAINT uq_role_assignments_user_role_org DO NOTHING`, then write the audit rows ('invitation accepted' plus the role grant).
- Any failure after the UPDATE must roll the whole transaction back, so the token stays unconsumed. For B1 (closed app), refuse BEFORE the UPDATE.

### Inviter-permission subset check (at create, org-exact)
Check that no row exists matching:

```sql
SELECT 1 FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
 WHERE rp.role_id = :target_role
   AND (p.is_admin OR rp.permission_id NOT IN (
        SELECT rp2.permission_id FROM role_assignments ra
        JOIN role_permissions rp2 ON rp2.role_id = ra.role_id
        WHERE ra.user_id = :inviter AND ra.org_id = :org
          AND (ra.expires_at IS NULL OR ra.expires_at > now())))
```

Also verify the target role's `app_id` equals the app (which must have `org_mode='required'` and not be `admin`) and that the inviter holds `<app>:org:invite` org-exact.

### Migration (one file, down_revision `b4d82f6a1c93`)
upgrade:
1. `create_table org_invitations` with the constraints and indexes above.
2. Replace the send-log CHECK. Drop `ck_email_send_log_purpose_valid`, then add `purpose IN ('verify','reset','invite')` as `NOT VALID` and `VALIDATE CONSTRAINT`. This widens the constraint, so no existing row can fail. The brief ACCESS EXCLUSIVE lock is fine because the table is small (48 h of data).

downgrade (reverse order):
1. Drop `org_invitations` indexes and the table.
2. `DELETE FROM email_send_log WHERE purpose = 'invite'` (otherwise the narrower CHECK fails; the data is 48 h of throttle rows, so it is harmless and lossy only for those).
3. Recreate the narrow CHECK.

Docstring: expand-only except the CHECK swap; lossy for invitations and invite send rows. Update `models.py` and the new model, and register it in `app/db/models.py` (R2). Run `alembic check` to prove no drift. Add one test that reads `pg_get_constraintdef` for the send-log CHECK and the invitations table, because Alembic does not compare CHECKs.

## Risks
- Cap bypass: invites are not counted unless `count_issued_24h` is extended (Finding 2).
- Cap share arithmetic can silently break the verify-starvation invariant (above).
- Without the `email_hmac` predicate in the accept UPDATE, a wrong-account attempt consumes the token.
- Stale partial-unique conflicts if create does not supersede expired pending rows.
- A pending invitation whose role later gains an `is_admin` permission: must be refused at accept (re-check), not just at create.
- HMAC key rotation kills pending invites; Gmail aliases do not match.
- CASCADE on the role FK drops pending invites quietly; confirm that is acceptable.
- Timing or response differences on create could still leak account existence; test response shape and avoid any users lookup.
- Audit `changes` must carry only ids, role key and org id, never the email, hmac or token. A new key in `_ALLOWED_KEYS` may be needed.

## Open questions for the planner
1. Cap split: choose invite share and resolve the verify ≥50% invariant (update ADR-0007).
2. Store a masked `email_hint` for the admin display, or none?
3. When `join_policy = 'approval'`: does an invitation bypass approval, or create a pending membership and still grant the org role? B1 says "per join_policy"; this needs one concrete answer.
4. If the inviter is removed or loses `<app>:org:invite` after sending: keep the invitation valid (recommended: yes, `invited_by` SET NULL; audit has the history) or revoke?
5. Role FK on delete: CASCADE (recommended) or refuse role delete with 409?
6. Retention window for used/revoked invitation rows (proposed 7 d, with the 24 h floor), and whether accepted rows should be kept longer for forensics (the audit log already has it).
7. Per-inviter, per-org and per-email numeric limits (not given in the ticket).
8. The identity doc §4 and the ticket use different accept shapes (`/invitations/{token}/accept` vs B3 body `{account_id, token}`); B3 wins, but update `docs/identity-architecture.md:157`.
