# Consult — database-optimizer — ADM-028 (2026-11-19)

## Findings (database-optimizer, read-only consult for ADM-028)

Read: ADM-028/029 tickets, spec §5.12 (lines 135-180), `app/modules/join_requests/models.py`, `app/modules/audit/models.py` and `actions.py`, `app/core/rate_limit.py`, and the advisory-lock pattern in `app/modules/auth/repository.py:208-260`. I did not read the join/org-create service code or the migrations in full. Confirm the cookie-session and membership helpers when you plan.

- **Model pattern to copy.** `AppJoinRequest` uses `UUIDPkMixin`, `String(20)` status plus a `CheckConstraint`, `postgresql_where` partial indexes, and FKs with explicit `name=`. The CHECK and index `where` texts must match the migration exactly (Alembic does not compare CHECKs).
- **Cap enforcement.** The repo already enforces daily caps in the DB with `pg_advisory_xact_lock(hashtextextended(key, 0))` followed by a count over `now() - interval '24 hours'` on the DB clock (`EmailSendLogRepository`). Reuse it. A plain count-then-insert races under concurrent submits. The in-process rate limiter is per-process and does not satisfy the "enforced in the DB" criterion.
- **Audit.** `audit_log` has no FKs, and actions must match `^[a-z_]+(\.[a-z_]+)+$`. Add `AuditAction.BUG_REPORT_CREATED = "bug_report.created"` and `AuditTarget.BUG_REPORT`. Set `app_key`, `org_id` and `target_id` on the row. `changes` should hold only counts and ids (attachment_count = 0 for now). Do not put `user_agent` content into `changes`. The audit row has its own `user_agent` and `ip` columns filled by the existing mechanism. Check that this is the request's UA, which is fine, and not the report's field.
- **Rate limits.** The `Scope` literal and `_LIMITS` in `rate_limit.py` need a new `bug_report_submit` scope, with settings `bug_report_submit_rate_limit` and `..._window_seconds`, and an entry in `.env.example`. `rate_limit()` is per-IP only. The per-user limit therefore needs either a second `limiter.hit("bug_report_user", str(user_id), ...)` call in the service or router dependency after the session is resolved, or the DB cap alone. Recommendation: keep both. The in-process per-user hit is cheap and runs before the DB lock. The DB cap is the authority.

## Recommendations (schema)

Table `bug_reports`:
- `id` uuid PK (`UUIDPkMixin`).
- `app_id` uuid NOT NULL, FK `apps.id`. Use ON DELETE CASCADE only if app deletion is meant to erase user content. Otherwise use RESTRICT, so a delete with reports is refused. ADM app delete is a staff action, and 365-day retention exists, so RESTRICT is safer. Planner to decide and document.
- `app_key`: do not duplicate it. Join to `apps` (ADM-029 lists across apps and needs `apps.key`).
- `user_id` uuid, FK `users.id`. Spec §5.12 says reports of a disabled reporter stay, which is easy since users are disabled and not deleted. Choose `ON DELETE SET NULL` and make the column nullable. It would otherwise block or erase on user hard delete. If NULL is allowed, the open-cap and filters must tolerate it.
- `org_id` uuid NULL, FK `organizations.id` ON DELETE SET NULL. Validate on submit that the caller is a member of that org and that the org belongs to this app, or store it only if valid. Otherwise a user can attach a foreign org id (an IDOR-style leak of org names to staff). Decide at PLAN.
- `description` Text NOT NULL, with `CHECK (length(btrim(description)) BETWEEN 1 AND N)`. Suggest N = 5000. Put the same bound in the Pydantic schema, and make the DB check a backstop.
- `page_url` String(2048) NULL, stored as scheme://host/path only. Normalise in the schema with `urlsplit` and accept only http/https. Add a CHECK `page_url !~ '[?#]'` as a backstop.
- `app_version` String(64) NULL. `user_agent` String(512) NULL (matches the audit column), truncated or validated to the cap.
- `status` String(20) NOT NULL DEFAULT 'open', CHECK `status IN ('open','done')`.
- `resolution_note` Text NULL, with a length CHECK (ADM-029). Adding it now avoids a second migration.
- `handled_by` uuid NULL, FK `users.id` ON DELETE SET NULL. `handled_at` timestamptz NULL.
- `created_at` timestamptz NOT NULL DEFAULT now().
- `updated_at` is needed for the ADM-029 `If-Match` (ADM-024) etag. Check the ADM-024 pattern and use `TimestampMixin` if that is what the etag is built from. Retrofitting it later costs a migration.

Consistency CHECK. Add it in ADM-028 so ADM-029 only exercises it:
- `(status = 'done') = (handled_at IS NOT NULL)`.
- Do not include `handled_by IS NOT NULL` in the equivalence. With `handled_by` SET NULL on user delete, a done report would violate it. Use `handled_by IS NULL OR status = 'done'` plus the `handled_at` equivalence above.

Indexes, all with `id` as the stable tie-breaker:
- `ix_bug_reports_created_at_id` on `(created_at, id)`. A backward scan serves the staff list (newest first), and the same index serves the 365-day purge (ADM-031).
- `ix_bug_reports_app_id_status_created_at_id` on `(app_id, status, created_at, id)`, for the ADM-029 app and status filters.
- Reporter index `(user_id, created_at)`, as a partial index `WHERE user_id IS NOT NULL`. It serves both the 24 h cap count and the ADM-029 reporter filter.
- Partial index `(user_id, app_id) WHERE status = 'open'`, for the open-cap count.
- `handled_at` partial index `WHERE status = 'done'`, for the ADM-031 attachment retention scan. This can wait for ADM-031.
- Index every FK: `org_id` partial `WHERE org_id IS NOT NULL`, and `handled_by` is optional (low cardinality, rarely joined).

Caps:
- Daily cap: 10 per user per 24 h, counted from `bug_reports.created_at > now() - interval '24 hours'`. This needs no extra table, because reports are retained for 365 days. A user who deleted nothing cannot evade it.
- Open cap: suggest 5 open reports per user per app. The ticket leaves the number to PLAN. It is low enough to curb abuse and high enough for a legitimate user. Make it a setting (`bug_report_open_cap_per_user_app`) and keep 10/day as a setting too.
- Lock key: a single `pg_advisory_xact_lock(hashtextextended('bug_report:' || user_id, 0))`, taken once. Do both counts under it, then INSERT. This serialises one user's submits only.
- Error codes: the daily cap and open cap should answer 429 with the existing envelope (per the ticket's AC) and distinct stable codes. Include `Retry-After` for the daily cap, computed from the oldest report in the window (`oldest + 24h - now`), as `SendWindow` already does.

Migration:
- Single revision. Place it with `down_revision` = the latest head (check `alembic heads`; the newest file I saw is `20261118_c7a3e91f5d28_usr15_join_requests_cancelled.py`, but confirm).
- `downgrade()` drops the indexes, then the table. Keep the `bug_reports` DDL in one revision.
- Attachments: recommend ADM-030 creates `bug_report_attachments` with FK to `bug_reports` ON DELETE CASCADE. That keeps this migration small. ADM-028 needs no attachment count column. Reporting `attachment_count: 0` in the audit is enough.
- Permissions seed (`admin:bug-reports:read` and `write`) belongs to ADM-029, as its ticket says. `app/db/models.py` must import the new model (R2).

## Forward-compatibility for ADM-029 to 031
- ADM-029 filters (app, status, reporter, date range, sort `created_at DESC, id DESC`) are covered by the indexes above. Pagination count is by `created_at, id`.
- Detail shows reporter email and display name via a join to `users`, so `user_id` should stay a real FK. With SET NULL, the staff schema must tolerate a null reporter.
- ADM-031 needs the `created_at` index for the 365-day purge and the `handled_at` partial index for the 90-day attachment purge. The purge should batch with `FOR UPDATE SKIP LOCKED`, like `audit/purge.py`.

## Risks
- Racing submits bypass the caps if the advisory lock is skipped (the main failure mode).
- The in-memory limiter is per-process. With multiple workers the per-user in-process limit is only a soft guard, so the DB cap is authoritative.
- A foreign or unvalidated `org_id` leaks or mislabels data. Validate membership.
- Stored page URL with userinfo (`https://user:pass@host/`) leaks credentials. Strip userinfo and the port as part of "scheme/host/path" normalisation, or reject URLs that carry userinfo.
- User content (description, UA) is untrusted text. Staff UIs must escape it, and the API must never log it (R11). Also check that the generic request-validation error handler does not echo the submitted description in 422 `details`.
- `ON DELETE CASCADE` from `apps` would silently destroy reports. Choose deliberately.
- The 90-day attachment retention depends on `handled_at`, which is why the consistency CHECK is needed from day one.

## Open questions for the planner
1. Numbers: open cap per user per app (suggest 5), daily cap 10 (given), per-IP limit (suggest ~20 per hour or per window, as with `join`), description max (suggest 5000), page URL 2048, app_version 64, UA 512.
2. `apps` FK behaviour on app delete: CASCADE or RESTRICT?
3. `org_id`: validate membership and app ownership, or accept and store unvalidated? I recommend validate.
4. Is `updated_at` / an ETag source needed on this table for ADM-029's `If-Match`? Check the ADM-024 implementation before finalising columns.
5. `user_id` nullable with SET NULL (my recommendation), or NOT NULL with RESTRICT?
6. Should the per-user in-process hit run in addition to the DB cap, or should the DB cap be the only per-user control?
7. Does ADM-028 add `resolution_note`, `handled_by`, `handled_at` and the consistency CHECK now (recommended, to avoid a second migration on the same table in ADM-029), or does ADM-029 add them?

Relevant files: app/modules/join_requests/models.py (pattern), app/modules/auth/repository.py (advisory-lock cap pattern, `EmailSendLogRepository`), app/core/rate_limit.py (scope and `_LIMITS`), app/modules/audit/actions.py (new action and target), docs/spec-v1.md lines 135-180.
