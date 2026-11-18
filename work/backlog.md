# Backlog

States: BACKLOG → PLANNING → IN_PROGRESS → REVIEW → DONE · plus REWORK, BLOCKED, ESCALATED.
Priority: P1 (next) > P2 > P3. `/run` with no ID picks the top BACKLOG ticket.

Ticket IDs: `USR-XX` for users, identity and authorization (mmlabs Identity, spec §5.3/§5.8); `ADM-XXX` for everything else; `BUG-XXX` for bugs (`/bug`, severity S1–S3; ready S1 bugs run first, ADR-0012).

| ID | Title | State | Priority | Areas | Attempts | Updated |
|---|---|---|---|---|---|---|
| USR-13 | Allow 12-hour access tokens (ADR-0005 amendment) | DONE | P1 | architecture, auth, security | 0 | 2026-11-17 |
| ADM-001 | Scaffold FastAPI + PostgreSQL service, tooling and CI | DONE | P1 | architecture, db | 1 | 2026-11-10 |
| ADM-007 | Release and deploy pipeline to api.mmlabs.online | IN_PROGRESS | P1 | architecture, security | 0 | 2026-11-10 |
| USR-01 | Users, passwords (Argon2id) and sign-up with Turnstile | DONE | P1 | architecture, db, api, auth, security | 1 | 2026-11-11 |
| USR-01b | Staff user management endpoints (list, get, enable/disable) | DONE | P1 | api, security | 1 | 2026-11-11 |
| USR-02 | Apps registry and per-app RBAC (join policy, org mode, origins, default role) | DONE | P1 | architecture, db, api, auth, security | 1 | 2026-11-11 |
| USR-02b | Apps registry staff API (flag-gated) and role catalog | DONE | P1 | api, auth, security | 1 | 2026-11-11 |
| USR-02d | Permissions and roles staff API (flag-gated) | DONE | P1 | api, auth, security | 1 | 2026-11-11 |
| USR-02e | Public role catalog with ETag | DONE | P1 | api, security | 1 | 2026-11-11 |
| ADM-011 | Platform hardening for public endpoints (client IP, limiter, error envelope, display text) | DONE | P1 | security, api | 0 | 2026-11-11 |
| USR-02c | Role assignments staff API (flag-gated) | DONE | P1 | api, auth, security | 1 | 2026-11-12 |
| USR-02f | Bootstrap super-admin CLI (grant-super-admin) with masked output and runbook | DONE | P1 | auth, security | 1 | 2026-11-12 |
| USR-03 | Device sessions (multi-account), cookie rotation, login/logout with Origin check | DONE | P1 | architecture, auth, security, db, api | 1 | 2026-11-12 |
| USR-03f | Session hardening: commit before response, atomic login throttle, cookie-tossing defence | DONE | P1 | auth, security, api | 1 | 2026-11-12 |
| USR-03c | Signing keys, per-app token endpoint, JWKS and key rotation runbook | DONE | P1 | architecture, auth, security, api | 1 | 2026-11-12 |
| USR-03e | Dynamic CORS from apps.origins; remove or restrict CORS_ORIGINS | DONE | P1 | api, security, auth | 1 | 2026-11-12 |
| USR-04 | Email verification via Gmail SMTP (sender, dispatcher, verify, resend) | DONE | P1 | auth, security, db, api | 1 | 2026-11-12 |
| USR-04c | Require X-MM-App on signup; remove the EMAIL_LINK_BASE_URL fallback | DONE | P1 | auth, security | 1 | 2026-11-13 |
| USR-07 | Register an app in production from the CLI (first consumer: ricotdin) | DONE | P1 | api, auth, security | 1 | 2026-11-13 |
| USR-04b | Password forgot/reset with all-session revocation | DONE | P1 | auth, security, api | 1 | 2026-11-13 |
| USR-03b | Accounts list and join flow (API) | DONE | P1 | api, auth, security, db | 1 | 2026-11-13 |
| ADM-002 | Lock admin endpoints (aud=admin + require_permission) | DONE | P1 | auth, security, api | 1 | 2026-11-15 |
| ADM-002c | Set admin app origins from the CLI | DONE | P1 | auth, security | 1 | 2026-11-15 |
| ADM-002b | Enforce admin RBAC guards and mount the users/RBAC APIs | DONE | P1 | auth, security, api | 1 | 2026-11-15 |
| ADM-002d | Admin auth hardening and runbook fixes | DONE | P2 | auth, security | 1 | 2026-11-15 |
| ADM-002e | Close the APP_ENV ?host= bypass and fix the admin runbook trap | DONE | P1 | auth, security | 2 | 2026-11-15 |
| ADM-012 | Non-credentialed CORS for the Bearer admin endpoints | DONE | P1 | api, security, auth | 1 | 2026-11-16 |
| USR-11 | Caller's effective admin permissions endpoint for the admin UI | DONE | P1 | api, auth, security | 0 | 2026-11-16 |
| ADM-013 | OpenAPI contract that clients can generate types from | DONE | P2 | api | 0 | 2026-11-16 |
| USR-12 | Allow resend-verification for the admin app | DONE | P2 | api, auth, security | 0 | 2026-11-16 |
| ADM-014 | Docs — admin UI hostname and forgot-password credentials | DONE | P2 | security | 1 | 2026-11-16 |
| ADM-015 | Dashboard stats endpoint for the admin UI | DONE | P3 | api, db | 1 | 2026-11-17 |
| ADM-016 | `member_count` on the staff apps list and app detail | DONE | P3 | api, db | 0 | 2026-11-17 |
| USR-10 | Staff session management for a user (list, revoke one, revoke all) | DONE | P3 | api, auth, security, db | 0 | 2026-11-17 |
| ADM-017 | Operational reports (placeholder; out of v1) | BLOCKED | P3 | architecture, db, api, security | 0 | 2026-11-16 |
| ADM-004 | Audit log (admin actions, login, join, role changes, org membership) | DONE | P1 | db, api, security | 1 | 2026-11-15 |
| ADM-004b | Audit identity events, denied privileged attempts and retention | DONE | P1 | db, api, security, auth | 1 | 2026-11-15 |
| ADM-004c | Audit retention purge | DONE | P1 | db, security | 1 | 2026-11-15 |
| ADM-004d | Audit log DB hardening | DONE | P2 | db, security | 1 | 2026-11-15 |
| USR-06 | Organizations and org-aware tokens (first consumer: calechip) — slice 06a | DONE | P2 | architecture, db, api, auth, security | 0 | 2026-11-11 |
| USR-06b | Org invitations (create, accept by verified email) | DONE | P2 | api, auth, security, db | 0 | 2026-11-17 |
| USR-06c | Org-scoped roles in tokens and default-role hygiene for org apps | DONE | P2 | architecture, db, api, auth, security | 0 | 2026-11-17 |
| USR-04d | Email hardening: dispatcher shutdown, retention cleanup, daily-cap alert | DONE | P1 | auth, db | 1 | 2026-11-13 |
| USR-03g | Join approval flow (join_requests + staff approve/reject) | DONE | P2 | api, auth, security, db | 1 | 2026-11-17 |
| USR-08 | Client libraries @mmlabs/auth-client (TS) and mmlabs-auth (Python) — separate repo | DONE | P1 | architecture, auth, security | 0 | 2026-11-16 |
| USR-09 | Create a staff-vouched account from the CLI (bootstrap for a new app's first user) | DONE | P1 | auth, security, db | 3 | 2026-11-15 |
| USR-05 | Apps self-register their permission manifest — slice 05a (app credentials) | DONE | P2 | api, auth, security | 0 | 2026-11-17 |
| USR-05b | Manifest push endpoint (app credential auth) | DONE | P2 | api, auth, security | 1 | 2026-11-17 |
| ADM-006 | Org product entitlements | DONE | P2 | architecture, db, api | 0 | 2026-11-17 |
| ADM-018 | Service read of org entitlements (`ent` token claim) | DONE | P3 | architecture, auth, security, api | 0 | 2026-11-18 |
| USR-14 | Admin tokens end on logout, password reset and staff session revoke | DONE | P2 | architecture, auth, security, api | 1 | 2026-11-17 |
| USR-15 | Join requests hardening | DONE | P3 | api, auth, security, db | 0 | 2026-11-18 |
| ADM-019 | Guard org_mode required → none while org data exists | DONE | P3 | api, db | 0 | 2026-11-18 |
| BUG-001 | `test_purge_batches_share_cutoff` fails intermittently in `make check` | DONE | P3 | db | 1 | 2026-11-18 |
| BUG-002 | `test_changing_push_writes_one_row` fails intermittently | DONE | P3 | api | 0 | 2026-11-18 |
| USR-16 | Staff role listing with ids (admin Bearer), including the admin app and disabled apps | DONE | P1 | api, auth, security | 0 | 2026-11-18 |
| USR-17 | Holders of a role: list assignments by role (admin Bearer) | DONE | P1 | api, auth, security, db | 0 | 2026-11-18 |
| ADM-020 | Filters and sort for the staff organizations list | DONE | P1 | api, db | 0 | 2026-11-18 |
| USR-18 | Filters and sort for the staff apps list | DONE | P1 | api | 0 | 2026-11-18 |
| ADM-021 | Audit log: org filter and single-entry read | DONE | P2 | api, db | 1 | 2026-11-18 |
| ADM-023 | Declare 404/409 responses and admin:* requirements in OpenAPI | DONE | P2 | api | 0 | 2026-11-18 |
| ADM-025 | Grant admin:entitlements:* to a staff role (needs owner) | BACKLOG | P2 | db, auth, security | 0 | 2026-11-17 |
| ADM-022 | Server-side CSV export of the audit log (needs owner: §5.11 overlap) | BACKLOG | P3 | api, security | 0 | 2026-11-17 |
| ADM-024 | Optimistic concurrency (ETag / If-Match) on PATCH for organizations and entitlements | BACKLOG | P3 | api, architecture | 0 | 2026-11-17 |
| USR-19 | Label and optional expiry for app credentials | BACKLOG | P3 | api, auth, security, db | 0 | 2026-11-17 |
| USR-20 | Join requests: richer decision response | BACKLOG | P3 | api, auth | 0 | 2026-11-17 |
| USR-21 | Join requests: reject cooldown controls and pending count per app | BACKLOG | P3 | api, auth, security | 0 | 2026-11-17 |
| USR-22 | Join requests: optional reject reason and expiry of stale pending requests | BACKLOG | P3 | api, auth, security, db | 0 | 2026-11-17 |

Order of work (dependencies; reprioritised 2026-11-12 by the owner for the ricotdin integration path): USR-01 → USR-01b → USR-02 → USR-02b → USR-02d → USR-02e → ADM-011 → USR-02c → USR-02f → USR-03 → USR-03f → USR-03c → USR-03e → USR-04 → USR-04c → USR-07 → USR-04b → USR-03b → ADM-002 → ADM-002c → ADM-002b → ADM-004 → ADM-004b → ADM-004c → ADM-002e → ADM-012 → USR-08 → ADM-013 → ADM-014 → USR-11 → USR-12 → USR-06 → USR-06b → USR-06c → USR-05 → USR-05b. admin-pan requests (2026-11-17): USR-16 → USR-17 → ADM-020 → USR-18 → ADM-021 → ADM-023 → ADM-025 → ADM-022 → ADM-024 → USR-19 → USR-20 → USR-21 → USR-22.
Hard dependencies: USR-01b ← USR-01 · USR-02 ← USR-01 · USR-02b ← USR-02 · USR-02d ← USR-02b · USR-02e ← USR-02d · USR-02c ← USR-02, USR-02b, USR-02d · USR-02f ← USR-02c · USR-03 ← USR-01, USR-02 · USR-03f ← USR-03 · USR-03c ← USR-03, USR-03f, USR-02 · USR-03e ← USR-03, USR-02 · USR-03b ← USR-03, USR-03c, USR-03e · ADM-002 ← USR-02, USR-02b, USR-02c, USR-02d, USR-02f, USR-02e, USR-03, USR-03c, USR-03e · ADM-002b ← ADM-002 · ADM-002c ← ADM-002 · ADM-002d ← ADM-002b · ADM-002e ← ADM-002d · ADM-012 ← ADM-002, ADM-002c, USR-03e · ADM-013 ← ADM-002, USR-03b · ADM-014 ← ADM-002e · USR-11 ← ADM-002, ADM-013 · USR-12 ← ADM-002d, USR-04 · ADM-015 ← ADM-004b, ADM-012 · ADM-016 ← USR-02c, ADM-012 · USR-10 ← USR-03, ADM-002b, ADM-004b, ADM-012 · ADM-017 ← ADM-012, ADM-004b · ADM-004 ← ADM-002 · ADM-004b ← ADM-004 · ADM-004c ← ADM-004b · ADM-004d ← ADM-004c · USR-04 ← USR-01, USR-03, USR-03c · USR-07 ← USR-02, USR-02f · USR-04c ← USR-04 · USR-04b ← USR-04, USR-04c · USR-04d ← USR-04b · USR-06 ← USR-03b, USR-03c, ADM-004, USR-04 · USR-06b ← USR-06 · USR-06c ← USR-06, USR-06b · USR-03g ← USR-03b · USR-08 ← USR-03b · USR-05 ← USR-02e, USR-03b, USR-06c · USR-05b ← USR-05 · ADM-006 ← USR-02 · ADM-018 ← ADM-006 · USR-14 ← USR-13, USR-10 · USR-15 ← USR-03g · ADM-019 ← ADM-006 · USR-09 ← USR-01, USR-02f, USR-07 · USR-16 ← USR-02d, ADM-002b, ADM-012, ADM-013 · USR-17 ← USR-16, USR-02c · ADM-020 ← ADM-002b, ADM-013 · USR-18 ← USR-02b, ADM-016 (shares design with ADM-020) · ADM-021 ← ADM-004, ADM-004b · ADM-022 ← ADM-021, ADM-004b · ADM-023 ← ADM-013 · ADM-024 ← ADM-006, ADM-012, ADM-013 · ADM-025 ← ADM-006 · USR-19 ← USR-05, USR-05b · USR-20 ← USR-03g · USR-21 ← USR-03g · USR-22 ← USR-03g.
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
