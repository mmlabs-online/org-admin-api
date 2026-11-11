# Backlog

States: BACKLOG → PLANNING → IN_PROGRESS → REVIEW → DONE · plus REWORK, BLOCKED, ESCALATED.
Priority: P1 (next) > P2 > P3. `/run` with no ID picks the top BACKLOG ticket.

Ticket IDs: `USR-XX` for users, identity and authorization (mmlabs Identity, spec §5.3/§5.8); `ADM-XXX` for everything else.

| ID | Title | State | Priority | Areas | Attempts | Updated |
|---|---|---|---|---|---|---|
| ADM-001 | Scaffold FastAPI + PostgreSQL service, tooling and CI | DONE | P1 | architecture, db | 1 | 2026-11-10 |
| ADM-007 | Release and deploy pipeline to api.mmlabs.online | IN_PROGRESS | P1 | architecture, security | 0 | 2026-11-10 |
| USR-01 | Ecosystem users and password credentials | BACKLOG | P1 | architecture, db, api, auth, security | 0 | 2026-11-11 |
| USR-02 | Apps registry and multi-app RBAC | BACKLOG | P1 | architecture, db, api, auth, security | 0 | 2026-11-11 |
| USR-03 | Login, refresh and per-app access tokens, JWKS | BACKLOG | P1 | architecture, auth, security, db, api | 0 | 2026-11-11 |
| ADM-002 | Protect admin endpoints with mmlabs Identity tokens and admin permissions | BACKLOG | P1 | auth, security, api | 0 | 2026-11-11 |
| ADM-004 | Audit log for mutating admin actions | BACKLOG | P1 | db, api, security | 0 | 2026-11-10 |
| USR-04 | Email verification and password reset | BACKLOG | P2 | auth, security, db, api | 0 | 2026-11-11 |
| USR-05 | App catalog registration and cached role catalog for apps | BACKLOG | P2 | api, auth, security | 0 | 2026-11-11 |
| ADM-006 | Org product entitlements | BACKLOG | P2 | architecture, db, api | 0 | 2026-11-10 |

Order of work (dependencies): USR-01 → USR-02 → USR-03 → ADM-002 → ADM-004; USR-04 after USR-03; USR-05 after USR-02 + USR-03; ADM-006 after USR-02.

### Renamed / merged (2026-11-11)
| Old | New |
|---|---|
| ADM-003 RBAC for staff | merged into USR-02 |
| ADM-005 Products registry | merged into USR-02 (apps registry) |
| ADM-008 Users and password credentials | USR-01 |
| ADM-009 Login, tokens and JWKS | USR-03 |
| ADM-010 Email verification and password reset | USR-04 |
