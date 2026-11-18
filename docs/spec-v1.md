# org-admin-api — Product spec v1

Status: **Draft** — written while bootstrapping the repo (2026-11-10); §5.9–§5.11 and OQ4–OQ18 added 2026-11-16 for the admin UI `admin-pan`. Everything below is a proposal for the owner to confirm, cut or change, especially §8 Open questions.
Source of truth for scope: if `CLAUDE.md`, a ticket and this spec disagree on scope, **this spec wins** — flag the conflict.

## §1 Problem

mmlabs runs several products/services. Admin tasks (managing customer orgs, users, which products an org can use, support actions) are spread across each product, with no single place, no shared permission model, and no audit trail of who changed what.

## §2 Goals

- One admin API that the mmlabs admin web UI (and internal tools) use for cross-product administration.
- **mmlabs Identity:** one user account for the whole mmlabs ecosystem, with email + password managed by this service, and tokens every mmlabs app can verify (owner, 2026-11-11; §5.8).
- A shared permission model for internal staff.
- A complete, queryable audit log of admin actions.

## §3 Non-goals (v1)

- ~~End-user (customer) login/auth for the products themselves.~~ Removed 2026-11-11 (owner): end-user identity is now in scope, see §5.8.
- Social login (Google, Apple…), acting as a full OIDC/OAuth2 provider, and a hosted login UI — not in v1 of §5.8. Each app keeps its own login page (2026-11-11, D1/D4).
- Migrating users from Supabase Auth (working assumption OQ2: existing users re-register).
- Billing / payments.
- Replacing each product's own internal data model.

## §4 Actors

| Actor | Wants to… | So that… |
|---|---|---|
| Super admin | manage staff accounts and their roles | access follows least privilege |
| Support staff | look up an org and its users, suspend/reactivate | tickets get resolved quickly |
| Ops / product owner | enable/disable a product for an org, set plan/limits | customers get the right entitlements |
| mmlabs services | read an org's status and entitlements | products enforce what admin decided |
| Auditor | see who did what and when | changes are accountable |

## §5 Features

### §5.1 Organizations — *done (ADM-001)*
CRUD for organizations (slug, name, description, status `active`/`suspended`). Paginated list.
> 2026-11-11 (D10, D15): this table moves to the Identity module (USR-06), gains `created_by`, keeps `description` and `status`; apps use it per `apps.org_mode`.
> 2026-11-17 (USR-06a, done): owned by Identity (D15 = ownership; code stays in `app/modules/organizations/`), `created_by` added, FK from `role_assignments.org_id` (RESTRICT; admin delete with assignments answers 409 `org_has_assignments`). End-user create/list and org-scoped tokens: ADR-0013. Invitations: USR-06b.

### §5.2 Staff authentication
Only authenticated mmlabs staff can call `/api/v1/*`. Health endpoints stay public. Approach (SSO/OIDC vs. other) is an open question (§8 Q2).

### §5.3 Authorization — multi-app RBAC (owner, 2026-11-11; [ADR-0001](adr/0001-multi-app-authorization.md))
One model for every mmlabs app, the admin API included (app `admin`). Apps are registered; each app declares namespaced permissions (`<app>:<resource>:<action>`) and its own roles; users get roles per app, optionally limited to one organization. Every admin `/api/v1` endpoint declares the `admin:*` permission it needs, except self-scoped reads of the caller's own authorization (`GET /api/v1/me/permissions`, ADR-0008). A bootstrap super-admin (`admin:*`) exists. Business rules on an app's own data stay in that app.

Membership (2026-11-11, D8–D10; details in [`identity-architecture.md`](identity-architecture.md)): each app has a `join_policy` (`open` | `approval` | `closed`; default `open`, `admin` = `closed`), a default role `<app>:member` holding all its permissions except those flagged `is_admin` (for `org_mode = required` apps also except org-level permissions, `permissions.scope = 'org'`; ADR-0015; owner, 2026-11-17), an `org_mode` (`none` | `required`; calechip = `required`) and its allowed browser `origins`. Joining assigns the default role at app scope (`org_id NULL`). Policy and role contents are data. The role catalog `GET /api/v1/apps/{key}/roles` is public (D17); app origins are limited to `mmlabs.online`, its subdomains and localhost (D19).

### §5.4 Audit log
Append-only record of every mutating admin action: actor, action, target type/id, before/after (or diff), timestamp, request id. Read-only, filterable listing for auditors.

### §5.5 Products registry — merged into the apps registry of §5.3 (2026-11-11)
The list of mmlabs products is the `apps` table of §5.3 (key, name, status).

### §5.6 Org product entitlements
Which org can use which product, with plan and limits; enable/disable per org. Readable by mmlabs services through the `ent` access-token claim (§8 Q4, ADR-0017).

> 2026-11-17 (ADM-006): staff Bearer endpoints under /api/v1/organizations/{org_id}/entitlements (admin:entitlements:read|write). Active = enabled AND org active AND app active (?active=true). Only non-admin, active, org_mode=required apps can be granted. ADM-018: services read it through the `ent` claim (Q4, ADR-0017).

### §5.8 mmlabs Identity — ecosystem users and authentication (owner, 2026-11-11)
One account per person across all mmlabs apps, **fully self-hosted here** (D1): email + password only, no external IdP, no social login, not an OIDC provider. Architecture, data model, API and decision log D1–D12: [`identity-architecture.md`](identity-architecture.md).

- **Users:** email (unique, case-insensitive), password, status (`active` / `disabled`), email verified flag, timestamps. Staff are users too; what they may do in the admin API is RBAC (§5.3).
- **Sign-up:** open self-registration, protected by Cloudflare Turnstile and a per-IP rate limit (D2). Unverified users may log in but must verify their email before joining any app or getting an app token.
- **Passwords:** hashed with Argon2id; never logged or returned; minimum length and a breached/common-password check per the plan.
- **Login and sessions:** each app has its own login page and uses `@mmlabs/auth-client`, which posts credentials straight to this API (D4). A browser holds one device session (cookie `__Host-mm_device`, rotated with a 60 s grace) with any number of signed-in accounts; each app remembers its own chosen account, so signing into another account in one app does not change another app (D5, D6). Logout per account or for the whole device.
- **Per-app access tokens:** an app asks for its own per-app access JWT (lifetime `ACCESS_TOKEN_TTL_SECONDS`, up to 12 h; 12 h in production (owner, 2026-11-17)) for a chosen account (and org, if any), carrying only that app's roles (D7). Not a member → `not_member` with `join_available`; joining follows the app's `join_policy` (D8–D9).
- **Verification by apps:** public keys at `/.well-known/jwks.json`; apps verify signature, `iss`, `aud` and expiry locally. Claims: `sub`, `email`, `email_verified`, `aud`, `roles` (app-scope plus selected-org roles), `org` (if scoped), `org_roles` (if scoped: role keys held in that org only; org apps authorize org data from it, ADR-0015) (owner, 2026-11-17), `ent` (if scoped: `{plan, limits}` of the org's active entitlement for the app, else `null`; ADR-0017), `iat`, `exp`. Backends use `mmlabs-auth` (Python) (D4).
- **Abuse protection:** no hard lockout; per-email exponential backoff after 5 failures (cap 15 min), per-IP limit, Turnstile after 3 failures; the same error for unknown email and wrong password (D11). CORS only for registered app origins; cookie-authenticated POSTs check Origin and `X-MM-App` (D12).
- **Email flows:** email verification and password reset by one-time, expiring links, sent through Gmail SMTP behind an `EmailSender` interface; resend at most 1/min and 5/day per email (D3).
- **Organizations:** optional per app (`org_mode`); one `organizations` table owned by this Identity module (D10, D15, USR-06).
- **Endpoints:** all under `/api/v1/auth/*` (cookie `__Host-mm_device`, `Path=/`, host-only; D13 as amended by D20); JWKS at `/.well-known/jwks.json` (D13).
- **Admin:** staff can list, look up, disable/enable users, manage their roles per app and revoke their sessions (audited, §5.4). Session list and revocation per user: §5.10.

### §5.9 Admin UI read models — dashboard stats and per-app member counts (owner idea, 2026-11-16; draft)
Context (owner, 2026-11-16): the admin UI **`admin-pan`** (repo `mmlabs-online/admin-pan`, served at `https://admin-portal.mmlabs.online`) is a browser SPA that calls this API with Bearer `aud=admin` tokens. Its mock screens need the read models below. Field names are **snake_case** (owner, 2026-11-16), like the rest of v1.

**Prerequisite — browser access for admin routes.** Today CORS covers `/api/v1/auth/*` only (ADR-0006); admin routes send no CORS headers, so a SPA on another origin cannot call them. Owner note (2026-11-12, ADM-002 ticket, USR-03e A1): "if an admin web UI needs cross-origin Bearer calls, add non-credentialed CORS from `apps[admin].origins`". So: `https://admin-portal.mmlabs.online` is registered as an `admin` app origin (CLI `set-admin-origins`), and admin routes answer CORS for those origins only, **without** credentials (Bearer in the `Authorization` header, never the cookie). The SPA gets its `aud=admin` token through the existing `/api/v1/auth/*` flow with `X-MM-App: admin`.

**Dashboard stats** — one read-only document for the dashboard screen (a single document, not `Page[T]`: an R9 exception to record like D18). Every number comes from data that already exists; counts are returned as breakdowns so the UI, not this spec, chooses what a tile shows:

| Tile (admin-pan) | Source | Computable today? |
|---|---|---|
| Total users | `users` — total, by `status` (`active`/`disabled`) | yes |
| Unverified users | `users.email_verified_at IS NULL` | yes |
| Active / suspended orgs | `organizations.status` (`active`/`suspended`, §5.1) | yes |
| Apps | `apps` — total, by `status` | yes |
| Sign-ins, last 24 h (succeeded / failed) | `audit_log` rows `auth.login_succeeded` / `auth.login_failed` in a rolling 24 h window (event counts, not distinct users) | yes, with caveats below |
| Sign-ups per month | `users.created_at` grouped by calendar month | yes (last 12 months, month boundaries in `Asia/Ho_Chi_Minh`: OQ6, decided 2026-11-17) |
| Members per app | same definition as the per-app member count below | yes (OQ5 definition, decided 2026-11-17) |

Caveats to keep visible in the UI and API docs: failed sign-ins include unknown emails (anonymous actor) but **not** requests refused with 429 or by Turnstile (ADM-004b writes nothing for those); sign-up counts include self sign-ups and CLI-created accounts, and do not depend on the audit log (whose retention is ≥ 90 days, ADM-004c). Reads must meet §6 (p95 < 200 ms) — the planner checks that the audit-log count is index-backed.

**Per-app member count** — the "Members" column of the Apps screen: an additive `member_count` field on the existing staff apps list and app detail (no new endpoint, no breaking change in v1). Definition (owner, 2026-11-17, OQ5): a member is a distinct user with at least one non-expired role assignment on the app (app scope or any org); only users with status `active` count (unverified email still counts); the `admin` app shows its count (= staff).

Permission (owner, 2026-11-17, OQ4): new permission `admin:stats:read` for the dashboard stats document (counts only, no audit rows exposed); `member_count` uses the existing `admin:apps:read`. Read-only, so no audit entry (R4 covers mutations only).

> 2026-11-17 (ADM-015): `GET /api/v1/stats/dashboard` (admin:stats:read, is_admin, granted to no role; a single document, R9 exception D26). Sign-ups: last 12 months including the current partial month, boundaries in Asia/Ho_Chi_Minh. Sign-ins: 24 h totals (succeeded / failed), not split per app.

### §5.10 Staff session management (owner idea, 2026-11-16; draft)
Staff can see a user's signed-in sessions and sign them out — completes the "revoke their sessions" line of §5.8 Admin.

- **List** a user's account sessions (paginated), each with: account session id, created / last used, its device session's last seen, IP and user agent (from `device_sessions`), and revoked state. Default: non-revoked only.
- **Revoke one** account session, and **revoke all** of the user's account sessions on every device (the same effect as a password reset, USR-04b). Staff revoke **account** sessions, not device sessions: a device session may also hold other people's accounts on a shared browser, and they must stay signed in.
- **Permission `admin:*`** (owner, 2026-11-16) for list and revoke.
- **Audited** (R4, §5.4): one entry per revoke action in the same transaction, actor = staff user, target = the user; `changes` carries session ids / counts only, never IPs, user agents, cookies or tokens. New action names are set at PLAN (e.g. `user.session_revoked`, `user.sessions_revoked`).
- **Known limit (no refresh tokens, D7):** access tokens already issued stay valid until they expire (up to `ACCESS_TOKEN_TTL_SECONDS`: 12 h in production (owner, 2026-11-17); admin role revoke and disable still apply on the next admin request, ADR-0008 §3). Revocation stops new tokens; it does not kill live app tokens. Admin (`aud=admin`) tokens carry `sid` and end on the next admin request after logout, password reset, staff session revoke or disable (USR-14). Whether the remaining limits are acceptable for admin tokens: §8 OQ8.
- **USR-10 (owner, 2026-11-17):** `GET /api/v1/users/{id}/sessions` (active sessions only; fields `id`, `created_at`, `last_used_at`, `device_last_seen_at`, `ip`, `user_agent`; `Cache-Control: no-store`), `DELETE /api/v1/users/{id}/sessions/{session_id}` and `DELETE /api/v1/users/{id}/sessions` (revoke one / all; 204). All three need `admin:*`; staff cannot use them on themselves (403 `self_session_change_forbidden`). Audit actions `user.session_revoked`, `user.sessions_revoked` (ids capped at 100, exact count); a revoke that changes nothing writes no row. Revoking sessions stops the user from getting **new** access tokens (`/auth/token` -> 401 `session_invalid`) and ends the user's admin (`aud=admin`) tokens bound to those sessions on their next admin request (USR-14). Tokens of other apps stay valid until `exp`, at most `ACCESS_TOKEN_TTL_SECONDS` (12 h in production) plus 30 s leeway. It does not stop a new sign-in with the password. IP and user agent are recorded when the browser first signed in and are shared by every account on that browser.
- Open: Q6 (showing IP / user agent to staff is PII).

### §5.11 Operational reports (owner idea, 2026-11-16; **scope not decided**)
admin-pan has a mock Reports screen: define, run and download operational reports — types `user_signups`, `active_users`, `app_memberships`, `org_overview`, `audit_activity`; formats `csv` / `xlsx`; schedule `none` / `daily` / `weekly` / `monthly`. **The owner has not settled the scope (2026-11-16).** Nothing here is in scope until the questions in §8 OQ9–OQ18 are answered; no ticket may implement reports before then.

Facts the decision should weigh (not decisions):
- `active_users` has no defined source: `users` has no last-login field. Candidates are `account_sessions.last_used_at` or `auth.login_succeeded` audit rows (gone after the ≥ 90-day retention) — §8 OQ12.
- Schedules need a scheduler; the runtime today is uvicorn only, with migrations and CLI jobs run separately (§6.1). Delivering scheduled results by email would compete with the Gmail SMTP daily limits (D3).
- `xlsx` needs a new library (stack rule: owner's decision).
- Reports listing users export emails (PII, Q6); `audit_activity` exposes audit data, today readable only with `admin:*` (`admin:audit:read`).
- Partial overlap: `user_signups`, `app_memberships` and `org_overview` are close to the §5.9 dashboard numbers; `audit_activity` is close to `GET /api/v1/audit-logs`.

No §3 non-goal conflicts with §5.9–§5.11 (no billing, no social login / OIDC provider / hosted login UI involved).

### §5.7 Later (not v1)
Org members view, per-org feature flags, announcements, impersonation (heavily audited), webhooks/events to products.

## §6 Non-functional

- p95 latency < 200 ms for reads at expected admin load.
- 100% of mutating endpoints write an audit entry (once §5.4 lands).
- No secrets in code or logs; config via env only.

### §6.1 Deployment
- Public hostname: **`api.mmlabs.online`** (owner, 2026-11-11; was `api-admin.mmlabs.online` from 2026-11-10).
- Release/deploy follows the mmlabs deployment repo: branch `release/vX.Y.Z` from main (owner, 2026-11-10; the workflow creates the tag) → image on Docker Hub → PR on `mmlabs-online/deployment` → owner merges → deploy on the Mac self-hosted runner, exposed through Cloudflare Tunnel. Database: the shared mmlabs Postgres 17 (compose on the Mac runner), database `org_admin`, persistent `pgdata` volume with nightly and offsite backups (owner, 2026-11-10). Runbook: `docs/deploy.md`.
- API docs: Swagger UI at `/docs` and `/openapi.json` are public in production too (owner, 2026-11-11; temporary — revisit when ADM-002 mounts the admin routes).
- No Cloudflare Access gate is required before staff authentication (§5.2) ships (owner, 2026-11-10). Accepted risk: until ADM-002 lands, anyone who can reach the hostname can call the admin endpoints.

## §7 Success metrics

- All admin actions for in-scope products go through this API (no direct DB edits).

## §8 Open questions (owner)

- [ ] Q1. Which mmlabs products are in scope first?
- [x] Q2. Staff login — **answered 2026-11-11:** email + password managed by this service (§5.8); staff are §5.8 users with RBAC roles. 2FA for staff: see OQ3.
- [ ] Q3. Is this API the **source of truth** for orgs/users, or does it aggregate data owned by each product?
- [x] Q4. How do products consume admin decisions — pull (API with service keys), push (webhooks/events), or both? **Answered 2026-11-17 (owner):** through an additive `ent` claim (`{plan, limits}`) in the access token, added only when the `org` claim is present; staleness up to the token TTL (12 h) is accepted. No pull endpoint and no app-credential widening for now (R3 class 4 unchanged). **2026-11-18:** with `org` but no active entitlement (none, disabled, app disabled) the token carries `"ent": null`; issuance is never gated on entitlements. See ADR-0017.
- [x] Q5. Deployment target — **answered 2026-11-10:** same Mac self-hosted runner as stoktok, Docker Compose, Cloudflare Tunnel; Postgres runs as a service in the same compose stack on that Mac. Hostname `api.mmlabs.online` (renamed from `api-admin.mmlabs.online` on 2026-11-11).
- [ ] Q6. Compliance needs (audit retention period, PII handling)?
- [x] Q7. Sign-up — **answered 2026-11-11 (D2):** open self-registration with Turnstile + per-IP limit; verified email required to join an app.
- [x] Q8. Email provider — **answered 2026-11-11 (D3):** Gmail SMTP (dedicated account, App Password, `smtp.gmail.com:587`) behind an `EmailSender` interface. Sender address: the dedicated account (exact address to be set in env).
- [ ] OQ1. Organizations platform-wide (shared across apps) or per app? Working assumption: platform-wide + `apps.org_mode`; adding `organizations.app_key` later must be purely additive.
- [x] OQ2 (was Q9). No migration from Supabase Auth; working assumption: existing users re-register. **Confirmed 2026-11-12** for ricotdin too: no existing ricotdin users are migrated; everyone starts fresh.
- [ ] OQ3 (was Q10). Is 2FA mandatory for staff (admin app)?

Admin UI `admin-pan` (added 2026-11-16, §5.9–§5.11):
- [x] OQ4. Permission for dashboard stats and `member_count`: a new admin permission (e.g. `admin:stats:read`, non-`is_admin`), an existing one (`admin:apps:read` for `member_count`, `admin:users:read` for stats), or `admin:*`? The sign-in counts come from the audit log, which today needs `admin:*`. — **answered (owner, 2026-11-17):** new permission `admin:stats:read` for the dashboard stats document (counts only, no audit rows exposed); `member_count` uses the existing `admin:apps:read`.
- [x] OQ5. Definition of an app "member": distinct users with any non-expired role assignment on the app (app scope or any org) — proposed. Do disabled users and unverified users count? Does the `admin` app show a member count (= staff)? — **answered (owner, 2026-11-17):** a member = distinct user with at least one non-expired role assignment on the app (app scope or any org); only users with status `active` count (unverified email still counts); the `admin` app shows its count (= staff).
- [x] OQ6. Sign-ups per month: how many months back (e.g. last 12), and which time zone sets month boundaries (UTC or `Asia/Ho_Chi_Minh`)? Should "sign-ins last 24 h" be split per app (the audit row carries `app_key`)? — **answered (owner, 2026-11-17):** sign-ups per month: last 12 months, month boundaries in `Asia/Ho_Chi_Minh`; sign-ins last 24 h are totals (succeeded / failed), not split per app.
- [x] OQ7. Staff session management: may a staff user list/revoke their own sessions through the admin API (other admin endpoints forbid self-changes)? Any extra guard for users holding admin roles, beyond `admin:*`? — **answered (owner, 2026-11-17):** no, self is 403; listing/revoking sessions of a user who holds any admin role requires `admin:*` (list and both revokes need `admin:*` anyway).
- [x] OQ8. Is the ~15 min tail of already-issued access tokens after a staff revoke acceptable, including `aud=admin` tokens? (Making revoke immediate would need a session check per admin request or a token deny-list — new scope.) — **answered (owner, 2026-11-17):** accepted with 12 h tokens for every audience, `admin` included (USR-13, ADR-0005 §6 amendment) (admin gap: ADR-0005 amendment risk 4).
- [ ] OQ9. Reports — the problem: who needs these reports, for which decision or recurring task, and why the dashboard (§5.9) and the existing list/audit endpoints are not enough?
- [ ] OQ10. Reports — which of `user_signups`, `active_users`, `app_memberships`, `org_overview`, `audit_activity` are in v1, and the exact columns and filters (date range, app, org) of each?
- [ ] OQ11. Reports — "define": are report definitions saved objects (name, type, parameters, owner) that others can see and rerun, or is v1 just "pick a type + parameters → download"?
- [ ] OQ12. Reports — `active_users`: what counts as active (signed in, or used a session / got a token) and over which period? Account sessions or audit rows (which disappear after the ≥ 90-day retention)?
- [ ] OQ13. Reports — formats: is `csv` enough for v1, or is `xlsx` required (adds a library)?
- [ ] OQ14. Reports — schedules `daily`/`weekly`/`monthly`: needed in v1? If yes, where do results go (stored for download in admin-pan, or emailed — to whom, within the Gmail SMTP limits), and is a scheduler process (new runtime piece) acceptable?
- [ ] OQ15. Reports — run synchronously (download in the response) or as a background job with status and a download link? Expected max size (rows)?
- [ ] OQ16. Reports — storage and retention of generated files (DB, volume, none — regenerate on download), and how long they are kept.
- [ ] OQ17. Reports — PII: may exports contain full emails, IPs or user agents? Who may run and download each type (`admin:*` only, as for the audit log?), and is each run/download audited?
- [ ] OQ18. Reports — time zone and date boundaries for date-based reports (same answer as OQ6?).

Answers go here with date, and into the relevant ticket's "Notes / answers from owner".
