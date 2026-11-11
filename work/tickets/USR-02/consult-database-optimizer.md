# consult-database-optimizer — USR-02

## Findings
- SQLAlchemy 2.1.8 / Alembic 1.20.1: `postgresql_nulls_not_distinct=True` on `UniqueConstraint`/`Index` is supported (SQLAlchemy ≥ 2.0.16), and Alembic's PG impl compares it (`_dialect_options`) → `alembic check` stays drift-free. `env.py` sets only `compare_type=True`, so server defaults and CHECKs are **not** compared — they must be hand-written correctly.
- Naming convention `uq_%(table_name)s_%(column_0_name)s` gives ambiguous names for multi-column uniques (`uq_permissions_app_id`); FKs likewise (two composite FKs on one table starting with the same column would collide).
- §3 cycle: `apps.default_role_id → roles` while `roles.app_id → apps`.
- Scope conflict: the ticket defers the `role_assignments.org_id` FK to USR-06, but identity-architecture.md §3 already shows `org_id → organizations`, and `organizations` exists (ADM-001). USR-06 only moves the module and adds `created_by` (same table name), so an FK added now survives.
- Existing migrations are hand-cleaned autogenerate with `op.f()` names; `citext` is already installed.

## Recommendations
1. **Tables (AC1, R9):** UUID PKs, `timestamptz`.
   - `apps`: `key String(63)` unique + CHECK `key ~ '^[a-z][a-z0-9-]{1,62}$'`; `status` CHECK (organizations pattern); `join_policy String(20) server_default 'open'` CHECK `IN ('open','approval','closed')` named `join_policy_valid` → `ck_apps_join_policy_valid`; `org_mode server_default 'none'` CHECK `IN ('none','required')`; `origins ARRAY(Text) NOT NULL server_default '{}'::text[]`.
   - `permissions`: + `is_admin bool NOT NULL server_default false`; optional CHECK `key ~ '^[a-z0-9-]+:(\*|[a-z0-9-]+:[a-z0-9-]+)$'` (the `<app>:` prefix = app key needs a service check; a CHECK can't look at another table).
   - `roles`/`permissions`: `TimestampMixin`.
2. **Uniques:** explicit names, e.g. `UniqueConstraint("app_id","key", name="uq_permissions_app_id_key")`. Don't change `NAMING_CONVENTION` in this ticket (R7).
3. **Same-app integrity (AC2) — DB backstop + service check (the 422 comes from the service):**
   - `UNIQUE (id, app_id)` on `roles` and `permissions`.
   - `role_permissions(role_id, permission_id, app_id)` PK `(role_id, permission_id)`; composite FKs `(role_id, app_id) → roles(id, app_id)` and `(permission_id, app_id) → permissions(id, app_id)`, both `ON DELETE CASCADE`, explicitly named. (Adds a column not in the spec.)
   - `apps.default_role_id`: composite FK `(id, default_role_id) → roles(app_id, id)` with `use_alter=True`, explicit name (MATCH SIMPLE skips it when NULL). Migration order: `apps` → `roles` → `op.create_foreign_key(...)`; `downgrade()` drops that FK first. `ON DELETE RESTRICT` (SET NULL on a composite FK would also null `apps.id`) → deleting an app's default role = service 409.
4. **Assignments (AC1, AC6):** `role_assignments`: `user_id → users CASCADE`; `role_id → roles RESTRICT` (service 409 "role in use", or revoke first); `org_id uuid NULL → organizations CASCADE` (FK now — open question 1); `granted_by → users SET NULL`; `expires_at timestamptz NULL`; `created_at`.
   - Uniqueness: `UniqueConstraint("user_id","role_id","org_id", name="uq_role_assignments_user_role_org", postgresql_nulls_not_distinct=True)` — one object, clean Alembic compare, single `ON CONFLICT` target (vs two partial unique indexes). Needs PG ≥ 15 (PG 17 OK).
   - `effective_roles`: `ra JOIN roles r ON r.id = ra.role_id WHERE ra.user_id = :u AND r.app_id = :a AND (ra.org_id IS NULL OR ra.org_id = :o) AND (ra.expires_at IS NULL OR ra.expires_at > now())` — DB `now()`, not the app clock. The unique index (leading `user_id`) covers it. Add `ix(role_id)`, `ix(org_id)` for FK checks/deletes. A partial index on `expires_at` is useless (`now()` can't be in a predicate).
5. **Apps delete:** don't cascade silently into assignments. `roles.app_id`, `permissions.app_id` `ON DELETE CASCADE`; the service refuses (409) to delete an app with live assignments, or soft-disables via `status` (under R4, a cascade would also wipe access without audit).
6. **origins:** validate in Pydantic, not the DB — exact `scheme://host[:port]`, no path/trailing slash/`*`, https only (except `http://localhost`), lowercase, dedupe. Optional DB backstop: CHECK `cardinality(origins) <= 50`.
7. **Seed (AC4):** data migration in the same or a follow-on revision with fixed UUID literals and `INSERT … ON CONFLICT DO NOTHING`; `downgrade()` deletes by those ids. Inserts: app `admin` (`join_policy='closed'`, `default_role_id` NULL), permission `admin:*`, role `admin:super-admin` (`is_system=true`), and their `role_permissions` link. Granting super-admin to the first user is runtime (CLI/startup config, idempotent via NULLS NOT DISTINCT) — never in a migration (must not depend on users). Leave the full `admin:` permission catalog to ADM-002.
8. **ETag (AC7):** sha256 of sorted rendered catalog JSON — no `catalog_version` column, no triggers.
9. **Drift (R2):** import all five models in `app/db/models.py`; autogenerate then hand-edit (use_alter FK, array default, CHECKs, seed); run `alembic upgrade head && alembic check && alembic downgrade -1 && alembic upgrade head`.

## Risks
- If the ORM `server_default` for `origins` doesn't exactly match the migration, nothing catches it (`compare_server_default` off) — write it identically.
- `use_alter` FK: dropping tables before that FK in `downgrade()` fails.
- Cascades from `users`/`organizations` delete assignments without an audit trail — acceptable now; ADM-004 should wrap these deletes.
- The member-role builder writes `role_permissions` from a snapshot; permissions added later aren't added to `<app>:member` automatically (matches D9 "data not code"; document it).
- Postgres < 15 in CI/local would fail `NULLS NOT DISTINCT` — `docker-compose`/`ci.yml` are expected to be 17; check.

## Open questions
1. Add the `org_id → organizations` FK now (recommended), or defer to USR-06 per the ticket? (Ticket vs identity-architecture.md conflict.)
2. Is the extra `role_permissions.app_id` column (composite FKs) acceptable, or service-only enforcement with exactly the spec's columns?
3. `<app>:*` as a real `permissions` row (recommended, visible in the catalog) or implicit on super-admin-flagged roles?
4. App deletion: hard delete with a 409 guard, or soft delete via `status` only?
