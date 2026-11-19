# Consult — identity-access-engineer — ADM-028 (2026-11-19)

## ADM-028 consult: identity and access (read-only)

### Findings

1. **Closest pattern: org create, not join.** Look at `app/modules/organizations/auth_router.py` and `auth_service.py`, lines 67–113. The reusable pieces are:
   - `AppContextDep` declared before `DeviceDep`, so a bad app gets 403 before any device lookup or cookie rotation (comment at `app/modules/auth/router.py:225`).
   - `_user()` helper. Its order is: `get_active_account` → user active (otherwise 401 `SessionInvalidError`, without clearing the cookie) → verified (otherwise 403 `email_unverified`). The membership check is `effective_roles(user.id, app.key)` being non-empty, otherwise 403 `not_member` with `join_available`. `TokenService.issue` (`app/modules/tokens/service.py:86-105`) does the same.
   - `_finish()` for cookie rotation plus `Cache-Control: no-store`.
2. **Conflict with an acceptance criterion (AC2).** The AC says a disabled user gets "403 with a stable code". Every existing cookie route (token, org create, list orgs) answers **401 `session_invalid`** for a disabled user. Disabling a user also revokes their account sessions (ADR-0008), so `get_active_account` normally returns None first anyway. A 403 here would differ from the other cookie routes and would reveal account status.
3. **`admin` app.** `AppContextDep` accepts `X-MM-App: admin`. `MailLinkAppContextDep` (`app/modules/auth/deps.py:84-92`) refuses it with the same 403 `origin_not_allowed` as an unregistered app (no oracle). That matches spec §5.12 ("refused like other app-scoped cookie endpoints"), but the dependency's name is mail-specific.
4. **Disabled or unknown apps** are already refused (403) by `AppContextService.check` (active app plus an Origin registered for it).
5. **CORS needs no code.** `DynamicCORSMiddleware` applies `IDENTITY_POLICY` by path prefix `/api/v1/auth/` (`app/core/cors.py:34-41, 80-82`): credentials on, POST allowed, `Content-Type, X-MM-App` allowed. Tests are still needed.
6. **Rate limits.** `app/core/rate_limit.py` is per-IP only (a `Scope` Literal plus a `_LIMITS` map plus Settings). There is no per-user key. The per-user caps must come from database counts, which AC4 requires anyway.
7. **Race on the per-user caps.** Org create counts under `lock_app` (FOR NO KEY UPDATE), which only serializes within one app. The 10-per-24 h cap covers all of a user's apps, so an app lock does not protect it. Locking the user row is also unsafe:
   - `users.get_for_update` is `FOR NO KEY UPDATE`. Here it would be taken after `DeviceDep` may have locked or rotated the device row.
   - Login takes `FOR SHARE` on the user before the device row (warning at `app/modules/users/repository.py:44-47`).
   - The reversed order can deadlock.
   - The existing safe tool is a transaction-scoped advisory lock: `pg_advisory_xact_lock(hashtextextended(...))`, as in `EmailSendLogRepository.lock` (`app/modules/auth/repository.py:212-220`).
8. **Existing cap errors are 403.** `org_limit_reached` and `join_queue_full` are both 403 `PermissionDeniedError`. `RateLimitedError` is 429 `rate_limited` and requires a `retry_after`. AC4 asks for 429.
9. **Audit.**
   - `AuditLogger.record(..., actor=EndUser(user.id), target=..., app_key=..., changes=...)` flushes in the request transaction, which satisfies R4/AC5.
   - `bug_report.created` matches the `action_format` check constraint.
   - It needs a new `AuditAction` and a new `AuditTarget.BUG_REPORT` in `app/modules/audit/actions.py`.
   - The audit row stores `ip` and `user_agent` from `AuditContext`. That is request metadata, not report content, so it is acceptable.
10. **Fragment tokens.** Reset, verify and invite links put the token in the URL fragment (`#token=`). Dropping the fragment and query from `page_url` therefore stops those secrets from being stored.
11. **No request body size limit.** There is no body-size middleware, and FastAPI reads and parses the JSON body before dependencies run (before the cookie check).
12. **Decision record numbering.** The next free numbers are **D28** in the decision log and **ADR-0018**; neither is referenced anywhere yet. The R3 class 2 list in `CLAUDE.md` and in `docs/identity-architecture.md` §4 must both gain `bug-reports`.

### Recommendations

- **R1 – route shape (AC1, R3 class 2).**
  - New `app/modules/bug_reports/auth_router.py`, mounted at `/auth/bug-reports`, with `POST ""` returning 201 `{id}`.
  - Signature order: a non-admin app context dep → `DeviceCookieDoc` → body → `DeviceDep` → `AuditContextDep`.
  - Dependency `rate_limit("bug_report_create")`.
  - Set rotation and `no-store` as in `_finish`.
  - Do not lock the app with `lock_app`: an app disable racing with a report is harmless, and the lock would serialize submits with RBAC writers.
- **R2 – refusing `admin` (AC2).** Add a generic alias in `auth/deps.py` (e.g. `NonAdminAppContextDep`, same body as `get_mail_link_app_context`), or reuse that one with a renamed docstring. Answer 403 `origin_not_allowed` for `admin`, unknown and disabled apps alike (no oracle).
- **R3 – reporter checks (AC2).**
  - Reuse the `_user()` sequence; consider lifting it into a shared helper, otherwise copy it.
  - Missing or invalid cookie → 401. Unknown or inactive account, or disabled user → 401 `session_invalid`, matching all other cookie routes; ask Ming to accept this reading of AC2.
  - Unverified → 403 `email_unverified`. Non-member → 403 `not_member` with `join_available` (reuse `join_available`).
- **R4 – body schema (AC1, AC3).** Use `extra="forbid"` so an `app` or `app_key` field in the body is rejected with 422 rather than silently ignored. Fields:
  - `account_id: UUID`
  - `description`: stripped, 1–5000 characters
  - `org_id: UUID | None`
  - `page_url: str | None`, at most 2048 on input
  - `app_version: str | None`, at most 64, printable
  - `user_agent: str | None`, at most 512 (the same cap as `audit_log.user_agent`)
- **R5 – `page_url` normalization (AC3).**
  - Parse with `urlsplit`. Accept only `http`/`https` with a host; otherwise 422. This blocks `javascript:` and `data:` links, which matter because admin-pan will render the URL.
  - Drop **userinfo** (`user:pass@`) as well as the query and fragment. Store `scheme://host[:port]/path`, lower-casing the scheme and host.
  - Do not bind the host to `apps.origins` (apps may run on other paths and hosts); just store it.
- **R6 – `org_id` (AC1, mirrors D7).**
  - If the app's `org_mode` is `none` and `org_id` is sent → 422 `org_not_supported`.
  - Otherwise require `authz.org_role_keys(user.id, app.key, org_id)` to be non-empty. If it is empty → 403 `org_access_denied`, with one answer for unknown, suspended and foreign orgs.
  - This stops users tagging reports with other tenants' org ids and avoids an existence oracle.
- **R7 – caps in the database (AC4).** In the service, after the identity checks:
  1. `pg_advisory_xact_lock(hashtextextended('bug_report:' || user_id, 0))`, taken after the device and user reads; no row locks on `users`.
  2. Count the user's reports where `created_at > now() - interval '24 hours'`, using the database clock. If ≥ 10 → 429 `rate_limited` with `Retry-After` = oldest-in-window + 24 h − now (at least 1 second).
  3. Count `status='open'` for (user, app). If ≥ the open cap → reject.
  4. Insert.
- **R8 – suggested numbers.** All go in `config.py` and `.env.example` (R5).
  - Per-IP: `BUG_REPORT_RATE_LIMIT=20` per `BUG_REPORT_RATE_WINDOW_SECONDS=3600`. This is NAT-friendly, and the per-user cap is the real limiter.
  - `BUG_REPORT_MAX_PER_USER_DAY=10`, as fixed by the spec.
  - `BUG_REPORT_MAX_OPEN_PER_USER_APP=20`.
  - Indexes on `(user_id, created_at)` and `(user_id, app_key) WHERE status='open'` keep the counts cheap. Defer to the DB consultant for table and FK shape.
- **R9 – audit (AC5).**
  - Write `AuditAction.BUG_REPORT_CREATED` in the same transaction.
  - Fields: `actor=EndUser(user.id)`, `target=(BUG_REPORT, report.id)`, `app_key=app.key`, `changes={"attachment_count": 0}`.
  - Leave the audit `org_id` NULL, since the spec lists report id, app key, reporter id and attachment count only (see open question 3).
  - Log only `bug_report_created user_id=… app=… report_id=…`, never the description, URL, user agent or email.
  - Tests should assert that the audit row's `changes` has no description or URL keys.
- **R10 – tests (AC1–AC6), beyond the happy path:**
  - No cookie, revoked account, `account_id` from another device → 401.
  - Missing `X-MM-App`, Origin not registered for that app, Origin of app A with `X-MM-App` B, `X-MM-App: admin`, disabled app → 403.
  - Unverified → 403 `email_unverified`; non-member → 403 `not_member`.
  - `org_id` the user has no role in → 403; `org_id` on an app with `org_mode` `none` → 422.
  - An `app_key` field in the body → 422.
  - `page_url` with userinfo, query and fragment is stored stripped; `javascript:` → 422.
  - 11th report in 24 h → 429 with `Retry-After`. The open cap, tested across two apps, shows the cap is per app while the daily cap is global.
  - A CORS preflight from a registered origin echoes the origin with credentials; an unregistered origin gets no ACAO header.
  - Cookie rotation is set on the 201 response.
- **R11 – docs (ticket scope item 6).** Add `POST /api/v1/auth/bug-reports` to the R3 class 2 lists in `CLAUDE.md` and `identity-architecture.md` §4. Add **D28** (cookie class for bug-report submit, not an identity action; per spec OQ20). Write ADR-0018 only if PLAN also wants the cap and lock design recorded; a D28 entry alone is enough.

### Risks

- **Deadlock** if the implementer locks the `users` row (`get_for_update`) after `DeviceDep` has touched the device row. Use the advisory lock (R7).
- **Unbounded body before auth.** A large JSON body is parsed before the cookie check. Recommend a cheap `Content-Length` guard on this route, e.g. 413 above 32 KiB, or file a follow-up (R7 scope). ADM-030 multipart will need a real limit anyway.
- **Stored XSS risk in admin-pan** if `page_url`, `app_version` or `user_agent` is ever rendered unescaped or as a link. The http(s)-only rule helps, but the admin UI must still escape. Note this for ADM-029.
- **The path may still hold PII or ids** (e.g. `/users/<id>`) even after stripping the query and fragment. This is accepted by spec OQ26 and covered by the 365-day retention.
- **Per-IP limits are in-process (ADR-0002)** and reset on deploy. The per-user database caps are the real guard.
- **Attachment table in this ticket?** If it lands in ADM-028 without an upload path, it is dead schema. Defer it to ADM-030 unless the DB consultant objects.

### Open questions

1. AC2 says "disabled user → 403". Should we keep the existing 401 `session_invalid`, which is consistent and gives no status oracle? I recommend yes, and asking Ming to reword the AC.
2. Open-report cap reached: 429 `rate_limited` (per AC4, but there is no meaningful `Retry-After`, since it clears only when staff handle a report), or a distinct 403 `bug_report_limit_reached` like `org_limit_reached`? I recommend 429 for the 24 h cap and a distinct stable code for the open cap, so apps can show a useful message. This needs Ming's word because AC4 says 429.
3. Should the audit row's `org_id` column be filled? The spec lists the audit fields as "ids and counts only" but excludes "context fields", and `org_id` is both an id and a context field. I recommend NULL.
4. `user_agent`: trust the app's body field (per spec) or take the request's `User-Agent` header, which is simpler and cannot be spoofed by the app's JavaScript in any useful way? Either is acceptable; the spec wording favours the body.

Key files: app/modules/organizations/auth_service.py, app/modules/organizations/auth_router.py, app/modules/auth/deps.py, app/modules/tokens/service.py, app/core/rate_limit.py, app/core/cors.py, app/modules/auth/repository.py, app/modules/users/repository.py, app/modules/audit/actions.py, docs/spec-v1.md (section 5.12).
