# Security review — ADM-028 (appsec-engineer)

## Attempt 1 (commit 1c2ffb2)

**Verdict: PASS. No blockers.** I found one 🟡 hardening item and a few 💭 notes. The implementation matches plan.md and all four consult outcomes.

I ran the six bug-report and route-auth test files: 248 passed, 1 failed. The failure was `test_submit_rotated_cookie_is_set` with `relation "bug_reports" does not exist`. Rerun on its own, and with its whole file, it passes (46/46). It looks like shared test-DB contention: something else was probably running the migration down/up test at the same moment. It is not a security issue, but the reviewer should confirm `make check` is green under R8.

### What I checked and found correct
- **R3 cookie class.** `POST /api/v1/auth/bug-reports` resolves dependencies in this order: per-IP `rate_limit("bug_report_create")`, then `ReportAppContextDep`, then the body, then `DeviceDep` (`app/modules/bug_reports/auth_router.py:159-175`).
  - `get_app_context` requires an active `X-MM-App` plus a registered `Origin` (`app/modules/auth/service.py:83-96`).
  - `admin` is refused with the same `OriginNotAllowedError("Origin is not allowed")` (`app/modules/bug_reports/deps.py:9-13`), so there is no oracle. Test: `tests/test_bug_reports_submit.py:284`.
  - The path is under the identity CORS prefix and the cookie `Path=/api/v1/auth`, so no CORS change was needed. The IDENTITY_POLICY headers and methods cover it (`app/core/cors.py:34-39`).
- **Session, user, verified, member checks** (`app/modules/bug_reports/service.py:495-520`).
  - The account must belong to this device (`get_active_account` with `created_before`). Inactive user gives 401; unverified gives 403; non-member gives 403 with `join_available`. This is the same shape as org-create and token.
- **org_id.** It is checked after membership. An org-less app gives 422. Otherwise `org_role_keys` is org-exact, unexpired and org `active`. Unknown, foreign and suspended orgs all get one answer: 403 `org_access_denied` with `details={}` (`service.py:521-529`). No IDOR or oracle.
- **Mass assignment.** `extra="forbid"`; app and user come from the header and session only; `status` is set server-side (`schemas.py:413-423`, `service.py:544-554`). Test: `test_submit_body_extra_field_rejected_422`.
- **R11 logs and audit.** The log line is `report_id`, `app`, `user_id` only (`service.py:566`). The audit entry is ids plus `{"attachment_count": 0}`, in the same transaction (`service.py:557-565`). The 422 handler never echoes `input` (`app/core/errors.py:194-196`). Reset, verify and invite tokens travel in the `#token=` fragment, which the URL reducer drops (`auth/password_reset.py:85`, `auth/email_verification.py:110`, `invitations/service.py:236`).
- **Input validation** (`app/modules/bug_reports/text.py`).
  - NUL, C0/C1 and lone surrogates are rejected in every field; the description allows only `\t\n\r`.
  - URL: http(s) only, non-empty host, userinfo/query/fragment dropped, default port dropped, bad port gives 422. `urlsplit` errors are caught. A double `@` like `u@evil@good` takes the host after the last `@`.
  - The DB CHECKs mirror these rules (migration lines 695-714).
- **Cap race-safety and deadlocks.**
  - The per-user advisory xact lock is taken after the device row (via rotation) and the user `FOR SHARE` lock, and before the count-then-insert (`service.py:535-543`, `repository.py:356-364`).
  - Only this path takes that advisory key, and always last, so two submits from the same user cannot form a cycle: the FK `KEY SHARE` on users is compatible with `FOR SHARE`, and `lock_app` is `NO KEY UPDATE`, which does not block FK checks.
  - The 24 h window uses the DB clock. The concurrency test exists.
- **Migration.** RESTRICT on apps/users and SET NULL on org/handled_by. Status, handled and length CHECKs plus the open-partial index; the downgrade is symmetric.

### Findings
- 🟡 **`page_url` host and `app_version` accept HTML metacharacters.** `text.py:620-637` takes any `urlsplit().hostname` without checking its grammar. The CHECK `[^/?#@[:space:]]+` (models.py:283, migration:704) agrees. So `https://<svg/onload=alert(1)>` is stored as `https://<svg/onload=alert(1)>`, and `app_version` `^[\x20-\x7e]` (`text.py:593`) allows `<>"'`.
  - **Risk:** nothing is exploitable today (there is no read path), but staff will view and click these fields in the ADM-029 admin UI. A stored-XSS precursor sits in a staff-facing field.
  - **Fix:** after `hostname`, require `re.fullmatch(r"[a-z0-9.-]{1,253}|\[[0-9a-f:.%a-z]+\]", host)` (IDNA hosts arrive as text; either encode with `host.encode("idna")` or reject non-ASCII), else 422. Optionally tighten the CHECK host class to match.
  - Either way, add a follow-up for ADM-029: render every report field text-escaped, and only build links from `page_url` after re-checking the `http(s)://` scheme.
- 💭 **Invisible and bidi characters are accepted** in `description` and `user_agent` (U+202E, U+200B, etc.; `text.py:591-592`). This enables report-spoofing in the staff UI, not injection. ADM-029 should display them visibly, or a follow-up could strip category Cf.
- 💭 **Python `isspace()` vs Postgres `[:space:]` can disagree** on rare code points (e.g. U+180E, depending on glibc). Such a URL passes `reduce_page_url`, then fails the CHECK, giving 500 instead of 422. This is not a security issue. Optional fix: also reject category `Z*` and `Cf` in `reduce_page_url`.
- 💭 **Backslash host mismatch.** `https://good.com\@evil.com/` is reduced to `https://evil.com/`, while browsers would show good.com. The reporter controls this data anyway, so it is only a fidelity issue.
- 💭 **Lock order device row → user `FOR SHARE`** is the same as the existing verification resend path (ADM-002e, documented in `users/repository.py:42-47`). It adds no new cycle.

### Follow-ups (out of scope, R7)
1. ADM-029: output-encode all bug-report fields in the admin UI and re-check the scheme before rendering `page_url` as a link.
2. The body-size cap is already recorded in the plan as a follow-up.
3. Look into the shared-test-DB flake: `test_submit_rotated_cookie_is_set` failed when run together with the migration down/up test.
