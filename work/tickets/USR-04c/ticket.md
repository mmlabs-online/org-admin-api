# USR-04c: Require X-MM-App on signup; remove the EMAIL_LINK_BASE_URL fallback

| Field | Value |
|---|---|
| State | REVIEW |
| Priority | P1 |
| Areas | auth, security |
| Attempts | 0 |
| Created | 2026-11-13 |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/21 (same branch, not yet merged) |

## Goal
Verification links always point to the app the user signed up in. No app-specific default page: many apps share the user service, so a single fallback origin (USR-04 set it to ricotdin) would send users of other apps to the wrong app.

## Spec reference
§5.8 (Email flows, each app has its own pages); identity-architecture.md §4, D19; USR-04 plan A1 (optional `X-MM-App` on signup + `EMAIL_LINK_BASE_URL` fallback) — superseded by this ticket (owner, 2026-11-13).

## Acceptance criteria
- [ ] `POST /api/v1/auth/signup` requires the app context (`X-MM-App` + `Origin` matching that app's allowed origins, same rule as login): missing/unknown/disabled app or bad Origin → 403 (`app_header_required` / `origin_not_allowed`) **before** any user is created or any email is queued
- [ ] The verification link origin is always the request's validated Origin; `EMAIL_LINK_BASE_URL` is removed (setting, staging/production gate, `.env.example`, docs); a leftover env value is ignored
- [ ] Resend behaviour unchanged (already requires app context)
- [ ] Tests updated: signup without header → 403 and no user/no mail; with header → link uses that app's origin; existing signup tests send the header; config tests for the removed key
- [ ] Docs: `docs/consumers.md` (signup must send `X-MM-App` + credentials), `docs/deploy.md` (key removed; deployment compose line can be dropped), ADR-0007 amendment, identity-architecture §4 signup row; note the v1 contract change (no consumers yet)
- [ ] Forgot password (USR-04b) follows the same rule — note on USR-04b

## Dependencies
USR-04 (same branch).

## Notes / answers from owner
- 2026-11-13 (owner): many apps share the user service, so a ricotdin fallback is wrong; fix inside PR #21 before merge.
