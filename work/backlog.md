# Backlog

States: BACKLOG → PLANNING → IN_PROGRESS → REVIEW → DONE → MERGED · plus REWORK, BLOCKED, ESCALATED.
Priority: P1 (next) > P2 > P3. `/run` with no ID picks the first runnable ticket by priority, then board order
(`.claude/loop.config.md` → Selection): BACKLOG (or answered BLOCKED), every `Depends on` ticket MERGED, no fresh claim;
ready S1 bugs first. `/run-batch` runs several. This board is changed only on `main`; each `ticket.md` is the source of truth.

Ticket IDs: `USR-XX` for users, identity and authorization (mmlabs Identity, spec §5.3/§5.8); `ADM-XXX` for everything else; `BUG-XXX` for bugs (`/bug`, severity S1–S3; ready S1 bugs run first, ADR-0012).

| ID | Title | State | Priority | Areas | Attempts | Updated |
|---|---|---|---|---|---|---|
| USR-13 | Allow 12-hour access tokens (ADR-0005 amendment) | MERGED | P1 | architecture, auth, security | 0 | 2026-11-17 |
| ADM-001 | Scaffold FastAPI + PostgreSQL service, tooling and CI | DONE | P1 | architecture, db | 1 | 2026-11-10 |
| ADM-007 | Release and deploy pipeline to api.mmlabs.online | IN_PROGRESS | P1 | architecture, security | 0 | 2026-11-10 |
| USR-01 | Users, passwords (Argon2id) and sign-up with Turnstile | MERGED | P1 | architecture, db, api, auth, security | 1 | 2026-11-11 |
| USR-01b | Staff user management endpoints (list, get, enable/disable) | MERGED | P1 | api, security | 1 | 2026-11-11 |
| USR-02 | Apps registry and per-app RBAC (join policy, org mode, origins, default role) | MERGED | P1 | architecture, db, api, auth, security | 1 | 2026-11-11 |
| USR-02b | Apps registry staff API (flag-gated) and role catalog | MERGED | P1 | api, auth, security | 1 | 2026-11-11 |
| USR-02d | Permissions and roles staff API (flag-gated) | MERGED | P1 | api, auth, security | 1 | 2026-11-11 |
| USR-02e | Public role catalog with ETag | MERGED | P1 | api, security | 1 | 2026-11-11 |
| ADM-011 | Platform hardening for public endpoints (client IP, limiter, error envelope, display text) | MERGED | P1 | security, api | 0 | 2026-11-11 |
| USR-02c | Role assignments staff API (flag-gated) | MERGED | P1 | api, auth, security | 1 | 2026-11-12 |
| USR-02f | Bootstrap super-admin CLI (grant-super-admin) with masked output and runbook | MERGED | P1 | auth, security | 1 | 2026-11-12 |
| USR-03 | Device sessions (multi-account), cookie rotation, login/logout with Origin check | MERGED | P1 | architecture, auth, security, db, api | 1 | 2026-11-12 |
| USR-03f | Session hardening: commit before response, atomic login throttle, cookie-tossing defence | MERGED | P1 | auth, security, api | 1 | 2026-11-12 |
| USR-03c | Signing keys, per-app token endpoint, JWKS and key rotation runbook | MERGED | P1 | architecture, auth, security, api | 1 | 2026-11-12 |
| USR-03e | Dynamic CORS from apps.origins; remove or restrict CORS_ORIGINS | MERGED | P1 | api, security, auth | 1 | 2026-11-12 |
| USR-04 | Email verification via Gmail SMTP (sender, dispatcher, verify, resend) | MERGED | P1 | auth, security, db, api | 1 | 2026-11-12 |
| USR-04c | Require X-MM-App on signup; remove the EMAIL_LINK_BASE_URL fallback | MERGED | P1 | auth, security | 1 | 2026-11-13 |
| USR-07 | Register an app in production from the CLI (first consumer: ricotdin) | MERGED | P1 | api, auth, security | 1 | 2026-11-13 |
| USR-04b | Password forgot/reset with all-session revocation | MERGED | P1 | auth, security, api | 1 | 2026-11-13 |
| USR-03b | Accounts list and join flow (API) | MERGED | P1 | api, auth, security, db | 1 | 2026-11-13 |
| ADM-002 | Lock admin endpoints (aud=admin + require_permission) | MERGED | P1 | auth, security, api | 1 | 2026-11-15 |
| ADM-002c | Set admin app origins from the CLI | MERGED | P1 | auth, security | 1 | 2026-11-15 |
| ADM-002b | Enforce admin RBAC guards and mount the users/RBAC APIs | MERGED | P1 | auth, security, api | 1 | 2026-11-15 |
| ADM-002d | Admin auth hardening and runbook fixes | MERGED | P2 | auth, security | 1 | 2026-11-15 |
| ADM-002e | Close the APP_ENV ?host= bypass and fix the admin runbook trap | MERGED | P1 | auth, security | 2 | 2026-11-15 |
| ADM-012 | Non-credentialed CORS for the Bearer admin endpoints | MERGED | P1 | api, security, auth | 1 | 2026-11-16 |
| USR-11 | Caller's effective admin permissions endpoint for the admin UI | MERGED | P1 | api, auth, security | 0 | 2026-11-16 |
| ADM-013 | OpenAPI contract that clients can generate types from | MERGED | P2 | api | 0 | 2026-11-16 |
| USR-12 | Allow resend-verification for the admin app | MERGED | P2 | api, auth, security | 0 | 2026-11-16 |
| ADM-014 | Docs — admin UI hostname and forgot-password credentials | MERGED | P2 | security | 1 | 2026-11-16 |
| ADM-015 | Dashboard stats endpoint for the admin UI | MERGED | P3 | api, db | 1 | 2026-11-17 |
| ADM-016 | `member_count` on the staff apps list and app detail | MERGED | P3 | api, db | 0 | 2026-11-17 |
| USR-10 | Staff session management for a user (list, revoke one, revoke all) | MERGED | P3 | api, auth, security, db | 0 | 2026-11-17 |
| ADM-017 | Operational reports (placeholder; out of v1) | BLOCKED | P3 | architecture, db, api, security | 0 | 2026-11-16 |
| ADM-004 | Audit log (admin actions, login, join, role changes, org membership) | MERGED | P1 | db, api, security | 1 | 2026-11-15 |
| ADM-004b | Audit identity events, denied privileged attempts and retention | MERGED | P1 | db, api, security, auth | 1 | 2026-11-15 |
| ADM-004c | Audit retention purge | MERGED | P1 | db, security | 1 | 2026-11-15 |
| ADM-004d | Audit log DB hardening | MERGED | P2 | db, security | 1 | 2026-11-15 |
| USR-06 | Organizations and org-aware tokens (first consumer: calechip) — slice 06a | MERGED | P2 | architecture, db, api, auth, security | 0 | 2026-11-11 |
| USR-06b | Org invitations (create, accept by verified email) | MERGED | P2 | api, auth, security, db | 0 | 2026-11-17 |
| USR-06c | Org-scoped roles in tokens and default-role hygiene for org apps | MERGED | P2 | architecture, db, api, auth, security | 0 | 2026-11-17 |
| USR-04d | Email hardening: dispatcher shutdown, retention cleanup, daily-cap alert | MERGED | P1 | auth, db | 1 | 2026-11-13 |
| USR-03g | Join approval flow (join_requests + staff approve/reject) | MERGED | P2 | api, auth, security, db | 1 | 2026-11-17 |
| USR-08 | Client libraries @mmlabs/auth-client (TS) and mmlabs-auth (Python) — separate repo | DONE | P1 | architecture, auth, security | 0 | 2026-11-16 |
| USR-09 | Create a staff-vouched account from the CLI (bootstrap for a new app's first user) | MERGED | P1 | auth, security, db | 3 | 2026-11-15 |
| USR-05 | Apps self-register their permission manifest — slice 05a (app credentials) | MERGED | P2 | api, auth, security | 0 | 2026-11-17 |
| USR-05b | Manifest push endpoint (app credential auth) | MERGED | P2 | api, auth, security | 1 | 2026-11-17 |
| ADM-006 | Org product entitlements | MERGED | P2 | architecture, db, api | 0 | 2026-11-17 |
| ADM-018 | Service read of org entitlements (`ent` token claim) | MERGED | P3 | architecture, auth, security, api | 0 | 2026-11-18 |
| USR-14 | Admin tokens end on logout, password reset and staff session revoke | MERGED | P2 | architecture, auth, security, api | 1 | 2026-11-17 |
| USR-15 | Join requests hardening | MERGED | P3 | api, auth, security, db | 0 | 2026-11-18 |
| ADM-019 | Guard org_mode required → none while org data exists | MERGED | P3 | api, db | 0 | 2026-11-18 |
| BUG-001 | `test_purge_batches_share_cutoff` fails intermittently in `make check` | MERGED | P3 | db | 1 | 2026-11-18 |
| BUG-002 | `test_changing_push_writes_one_row` fails intermittently | MERGED | P3 | api | 0 | 2026-11-18 |
| USR-16 | Staff role listing with ids (admin Bearer), including the admin app and disabled apps | MERGED | P1 | api, auth, security | 0 | 2026-11-18 |
| USR-17 | Holders of a role: list assignments by role (admin Bearer) | MERGED | P1 | api, auth, security, db | 0 | 2026-11-18 |
| ADM-020 | Filters and sort for the staff organizations list | MERGED | P1 | api, db | 0 | 2026-11-18 |
| USR-18 | Filters and sort for the staff apps list | MERGED | P1 | api | 0 | 2026-11-18 |
| ADM-021 | Audit log: org filter and single-entry read | MERGED | P2 | api, db | 1 | 2026-11-18 |
| ADM-023 | Declare 404/409 responses and admin:* requirements in OpenAPI | MERGED | P2 | api | 0 | 2026-11-18 |
| ADM-025 | Grant admin:entitlements:* to a staff role (needs owner) | BACKLOG | P2 | db, auth, security | 0 | 2026-11-17 |
| ADM-022 | Server-side CSV export of the audit log | MERGED | P3 | api, security | 0 | 2026-11-18 |
| ADM-024 | Optimistic concurrency (ETag / If-Match) on PATCH for organizations and entitlements | MERGED | P3 | api, architecture | 0 | 2026-11-19 |
| USR-19 | Label and optional expiry for app credentials | BACKLOG | P3 | api, auth, security, db | 0 | 2026-11-17 |
| USR-20 | Join requests: richer decision response | BACKLOG | P3 | api, auth | 0 | 2026-11-17 |
| USR-21 | Join requests: reject cooldown controls and pending count per app | BACKLOG | P3 | api, auth, security | 0 | 2026-11-17 |
| USR-22 | Join requests: optional reject reason and expiry of stale pending requests | BACKLOG | P3 | api, auth, security, db | 0 | 2026-11-17 |
| USR-23 | Staff read of an organization's members and invitations | MERGED | P1 | api, auth, security, db | 1 | 2026-11-19 |
| USR-24 | Optimistic concurrency on PUT role permissions | MERGED | P2 | api, db | 0 | 2026-11-19 |
| USR-25 | Scope of `self_assignment_forbidden` beyond the admin app | BACKLOG | P2 | auth, security | 0 | 2026-11-19 |
| USR-26 | Role definitions: assignment count, org-owner flag, stable reasons on 409 conflicts | BACKLOG | P3 | api, db | 0 | 2026-11-19 |
| USR-27 | Role assignments: return role and org names | BACKLOG | P3 | api | 0 | 2026-11-19 |
| USR-28 | Exact email lookup for staff user search | BACKLOG | P3 | api, security | 0 | 2026-11-19 |
| ADM-026 | Organizations and apps lists: name sort collation and member_count sort | BACKLOG | P3 | api, db | 0 | 2026-11-19 |
| ADM-027 | Google Drive attachment client for bug reports | MERGED | P2 | architecture, security | 0 | 2026-11-19 |
| ADM-028 | Bug reports: data model and text submit from apps (cookie class) | MERGED | P2 | db, api, auth, security | 0 | 2026-11-19 |
| ADM-029 | Bug reports: staff list, detail, mark done and reopen | MERGED | P2 | api, auth, db | 0 | 2026-11-20 |
| ADM-030 | Bug reports: image attachments (upload from apps, download for staff) | DONE | P2 | api, security | 0 | 2026-11-20 |
| ADM-030b | Bug reports: wire the attachment sweep to the attachment table | BACKLOG | P3 | db, security | 0 | 2026-11-20 |
| ADM-031 | Bug reports: retention purge | BACKLOG | P3 | db, security | 0 | 2026-11-19 |
| ADM-032 | Bug reports: guide for app teams | BACKLOG | P3 | api | 0 | 2026-11-19 |
| ADM-033 | Release log: data model and staff read (list, detail, live version, delete) | MERGED | P2 | db, api | 0 | 2026-11-20 |
| ADM-034 | Release log: scoped app credentials and the app's release write | BACKLOG | P2 | db, api, auth, security | 0 | 2026-11-19 |
| ADM-035 | Deployer credential: data model and staff issue, list, revoke | MERGED | P2 | db, auth, security | 1 | 2026-11-20 |
| ADM-036 | Release log: deploy result report (deployer credential) | BACKLOG | P2 | api, auth, security | 0 | 2026-11-19 |
| ADM-037 | Release log: guide for app repos and the deployment repo | BACKLOG | P3 | api | 0 | 2026-11-19 |
| ADM-038 | Bug reports carry the release the user was running | BACKLOG | P2 | db, api | 0 | 2026-11-19 |
| USR-29 | App-facing read of an org's members for org-mode apps (requested by simap) | BACKLOG | P1 | api, auth, security, db | 0 | 2026-11-20 |
| USR-30 | Org owners remove a member's org roles from the app (requested by simap) | BACKLOG | P1 | api, auth, security, db | 0 | 2026-11-20 |
| ADM-039 | Usage metrics, defaults and periods (staff config) | MERGED | P2 | db, api, auth | 0 | 2026-11-20 |
| ADM-040 | Per-user usage limit overrides (staff) | BACKLOG | P2 | db, api | 0 | 2026-11-20 |
| ADM-041 | Usage key scopes (ADR-0022, amends ADR-0021) | MERGED | P2 | architecture, auth, security | 0 | 2026-11-20 |
| ADM-042 | Usage consume, refund and current-usage read for app backends | BACKLOG | P2 | db, api, auth, security | 0 | 2026-11-20 |
| ADM-043 | Staff usage reads | BACKLOG | P3 | api | 0 | 2026-11-20 |
| ADM-044 | App-admin usage actions through limits:write with on_behalf_of | BACKLOG | P3 | auth, api, security | 0 | 2026-11-20 |
| ADM-045 | Usage event retention purge | BACKLOG | P3 | db | 0 | 2026-11-20 |
| ADM-046 | Usage fail-open grace and late reconciliation | BACKLOG | P3 | api, db | 0 | 2026-11-20 |

Order of work (dependencies; reprioritised 2026-11-12 by the owner for the ricotdin integration path): USR-01 → USR-01b → USR-02 → USR-02b → USR-02d → USR-02e → ADM-011 → USR-02c → USR-02f → USR-03 → USR-03f → USR-03c → USR-03e → USR-04 → USR-04c → USR-07 → USR-04b → USR-03b → ADM-002 → ADM-002c → ADM-002b → ADM-004 → ADM-004b → ADM-004c → ADM-002e → ADM-012 → USR-08 → ADM-013 → ADM-014 → USR-11 → USR-12 → USR-06 → USR-06b → USR-06c → USR-05 → USR-05b. admin-pan requests (2026-11-17): USR-16 → USR-17 → ADM-020 → USR-18 → ADM-021 → ADM-023 → ADM-025 → ADM-022 → ADM-024 → USR-19 → USR-20 → USR-21 → USR-22. admin-pan requests (2026-11-19): USR-23 → USR-24 → USR-25 → USR-26 → USR-27 → USR-28 → ADM-026.
Hard dependencies: USR-01b ← USR-01 · USR-02 ← USR-01 · USR-02b ← USR-02 · USR-02d ← USR-02b · USR-02e ← USR-02d · USR-02c ← USR-02, USR-02b, USR-02d · USR-02f ← USR-02c · USR-03 ← USR-01, USR-02 · USR-03f ← USR-03 · USR-03c ← USR-03, USR-03f, USR-02 · USR-03e ← USR-03, USR-02 · USR-03b ← USR-03, USR-03c, USR-03e · ADM-002 ← USR-02, USR-02b, USR-02c, USR-02d, USR-02f, USR-02e, USR-03, USR-03c, USR-03e · ADM-002b ← ADM-002 · ADM-002c ← ADM-002 · ADM-002d ← ADM-002b · ADM-002e ← ADM-002d · ADM-012 ← ADM-002, ADM-002c, USR-03e · ADM-013 ← ADM-002, USR-03b · ADM-014 ← ADM-002e · USR-11 ← ADM-002, ADM-013 · USR-12 ← ADM-002d, USR-04 · ADM-015 ← ADM-004b, ADM-012 · ADM-016 ← USR-02c, ADM-012 · USR-10 ← USR-03, ADM-002b, ADM-004b, ADM-012 · ADM-017 ← ADM-012, ADM-004b · ADM-004 ← ADM-002 · ADM-004b ← ADM-004 · ADM-004c ← ADM-004b · ADM-004d ← ADM-004c · USR-04 ← USR-01, USR-03, USR-03c · USR-07 ← USR-02, USR-02f · USR-04c ← USR-04 · USR-04b ← USR-04, USR-04c · USR-04d ← USR-04b · USR-06 ← USR-03b, USR-03c, ADM-004, USR-04 · USR-06b ← USR-06 · USR-06c ← USR-06, USR-06b · USR-03g ← USR-03b · USR-08 ← USR-03b · USR-05 ← USR-02e, USR-03b, USR-06c · USR-05b ← USR-05 · ADM-006 ← USR-02 · ADM-018 ← ADM-006 · USR-14 ← USR-13, USR-10 · USR-15 ← USR-03g · ADM-019 ← ADM-006 · USR-09 ← USR-01, USR-02f, USR-07 · USR-16 ← USR-02d, ADM-002b, ADM-012, ADM-013 · USR-17 ← USR-16, USR-02c · ADM-020 ← ADM-002b, ADM-013 · USR-18 ← USR-02b, ADM-016 (shares design with ADM-020) · ADM-021 ← ADM-004, ADM-004b · ADM-022 ← ADM-021, ADM-004b · ADM-023 ← ADM-013 · ADM-024 ← ADM-006, ADM-012, ADM-013 · ADM-025 ← ADM-006 · USR-19 ← USR-05, USR-05b · USR-20 ← USR-03g · USR-21 ← USR-03g · USR-22 ← USR-03g · USR-23 ← USR-06, USR-06b, ADM-002b, ADM-012 · USR-24 ← USR-02d, USR-16 (shape shared with ADM-024) · USR-25 ← USR-02c, ADM-002b, USR-03g · USR-26 ← USR-16, USR-17 · USR-27 ← USR-02c, USR-17 · USR-28 ← USR-01b, ADM-002b · ADM-026 ← ADM-020, USR-18, ADM-016.
Note (D16): joining an app or getting any token needs a verified email, which only works after USR-04. Release ADM-002 together with or after USR-04.
Plan and decisions: `docs/identity-architecture.md` (decision log D1–D12, 2026-11-11).

### Renamed / merged (2026-11-11)
| Old | New |
|---|---|
| ADM-003 RBAC for staff | merged into USR-02 |
| ADM-005 Products registry | merged into USR-02 (apps registry) |
| ADM-008 Users and password credentials | USR-01 |
| ADM-009 Login, tokens and JWKS | USR-03 |
| ADM-010 Email verification and password reset | USR-04 |
