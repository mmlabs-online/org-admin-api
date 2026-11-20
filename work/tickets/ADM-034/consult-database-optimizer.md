# Consult — database-optimizer — ADM-034 (2026-11-20)

**Findings**

- **Head and chain.** The chain is c4e8a2f7b916, 89fd8d667a25 (ADM-033), 5b7d1e9c3a42 (ADM-035), 8c2f6a1d4e93 (ADM-039), then d7a3f5b2e801 (ADM-030). That is one linear head, so ADM-034 must set `down_revision = "d7a3f5b2e801"`.
- **No `scope` column yet.** `app_credentials` has two CHECKs and two indexes, including the partial `ix_app_credentials_app_id_active` on `(app_id) WHERE revoked_at IS NULL` (`app/modules/app_credentials/models.py:21-28`, migration `f3a8d51c9e27:61-69`). The "at most 2 active" limit is app-level only (`service.py:34`, `service.py:119`, `repository.py:20-28`).
- **The limit is serialised by the app lock.** `issue()` calls `lock_app` first (`service.py:107`), and `authenticate()` also calls `lock_app` before `touch_last_used` (`service.py:178`). ADR-0021 §1 requires this for both scopes. Per-(app, scope) counting therefore needs no new locking, only a `scope` filter in `count_active`.
- **A partial unique index cannot enforce ≤2.** The app lock is the enforcement, as it is today.
- **ADR-0022 expects the CHECK to be extended later.** ADM-042 "extends the `app_credentials.scope` CHECK" with `usage:read`, `usage:write` and `limits:write` (ADR-0022 §10, line 100). That implies ADM-034 ships only `('manifest','releases')`.
- **The app lock already serialises first writes from one app.** The releases-key PUT passes through `authenticate`, which holds the app row lock until commit. Two concurrent PUTs for one app therefore queue. The deployer report route (ADM-036) is a different path and may not take that lock. The unique constraint `uq_app_releases_app_id_environment_version` (`releases/models.py:96`) is the real arbiter.
- **Release row constraints.** ADM-033's CHECKs cover:
  - the version grammar per environment;
  - the git_sha format;
  - `staging_version_matches_sha`;
  - `deployed_iff_deployed_at`;
  - the bounded counts for images and changes;
  - the deployment_pr and deploy_run_url allow-lists.
- **What the DB cannot check.** Element-level rules live only in the app: digest `sha256:<64hex>`, title length, control characters, ticket id and risk formats.
- **Enrichment can trip a CHECK.** Filling a NULL `git_sha` on a staging row hits `staging_version_matches_sha` (`releases/models.py:71-74`). That must come back as 422, not as an `IntegrityError` 500.
- **`updated_at` is ORM-driven.** Check `TimestampMixin` before using a Core `UPDATE`.
- **Existing indexes are enough.** The release PUT reads one row through the unique index. `ix_app_releases_*` need no change.

**Recommendations**

1. **Migration `adm034_app_credential_scope`** (AC1: existing keys behave as before). All steps are in one transaction.
   - `ADD COLUMN scope text NOT NULL DEFAULT 'manifest'`. On PG17 this is metadata-only and backfills existing rows as manifest.
   - Keep the server default. `issue()` then still works for old callers, and the ORM sets it explicitly.
   - Add `CONSTRAINT ck_app_credentials_scope_valid CHECK (scope IN ('manifest','releases'))`. Keep the CHECK text identical in `models.py`, since Alembic does not compare CHECKs.
   - Replace the partial index with `ix_app_credentials_app_id_scope_active ON (app_id, scope) WHERE revoked_at IS NULL`. Drop the old one in the same migration. The new index also serves the count query.
2. **Downgrade must delete `releases` rows before dropping the column.** Otherwise those keys silently become `manifest` keys, which is a privilege escalation. It can also break the 2-per-app limit. The order is:
   - `DELETE ... WHERE scope <> 'manifest'`;
   - drop the new index;
   - drop the CHECK;
   - drop the column;
   - recreate the old index.
   - State in the docstring that downgrade deletes releases keys, as `f3a8d51c9e27:73` does.
3. **Service layer** (AC2: wrong-scope 403).
   - Add `scope` to the model, the `CredentialView` and the issue request.
   - Make `count_active(app_id, scope)` count per scope.
   - Apply the `admin_app` refusal only when `scope == 'manifest'` (`service.py:115`).
   - `authenticate` takes the required scope and the denied audit action. Today the action is hard-coded to `APP_MANIFEST_SYNCED` at `service.py:169`.
   - Compare the scope after the secret and revoked checks, so an unauthenticated caller learns nothing.
   - Keep the order (disabled, then mismatch, then scope) deterministic and test it.
4. **Release upsert** (AC3: every PUT outcome, including the race). Use `INSERT ... ON CONFLICT (app_id, environment, version) DO NOTHING RETURNING id`, then `SELECT ... FOR UPDATE` on the row.
   - If the insert returned a row, it is a 201 `pending` with `created_via='app'`.
   - If it returned nothing, the winner has committed, so the `FOR UPDATE` read sees it under READ COMMITTED. Then decide in Python: replace while pending, no-op if identical, enrich NULL or empty fields, or 409.
   - Do not use `DO UPDATE`. The branching depends on status, and a `DO UPDATE ... WHERE` cannot give the 409 versus no-op versus enrich distinction.
   - Prefer this to an advisory lock. It uses the existing unique constraint and does not depend on the app lock, so it keeps working against the ADM-036 report route, which may not take that lock.
   - Add a real concurrency test, for example two sessions via `asyncio.gather` on a committed (non-rolled-back) engine.
5. **Enrichment and pending replace.**
   - Treat "empty" as `NULL` for scalars and `'[]'` for `images` and `changes`.
   - Compare as Python values. Normalise JSONB key order and `has_migrations` None versus false.
   - Never touch `status`, `deployed_at` or `created_via`.
   - Set `updated_at` only on a real change. Write an audit row only on a real change.
   - Pre-validate the staging sha/version consistency in the schema, so enrichment cannot raise an IntegrityError.
6. **Validation versus DB.** The Pydantic layer must be at least as strict as the CHECKs:
   - the same regexes (reuse the constants in `releases/models.py:45-51`);
   - the 10 and 200 caps;
   - `extra="forbid"`;
   - a UTF-8 control-character strip on titles.
   - I do not recommend adding JSONB element CHECKs. The cost is high and ADM-033 deliberately left them lenient.
7. **ADM-042 compatibility.** Do not include the usage scopes now. Name the CHECK `scope_valid` and note in the migration docstring that ADM-042 will drop and recreate it.

**Risks**

- **Downgrade privilege escalation** (see item 2). It is the main DB risk.
- **Core `UPDATE` skipping `updated_at`**, if `TimestampMixin` is ORM-only.
- **A 500 from an IntegrityError** if a CHECK-only rule has no schema pre-check. This applies to staging sha/version, deployed_iff, and the images and changes caps.
- **Lock hold time.** `authenticate` holds the app lock across the whole PUT. It is acceptable at the 30/min rate limit, but keep the body parse and size check before the lock.
- **Alembic drift.** `alembic check` does not see the CHECK text, so a text mismatch is invisible. Add a migration round-trip test that asserts the constraint names.

**Open questions for Ming**

1. Should the CHECK include the ADR-0022 usage scopes now? Suggested default: no. Ship `('manifest','releases')` and let ADM-042 extend it, as ADR-0022 §10 says.
2. On a downgrade with live releases keys, should they be deleted or refused? Suggested default: delete them (revocation is not enough), and document it in the docstring.
3. Should the `admin`-app collision (org-admin-api versus admin-pan) be resolved by allowing releases keys and releases for the `admin` key only? Suggested default: yes, per ADR-0021 §11. Add a note in the docs that `admin` means org-admin-api.
4. Should the scope be added to the credential audit changes (`app_credential.issued`)? Suggested default: yes, `changes={"key_id":…, "scope":…}`. It contains no secret.

Files reviewed:
- app/modules/app_credentials/models.py
- app/modules/app_credentials/service.py
- app/modules/app_credentials/repository.py
- app/modules/releases/models.py
- app/modules/releases/repository.py
- migrations/versions/20261120_89fd8d667a25_adm033_app_releases.py
- migrations/versions/20261117_f3a8d51c9e27_usr05a_app_credentials.py
- docs/adr/0021-release-log-and-deployer-credential.md
- docs/adr/0022-usage-key-scopes.md
