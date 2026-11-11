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
| USR-02d | Permissions and roles staff API (flag-gated) | REVIEW | P1 | api, auth, security | 0 | 2026-11-11 |
| USR-02e | Role catalog with ETag (flag-gated) | BACKLOG | P1 | api, security | 0 | 2026-11-11 |
| USR-02c | Role assignments staff API (flag-gated) and bootstrap super-admin CLI | BACKLOG | P1 | api, auth, security | 0 | 2026-11-11 |
| USR-03 | Device sessions (multi-account), login/logout, per-app token, JWKS, CORS/CSRF | BACKLOG | P1 | architecture, auth, security, db, api | 0 | 2026-11-11 |
| USR-03b | Accounts list, join flow, @mmlabs/auth-client and mmlabs-auth | BACKLOG | P1 | architecture, api, auth, security | 0 | 2026-11-11 |
| ADM-002 | Lock admin endpoints (aud=admin + require_permission) | BACKLOG | P1 | auth, security, api | 0 | 2026-11-11 |
| ADM-004 | Audit log (admin actions, login, join, role changes, org membership) | BACKLOG | P1 | db, api, security | 0 | 2026-11-11 |
| USR-04 | Email verification and password reset via Gmail SMTP | BACKLOG | P1 | auth, security, db, api | 0 | 2026-11-11 |
| USR-06 | Organizations and org-aware tokens (first consumer: calechip) | BACKLOG | P2 | architecture, db, api, auth, security | 0 | 2026-11-11 |
| USR-05 | Apps self-register their permission manifest | BACKLOG | P2 | api, auth, security | 0 | 2026-11-11 |
| ADM-006 | Org product entitlements | BACKLOG | P2 | architecture, db, api | 0 | 2026-11-10 |

Order of work (dependencies): USR-01 → USR-01b → USR-02 → USR-02b → USR-02d → USR-02e → USR-02c → USR-03 → USR-03b → ADM-002 → ADM-004 → USR-04 → USR-06 → USR-05.
Hard dependencies: USR-01b ← USR-01 · USR-02 ← USR-01 · USR-02b ← USR-02 · USR-02d ← USR-02b · USR-02e ← USR-02d · USR-02c ← USR-02, USR-02b, USR-02d · USR-03 ← USR-01, USR-02 · USR-03b ← USR-03 · ADM-002 ← USR-02, USR-02b, USR-02c, USR-02d, USR-02e, USR-03 · ADM-004 ← ADM-002 · USR-04 ← USR-01, USR-03 · USR-06 ← USR-03b, ADM-004, USR-04 · USR-05 ← USR-02e, USR-03b · ADM-006 ← USR-02.
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
