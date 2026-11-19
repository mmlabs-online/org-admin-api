# Consult — api-platform-engineer — ADM-028 (2026-11-19)

## ADM-028 consult (API / contract). Read-only; I did not run anything.

### Findings
- The route fits the existing cookie-class pattern. The closest template is `app/modules/invitations/router.py` (`accept_invitation`, lines 91-114) plus `InvitationService._user`, `_lock_active_app` and `create` in `app/modules/invitations/service.py`.
  - It uses `app: AppContextDep` (X-MM-App + Origin, 403 otherwise), `_cookie: DeviceCookieDoc` (OpenAPI doc of the cookie), `device: DeviceDep`, and `body.account_id`.
  - It ends with `_finish(request, response, device)` (cookie rotation and headers). I did not read `_finish` or `DeviceCookieDoc`; reuse them as the invitations router does.
  - `app/modules/auth/router.py` notes that `AppContextDep` must come before `DeviceDep`, so a bad app context returns 403 before any device or cookie work.
- `get_app_context` (`app/modules/auth/deps.py`) does not by itself refuse `admin`. `get_mail_link_app_context` does that only for mail-link routes.
  - The bug-report route needs its own `admin` refusal, using the same 403 as an unregistered app so there is no oracle. The spec requires this (§5.12, OQ21).
  - Unknown and disabled apps are already handled by `AppContextService.check`. `_lock_active_app` in the invitations service also re-checks status under lock.
- The account, session, active and verified checks are already in `InvitationService._user`.
  - Unknown, expired or non-device account gives `SessionInvalidError` (401). Disabled user also gives 401. Unverified gives `EmailUnverifiedError` (403).
  - That does not satisfy the AC "disabled user, 403 with a stable code". Either the AC means 401 for disabled (the existing behaviour, no oracle), or a new 403 code is needed. Clarify it at PLAN. I recommend keeping 401.
  - Extract `_user` into a shared helper rather than copying it a third time. The ticket only says to "reuse patterns". Check whether `join` or `org create` already has such a helper, in `app/modules/auth/service.py` or `membership`.
- Membership check: `AuthzService.effective_roles(user.id, app.key)` (non-empty means member) is used in the invitations service at line 310. A non-member should get 403 with a new stable code, for example `not_app_member`. Look in `app/modules/membership/errors.py` for an existing error to reuse.
- `org_id` (optional) must be validated for the reporter. Use `authz.org_role_keys(user.id, app.key, org_id)`. A non-member org should return one answer for unknown, suspended and not-a-member (no oracle), like `OrgAccessDeniedError`. Otherwise a user could probe org ids or attach reports to a foreign org. Also require that the app's `org_mode` supports orgs, or ignore or reject `org_id` for non-org apps (decide at PLAN).
- Rate limiting: `app/core/rate_limit.py` is per-IP only (`rate_limit(scope)` dependency; `Scope` Literal and `_LIMITS` dict, with config fields in `app/core/config.py`).
  - Add scope `bug_report_create` with `bug_report_rate_limit` and `bug_report_rate_window_seconds`, following the `org_create` pattern.
  - Per-user limits are not in the in-memory limiter, and the AC requires the daily and open-report caps "enforced in the DB". Use `count_created_by_24h`-style repository counts, as the invitations service does with `count_created_by_24h` and `count_pending_for_org`.
- Audit: `AuditLogger.record(action, actor, ctx, target=(AuditTarget.X, id), app_key, changes)` in `app/modules/audit/`.
  - Add `AuditAction.BUG_REPORT_CREATED = "bug_report.created"` and `AuditTarget.BUG_REPORT`.
  - `changes` must carry ids and counts only, per spec §5.12: report id, app key, reporter id, `attachment_count=0`. Never description or URL.
  - Caveat: the audit record already stores `ip` and `user_agent` from `ctx`. That is existing behaviour and fine, but do not repeat the report's user agent in `changes`.
- No existing bug-report code outside the spec; the module is greenfield.

### Recommendations
1. Endpoint: `POST /api/v1/auth/bug-reports`, status 201, `dependencies=[rate_limit("bug_report_create")]`, `responses={401,403,422,429: _ERR}` (also 409 or 429 for caps, see 4).
   - Request body: `account_id` (UUID, required), `description`, optional `org_id`, `page_url`, `app_version`, `user_agent`. Reject unknown fields (`extra="forbid"`), so a body `app` or `app_key` is a 422 and cannot override X-MM-App.
   - Response: minimal `{id, status, created_at}`. Do not echo the description or URL. Set `Cache-Control: no-store` for consistency.
2. Field caps (set at PLAN; the numbers below are suggestions):
   - description 1..4000 chars after strip; whitespace-only rejected with 422 `min_length`.
   - `app_version` ≤ 64, `user_agent` ≤ 512 (spec says "capped"). Decide whether over-long optional fields are rejected with 422 or truncated. The AC says 422 for over-long optional fields, so reject, do not truncate.
   - `page_url` ≤ 2048 on input, http/https only, parsed with `urllib.parse`. Store `scheme://host[:port]/path` only, dropping userinfo, query and fragment. Decide whether to keep the port, and document it. Reject other schemes (`javascript:`, `data:`) with 422. Normalise in the schema validator, so the DB never sees the raw URL. A DB CHECK on column length is cheap defence in depth.
   - Strip control characters and NUL. Postgres rejects NUL in text, which would otherwise surface as a 500.
3. Table `bug_reports`:
   - Columns: `id` UUID, `app_id` FK, `reporter_id` FK users, `org_id` nullable FK, `description`, `page_url` nullable, `app_version`, `user_agent`, `status` default `open`, `handled_by`, `handled_at`, `resolution_note` (null; ADM-029 fills them), `created_at`, `updated_at`.
   - Status: use a CHECK constraint on `status` (`open|done`) rather than a PG enum, so ADM-029 can extend it without a hard migration.
   - FKs: decide the reporter FK delete behaviour at PLAN (cascade vs SET NULL). It interacts with ADM-031 retention and user deletion. Reports as "user content" suggests the reporter should not be silently orphaned; follow what `app_join_requests` does (cascade). Index `(reporter_id, created_at)` and a partial index `(reporter_id, app_id) WHERE status='open'` for the cap counts. Plus `(app_id, status, created_at)` for ADM-029 lists.
   - Attachments: put the attachment table in ADM-030, not here. The ticket allows either; this keeps the migration smaller and the High-path review narrower. Audit `attachment_count` is fixed at 0 for now.
   - Migration needs a working `downgrade()` (R2). Import the model in `app/db/models.py`. Run `alembic check`.
4. Caps enforced in the DB, race-safe:
   - Concurrent submits can both pass a count-then-insert check. Serialise per reporter, for example with `SELECT … FOR UPDATE` / `FOR SHARE` on the user row (ADR-0003 §10 lock order: app, then user), as join and invitations do. Then count and insert under that lock. Or use `pg_advisory_xact_lock(hashtext(user_id))`.
   - Daily: `count(created_at > now() - 24h) >= 10` gives 429. Open cap per user per app: pick a number (suggest 5 or 10), and answer 409 or 429.
   - The status choice is a contract decision. R6 lists 409 for conflict and 429 is the existing rate-limit envelope. The AC says "rate limits answer 429" and that the caps "are enforced in the DB". Recommend 429 with stable codes `bug_report_daily_limit` and `bug_report_open_limit`, a `Retry-After` header for the daily cap (seconds until the oldest report in the window ages out), and none for the open cap, because it clears only when staff handle reports. A 409 for the open cap is arguably more accurate (retry will not help soon). The planner should pick one and record it in the ADR.
   - Per-IP limit: suggest 20 per hour, `bug_report_rate_limit` config.
5. Audit in the same transaction as the insert (R4 analogue). If the audit write fails, the report must roll back. Cover this in a test.
6. Do not make the POST idempotent via `Idempotency-Key`. For an app-originated form, a double-submit is the real risk. Cheap mitigation: reject an identical description from the same reporter in the same app within N minutes, or leave it to the caps. I would not add a key header in v1 (the CORS allow-headers list would need it); record it as a follow-up.
7. CORS: look at how `/auth/*` CORS is wired in `app/main.py`. I did not read it. Allow-origin echoes only `apps.origins` with credentials, and the preflight must allow `X-MM-App` and `Content-Type`. A test is required for preflight and for a non-registered origin getting no allow-origin header. Confirm the new path is under the existing `/api/v1/auth` prefix so it inherits this. If a prefix-based CORS middleware exists, no change is needed.
8. Docs and decisions:
   - Add the route to the R3 class 2 list in `CLAUDE.md` and to `docs/identity-architecture.md` §4. Add the route to the R9 exceptions? R9 requires `/api/v1/<plural-noun>`; `/api/v1/auth/bug-reports` follows it under the auth prefix, so no R9 exception is needed. If a fresh decision-log entry is preferred, it needs a D-number in `docs/identity-architecture.md`, or a new ADR; the ticket allows either.
   - The ADR should record: URL reduction, caps, status codes for caps, `org_id` validation, 401-vs-403 for disabled user, and why no Turnstile.
9. Tests (R8):
   - Happy path, plus the recorded reporter and app, and URL stored without query or fragment.
   - 401 (no cookie, invalid cookie, account not on device). 403 for bad Origin, missing or unknown X-MM-App, `admin`, disabled app, unverified user, non-member.
   - 404 is not applicable; note it explicitly in the plan.
   - 422 cases, body app override ignored (422), 429 per-IP, daily cap, open cap, concurrency test for the caps if feasible.
   - Audit row: exact `changes`, no description or URL. Log capture: no content or email in logs.
   - CORS preflight.

### Risks
- The user-content table and URL handling are an XSS and PII surface for ADM-029. Content must be treated as untrusted text by the admin UI; the API stores plain text and never HTML.
- A race on the caps gives a bypass if only count-then-insert is used (see 4).
- `org_id` without a membership check allows cross-org tagging.
- Disabled-user response differs between the AC (403) and existing helper (401). This is a contract mismatch to settle before coding.
- Migration, auth and cookies are High paths per `CLAUDE.md`, so this ticket stops at PLAN for the owner's approval.
- In-process limiter state is lost on restart, and is per-process. That is acceptable per ADR-0002 only because the DB caps are the real enforcement.
- Reporter FK behaviour on user deletion is undecided and affects ADM-031 retention.

### Open questions for the planner
1. Disabled user: 401 (existing `SessionInvalidError`) or a new 403 code? Recommend 401.
2. Cap statuses and codes: 429 for both caps, or 409 for the open cap? Open-cap number (suggest 5 or 10).
3. `org_id`: allowed only for `org_mode=required` apps? What if the app is not org-based (422 or ignore)? Reuse `OrgAccessDeniedError`?
4. Description max length, `user_agent` and `app_version` caps; reject vs truncate (recommend reject), and whether the URL keeps the port.
5. Reporter FK on user delete: cascade or SET NULL? Align with ADM-031 retention.
6. Attachment table: ADM-030 (recommended) vs here.
7. Decision record: new ADR or D-number entry? Per-IP limit values (suggest 20 per hour).

Key files: `app/modules/invitations/router.py`, `app/modules/invitations/service.py`, `app/modules/auth/deps.py`, `app/core/rate_limit.py`, `app/core/config.py`, `app/modules/audit/actions.py`, `docs/spec-v1.md` (§5.12, OQ19-OQ30).
