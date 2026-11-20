# Security review — ADM-029 (appsec-engineer)

## Attempt 1 (commit 8040b1a). Verdict: PASS, no blockers.

I reviewed this by reading the code only. I did not run pytest, alembic or anything that touches the database. Line numbers are in the new files.

### Findings

**🔴 Blockers:** none.

**🟡 Should-fix:** none.

**🟢 Checked and OK:**
- **Auth class (R3).** All three routes are Bearer (class 1), each with the right permission:
  - The router has `dependencies=[Depends(current_admin)]` (`app/modules/bug_reports/router.py:242-244`).
  - List and detail use `require_permission(READ)` (`router.py:314`, `:346`). PATCH uses `require_permission(WRITE)` (`router.py:362`).
  - `require_permission` really enforces the check: it adds a `RequirePermission(key)` dependency and sets `x-permission` (`app/modules/admin_auth/deps.py:146-150`).
  - The router is mounted under `/api/v1/bug-reports`, not under `/auth/*` (`app/api/router.py`).
  - Tests cover 401, 403 for an unrelated permission, 403 on PATCH with read only, and `admin:*` (`tests/test_bug_reports_staff_api.py:72-93`).
- **PII and no-store.**
  - The list sets `no-store` (`router.py:338`). Detail and PATCH both go through `_detail()`, which sets `no-store` and the ETag (`router.py:304-307`).
  - Tests check all three (`test_list_and_detail_no_store` :250, `test_patch_response_no_store` :407, and :302).
  - The handler object has no email (`schemas.py` `BugReportHandler`).
  - `reporter_id` is a UUID filter only (`router.py:322`).
  - The 404 and 412 bodies carry no resource data; the 412 sends only an ETag header (`app/core/errors.py:135-145`), so they need no `no-store`.
  - 422 responses never echo the submitted values (`errors.py:194-196`), and the `ValueError` messages contain only labels (`text.py` `_clean_text`).
  - The service log line holds only ids and status (`staff_service.py:589-594`).
- **Audit payloads (R4/R11).**
  - The `changes` field holds only `{"status": {from, to}}` or `{"resolution_note": {"changed", "cleared"}}`, with no text and no lengths (`triage.py:693-710`).
  - The audit rows are written in the same session and transaction as the UPDATE (`staff_service.py:577-588`), with `org_id=None` and `app_key` set.
  - A sentinel test proves no description or note text reaches `changes` (`test_audit_rows_ids_and_status_only` :380-397).
- **PATCH mass assignment.**
  - `BugReportUpdate` uses `extra="forbid"` and takes only `status` and `resolution_note` (`schemas.py:473-489`).
  - `handled_by` and `handled_at` are set only by the server, from `actor.user_id` and `func.now()` (`staff_service.py:570-576`).
  - A body with `handled_by` gives 422 (test :428 parametrize).
  - An empty body and `status: null` are rejected (`schemas.py:483-489`).
- **Row lock, then If-Match, then no-op.**
  - The service takes the row lock with `get_for_update` (`FOR NO KEY UPDATE`, populate_existing; `repository.py:186-193`) before anything else.
  - The If-Match check comes next (`staff_service.py:552-560`) and the no-op decision after it (`:566-568`). So a stale tag on a no-op still gives 412 (test :450).
  - The single UPDATE uses `bumped_updated_at`, so the ETag strictly increases and cannot repeat an old value (`repository.py:195-202`, `app/db/base.py:17-23`).
- **Done on done.** `triage.decide` produces no `status_change` when the status is unchanged (`triage.py:682-685`). So `handled_by` and `handled_at` are not touched, no audit row is written and the ETag stays the same. Tested in `test_done_again_keeps_original_handler` (:322-332). Editing only the note on a done report also leaves `handled_at` alone, because that path has no `status_change`. Reopen clears the handler and `handled_at` and keeps the note (:305).
- **Permission seed migration** (`migrations/versions/20261120_c4e8a2f7b916_adm029_bug_report_permissions.py`):
  - Data only. It inserts both keys with `is_admin=true`, `ON CONFLICT (app_id, key) DO NOTHING`, and grants them to no role (`:802-810`).
  - Values are passed as bound parameters.
  - `downgrade()` deletes both keys using an expanding bindparam (`:813-818`). The docstring says the cascade to `role_permissions` is lossy.
  - `tests/test_bug_reports_permissions_migration.py` checks downgrade, then upgrade, then `is_admin` true with 0 role grants.
- **Untrusted text contract.**
  - The server does no escaping; values go out verbatim as JSON strings.
  - The `UNTRUSTED` and `UNTRUSTED_URL` descriptions apply to `description`, `description_preview`, `page_url`, `app_version`, `user_agent`, both `display_name` fields and `resolution_note` (`schemas.py:393-401`).
  - ADR-0020 §7 documents the contract.
  - `test_metacharacters_round_trip` (:257-270) pins `< > & " '` and U+202E in description, page_url, app_version, user_agent and the list preview.
  - The note goes through the same control-character rule and the 5000 limit as the description (`text.py`).

### Notes (💭, optional hardening)
1. **The write permission can read reporter email.** PATCH returns the full `BugReportRead`, including `reporter.email` (`router.py:362`, `:378`). A role that has only `admin:bug-reports:write` can therefore read any report's PII without an audit row: a no-op PATCH such as `{"status":"<current>"}` returns the full body and writes nothing. This matches the existing pattern (join-request approve/reject return email under a write permission, `app/modules/join_requests/router.py:62,80`). The permissions are granted to no role today, so it is not exploitable now. When Ming assigns roles, either always pair write with read, or make PATCH also require `admin:bug-reports:read`. A one-line note in ADR-0020 §1 would cover it.
2. **No `X-Content-Type-Options: nosniff`.** The API consult asked to confirm it is set globally (`consult-api-platform-engineer.md:28` (b)). It is not: only the audit export sets it (`app/modules/audit/router.py:119`). Risk is low because responses are `application/json`. Follow-up: add a global `nosniff` (an `app/core/` middleware, which is a High path).
3. **Round-trip test gaps.** The metacharacter test does not cover `resolution_note` written through PATCH, or `display_name`. The contract holds by construction, but extending the test would pin it.

### Follow-ups (outside this ticket)
- A global `nosniff` header (see note 2).
- The admin web UI (admin-pan, outside this repo) must render every field above as text and re-check the http(s) scheme before turning `page_url` into a link (`rel="noopener noreferrer"`).
- When Ming assigns bug-report permissions to roles, decide whether write implies read (see note 1).
