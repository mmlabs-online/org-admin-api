# Consult — security-architect — ADM-028 (2026-11-19)

## Security-architect consult: ADM-028 (bug report text submit, cookie class)

I only read files and changed nothing. Sources: the ticket, spec §5.12, `app/modules/auth/{router,deps,service}.py`, `app/modules/membership/service.py` (join), `app/modules/organizations/{auth_router,auth_service}.py` (org create), `app/core/{rate_limit,cors,errors}.py`, `app/modules/audit/{service,models}.py`, `tests/test_route_auth_classes.py`.

### Findings

**F1 (High if missed). `AppContextDep` does not refuse the `admin` app on its own.**
- `get_app_context` (`app/modules/auth/deps.py:67`) accepts any active app whose Origin matches, `admin` included.
- Only `MailLinkAppContextDep` (deps.py:84) and the join service (`membership/service.py:50`) refuse `admin` explicitly.
- Staff hold app-scope roles in `admin`, so the membership test (`authz.effective_roles(user.id, app.key)`) would pass for them. Without an explicit check, admin-pan staff could file reports with `X-MM-App: admin`, which the spec forbids (§5.12, OQ21).

**F2. Conflict: the ticket wants 403 for a disabled user, but every existing cookie route answers 401.**
- AC2 says "unverified/disabled user … 403 with a stable code".
- Join (`membership/service.py:132`) and org create (`organizations/auth_service.py:75`) both answer a disabled (non-active) user with `SessionInvalidError` (401 `session_invalid`), and do not clear the cookie.
- Unverified email is 403 `email_unverified` and non-member is 403 `not_member` (with `join_available`). Those match AC2.

**F3. The per-user caps can be raced.**
- The org-create pattern counts and then inserts, serialized by `rbac.lock_app()`.
- Locking the app row for every bug report would serialize every reporter of an app (contention and DoS amplification).
- Without some lock, parallel requests from one user can go past both the 10/24 h cap and the open-report cap. AC4 says "enforced in the DB".

**F4. Existing limiter and CORS are reusable as they are.**
- `rate_limit()` is per-IP only (`app/core/rate_limit.py:122`). There is no per-user key, so per-user limits must live in the service.
- CORS: `IDENTITY_POLICY` already covers `/api/v1/auth/*` with credentials, and allows only the `Content-Type` and `X-MM-App` headers. JSON plus the custom header forces a preflight, and `AppContextService.check` binds Origin to the `X-MM-App` app (`auth/service.py:83`). No CORS code change is needed, only tests.

**F5. 422 responses already strip `input`/`ctx`** (`app/core/errors.py:194`), so submitted descriptions are not echoed back. Good.

**F6. No request-body size limit exists in the app** (no Content-Length or ASGI cap in `main.py`). Pydantic `max_length` runs only after the whole body has been read. This is a gap in existing behaviour, not new, but this is the first route that accepts free text from end users.

**F7. Postgres `text` rejects NUL (`\x00`).** asyncpg would raise an error that comes back as a 500, not a 422. This applies to description, app version, page URL and user agent.

**F8. Stripping the query and fragment does not make the page URL safe on its own.**
- Our own one-time tokens are in fragments (`#token=`, e.g. `password_reset.py:85`), so dropping the fragment covers those.
- Still not covered: userinfo (`https://user:pass@host/…`), non-http schemes (`javascript:`, `data:`, `file:`), and app paths that carry ids or secrets.

**F9. `org_id` comes from the client.** If it is stored without a check, a reporter can tie a report to any org UUID. Staff would see the wrong attribution, and it opens a small existence oracle if FK errors surface.

**F10. Audit is a good fit.**
- The `action_format` check accepts `bug_report.created`, and `target_type` has no enum constraint, so the only audit change is a new `AuditTarget` value.
- `_check_keys` blocks secret-looking keys, but nothing stops content keys. The service must pass only ids and counts.

### Recommendations

**R1 (F1, AC2, spec OQ21).** Refuse `admin` first in the service (or with a dependency like `MailLinkAppContextDep`), before any account or user lookup.
- Answer with the same 403 `origin_not_allowed` used for unknown or disabled apps, so it gives no oracle. This matches join and signup.
- Test: an admin-pan origin with `X-MM-App: admin` and a staff account that has admin roles gets 403, and no row is written.

**R2 (F2).** Keep consistency and no oracle: inactive user → 401 `session_invalid` (cookie not cleared), unverified → 403 `email_unverified`, non-member → 403 `not_member` with `join_available`.
- The planner should record the change to AC2 and flag it to Ming. Do not invent a new `account_disabled` 403 on a cookie route.
- Read the user with `users.get_for_share()` as join does, so a concurrent disable cannot slip through.

**R3 (F3, AC4).** Serialize per user, not per app.
- Take `SELECT pg_advisory_xact_lock(hashtextextended('bug_report:' || :user_id, 0))` (or `SELECT … FOR UPDATE` on the user row) in the repository, then count, then insert, all in one transaction.
- Two caps:
  - **Daily:** `count(*) WHERE user_id=:u AND created_at > now() - interval '24 hours'` must stay below 10, across all apps (rolling window, DB clock).
  - **Open:** `count(*) WHERE user_id=:u AND app_id=:a AND status='open'` must stay below N. I suggest N = 5.
- Indexes:
  - `(user_id, created_at)`
  - a partial index `(user_id, app_id) WHERE status='open'`
- Errors: answer 429 with the existing envelope, using `RateLimitedError` or a subclass with stable codes such as `bug_report_daily_limit` / `bug_report_open_limit`.
  - The daily cap should send `Retry-After`, computed as the oldest row in the window + 24 h − now.
  - For the open cap, 429 vs 409 is the planner's call. 429 matches the AC wording.
- Add a concurrency test: run 2 or more submits in parallel at cap − 1 and assert that exactly one succeeds.

**R4 (OQ30, AC4).**
- **Per-IP limit:** a new scope `bug_report_create` in `Scope`/`_LIMITS`, with settings `BUG_REPORT_RATE_LIMIT` / `BUG_REPORT_RATE_WINDOW_SECONDS` in `.env.example` (R5). Suggested 20 per 3600 s. Leave room for NAT; the DB caps are the real per-user limit.
- **Per-user limit:** the DB caps already satisfy "per-user limit". An extra in-process per-user burst limit, e.g. 3 per 60 s, is optional. If added, key it on `user_id` in the service after authentication, through the same `RateLimiter` (scope `bug_report_user`), so it cannot be bypassed by rotating IPs.
- Keep `rate_limit(...)` in the decorator's `dependencies=[…]`, so unauthenticated floods count before any DB work. That is the existing order.

**R5 (AC3, F7, F8).** Use a strict schema with `ConfigDict(extra="forbid")`, so `app_key`, `user_id`, `status` and `handled_by` in the body return 422 (mass-assignment defence).
- **`account_id`:** UUID, required.
- **`description`:**
  - Strip it; length 1..5000 characters after stripping.
  - Reject `\x00` and C0/C1 control characters except `\n`, `\r`, `\t`.
  - Optionally reject the Unicode bidi overrides U+202A–202E and U+2066–2069, which can be used to spoof text in admin-pan.
- **`app_version`:** optional, 1..64 characters, printable ASCII (e.g. `^[\x20-\x7E]{1,64}$`).
- **`page_url`:** optional, input ≤ 2048 characters.
  - Parse with `urllib.parse.urlsplit`. Accept only `http`/`https` with a non-empty host; otherwise 422.
  - Rebuild it as `scheme://host[:port]/path` with userinfo, query and fragment dropped. Lowercase scheme and host. Store ≤ 2048 characters.
  - Test with `https://u:p@h/x?q=1#token=…`, which must be stored as `https://h/x`, and with `javascript:alert(1)`, which must get 422.
- **`user_agent`:** optional, capped at 512.
  - My suggestion: truncate (do not 422) and strip control characters, reusing the `_clean_user_agent` approach in `app/modules/audit/service.py:58`.
  - Planner's choice: take it from the request `User-Agent` header rather than the body. It is equally unverified but simpler for clients.
- **`org_id`:** optional UUID. If present, the org must exist, belong to the calling app, and the reporter must hold a role in it. Otherwise 422 with a generic `org_id` field error that is the same for "missing" and "not a member" (no oracle). The FK should be `ON DELETE SET NULL`.

**R6 (spec §5.12, R11, AC5).** Store `app_id` (FK to `apps`) and `user_id` (FK to `users`) from the resolved app and user, never from the body.
- Use `ON DELETE RESTRICT` or `SET NULL`, not `CASCADE` from `apps`. Reports are user content governed by the ADM-031 retention policy, so an app delete should not silently wipe them.
- Exact FK behaviour is database-optimizer's call.

**R7 (AC5).** Audit write: `audit.record(AuditAction.BUG_REPORT_CREATED, actor=EndUser(user.id), target=(AuditTarget.BUG_REPORT, report.id), app_key=app.key, org_id=<validated org_id or None>, changes={"attachment_count": change(None, 0)})` in the same session. No description, URL, version or user agent goes in `changes`.
- The audit row's own `user_agent` column is request metadata from `AuditContext`, which is already the existing behaviour and acceptable.
- The test should assert the exact key set of `changes`.
- Log line: `logger.info("bug_report_created report_id=%s app=%s user_id=%s", …)` and nothing else.

**R8 (F4, AC6).** Copy the `auth_router.py` shape:
- Dependency order `rate_limit` → `AppContextDep` → `DeviceDep`, so a bad app context answers 403 before any cookie rotation.
- Rotate the cookie via `_finish`, and set `Cache-Control: no-store`.
- Return 201 with `{ "id": … }` only.
- Tests:
  - A preflight from a registered origin echoes the origin with `Allow-Credentials: true`.
  - A preflight from an unregistered origin gets no ACAO.
  - A registered origin of app B with `X-MM-App: A` gets 403.
  - A missing `X-MM-App` gets 403 `app_header_required`.

**R9 (R3, R7).**
- Add `("POST", f"{V1}/auth/bug-reports")` to `COOKIE` in `tests/test_route_auth_classes.py`.
- Update the R3 class 2 text in `CLAUDE.md` and `identity-architecture.md` §4.
- Write ADR-0018 (or a D-log entry). It should record: the route sits under `/auth/*` although it is not an identity action; `admin` is refused; caps are serialized by an advisory lock per user.

**R10 (F6, follow-up, not in this diff per R7).** Add a global request-body size cap (ASGI middleware, e.g. 64 KiB for JSON routes, larger for the ADM-030 uploads) that answers 413 `payload_too_large`. That code is already mapped in `errors.py:210`.

### Risks

- **Spam and abuse by members (Medium):** a verified member can still file 10 reports a day per account. Sybil accounts are limited only by the existing signup controls (Turnstile, verification). Accepted per OQ30.
- **Stored XSS in admin-pan (Medium, outside this repo):** the API stores raw text. admin-pan must render description, URL and version as text and never as HTML or links without escaping. `page_url` should be shown as text or with `rel="noopener noreferrer"` after a check that the scheme is http(s). This belongs in admin-pan's ticket for ADM-029.
- **PII in the description:** users will paste emails or tokens. That is acceptable as user content, but it must never reach logs or audit. ADM-031 retention covers deletion.
- **Advisory-lock key collisions:** a collision on `hashtextextended` only causes extra serialization, never a correctness problem.
- **Race with app disable:** the app status is read before the user lock. A report filed during an app disable can still land. Low risk, acceptable.
- **Multi-instance:** the in-process per-IP limit is per worker (ADR-0002). The DB caps are global, which is why AC4 puts them in the DB.

### Open questions

1. AC2 says 403 for a disabled user, while every existing cookie route answers 401 `session_invalid`. Should AC2 be amended (my recommendation) or should this route deliberately diverge?
2. The open-report cap and its error: N = 5? 429 or 409?
3. If `org_id` is invalid or the reporter is not a member of that org: 422 (recommended), or silently store null?
4. `user_agent`: from the body (spec wording: "sent by the app") or from the request header?
5. Should the attachment table land in this migration? From a security view it is neutral, but if it does, ADM-030 must not be able to attach to another user's report. Bind attachments to the creating request or user.
