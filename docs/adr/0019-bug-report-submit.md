# ADR-0019: Bug-report submit from apps (cookie class under `/auth/*`)

Status: Accepted on merge (owner approved the plan 2026-11-19)
Date: 2026-11-19

## Context
Spec §5.12 (OQ19, OQ20, OQ21, OQ26, OQ28, OQ30): a signed-in member files a text bug report from
an mmlabs app. ADM-028 adds the table and `POST /api/v1/auth/bug-reports`; attachments (ADM-030),
staff endpoints (ADM-029) and retention (ADM-031) follow.

## Decision
1. **Auth class.** R3 class 2 (cookie): `__Host-mm_device` + `Origin` + `X-MM-App`, no new class.
   The route sits under `/api/v1/auth/*` although it is not an identity action, so the existing
   CORS policy for that prefix applies unchanged (D28).
2. **App.** From `X-MM-App` after the `Origin` check, never from the body (`extra="forbid"`).
   The `admin` app is refused with the same 403 `origin_not_allowed` as an unknown app. Staff do
   not file reports from admin-pan.
3. **Reporter.** Account session on the device, active, verified email, member of the app. A
   disabled user gets 401 `session_invalid` like every other cookie route (no account-status
   oracle); this amends the ticket's "403" for that case. Unverified is 403 `email_unverified`,
   a non-member 403 `not_member`.
4. **`org_id`** is optional and validated like `/auth/token`: 422 `org_not_supported` for an
   `org_mode = none` app, 403 `org_access_denied` for an unknown, suspended or foreign org (one
   answer).
5. **Fields.** Description 1..5000 characters after stripping, no control characters except
   `\n \r \t`. `page_url`: http(s) only, stored as `scheme://host[:port]/path` (userinfo, query and
   fragment dropped, host lowercased, default port dropped, a non-default port kept). `app_version`
   1..64 printable ASCII; `user_agent` 1..512 from the body, no control characters. Over-long or
   invalid values are 422, never truncated. Empty optional strings are 422.
6. **Limits (R5, all env-configurable).** Per IP 20/hour and per user 3/minute, in-process
   (ADR-0002). In the database, serialized by a per-user advisory lock on the DB clock: at most 10
   reports per user per rolling 24 h (429 `bug_report_daily_limit`, with `Retry-After`) and 10 open
   reports per user per app (429 `bug_report_open_limit`, `details.limit`, no `Retry-After`: it
   clears when staff mark a report done). No Turnstile.
7. **Schema.** `bug_reports` with `status` (`open`/`done`), `handled_by`, `handled_at`,
   `resolution_note` and `updated_at` already present for ADM-029; CHECKs keep status and handler
   fields consistent. `app_id` and `user_id` are `RESTRICT` (reports of a disabled reporter stay; an
   app with reports cannot be deleted, disable it), `org_id` and `handled_by` `SET NULL`.
8. **Audit.** `bug_report.created` in the same transaction: target report id, app key, actor user,
   `changes = {"attachment_count": 0}`, `org_id` NULL. Never the description, context fields or
   attachment content. Logs carry ids only.

## Consequences
- No new auth class, CORS or error shape; two new 429 codes.
- Deleting an app that only has reports left answers 409 through the existing handler (imprecise
  message; a precise reason is a follow-up).
