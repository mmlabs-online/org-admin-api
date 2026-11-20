Status: READY

# Plan — ADM-028: Bug reports — data model and text submit from apps (cookie class)

Batch 2026-11-19-2 (plan round). **High path ticket** (migration, `app/db/`, `app/core/`, `.env.example`): stops at
PLAN for Ming's approval and is never merged by an agent. The plan is implementable with the proposed defaults under
`## Blocking questions`; Ming confirms or overrides them in the batch approval round.

## Scope summary
`docs/spec-v1.md` §5.12 (scope decided 2026-11-19):
- L147: "The app's browser posts **directly** to `/api/v1/auth/bug-reports` in the **cookie class** (R3 class 2, the
  same pattern as join and org create): `mm_device` cookie, `Origin` check, `X-MM-App: <app>`. No new auth class. The
  reporter must be **signed in** (account session), **active**, have a **verified email**, and be a **member of that
  app**. … Staff do not file reports from admin-pan (OQ21)."
- L151–153: "`id` (UUID), `app_key` (a registered app), reporter account (`user_id`), description (text; length limit
  set at PLAN), attachments …, `status`, `created_at`. On "done": the handler account (`handled_by`) and `handled_at`;
  an optional staff **resolution note** (OQ25). **Optional context** …: `org_id` (for org apps), **page URL** (stored
  as scheme, host and path only; query string and fragment are dropped), **app version**, **user agent** (capped
  length)."
- L154: "the app comes from `X-MM-App` **after the `Origin` check**, never from the body … The reporter must be a
  member of that app. … Disabled or unknown apps are refused, and **the `admin` app cannot be a report target**
  (`X-MM-App: admin` is refused like other app-scoped cookie endpoints)."
- L156: "Two states: `open` and `done`. Marking done records the handler (`handled_by`) and `handled_at`. Staff can
  **reopen** a report (`done` → `open`), which clears the handler and `handled_at`."
- L158: "The reporter submits and gets the created report id back (and the per-attachment outcome, OQ35)."
- L162: "Report creation writes an audit entry `bug_report.created` in the same transaction, carrying ids and counts
  only (report id, app key, reporter id, attachment count), never the description, context fields or attachment
  content."
- L165–168 (retention, shapes the schema): attachments purge "90 days after the report is marked done (counted from
  `handled_at`…)"; reports "deleted 365 days after creation"; "Reports of a **disabled reporter stay**".
- L171: "Per-IP and per-user rate limits on submit, in-process like the identity controls (ADR-0002). At most **10
  reports per user per 24 h**, and a **cap on open reports per user per app** (number set at PLAN). **No Turnstile**
  on submit in v1."
- L178: "the route sits under `/api/v1/auth/*` although it is not an identity action … adding `bug-reports` to that
  list (and recording the choice in an ADR or the decision log) is part of the first ticket."
- Ticket ADM-028 scope + AC1–AC6; ADM-029 ticket (next batch) for forward-compatible columns: "`status` `done` (sets
  handler = caller, `handled_at` = now) or `open` (reopen clears both); `resolution_note` …; Optional `If-Match`
  (ADM-024 pattern, 412)" and AC2 "a DB constraint keeps status and handler fields consistent".
- `work/decisions.md`: nothing on bug reports.

**Conflict flagged (spec/house pattern vs ticket AC2):** AC2 says "unverified/disabled user … (403 with a stable
code)". Every existing cookie route answers a disabled user with **401 `session_invalid`** (org create
`app/modules/organizations/auth_service.py:75-76`, token, join), and disabling a user revokes its account sessions
(ADR-0008) so `get_active_account` already returns None. The plan keeps 401 (Blocking question Q1).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | A verified member submits a report → 201 with its id; reporter and app recorded from the session and `X-MM-App` | `tests/test_bug_reports_submit.py::test_submit_201_records_reporter_and_app` (row has `user_id` = account's user, `app_id` = X-MM-App app, `status='open'`, handler fields NULL; response `{id,status,created_at}`, `Cache-Control: no-store`, rotated cookie set when the device rotates); `test_submit_body_app_key_rejected_422` |
| 2 | Refused: no/invalid cookie (401), bad Origin or missing `X-MM-App` (403), unverified/disabled user or non-member (403 with a stable code — **disabled = 401 `session_invalid`, see Q1**), `admin`/disabled/unknown app | `test_submit_no_cookie_401`, `test_submit_invalid_cookie_401`, `test_submit_foreign_account_id_401`, `test_submit_disabled_user_401_session_invalid`, `test_submit_missing_app_header_403`, `test_submit_origin_of_other_app_403`, `test_submit_unknown_app_403`, `test_submit_disabled_app_403`, `test_submit_admin_app_403_for_staff` (staff with admin roles, `X-MM-App: admin` → 403 `origin_not_allowed`, no row), `test_submit_unverified_403_email_unverified`, `test_submit_non_member_403_not_member` (`details.join_available`); every refusal writes no report and no audit row (`test_submit_refusals_write_nothing`) |
| 3 | 422 for empty/too long description and over-long optional fields; page URL stored without query or fragment | Unit: `tests/test_bug_reports_schemas.py` (all URL / text cases below). API: `test_submit_validation_422` (parametrized), `test_submit_page_url_stored_reduced` (`https://u:p@Example.COM:8443/a/b?q=1#token=x` → `https://example.com:8443/a/b`), `test_submit_org_id_*` |
| 4 | Rate limits answer 429 with the existing envelope; per-user daily cap and open-report cap enforced in the DB | `test_submit_per_ip_rate_limited_429`, `test_submit_per_user_burst_429`, `test_submit_daily_cap_429_retry_after` (11th in 24 h, across two apps), `test_submit_open_cap_429_per_app` (cap per (user, app); other app still accepted), `test_submit_old_reports_do_not_count` (rows with `created_at` 25 h ago); concurrency: `tests/test_bug_reports_concurrency.py::test_parallel_submits_at_cap_one_wins` |
| 5 | `bug_report.created` audit row in the same transaction, no description or URL; nothing logged with content or full email | `test_submit_audit_row_ids_only` (exact `changes` key set == `{"attachment_count"}`, `target_type='bug_report'`, `target_id` = report id, `app_key`, `actor_user_id`, `org_id` NULL — see Q7); `test_submit_logs_no_content` (caplog: no description, URL, UA, app version or email substring); `test_submit_audit_failure_rolls_back_report` |
| 6 | CORS answered for the app's registered origins with credentials; tests | `test_bug_reports_preflight_registered_origin` (ACAO echo, `Access-Control-Allow-Credentials: true`, allow-headers include `X-MM-App`), `test_bug_reports_preflight_unregistered_origin_no_acao`, `test_bug_reports_post_response_has_acao`; `tests/test_route_auth_classes.py` COOKIE set includes the route |
| 7 | Schema + migration (scope item 1, R2) | `uv run alembic check` clean; `tests/test_bug_reports_migration.py::test_bug_reports_down_up` (downgrade to `d9e4b2a61f73` drops the table, upgrade recreates it); `tests/test_bug_reports_schema_db.py` CHECK tests (status values, handler consistency, description length, reduced URL) via raw inserts |
| 8 | Docs: route in R3 class 2 list of `CLAUDE.md` and `identity-architecture.md` §4; decision recorded | Diff review: CLAUDE.md R3 (2), §4 table row, D28 entry, `docs/adr/0018-bug-report-submit.md` |

## Ming verification after merge
- After deploy, from a real app origin (e.g. ricotdin staging/production) with `@mmlabs/auth-client` signed in:
  submit one report and see it in the DB / audit log (`bug_report.created`). The deploy runs the migration as its
  separate step (Ming's, `docs/deploy.md`).
- None else.

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/modules/bug_reports/__init__.py` | new, empty | new module (copy organizations shape) | no |
| `app/modules/bug_reports/models.py` | new `BugReport` model + `BugReportStatus` StrEnum (`open`, `done`) | table (AC7) | no |
| `app/modules/bug_reports/schemas.py` | `BugReportCreate` (strict, `extra="forbid"`, normalisers), `BugReportCreated` response | AC1, AC3 | no |
| `app/modules/bug_reports/text.py` | pure helpers: `reduce_page_url`, `clean_description`, `check_printable` | pure logic first (rule 6), unit-testable | no |
| `app/modules/bug_reports/errors.py` | `BugReportDailyLimitError`, `BugReportOpenLimitError` | AC4 stable codes | no |
| `app/modules/bug_reports/repository.py` | `lock_reporter`, `daily_window`, `count_open`, `add` | R1, AC4 | no |
| `app/modules/bug_reports/service.py` | `BugReportSubmitService.submit` | R1 business rules | no |
| `app/modules/bug_reports/deps.py` | `get_report_app_context` / `ReportAppContextDep` (refuses `admin` with 403 `origin_not_allowed`) | AC2, spec L154 (keeps `app/modules/auth/deps.py` untouched) | no |
| `app/modules/bug_reports/auth_router.py` | `POST /auth/bug-reports` | AC1 | no (`**/auth*` triggers appsec review) |
| `app/api/router.py` | include `bug_reports_auth_router` | route mount | no |
| `app/db/models.py` | import + `__all__` `BugReport` | R2 | **yes** |
| `migrations/versions/20261119_<rev>_adm028_bug_reports.py` | create table, CHECKs, indexes; `downgrade()` drops them | R2 | **yes** |
| `app/core/config.py` | 6 settings (below) | R5, AC4 | **yes** |
| `app/core/rate_limit.py` | `Scope` + `_LIMITS` entry `bug_report_create` | per-IP limit | **yes** |
| `.env.example` | 6 keys, no values | R5 (`tests/test_config.py::test_env_example_keys_only`) | **yes** |
| `app/modules/audit/actions.py` | `AuditTarget.BUG_REPORT = "bug_report"`, `AuditAction.BUG_REPORT_CREATED = "bug_report.created"` | AC5 | no |
| `tests/test_route_auth_classes.py` | add `("POST", f"{V1}/auth/bug-reports")` to `COOKIE` (line 47-55) | R3 guard test | no |
| `tests/test_bug_reports_schemas.py` | new, unit tests of `text.py` + schema | AC3 | no |
| `tests/test_bug_reports_submit.py` | new, API tests | AC1–AC6 | no |
| `tests/test_bug_reports_concurrency.py` | new, parallel submit at cap | AC4 race | no |
| `tests/test_bug_reports_migration.py` | new, down/up (copy `tests/test_audit_org_index_migration.py`) | R2 | no |
| `tests/test_bug_reports_schema_db.py` | new, CHECK constraints by raw insert | AC7, ADM-029 invariant | no |
| `docs/adr/0018-bug-report-submit.md` | new ADR | decision outlives the ticket (loop.config Domain focus) | no |
| `docs/identity-architecture.md` | §4 table row (after L172); §8 decision log D28 (after L317) | scope item 6 | no |
| `CLAUDE.md` | R3 class (2) list gains `bug-reports` | scope item 6 | no |

Not touched: `app/core/cors.py` (prefix `/api/v1/auth/` already applies `IDENTITY_POLICY`, `cors.py:34-41, 80-82`),
`app/modules/auth/deps.py`, `app/core/errors.py`, `app/modules/rbac/**`.

## Data model / migration
Revision `20261119_<rev>_adm028_bug_reports.py`, `down_revision = "d9e4b2a61f73"` (current head,
`20261118_d9e4b2a61f73_adm021_audit_log_org_id_index.py`; re-check `uv run alembic heads` before writing — ADM-027
adds no migration per its ticket). Generate with `make migration m="adm028 bug reports"` and hand-review. One
revision, table only; **no attachment table** (ADM-030).

Table `bug_reports` (`UUIDPkMixin`, `TimestampMixin`, `Base`; model docstring repeats the join_requests warning that
CHECK and `postgresql_where` texts must match the migration exactly):

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | uuid | no | app-side uuid4 | PK `pk_bug_reports` |
| `app_id` | uuid | no | — | FK `fk_bug_reports_app_id_apps` → `apps.id` **ON DELETE RESTRICT** (Q5) |
| `user_id` | uuid | no | — | reporter; FK `fk_bug_reports_user_id_users` → `users.id` **ON DELETE RESTRICT** (Q6; users are never hard-deleted today) |
| `org_id` | uuid | yes | — | FK `fk_bug_reports_org_id_organizations` → `organizations.id` **ON DELETE SET NULL** |
| `description` | text | no | — | |
| `page_url` | varchar(2048) | yes | — | reduced form only |
| `app_version` | varchar(64) | yes | — | |
| `user_agent` | varchar(512) | yes | — | same cap as `audit_log.user_agent` |
| `status` | varchar(20) | no | server `'open'` | |
| `resolution_note` | text | yes | — | ADM-029 writes it |
| `handled_by` | uuid | yes | — | FK `fk_bug_reports_handled_by_users` → `users.id` ON DELETE SET NULL (like `join_requests.decided_by`) |
| `handled_at` | timestamptz | yes | — | |
| `created_at` | timestamptz | no | `now()` | `TimestampMixin` |
| `updated_at` | timestamptz | no | `now()` | `TimestampMixin`; ADM-029 ETag source via `bumped_updated_at` (`app/db/base.py:17`) |

CHECK constraints (naming convention gives `ck_bug_reports_<name>`; write the same `name=` in model and migration):
- `status_valid`: `status IN ('open', 'done')`
- `handled_iff_done`: `(status = 'done') = (handled_at IS NOT NULL)`
- `handler_only_when_done`: `handled_by IS NULL OR status = 'done'` (`handled_by` may become NULL by SET NULL, so it
  is not part of the equivalence)
- `description_length`: `char_length(description) BETWEEN 1 AND 5000`
- `resolution_note_length`: `resolution_note IS NULL OR char_length(resolution_note) BETWEEN 1 AND 5000`
- `page_url_reduced`: `page_url IS NULL OR page_url ~ '^https?://[^/?#@[:space:]]+/[^?#[:space:]]*$'`

Indexes (explicit names, as in `app/modules/join_requests/models.py:35-46`):
- `ix_bug_reports_created_at_id` (`created_at`, `id`) — ADM-029 newest-first list, ADM-031 365-day purge.
- `ix_bug_reports_app_id_status_created_at_id` (`app_id`, `status`, `created_at`, `id`) — ADM-029 filters; also
  covers the `app_id` FK (RESTRICT check on app delete).
- `ix_bug_reports_user_id_created_at` (`user_id`, `created_at`) — daily cap count, ADM-029 reporter filter, user FK.
- `ix_bug_reports_open_user_id_app_id` (`user_id`, `app_id`) `WHERE status = 'open'` — open cap count.
- `ix_bug_reports_org_id` (`org_id`) `WHERE org_id IS NOT NULL` — org delete SET NULL.
- (Deferred to ADM-031: `handled_at` partial index `WHERE status = 'done'`.)

`downgrade()`: drop the five indexes, then `op.drop_table("bug_reports")` (CHECKs and FKs go with the table).

ADM-029 then needs only its permission-seed migration, no change to `bug_reports`.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/bug-reports` | headers: `Cookie: __Host-mm_device`, `Origin`, `X-MM-App: <key>`, `Content-Type: application/json`. Body `BugReportCreate` (extra fields → 422): `account_id` UUID (req), `description` str (req), `org_id` UUID? , `page_url` str?, `app_version` str?, `user_agent` str? | 201 `BugReportCreated` `{id: UUID, status: "open", created_at: datetime}`; `Cache-Control: no-store`; `Set-Cookie` when the device rotated | 201; 401 `session_invalid` (no/invalid cookie — cleared; unknown/foreign `account_id`, disabled user — not cleared); 403 `app_header_required` / `origin_not_allowed` (missing header, bad Origin, unknown, disabled or `admin` app), `email_unverified`, `not_member` (`details.join_available`), `org_access_denied` (`details: {}`); 422 `validation_error`, `org_not_supported` (`details=[{"field":"org_id","reason":"not_supported"}]`); 429 `rate_limited` (per-IP, per-user burst; `Retry-After`), `bug_report_daily_limit` (`Retry-After`, `details.retry_after`), `bug_report_open_limit` (`details: {"limit": N}`, no `Retry-After`). No 404/409 (no path id, no unique field) | none — R3 class 2 (cookie), not `require_permission` |

Field rules (`schemas.py`, using `text.py`):
- `description`: strip outer whitespace; 1..5000 chars after strip (`BUG_REPORT_DESCRIPTION_MAX`, constant, matches the
  DB CHECK); reject NUL and C0/C1 control chars except `\n`, `\r`, `\t` (422; Postgres would 500 on NUL).
- `page_url`: input ≤ 2048; no whitespace/control chars; `urlsplit`; scheme `http`/`https` only and non-empty
  hostname, else 422 (`javascript:`, `data:`, `file:`, relative). Rebuild `scheme://hostname[:port]path` with
  lowercased scheme and hostname, **userinfo, query and fragment dropped**, port kept only if given and not the
  scheme default (Q8), empty path → `/`; invalid port (`ValueError` from `.port`) → 422.
- `app_version`: 1..64 printable ASCII (`^[\x20-\x7E]{1,64}$`), else 422.
- `user_agent`: 1..512, no control chars, else 422 (reject, not truncate — AC3 "over-long optional fields" → 422; Q4).
- Empty string for an optional field → 422 (send `null` or omit).
- Validation errors never echo values (`app/core/errors.py:194`, existing).

Dependency order in the route (copy `app/modules/organizations/auth_router.py:33-59`):
`dependencies=[rate_limit("bug_report_create")]` → `session` → `app: ReportAppContextDep` → `_cookie: DeviceCookieDoc`
→ `body` → `device: DeviceDep` → `ctx: AuditContextDep`; then `_finish` (cookie rotation + `no-store`; local copy of
the 4-line helper as in the orgs router).

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_bug_reports_schemas.py › test_reduce_page_url_strips_userinfo_query_fragment` | `https://u:p@Example.COM:8443/a/b?q=1#token=x` → `https://example.com:8443/a/b` |
| `… › test_reduce_page_url_default_port_dropped_and_empty_path` | `https://h:443` → `https://h/`; `http://h:80/x` → `http://h/x` |
| `… › test_reduce_page_url_rejects` (parametrized) | `javascript:alert(1)`, `data:…`, `file:///etc`, `/relative`, `https://`, `https://h:99999/`, URL with space/newline, > 2048 chars |
| `… › test_reduce_page_url_keeps_unicode_path` | non-ASCII path kept, result matches the DB CHECK regex |
| `… › test_description_rules` (parametrized) | `""`, `"   "`, 5001 chars, NUL, `\x07`, `\x85` → error; 5000 chars, text with `\n\t`, outer whitespace stripped → ok |
| `… › test_app_version_and_user_agent_rules` | 64/65 and 512/513 bounds, control chars, empty string |
| `… › test_create_schema_forbids_extra_fields` | `app_key`, `app`, `user_id`, `status`, `handled_by` → error |
| `tests/test_bug_reports_submit.py › test_submit_201_records_reporter_and_app` | AC1 |
| `… › test_submit_with_valid_org_id_201` | org app, member org stored |
| `… › test_submit_body_app_key_rejected_422` | AC1 / mass assignment |
| `… › test_submit_no_cookie_401`, `test_submit_invalid_cookie_401`, `test_submit_foreign_account_id_401`, `test_submit_disabled_user_401_session_invalid` | AC2 401 |
| `… › test_submit_missing_app_header_403`, `test_submit_origin_of_other_app_403`, `test_submit_unknown_app_403`, `test_submit_disabled_app_403`, `test_submit_admin_app_403_for_staff` | AC2 app refusals (`admin` refused before any device work: no `Set-Cookie`) |
| `… › test_submit_unverified_403_email_unverified`, `test_submit_non_member_403_not_member` | AC2 |
| `… › test_submit_org_id_not_member_403`, `test_submit_org_id_unknown_403_same_body`, `test_submit_org_id_on_org_less_app_422` | org_id validation (Q3), one answer for unknown / foreign / suspended |
| `… › test_submit_validation_422` (parametrized over the schema cases) and `test_submit_page_url_stored_reduced` | AC3 |
| `… › test_submit_per_ip_rate_limited_429` | AC4 per-IP (`Retry-After`, envelope) |
| `… › test_submit_per_user_burst_429` | AC4 in-process per-user limit (two IPs via trusted proxy header or limiter override, same user) |
| `… › test_submit_daily_cap_429_retry_after` | 10 rows (two apps) in 24 h → 11th 429 `bug_report_daily_limit`, `Retry-After` ≈ oldest + 24 h − now |
| `… › test_submit_old_reports_do_not_count` | rows at now − 25 h ignored |
| `… › test_submit_open_cap_429_per_app` | N open in app A → 429 `bug_report_open_limit`; app B accepted; one marked `done` (raw update) frees a slot |
| `… › test_submit_audit_row_ids_only` | AC5 exact audit content |
| `… › test_submit_refusals_write_nothing` | no report / audit row on 401/403/422/429 |
| `… › test_submit_logs_no_content` | AC5 / R11 caplog |
| `… › test_submit_audit_failure_rolls_back_report` | monkeypatch `AuditLogger.record` to raise; no `bug_reports` row afterwards |
| `… › test_bug_reports_preflight_registered_origin`, `test_bug_reports_preflight_unregistered_origin_no_acao`, `test_bug_reports_post_response_has_acao` | AC6 (copy helpers from `tests/test_auth_cors.py`) |
| `tests/test_bug_reports_concurrency.py › test_parallel_submits_at_cap_one_wins` | cap − 1 existing rows, 3 parallel submits on separate connections → exactly one 201, rest 429 (pattern: `tests/test_email_lock_concurrency.py`) |
| `tests/test_bug_reports_migration.py › test_bug_reports_down_up` | R2 downgrade/upgrade |
| `tests/test_bug_reports_schema_db.py › test_status_check`, `test_handled_iff_done_check`, `test_handler_only_when_done_check`, `test_description_length_check`, `test_page_url_reduced_check`, `test_app_delete_restricted_by_report` | DB backstops for ADM-029/031; RESTRICT behaviour (Q5) |
| `tests/test_route_auth_classes.py` (existing tests, COOKIE set extended) | R3 class 2 |

## Approach
1. **Model** `app/modules/bug_reports/models.py` exactly as in Data model; import in `app/db/models.py` (R2).
2. **Migration** via `make migration`, then hand-edit: explicit names, server default `'open'`, CHECK texts and
   `postgresql_where` identical to the model, working `downgrade()`. `uv run alembic check` clean (R2).
3. **Pure helpers** `text.py` + their unit tests first (rule 6). `schemas.py`: `BugReportCreate`
   (`ConfigDict(extra="forbid")`, field validators calling `text.py`), `BugReportCreated`.
4. **Config (R5)** in `app/core/config.py` next to the org settings (L63-67), all `Field(ge=1)`:
   `bug_report_rate_limit=20`, `bug_report_rate_window_seconds=3600` (per IP);
   `bug_report_user_rate_limit=3`, `bug_report_user_rate_window_seconds=60` (per user, in-process);
   `bug_report_max_per_user_day=10`; `bug_report_max_open_per_user_app=10` (Q2). Same 6 keys in `.env.example`
   without values, with one comment line.
5. **Rate limit** `app/core/rate_limit.py`: add `"bug_report_create"` to `Scope` and `_LIMITS`. The per-user limit
   uses the same `RateLimiter.hit("bug_report_user", str(user_id), RateLimit(...))` from the service (the router passes
   `request.app.state.rate_limiter` and the `RateLimit`); raises `RateLimitedError(retry_after)`. No new Scope entry
   (it is not a per-IP dependency).
6. **Errors** `errors.py`: `BugReportDailyLimitError(RateLimitedError)` with `code = "bug_report_daily_limit"`;
   `BugReportOpenLimitError(AppError)` with `status_code = 429`, `code = "bug_report_open_limit"`,
   `details={"limit": n}`, no `Retry-After` (Q2).
7. **Audit catalogue**: `AuditTarget.BUG_REPORT`, `AuditAction.BUG_REPORT_CREATED` (matches `action_format`).
8. **Repository (R1, SQLAlchemy only)**:
   - `lock_reporter(user_id)`: `select(func.pg_advisory_xact_lock(func.hashtextextended(literal("bug_report:") + <user_id text>, 0)))`
     — pattern `app/modules/auth/repository.py:212-220`. No row lock on `users` beyond `FOR SHARE` (deadlock note,
     `app/modules/users/repository.py:41-47`).
   - `daily_window(user_id) -> (now, count, oldest)` over `created_at > now() - interval '24 hours'` (DB clock; copy
     `EmailSendLogRepository.window`, L222-238).
   - `count_open(user_id, app_id)`; `add(report)` (add + flush).
9. **Service** `BugReportSubmitService(session, *, limiter, user_limit, max_per_day, max_open, join_reject_cooldown_seconds)`
   `.submit(app, device, data, ctx) -> BugReportCreated`, in this order:
   1. `_reporter(device, account_id)`: `accounts.get_active_account(...)` → None ⇒ `SessionInvalidError` (no cookie
      clear); `users.get_for_share(account.user_id)`; None or not `active` ⇒ `SessionInvalidError`; unverified ⇒
      `EmailUnverifiedError` (copy of `OrgSelfService._user`, L67-79, with `get_for_share`; not extracted into a
      shared helper — that would touch two High-path-adjacent modules outside this ticket; follow-up).
   2. Member: `authz.effective_roles(user.id, app.key)` empty ⇒ `NotMemberError(details={"join_available": …})` via
      `membership.service.join_available` (as `auth_service.py:93-100`).
   3. `org_id` given: `app.org_mode == none` ⇒ `OrgNotSupportedError(details=[{"field":"org_id","reason":"not_supported"}])`;
      `authz.org_role_keys(user.id, app.key, org_id)` empty ⇒ `OrgAccessDeniedError("No access to this organization", details={})`
      (exact pattern `app/modules/tokens/service.py:81-113`).
   4. In-process per-user hit (cheap, before DB lock).
   5. `lock_reporter`; `daily_window` ⇒ `count >= max_per_day` ⇒ `BugReportDailyLimitError(max(1, ceil(oldest + 24h − now)))`;
      `count_open >= max_open` ⇒ `BugReportOpenLimitError`.
   6. Insert `BugReport(app_id=app.id, user_id=user.id, org_id=…, description, page_url, app_version, user_agent)`
      (status from server default; set `status=OPEN` explicitly too).
   7. `audit.record(BUG_REPORT_CREATED, actor=EndUser(user.id), ctx=ctx, target=(BUG_REPORT, report.id), app_key=app.key, org_id=None, changes={"attachment_count": 0})`
      — same session, never commits (R4/AC5). If `_check_keys`/count shape needs `{"from","to"}`, use
      `{"attachment_count": {"from": None, "to": 0}}` as `diff.change` does; ids only either way.
   8. `logger.info("bug_report_created report_id=%s app=%s user_id=%s", …)` — nothing else.
   Services raise only `AppError` subclasses (R1).
10. **Deps** `deps.py`: `async def get_report_app_context(app: AppContextDep) -> App` — `admin` ⇒
    `OriginNotAllowedError("Origin is not allowed")` (same 403 as unknown app, no oracle; mirrors
    `app/modules/auth/deps.py:84-89`). Unknown/disabled apps are already refused by `AppContextService.check`. No
    `lock_app` (would serialise every reporter of an app with RBAC writers).
11. **Router** `auth_router.py` (`prefix="/auth/bug-reports"`, `tags=["auth"]`), `responses={401,403,422,429}`;
    mount in `app/api/router.py` next to `org_auth_router`.
12. **Tests** as listed; add the route to `COOKIE` in `tests/test_route_auth_classes.py`.
13. **Docs**: CLAUDE.md R3 (2) "identity endpoints under `/api/v1/auth/*` … (accounts, token, join, logout, orgs,
    resend verification, **bug reports** — D28)"; §4 table row
    `POST /api/v1/auth/bug-reports {account_id, description, org_id?, page_url?, app_version?, user_agent?}` | cookie +
    Origin + `X-MM-App` (not `admin`) | end-user bug report, 201 `{id,status,created_at}` | ADM-028; D28 decision-log
    row; ADR-0018 recording: cookie class under `/auth/*` though not identity (OQ20), `admin` refused, disabled-user
    401, field caps and URL reduction (port rule), org_id validation, caps (numbers, per-user advisory lock, DB
    clock, 429 codes), in-process per-IP/per-user limits (ADR-0002), no Turnstile, FK delete behaviour, columns added
    now for ADM-029/031.
14. `make check` and `uv run alembic check` green (R8).

Rules: R1 (layering above), R2 (step 2), R3 class 2 (steps 10-13), R4 (step 9.7), R5 (step 4), R6 (only existing
envelope; new codes are `AppError` subclasses), R7 (follow-ups below), R8 (tests), R9 (`/api/v1/auth/bug-reports`
plural noun, UUID, timestamptz; no exception needed), R10 (n/a), R11 (no content/email in logs or errors; Origin +
`X-MM-App`; verified email required).

## Specialist input used
- database-optimizer: adopted — `UUIDPkMixin` + String status + CHECK pattern; advisory-lock caps on DB clock; columns
  `status/resolution_note/handled_by/handled_at/updated_at` + consistency CHECK now (`handled_by` outside the
  equivalence); index set (handled_at index deferred to ADM-031); no `app_key` copy; attachments in ADM-030;
  `org_id` SET NULL; per-user in-process hit + DB cap. Rejected — `user_id` nullable SET NULL: no user hard delete
  exists; NOT NULL RESTRICT keeps ADM-029 schemas and caps simpler (Q6). Open cap 5 → proposed 10 (Q2).
- api-platform-engineer: adopted — invitations/org-create template, `extra="forbid"`, minimal response + `no-store`,
  reject (not truncate) over-long fields, NUL/control-char rejection, 429 with stable codes, no Idempotency-Key
  (follow-up), CORS needs no code. Rejected — reporter FK CASCADE (spec L168: reports stay; RESTRICT).
- security-architect: adopted — F1 explicit `admin` refusal (same 403), F2 keep 401, per-user advisory lock,
  concurrency test, strict schema, URL scheme allowlist + userinfo strip, org_id validation, audit `changes` key set
  test, log line shape. Changed — org_id refusal is 403 `org_access_denied` (as `/auth/token`), not 422, for one
  consistent answer across cookie routes; user_agent rejected (422) not truncated, per AC3. R10 body-size cap →
  follow-up (R7).
- identity-access-engineer: adopted — org-create shape, dependency order (app before device), no `lock_app`,
  `org_not_supported` 422 / `org_access_denied` 403, D28 + ADR-0018, audit `org_id` NULL, `Retry-After` for daily
  cap. Open cap 20 → proposed 10 (Q2).

## Assumptions
- **Hotspots (same batch, same session branch `claude/festive-volta-rikup7`):** ADM-027 also edits
  `app/core/config.py`, `.env.example` and probably `docs/`; expect a rebase/merge of adjacent lines. ADM-027 may also
  want ADR-0018 — whichever lands second renumbers its ADR (and the D-number if ADM-027 adds one).
- `alembic heads` = `d9e4b2a61f73` at implementation time; if another migration lands first, re-point `down_revision`.
- `AuditLogger._check_keys` accepts `attachment_count` (no secret-looking key); implementer confirms the accepted
  value shape for counts (`_is_count`, `app/modules/audit/service.py:64`).
- Account sessions of a disabled user are already revoked (ADR-0008), so the 401 path normally fires at
  `get_active_account`; the `status` check is the backstop.
- A report from a disabled-then-re-enabled reporter is fine; RESTRICT on `users` blocks nothing today (no delete path).
- RESTRICT on `apps`: app delete already requires "no role assignments"; a reporter must have been a member, so a
  remaining report most often coincides with that 409. If all members were revoked, `delete_app` hits the FK inside
  its savepoint and the existing handler answers 409 "App has role assignments; disable it instead"
  (`app/modules/rbac/service.py:560-563`) — correct status, imprecise message; a precise `app_has_bug_reports` reason
  is a follow-up (touches `rbac`, out of plan).
- Follow-ups (not in this diff): global request-body size cap → 413 (security F6/R10); `@mmlabs/auth-client` helper
  and `docs/consumers.md` note for apps; admin-pan must render description/URL/UA as text (ADM-029/admin-pan ticket);
  shared `_reporter`/`_user` helper across cookie services; precise app-delete 409 reason; optional double-submit
  dedupe / Idempotency-Key; reject Unicode bidi overrides in descriptions (spoofing in admin-pan) if wanted.
- Size: ~550 production/doc lines + ~550 test lines — above the ~400 guide mostly from tests (see Split proposal).

## Blocking questions
Status stays READY: each question has a default the plan already implements. Ming confirms or overrides in the batch
round.
1. **AC2 disabled user — 401 or 403?** Default: **401 `session_invalid`** (cookie not cleared), like token, join and
   org create; no account-status oracle; AC2 reworded to "unverified (403 `email_unverified`) or non-member (403
   `not_member`); disabled user 401". Alternative: a new 403 `account_disabled` only on this route.
2. **Caps and their status codes.** Default: per user **10 / rolling 24 h across all apps** (spec), **10 open per
   user per app**, both **429** with codes `bug_report_daily_limit` (with `Retry-After`) and `bug_report_open_limit`
   (no `Retry-After`; clears only when staff mark done). Per IP **20 / hour**; per-user in-process burst **3 / minute**.
   All configurable by env. Alternatives: open cap 5 or 20; open cap as 403 (like `org_limit_reached`) or 409.
3. **`org_id` validation.** Default: validate — `org_mode = none` app → 422 `org_not_supported`; org unknown,
   suspended or caller holds no role in it → 403 `org_access_denied` (one answer, same as `/auth/token`). Alternative:
   store unvalidated, or silently drop an invalid one.
4. **`user_agent` source and over-long optional fields.** Default: from the **body** (spec "sent by the app"), max 512,
   **422 when over-long** or containing control chars (AC3); `app_version` max 64 printable ASCII; description 1..5000
   after strip. Alternative: take the request `User-Agent` header, truncated to 512.
5. **`app_id` FK on app delete.** Default: **RESTRICT** (an app with reports cannot be deleted; disable it instead;
   reports follow the 365-day retention). Alternative: CASCADE (deleting an app erases its reports).
6. **`user_id` FK on user delete.** Default: **NOT NULL, RESTRICT** (users are only disabled today; spec: reports of a
   disabled reporter stay). Alternative: nullable SET NULL (future hard delete keeps reports anonymised).
   `handled_by` is SET NULL either way; `org_id` SET NULL on org delete.
7. **Audit row `org_id`.** Default: **NULL** — spec L162 lists report id, app key, reporter id, attachment count and
   excludes context fields. Alternative: fill it with the validated `org_id` so org-filtered audit queries find it.
8. **Page URL port.** Default: keep a non-default port (`https://h:8443/x`), drop default ports; userinfo, query and
   fragment always dropped. Alternative: drop the port always (strict "scheme, host and path").
9. **Decision record.** Default: ADR-0018 + decision-log D28. Alternative: D28 only.

## Split proposal
None. Production code is about one day; the size above ~400 lines comes from tests and docs. Splitting the migration
from its only writer would ship dead schema. If Ming prefers smaller PRs: slice A = model + migration + DB CHECK
tests + ADR; slice B = endpoint, config, rate limits, docs and API tests.
