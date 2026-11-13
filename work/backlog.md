# Backlog

States: BACKLOG → PLANNING → IN_PROGRESS → REVIEW → DONE · plus REWORK, BLOCKED, ESCALATED.
Priority: P1 (next) > P2 > P3. `/run` with no ID picks the top BACKLOG ticket.

Ticket IDs: `USR-XX` for users, identity and authorization (mmlabs Identity, spec §5.3/§5.8); `ADM-XXX` for everything else.

| ID | Title | State | Priority | Areas | Attempts | Updated |
|---|---|---|---|---|---|---|
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
| USR-03b | Accounts list, join flow, @mmlabs/auth-client and mmlabs-auth | BACKLOG | P1 | architecture, api, auth, security | 0 | 2026-11-11 |
| ADM-002 | Lock admin endpoints (aud=admin + require_permission) | BACKLOG | P1 | auth, security, api | 0 | 2026-11-11 |
| ADM-004 | Audit log (admin actions, login, join, role changes, org membership) | BACKLOG | P1 | db, api, security | 0 | 2026-11-11 |
| USR-06 | Organizations and org-aware tokens (first consumer: calechip) | BACKLOG | P2 | architecture, db, api, auth, security | 0 | 2026-11-11 |
| USR-04d | Email hardening: dispatcher shutdown, retention cleanup, daily-cap alert | DONE | P1 | auth, db | 1 | 2026-11-13 |
| USR-05 | Apps self-register their permission manifest | BACKLOG | P2 | api, auth, security | 0 | 2026-11-11 |
| ADM-006 | Org product entitlements | BACKLOG | P2 | architecture, db, api | 0 | 2026-11-10 |

Order of work (dependencies; reprioritised 2026-11-12 by the owner for the ricotdin integration path): USR-01 → USR-01b → USR-02 → USR-02b → USR-02d → USR-02e → ADM-011 → USR-02c → USR-02f → USR-03 → USR-03f → USR-03c → USR-03e → USR-04 → USR-04c → USR-07 → USR-04b → USR-03b → ADM-002 → ADM-004 → USR-06 → USR-05.
Hard dependencies: USR-01b ← USR-01 · USR-02 ← USR-01 · USR-02b ← USR-02 · USR-02d ← USR-02b · USR-02e ← USR-02d · USR-02c ← USR-02, USR-02b, USR-02d · USR-02f ← USR-02c · USR-03 ← USR-01, USR-02 · USR-03f ← USR-03 · USR-03c ← USR-03, USR-03f, USR-02 · USR-03e ← USR-03, USR-02 · USR-03b ← USR-03, USR-03c, USR-03e · ADM-002 ← USR-02, USR-02b, USR-02c, USR-02d, USR-02f, USR-02e, USR-03, USR-03c, USR-03e · ADM-004 ← ADM-002 · USR-04 ← USR-01, USR-03, USR-03c · USR-07 ← USR-02, USR-02f · USR-04c ← USR-04 · USR-04b ← USR-04, USR-04c · USR-04d ← USR-04b · USR-06 ← USR-03b, USR-03c, ADM-004, USR-04 · USR-05 ← USR-02e, USR-03b · ADM-006 ← USR-02.
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
