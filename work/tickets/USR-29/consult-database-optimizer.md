# Consult — database-optimizer — USR-29 (2026-11-21)

**Consult USR-29 (db). I could not verify every claim: I did not open ADR-0013/15/21, ADR-0022, or the spec sections.**

## Findings
- The USR-23 query is `page_org_members` (`app/modules/rbac/repository.py:591-626`). It is a plain per-assignment page, not per-user:
  - Filters: `RoleAssignment.org_id == org_id`, optional `App.key`, optional `active`.
  - Sort: `created_at DESC, id`.
  - `total` counts assignments, so a user with two roles appears twice (`organizations/router.py:176-179`).
  - It cannot be reused as is for USR-29, which needs one row per user with `org_roles` aggregated and `Page[T]` total = distinct users.
- `_is_active()` (`repository.py:547-549`) is the single definition of "not expired" and uses the DB clock (`expires_at IS NULL OR expires_at > now()`). Reuse it.
- `RoleAssignment` (`rbac/models.py:195-229`) has only `created_at`. It has **no `updated_at`**, and an assignment is either granted or deleted.
  - Expiry changes are invisible: an `expires_at` passing is not an event.
  - Deletions leave no row.
- `User` has `updated_at` (`app/db/base.py:38` mixin) and `display_name` / `status` / `email_verified_at` (`users/models.py:24-31`).
- Existing indexes on `role_assignments`: `ix_role_assignments_org_id`, `ix_role_assignments_role_id`, and the unique `(user_id, role_id, org_id)` (`models.py:200-208`). That is enough for one org: the planner will filter on `org_id`, then join to `roles` and `apps`.
- Scope CHECK: `ck_app_credentials_scope_valid` is `scope IN ('manifest','releases')` (migration `58a494f0449e`, lines 35-37). The model carries identical text (`app_credentials/models.py:31`). Alembic does not compare CHECKs, so `alembic check` will not catch a model/migration mismatch; the round-trip test `tests/test_app_credential_scope_migration.py:61-103` does.
- The index `ix_app_credentials_app_id_scope_active` (app_id, scope) WHERE `revoked_at IS NULL` already serves the "at most 2 active per scope" count. No new index is needed for credentials.

## Recommendations
1. **Query (AC1, AC3).** Add a new repository method rather than bending `page_org_members`:
   ```
   SELECT u.id, u.email, u.email_verified_at IS NOT NULL, u.display_name, u.status,
          array_agg(DISTINCT r.key ORDER BY r.key) AS org_roles,
          min(ra.created_at) AS joined_at
   FROM role_assignments ra
   JOIN roles r ON r.id = ra.role_id
   JOIN apps a ON a.id = r.app_id AND a.key = :key
   JOIN users u ON u.id = ra.user_id
   WHERE ra.org_id = :org AND (ra.expires_at IS NULL OR ra.expires_at > now())
   GROUP BY u.id
   ORDER BY u.id
   LIMIT/OFFSET
   ```
   - It is one statement, with no N+1 (org_roles come from `array_agg`).
   - Get the total with `count(DISTINCT ra.user_id)` over the same filters, or `count(*) OVER ()` in a subquery. Two statements is the existing pattern.
   - The `GROUP BY u.id` selects explicit columns only, so `password_hash` cannot leak (same rule as `repository.py:601`).
   - Sort by `u.id`: stable and unique. Do not use `joined_at`, which can tie and shifts as roles change. Offset paging is fine at org scale.
   - `joined_at` = earliest active `ra.created_at`. State this definition in `docs/consumers.md`.
2. **`updated_since` (AC6).** Define it honestly.
   - Add a visibility rule. A user is "changed" if `users.updated_at > :since` OR `max(ra.created_at) > :since` (a new grant). This needs no schema change.
   - Deletions and expiry are NOT detectable. Say so in the contract: `updated_since` is an additive sync and the app must reconcile with a periodic full listing.
   - Filter via `HAVING max(ra.created_at) > :since OR u.updated_at > :since`, after the active filter.
   - Check that `users.updated_at` is bumped on a display name or status change (the mixin's `onupdate`). Password hash and last-login writes may also bump it, which gives spurious but harmless changes. Verify what writes the user row on login, because a bump per login would make the filter noisy.
   - Do NOT add tombstones or a changelog table in this ticket (R7). Offer the full list as the fallback.
3. **`status` filter:** `u.status = :status`, trivial. Both predicates fit in a single query, with no index changes. If perf worries Ming, an optional later index on `role_assignments(org_id, role_id)` would help. I did not measure, so do not add it speculatively.
4. **Scope migration (AC2).** New revision with parent `911eb99ddf8b`.
   - upgrade: `ALTER TABLE app_credentials DROP CONSTRAINT ck_app_credentials_scope_valid`, then `ADD CONSTRAINT ... CHECK (scope IN ('manifest','releases','org-members'))`. Use `op.f("ck_app_credentials_scope_valid")`, as the ADM-034 security review advised (`work/tickets/ADM-034/security-review.md:42`).
   - downgrade: `DELETE FROM app_credentials WHERE scope = 'org-members'` FIRST, then restore the old CHECK. This avoids an invalid-constraint failure and preserves the ADM-034 escalation reasoning.
   - Update the model's `CheckConstraint` text byte-identically, and extend `tests/test_app_credential_scope_migration.py` for the round trip.
5. **Sequencing with ADM-042/ADR-0022.** ADM-042 will drop and recreate the same CHECK. Whichever lands second must start from the first's list, so state the expected final list in both migrations. Safest: make the scope list a single constant referenced in tests. The "only `releases` may be issued for `admin`" rule lives in service code, so confirm `org-members` is refused for the `admin` app (it should be, because it is a runtime read).
6. **Audit.** One row per call goes in the same transaction as the read. Store counts only (`returned`, `total`, `limit`, `offset`, filters present as booleans), never emails.

## Risks
- Offset paging plus a changing list while syncing can skip or duplicate users. Mitigate with a stable `u.id` sort and a periodic full resync.
- PII: a leaked key exposes emails. Keep `no-store`, rate limit per credential, and add no extra fields.
- Concurrent CHECK edits between USR-29 and ADM-042 can clobber each other's scope lists (see 5).
- `users.updated_at` noise could make `updated_since` return many rows. Check which write paths bump it.

## Open questions for Ming
1. Is `updated_since` best-effort (additive only; deletions found only via full listing)? Default: **yes**, documented.
2. Scope name? Default: **`org-members:read`**, matching `usage:read`. I used `org-members` above. Confirm whichever form ADR-0021/0022 uses.
3. Include pending invitations now? Default: **no**, a follow-up (separate query and PII surface).
4. Paging: offset, or a keyset cursor? Default: **offset**, consistent with `Page[T]`.
5. Should the member `status` also be filtered by org status or disabled users in the count? Default: list disabled users, as the ticket says.
