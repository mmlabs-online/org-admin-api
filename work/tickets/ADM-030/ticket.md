# ADM-030: Bug reports — image attachments (upload from apps, download for staff)

| Field | Value |
|---|---|
| State | DONE |
| Priority | P2 |
| Size | M |
| Areas | api, security |
| Gate | plan-approval |
| Depends on | ADM-027, ADM-028, ADM-029 |
| Needs Ming | — |
| Attempts | 1 |
| Created | 2026-11-19 |
| Claimed by | claude@2026-11-20T09:45Z |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/87 |

## Goal
Users attach screenshots/images to a bug report from the app; staff view them in admin-pan; files live in Google Drive.

## Scope reference
`docs/spec-v1.md` §5.12: OQ23, OQ24, OQ29 (PII), OQ35.

## Scope
- Upload: `POST /api/v1/auth/bug-reports/{id}/attachments` (cookie class, reporter only, report `open`, shortly after creation — window set at PLAN), one raw-body file per request; PNG/JPEG/WebP/GIF only, ≤ 5 MB each (stream-enforced, `Content-Length` checked first), ≤ 5 per report; magic bytes must match the declared type; store the sniffed type; no SVG/HTML/PDF; no re-encoding (apps strip EXIF client-side).
- Store in Drive via ADM-027 under `<root>/<app_key>/…`; the DB row is written after Drive succeeds and the Drive file deleted if the DB write fails.
- If Drive is not configured or fails, the report stays (text) and the app gets a per-file failure response.
- Download for staff: `GET /api/v1/bug-reports/{id}/attachments/{attachment_id}/content` (`admin:bug-reports:read`), proxied stream with `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'; sandbox`, `Cache-Control: no-store`, CORP; Drive ids never exposed; a missing Drive file → a stable "attachment unavailable" error.
- New error codes 413 `payload_too_large` and 415 `unsupported_media_type` added to R6 (CLAUDE.md) with the owner's approval at the plan gate.
- Wire the ADM-027 sweep CLI to the attachment rows.

## Out of scope
- Video and resumable upload, staff delete (§5.7 Later); retention purge (ADM-031).

## Acceptance criteria
- [ ] Each allowed type uploads and downloads byte-identical; disallowed or mismatched type → 415; > 5 MB → 413 without reading the whole body; 6th file → 409/422 (PLAN picks).
- [ ] Only the reporter, on an open report, within the window, can upload (403 otherwise).
- [ ] Drive off/failing: report still exists, per-file failure returned; download of a missing file → stable error.
- [ ] Download headers asserted by test; Drive ids absent from every response.
- [ ] No file content, filenames or Drive ids in logs or audit rows.

## Technical notes
- Files: `app/modules/bug_reports/`, migration (attachments table if not in ADM-028), `CLAUDE.md` R6, tests with the fake Drive.
- Cloudflare Tunnel body limit is 100 MB, so 5 MB files are safe.

## Test plan
- API tests with the fake Drive; magic-byte fixtures; header assertions.

## Notes / answers from Ming
- 2026-11-20 (Ming, chat): "sửa 2 🟡 trong #87" — fix appsec findings 1 (upload route must not rotate the mm_device cookie; non-rotating resolve in app/modules/sessions, High path approved here) and 2 (overall deadline around store.create, 503 on timeout) in PR #87 before merge.
- 2026-11-20 (Ming): Plan approved (2026-11-20, batch 2026-11-20-1); batch questions 1–8, 28 per the defaults — "duyệt hết; câu 1–28 theo mặc định".

## Handoff
### 2026-11-20 — claude — BLOCKED (waiting plan approval, batch 2026-11-20-1)
- Agent calls: 3, tokens ~226k (consultants + planner×1 opus 144k).
- Done: consult, plan.md READY.
- In progress / next: Ming answers batch questions + plan approval → `/run-batch --resume`.
- Gotchas: all five batch tickets share migration head c4e8a2f7b916, app/db/models.py, audit/actions.py and registry tests — built sequentially on the single session branch, each rebases on the previous merge.
### 2026-11-20 — claude — DONE (PR #87, waiting for Ming's merge)
- Agent calls (build): 3, tokens ~402k (implementer×1 sonnet 195k, reviewer×1 opus 106k, appsec-engineer×1 101k). Ticket total incl. plan: 6 calls, ~628k.
- Done: slice 1 — attachments table (migration d7a3f5b2e801), upload + download routes, R6 411/413/415/503, ADM-027 Drive deadline fix, ADR-0023 + D32; follow-up ticket ADM-030b created. Review PASS; appsec no 🔴, two 🟡 (rotation outliving the grace on long uploads; no overall deadline on store.create) put to Ming in the PR. PR https://github.com/mmlabs-online/org-admin-api/pull/87.
- In progress / next: Ming decides on the two 🟡 (rework in #87 or follow-up) and merges #87 (High).
