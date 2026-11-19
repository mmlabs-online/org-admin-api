# ADM-028: Bug reports — data model and text submit from apps (cookie class)

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P2 |
| Size | M |
| Areas | db, api, auth, security |
| Gate | plan-approval |
| Depends on | — |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-19 |
| Claimed by | — |
| PR | — |

## Goal
A signed-in member of an mmlabs app (e.g. ricotdin) can file a bug report from that app; the reporter account and the app are recorded.

## Scope reference
`docs/spec-v1.md` §5.12 (scope decided 2026-11-19): OQ19, OQ20, OQ21, OQ26, OQ28, OQ30.

## Scope
- Tables for reports (app, reporter, description, optional `org_id`, page URL reduced to scheme/host/path, app version, user agent capped, status `open`, handler fields null, timestamps) with a hand-reviewed migration and working `downgrade()` (R2); attachment table may be added here or in ADM-030 (PLAN decides).
- `POST /api/v1/auth/bug-reports`, R3 class 2: `mm_device` cookie + `Origin` check + `X-MM-App`; body carries `account_id` like other cookie routes; the app comes from `X-MM-App` after the Origin check, never from the body.
- Reporter must hold a valid account session on the device, be active, email-verified and a member of the app; `admin`, disabled and unknown apps refused.
- Per-IP and per-user limits (at most 10 reports per user per 24 h; a cap on open reports per user per app; numbers set at PLAN); no Turnstile.
- Audit `bug_report.created` in the same transaction with ids/counts only, never content.
- Add the route to the R3 class 2 list in `CLAUDE.md` and `docs/identity-architecture.md` §4, and record the decision (ADR or decision-log entry).

## Out of scope
- Attachments (ADM-030), staff endpoints (ADM-029), retention (ADM-031).

## Acceptance criteria
- [ ] A verified member submits a report → 201 with its id; reporter and app recorded from the session and `X-MM-App`.
- [ ] Refused: no/invalid cookie (401), bad Origin or missing `X-MM-App` (403), unverified/disabled user or non-member (403 with a stable code), `admin`/disabled/unknown app.
- [ ] Validation 422 for empty/too long description and over-long optional fields; page URL stored without query or fragment.
- [ ] Rate limits answer 429 with the existing envelope; the per-user daily cap and open-report cap are enforced in the DB.
- [ ] `bug_report.created` audit row in the same transaction, no description or URL in it; nothing logged with content or full email (R11).
- [ ] CORS: answered for the app's registered origins with credentials, like other `/auth/*` routes; tests.

## Technical notes
- Files: new `app/modules/bug_reports/`, migration, `app/db/models.py`, `app/core/rate_limit.py` (scopes), docs.
- Reuse the join/org-create patterns (USR-03b, USR-06a) for account/session/membership checks.

## Test plan
- API tests for each acceptance criterion, including CORS preflight and audit row content.

## Notes / answers from Ming

## Handoff
