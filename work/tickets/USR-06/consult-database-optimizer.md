## Consult: database-optimizer, USR-06 (org-admin-api, PostgreSQL 17 / Alembic)

Read-only. Based on the ticket, the existing models and migrations (`organizations`, `rbac`, `email_tokens`, `audit_log` and its guards), and `docs/identity-architecture.md` §3/§8. I did not run anything.

### Findings
1. **No table move is needed.** "Move to the Identity module" is a Python-only change: relocate the model out of `app/modules/organizations/` into the Identity module and keep `__tablename__ = "organizations"`. Keep `app/db/models.py` importing it. The DB change is one additive `ADD COLUMN created_by`. Do not rename or recreate the table, because that would risk the data and the constraint names. Current constraint names are `pk_organizations`, `uq_organizations_slug` and `ck_organizations_status_valid`.
2. **Current `role_assignments.org_id` state** (`b638a7c84c31`):
   - It is `uuid NULL` with no FK, and `ix_role_assignments_org_id` already exists. The FK will reuse that index, so there is no new index.
   - The unique constraint `uq_role_assignments_user_role_org (user_id, role_id, org_id)` is declared `NULLS NOT DISTINCT` (PG15+, fine on 17). That already gives exactly one app-scope row (org_id NULL) per user and role, and one row per user, role and org.
   - Do not change that constraint.
3. **`audit_log` has no FKs.** `org_id` and `target_id` are snapshot columns. The audit triggers (`audit_log_immutable`, `audit_log_stamp_occurred_at`) cover only `audit_log`, so they do not interact with the org migration. Deleting an org never touches audit rows, which is the intended design. The migration needs no audit changes.
4. **Admin org delete exists today** (`DELETE /api/v1/organizations/{id}`, `repository.delete` through `session.delete`). Once the FK exists, behaviour changes for orgs that have role assignments. See Recommendation 2.
5. **Existing data.** `role_assignments.org_id` may already hold values with no matching org, if tests or manual grants wrote one. The FK `ADD CONSTRAINT` would fail on those rows. See Recommendation 2, which preflights for this.
6. **Emails use CITEXT** (`users.email`). The invitations email column should use the same type, so matching and uniqueness are case-insensitive. The CITEXT extension is already installed.
7. **Token pattern to copy** from `email_tokens`: `token_hash bytea` unique with `CHECK octet_length = 32`, `expires_at`, `used_at`, a `CHECK expires_at > created_at`, a partial index `WHERE used_at IS NULL`, and `op.f()` constraint names. Reuse the same shape.
8. **Constraint and index naming.** Alembic does not compare CHECKs, so CHECK texts must match between the model and the migration exactly. Use the naming convention (`op.f(...)` or explicit names) as the other migrations do.

### Recommendations

**1. Migration A: `organizations.created_by`** (AC1)
- `ADD COLUMN created_by uuid NULL` with an FK to `users.id` and `ON DELETE SET NULL`. Do not use CASCADE: deleting a user must not delete the org. Name it `fk_organizations_created_by_users`.
- Add an index `ix_organizations_created_by` (partial `WHERE created_by IS NOT NULL` is fine). The FK needs it for user-delete SET NULL scans and for "orgs I created" lookups.
- Existing rows stay NULL, with no backfill. A column with no default is a metadata-only change, so there is no table rewrite and no lock concern.
- `downgrade()` drops the index, the FK and the column. This is lossless for everything except `created_by` itself, which is acceptable and should be stated in the docstring.
- Model: `created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))`. Add it to the admin response schema as optional and additive only (R9: no breaking change). Admin create leaves it NULL (or the admin's id if the plan wants it; either is compatible).

**2. Migration B: FK `role_assignments.org_id`** (AC2)
- `op.create_foreign_key("fk_role_assignments_org_id_organizations", "role_assignments", "organizations", ["org_id"], ["id"], ondelete=...)`.
- **ON DELETE choice.**
  - Option 1, `ON DELETE RESTRICT` (or NO ACTION). A non-empty org cannot be deleted, and the admin DELETE returns 409. This matches the existing `role_assignments.role_id` FK, which also has no ON DELETE. It is the safest for an identity table.
  - Option 2, `ON DELETE CASCADE`. Deleting an org silently removes everyone's org roles, and also invitations if those cascade. Also workable, but it is destructive and the audit entry then has to record what was dropped.
  - Recommend RESTRICT plus a service-level 409 `AppError` on admin delete when members exist. The alternative is to have admin delete explicitly remove assignments in the same transaction and audit it.
  - **Open decision, see Q1.** Whatever is chosen needs a test, and `tests/test_organizations.py` delete tests need checking.
- **Preflight in the same migration.** Before adding the FK, run `DELETE FROM role_assignments WHERE org_id IS NOT NULL AND org_id NOT IN (SELECT id FROM organizations)`, or raise with a clear message. In practice no org-scoped assignments should exist yet. Raising is safer for an identity table; the migration should fail loudly rather than delete grants silently.
- Large-table caution: `ADD CONSTRAINT ... FOREIGN KEY` validates the whole table under a SHARE ROW EXCLUSIVE lock. `role_assignments` is tiny, so a plain add is fine. If you want to be strict, use `NOT VALID` then `VALIDATE CONSTRAINT`, but that is not worth it here.
- Update the model: `org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"))`. Remove the "FK added in USR-06" comments in both the model and the original migration comment (do not edit the old migration, only the model).
- `downgrade()` simply drops the FK.
- **NULL semantics.** Keep NULL for "no org". The `NULLS NOT DISTINCT` unique constraint already handles it. Optionally add `CHECK (org_id IS NULL OR ...)`. Nothing else is needed. Never use a 0 or nil-UUID sentinel.
- **Cross-app guard** (recommended). An org role must belong to an org-scoped app, and an org assignment must use a role of that app. The DB cannot enforce "role's app has org_mode=required" without a trigger. Enforce it in the service and test it. Do not add a trigger.

**3. Migration C: `invitations` table** (AC4)

Suggested columns:
- `id uuid PK`
- `org_id uuid NOT NULL FK organizations.id ON DELETE CASCADE`. Invitations are transient, so cascade is right.
- `role_id uuid NOT NULL`
- `app_id uuid NOT NULL`, with a composite FK `(role_id, app_id) -> roles(id, app_id)`. This copies the existing same-app pattern (`uq_roles_id_app_id` exists) and prevents cross-app roles at the DB level. Alternatively drop `app_id` and derive it through `role_id`; the composite is the stricter option.
- `email citext NOT NULL`
- `token_hash bytea NOT NULL UNIQUE` with `CHECK octet_length(token_hash) = 32`
- `invited_by uuid NULL FK users.id ON DELETE SET NULL`
- `expires_at timestamptz NOT NULL`, `created_at timestamptz NOT NULL DEFAULT now()`, `CHECK expires_at > created_at`
- `accepted_at timestamptz NULL`, `accepted_by uuid NULL FK users.id ON DELETE SET NULL`. This gives single use as a used marker, plus an audit trail.
- optional `revoked_at timestamptz NULL`

Uniqueness per org and email: a **partial unique index** `uq_invitations_org_id_email_pending ON invitations (org_id, email) WHERE accepted_at IS NULL [AND revoked_at IS NULL]`. This permits history rows but only one pending invite per org and email. Expiry cannot be part of the predicate, because `now()` is not immutable. So re-inviting after expiry must either update the old row (new token_hash and expires_at, "reissue") or revoke and insert. Prefer reissue as an `UPDATE` in the same transaction, with `ON CONFLICT` handled by the service. A race on concurrent invites then yields a unique violation, which the service maps to 409 or an idempotent reissue.
- Other indexes: `ix_invitations_org_id` is covered by the leading column of the partial unique, but add a plain `(org_id)` index only if listing by org is needed. FK indexes are needed for `role_id` (or the composite) and `invited_by`. Partial indexes are enough for `invited_by` and `accepted_by` (`WHERE ... IS NOT NULL`). Add `(email) WHERE accepted_at IS NULL` for "pending invitations for my verified email" listings, if that endpoint is planned.
- `token_hash` stays unique. Generate the token with 32 bytes of CSPRNG and store `sha256` of it (same as `email_tokens`). Compare via the DB unique lookup, never in app string compare. The plaintext goes only in the email link and must never be logged (R11).
- **Accept atomically** to enforce single use under concurrency:
  ```sql
  UPDATE invitations SET accepted_at = now(), accepted_by = :uid
  WHERE token_hash = :h AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now()
  RETURNING org_id, role_id, email;
  ```
  Then verify in the same transaction that `email = user.email` (CITEXT) and `email_verified_at IS NOT NULL`, and insert the `role_assignments` row (the unique constraint makes a second accept idempotent; use `ON CONFLICT DO NOTHING`). Do all of this in one transaction. If the email check fails, roll back so the invitation is not burned.
- Return the same generic 404/410-style error for unknown, expired, used and wrong-email tokens (do not leak which).
- Add a purge or cleanup approach later (a CLI, like the audit purge). It is not needed now: expired rows are harmless.
- `downgrade()` drops the indexes and the table. This is lossy only for invitations. State that in the docstring.

**4. Org owner role seeding per app** (AC3)
- Do not seed `calechip` roles in a migration unless the `calechip` app row already exists. The only seeded app is `admin` (fixed ids `...a1`, `...b1`, `...c1`). Apps are created through the admin API. So "owner role per app" should be a **service-level rule**, not static migration data.
- Recommended: a new `roles.is_org_owner boolean NOT NULL DEFAULT false` with a partial unique index `uq_roles_app_id_org_owner ON roles (app_id) WHERE is_org_owner`, giving one org-owner role per app, found without parsing keys. The alternative is the convention `<app>:org-owner` plus `is_system=true`, found by key. The flag is more robust (keys are editable, `is_system` is already used for other meanings). It is a small additive column (metadata-only, default false). It adds an RBAC model change, so the planner must decide; see Q2.
- If you go with the key convention instead (`<app>:org-owner`, `is_system = true`), then ROLE_KEY_RE allows it (`^[a-z][a-z0-9-]{1,31}:[a-z][a-z0-9-]{0,47}$`, name part `org-owner` is valid). Creating it when an app is created or switched to `org_mode=required` must be an idempotent upsert in the same transaction, with an audit entry (R4).
- Seed the `<app>:org:create` permission and attach it to the app's default role at the same time, also idempotent. Permission key `calechip:org:create` matches `PERMISSION_KEY_RE`. The owner role needs the org-management permissions, for example `<app>:org:invite` (name to be chosen by the planner) and `<app>:org:manage`, plus whatever the app wants.
- Existing apps already in `org_mode=required` (calechip in production?) need a **data backfill**. Either do it in a migration (`INSERT ... SELECT FROM apps WHERE org_mode='required' ... ON CONFLICT DO NOTHING`, with a deterministic `uuid_generate`/`gen_random_uuid()`) or provide an idempotent service function that runs on org-mode change. If calechip is already `required` in prod, the migration backfill is needed (Q3). Keep the SQL idempotent and the downgrade a no-op or delete only what it created (identify by `is_org_owner`/`is_system`; prefer no-op, because deleting roles fails on assignments).
- Org creation itself, in one transaction: insert the org with `created_by`, insert the invitation or assignment for the owner (`role_assignments` with `org_id` set, `granted_by` = creator), write the audit entry. A slug unique violation returns 409.

**5. Audit** (AC6)
- No schema change. New `AuditAction` values (`org.created`, `org.invitation_created`, `org.invitation_accepted`, and the rest) must match `^[a-z_]+(\.[a-z_]+)+$` and be at most 64 characters. Set `org_id` and `app_key` on the entries, and `target_type`/`target_id` as a pair. Write the audit row in the same transaction as the change (R4/ADR-0009).
- Do not put the invitee email or token in the audit `changes` (use a user id or a masked email hash if needed, per R11). `tests/test_audit_coverage.py` probably enumerates mutating routes, so add the new routes there.

**6. Alembic drift / `alembic check`** (R2)
- One revision per concern (or a single revision with three clearly separated steps). `down_revision` must be the current head. The latest file by date is `20261115_e91b6c3d58a2_audit_log_db_guards.py`, but verify the single head with `alembic heads`; other `20261115`/`20261116` revisions are possible.
- To avoid drift, every model constraint must be declared both in the model and the migration with the same name. Pay particular attention to the partial unique indexes (`postgresql_where` in both), the `NULLS NOT DISTINCT` option, and the CITEXT column type (`from sqlalchemy.dialects.postgresql import CITEXT`).
- Alembic does not compare CHECKs, so keep their text identical and add a schema test like `tests/test_rbac_schema.py`. Add `tests/test_orgs_schema.py` covering: FK exists, RESTRICT behaviour, invitation unique-pending, token_hash length, same-app composite FK, `created_by` SET NULL.
- Add a migration test that upgrades from a database at the previous head with a pre-existing organization row and an org-scoped assignment, to prove data survival, then downgrade and re-upgrade.
- Import the new models in `app/db/models.py`.

**7. Query and index notes for the endpoints**
- "List my orgs per app" is `role_assignments JOIN roles ON role_id JOIN organizations ON org_id WHERE user_id = :u AND roles.app_id = :a AND org_id IS NOT NULL AND (expires_at IS NULL OR expires_at > now())`. The unique constraint's leading `user_id` serves it, and the join to `roles` uses the PK. This is fine at expected sizes. No extra index is needed. Use `DISTINCT` on org_id because one user can hold several roles in an org.
- Token issuance with `org_id`: select roles for `(user_id, org_id)` plus `org_id IS NULL`, filtered by `roles.app_id`. A single query with `WHERE user_id=:u AND (org_id IS NULL OR org_id=:o)` is served by the unique index prefix. Make sure expired assignments (`expires_at`) are filtered the same way the existing token query does.
- Suspended orgs (`status = 'suspended'`) should be refused or excluded when issuing org tokens. Decide this with the planner (Q4).

### Risks
- **FK ON DELETE choice** changes admin-delete behaviour (an existing endpoint). The R9 "no breaking change" rule applies, so 409 instead of 204 for an org with members must be called out. If tests exist that create an org with members and delete it, they will break.
- **Orphan org_ids** already in `role_assignments` would block the FK (Finding 5). Mitigated by the preflight.
- **Single-use invitations under concurrency.** Without the conditional UPDATE above, two parallel accepts can both succeed. Test with two concurrent sessions.
- **Re-invite/reissue** logic around the partial unique index. If a pending invite is silently overwritten, the previous link stops working; that is acceptable but should be documented.
- **Email matching:** CITEXT matches case-insensitively but not Unicode-normalised or dot/plus-address variants. Acceptable; document it. Never accept an invitation for an unverified email (spec: verified email required).
- **Owner removal / last owner.** Nothing in the DB prevents an org from ending up with no owner. Not in scope for USR-06 unless the planner adds it; log it as a follow-up (R7).
- **Downgrade is lossy** for `invitations` and `created_by`. Production does not downgrade past these, consistent with the other revisions; say so in the docstring.
- **platform-wide orgs (OQ1).** Orgs are not tied to an app, so one org can hold roles from several apps. The service must check that the invited role's app is org-scoped, and token issuance filters roles by the audience app. Adding `organizations.app_key` later stays additive, as the ticket assumes.
- **Slug uniqueness is global** (`uq_organizations_slug`). A user-created org can squat slugs that admins might want. Consider slug validation/reserved words in the service.

### Open questions for the planner
1. **ON DELETE for `role_assignments.org_id`:** RESTRICT with a 409 on admin delete (recommended), or CASCADE? Does the owner accept the change in admin DELETE behaviour for orgs with members?
2. **How to identify the owner role per app:** new `roles.is_org_owner` flag with a partial unique index (recommended), or the key convention `<app>:org-owner`? This touches the RBAC model (ADR-0003), so confirm it is in the USR-06 scope.
3. **Is any app already `org_mode='required'` in production** (calechip)? If yes, the migration must backfill the owner role and `org:create` permission. If not, seed it when the app is created or switched.
4. **Suspended orgs:** should token issuance and invites refuse a `suspended` org? Recommended yes (403).
5. **Invitation details:** default TTL (suggest 7 days), whether the invited role is limited to the app's org roles (recommended) or any role of an org-mode app, whether a revoke or list endpoint is in scope, and whether the invite accepts an already-existing member (idempotent) or errors.
6. **Who may invite** ("a member with the right org permission"): the permission name (for example `<app>:org:invite`), and whether it is seeded onto the owner role only.
7. **Does the admin API also need `created_by` set** when staff create an org (staff user id), or does it stay NULL for admin-created orgs?

Files reviewed (absolute paths):
- /home/user/org-admin-api/work/tickets/USR-06/ticket.md
- /home/user/org-admin-api/app/modules/organizations/models.py
- /home/user/org-admin-api/app/modules/rbac/models.py
- /home/user/org-admin-api/app/modules/users/models.py
- /home/user/org-admin-api/app/modules/audit/models.py
- /home/user/org-admin-api/migrations/versions/20261110_b1c724bc5edb_create_organizations.py
- /home/user/org-admin-api/migrations/versions/20261111_b638a7c84c31_create_rbac.py
- /home/user/org-admin-api/migrations/versions/20261112_91a78d0496f9_create_email_tokens.py
- /home/user/org-admin-api/migrations/versions/20261115_c3f1a9d27b60_create_audit_log.py
- /home/user/org-admin-api/migrations/versions/20261115_e91b6c3d58a2_audit_log_db_guards.py
- /home/user/org-admin-api/docs/identity-architecture.md
