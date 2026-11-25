# Consult — database-optimizer — USR-33 (2026-11-25)

**Findings**
- `users.display_name` is `String(100)` and nullable (`app/modules/users/models.py:31`). The table has only a `status_valid` CHECK (`:20`), so the length is enforced only by the column type.
- `updated_at` comes from `TimestampMixin` (`app/db/base.py:38-39`). Its `onupdate=func.now()` is transaction-scoped and can repeat the old value.
- USR-29 `updated_since` filters on `User.updated_at > …` (`app/modules/rbac/repository.py:691`). A name change that bumps `updated_at` therefore reaches the member sync with no extra code.
- A CHECK precedent exists: `usage/models.py:51` uses `char_length(display_name) BETWEEN 1 AND 100`. The naming convention (`base.py:9`) gives `ck_users_<name>`.
- Strict-bump and If-Match precedent: `bumped_updated_at` (`base.py:17-23`) is used with a conditional UPDATE in the organizations and entitlements repositories (ADM-024).

**Recommendations**
1. **Add the CHECK now.** Use `CHECK (display_name IS NULL OR char_length(display_name) BETWEEN 1 AND 100)`, named `display_name_length`, in the model and in a new migration after head `c91e4b7a3d58`. Nothing can write the column today, so every row is NULL. Add it plain rather than `NOT VALID` + `VALIDATE`, because the table is small and the lock is brief. Include a pre-check in the plan: `SELECT count(*) … WHERE char_length(display_name) NOT BETWEEN 1 AND 100`. If you want zero lock risk, use `ADD CONSTRAINT … NOT VALID` followed by `VALIDATE CONSTRAINT`, which also fits in one migration.
2. **Downgrade:** `op.drop_constraint("ck_users_display_name_length", "users", type_="check")`. Pass the full convention-expanded name, as earlier migrations do. Run `alembic check` to confirm there is no drift.
3. **Bump `updated_at` explicitly on every name write**, using `bumped_updated_at(User.updated_at)`. Don't rely on `onupdate`. Skip the bump when the new value equals the old one, so no-op writes don't create `updated_since` noise.
4. **No index.** The column is never filtered or sorted on.
5. **Concurrency:** use last-write-wins, with no If-Match. It is a single scalar field, and self and staff edits are rare and idempotent. The audit row records who wrote last. Write inside one transaction with the audit insert (R4), using a plain UPDATE with no read-modify-write.

**Risks**
- The bump also moves USR-29 `updated_since` for every org the user is in. That is intended, and the lag is the same as for the other `users` writes.
- The CHECK constrains the database to the same length as the API (1–100 characters). Python `len` and Postgres `char_length` both count code points. NFC normalization changes the count but happens before the write, so they agree.

**Open questions for Ming**
- Add the CHECK in this ticket? Default: yes, plain add in one migration. The ticket stays on the High path because it ships a migration.
- Concurrency: last-write-wins, or If-Match like ADM-024? Default: last-write-wins.
