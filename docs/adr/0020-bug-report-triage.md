# ADR-0020: Bug-report triage by staff (Bearer class 1)

Status: Accepted on merge (owner approved the plan 2026-11-20)
Date: 2026-11-20

## Context
Spec §5.12 (OQ22, OQ25, OQ28, OQ29): staff list reports from every app, open one, mark it done or
reopen it, and keep a resolution note. ADM-028 added the table and the submit route (ADR-0019).

## Decision
1. **Auth.** R3 class 1 on a separate router, `/api/v1/bug-reports`. `admin:bug-reports:read` (list,
   detail) and `admin:bug-reports:write` (PATCH), both `is_admin`, seeded by migration and granted
   to no role. `admin:*` covers both. The owner assigns them to roles later.
2. **List.** `Page[T]`, newest first (`created_at DESC, id DESC`). Filters: `app_key` (unknown key
   gives an empty page), `status`, `reporter_id` (UUID only, never an email), `from` (inclusive) and
   `to` (exclusive), timezone-aware; `from > to` is 422. Items carry the reporter `{id, display_name,
   email}` and a 200-character `description_preview`. Every 2xx is `Cache-Control: no-store`
   because reporter email is PII and reads are not audited.
3. **Detail.** Adds the full text fields, `resolution_note`, the handler `{id, display_name}` (no
   email; null when open or when the handler account was deleted) and `attachments` (always `[]`
   until ADM-030). `ETag` is `version_etag(updated_at)`.
4. **Transitions.** PATCH takes `status` (`open` | `done`) and `resolution_note`, under a row lock
   (`FOR NO KEY UPDATE`). `If-Match` is checked after the lock and before the no-op decision, so a
   stale tag is 412 even for a no-op. Done records the caller as `handled_by` and `handled_at`
   (database clock); reopen clears both and keeps the note. A status equal to the current one
   changes nothing (a second "done" keeps the first handler and `handled_at`, which matters for
   retention counted from `handled_at`, OQ29): 200, no audit row, ETag unchanged.
5. **Note.** Absent: unchanged. `null`: clear. Blank or whitespace: 422. Stripped, 1..5000
   characters, same control-character rule as the description. Allowed while open.
6. **Audit (R4).** `bug_report.marked_done`, `bug_report.reopened`, `bug_report.note_updated`, one
   row per change kind in the same transaction (status row first when both change). `changes` is
   `{"status": {"from", "to"}}` or `{"resolution_note": {"changed": true, "cleared": bool}}`;
   never note or description text, not even lengths. `app_key` is set, `org_id` is null (as for
   `bug_report.created`). Reads and attachment downloads are not audited.
7. **Untrusted text.** `description`, `page_url`, `app_version`, `user_agent`, `display_name` and
   `resolution_note` are returned verbatim as JSON strings (no server-side escaping) and documented
   in OpenAPI as untrusted plain text that clients must render as text, never as HTML. A round-trip
   test pins this. Tightening ADM-028 submit validation is a separate ticket.

## Consequences
- ADM-030 extends `BugReportAttachmentRead` (additive) and adds the download route.
- No staff delete in v1.
