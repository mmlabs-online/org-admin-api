# org-admin-api — Product spec v1

Status: **Draft** — written while bootstrapping the repo (2026-11-10). Everything below is a proposal for the owner to confirm, cut or change, especially §8 Open questions.
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
> 2026-11-11 (D10): organizations are to be owned by the Identity module and used per app (`apps.org_mode`); reconciling this existing Admin table with that design is open — see USR-06.

### §5.2 Staff authentication
Only authenticated mmlabs staff can call `/api/v1/*`. Health endpoints stay public. Approach (SSO/OIDC vs. other) is an open question (§8 Q2).

### §5.3 Authorization — multi-app RBAC (owner, 2026-11-11; [ADR-0001](adr/0001-multi-app-authorization.md))
One model for every mmlabs app, the admin API included (app `admin`). Apps are registered; each app declares namespaced permissions (`<app>:<resource>:<action>`) and its own roles; users get roles per app, optionally limited to one organization. Every admin `/api/v1` endpoint declares the `admin:*` permission it needs. A bootstrap super-admin (`admin:*`) exists. Business rules on an app's own data stay in that app.

Membership (2026-11-11, D8–D10; details in [`identity-architecture.md`](identity-architecture.md)): each app has a `join_policy` (`open` | `approval` | `closed`; default `open`, `admin` = `closed`), a default role `<app>:member` holding all its permissions except those flagged `is_admin`, an `org_mode` (`none` | `required`; calechip = `required`) and its allowed browser `origins`. Joining assigns the default role at app scope (`org_id NULL`). Policy and role contents are data.

### §5.4 Audit log
Append-only record of every mutating admin action: actor, action, target type/id, before/after (or diff), timestamp, request id. Read-only, filterable listing for auditors.

### §5.5 Products registry — merged into the apps registry of §5.3 (2026-11-11)
The list of mmlabs products is the `apps` table of §5.3 (key, name, status).

### §5.6 Org product entitlements
Which org can use which product, with plan and limits; enable/disable per org. Readable by mmlabs services (service-to-service auth, §8 Q4).

### §5.8 mmlabs Identity — ecosystem users and authentication (owner, 2026-11-11)
One account per person across all mmlabs apps, **fully self-hosted here** (D1): email + password only, no external IdP, no social login, not an OIDC provider. Architecture, data model, API and decision log D1–D12: [`identity-architecture.md`](identity-architecture.md).

- **Users:** email (unique, case-insensitive), password, status (`active` / `disabled`), email verified flag, timestamps. Staff are users too; what they may do in the admin API is RBAC (§5.3).
- **Sign-up:** open self-registration, protected by Cloudflare Turnstile and a per-IP rate limit (D2). Unverified users may log in but must verify their email before joining any app or getting an app token.
- **Passwords:** hashed with Argon2id; never logged or returned; minimum length and a breached/common-password check per the plan.
- **Login and sessions:** each app has its own login page and uses `@mmlabs/auth-client`, which posts credentials straight to this API (D4). A browser holds one device session (cookie `mm_device`, rotated with a 60 s grace) with any number of signed-in accounts; each app remembers its own chosen account, so signing into another account in one app does not change another app (D5, D6). Logout per account or for the whole device.
- **Per-app access tokens:** an app asks for its own short-lived JWT for a chosen account (and org, if any), carrying only that app's roles (D7). Not a member → `not_member` with `join_available`; joining follows the app's `join_policy` (D8–D9).
- **Verification by apps:** public keys at `/.well-known/jwks.json`; apps verify signature, `iss`, `aud` and expiry locally. Claims: `sub`, `email`, `email_verified`, `aud`, `roles`, `org` (if scoped), `iat`, `exp`. Backends use `mmlabs-auth` (Python) (D4).
- **Abuse protection:** no hard lockout; per-email exponential backoff after 5 failures (cap 15 min), per-IP limit, Turnstile after 3 failures; the same error for unknown email and wrong password (D11). CORS only for registered app origins; cookie-authenticated POSTs check Origin and `X-MM-App` (D12).
- **Email flows:** email verification and password reset by one-time, expiring links, sent through Gmail SMTP behind an `EmailSender` interface; resend at most 1/min and 5/day per email (D3).
- **Organizations:** optional per app (`org_mode`); owned by this Identity module (D10, USR-06).
- **Admin:** staff can list, look up, disable/enable users, manage their roles per app and revoke their sessions (audited, §5.4).

### §5.7 Later (not v1)
Org members view, per-org feature flags, announcements, impersonation (heavily audited), webhooks/events to products.

## §6 Non-functional

- p95 latency < 200 ms for reads at expected admin load.
- 100% of mutating endpoints write an audit entry (once §5.4 lands).
- No secrets in code or logs; config via env only.

### §6.1 Deployment
- Public hostname: **`api.mmlabs.online`** (owner, 2026-11-11; was `api-admin.mmlabs.online` from 2026-11-10).
- Release/deploy follows the mmlabs deployment repo: branch `release/vX.Y.Z` from main (owner, 2026-11-10; the workflow creates the tag) → image on Docker Hub → PR on `mmlabs-online/deployment` → owner merges → deploy on the Mac self-hosted runner, exposed through Cloudflare Tunnel. Database: the shared mmlabs Postgres 17 (compose on the Mac runner), database `org_admin`, persistent `pgdata` volume with nightly and offsite backups (owner, 2026-11-10). Runbook: `docs/deploy.md`.
- No Cloudflare Access gate is required before staff authentication (§5.2) ships (owner, 2026-11-10). Accepted risk: until ADM-002 lands, anyone who can reach the hostname can call the admin endpoints.

## §7 Success metrics

- All admin actions for in-scope products go through this API (no direct DB edits).

## §8 Open questions (owner)

- [ ] Q1. Which mmlabs products are in scope first?
- [x] Q2. Staff login — **answered 2026-11-11:** email + password managed by this service (§5.8); staff are §5.8 users with RBAC roles. 2FA for staff: see OQ3.
- [ ] Q3. Is this API the **source of truth** for orgs/users, or does it aggregate data owned by each product?
- [ ] Q4. How do products consume admin decisions — pull (API with service keys), push (webhooks/events), or both?
- [x] Q5. Deployment target — **answered 2026-11-10:** same Mac self-hosted runner as stoktok, Docker Compose, Cloudflare Tunnel; Postgres runs as a service in the same compose stack on that Mac. Hostname `api.mmlabs.online` (renamed from `api-admin.mmlabs.online` on 2026-11-11).
- [ ] Q6. Compliance needs (audit retention period, PII handling)?
- [x] Q7. Sign-up — **answered 2026-11-11 (D2):** open self-registration with Turnstile + per-IP limit; verified email required to join an app.
- [x] Q8. Email provider — **answered 2026-11-11 (D3):** Gmail SMTP (dedicated account, App Password, `smtp.gmail.com:587`) behind an `EmailSender` interface. Sender address: the dedicated account (exact address to be set in env).
- [ ] OQ1. Organizations platform-wide (shared across apps) or per app? Working assumption: platform-wide + `apps.org_mode`; adding `organizations.app_key` later must be purely additive.
- [ ] OQ2 (was Q9). No migration from Supabase Auth; working assumption: existing users re-register.
- [ ] OQ3 (was Q10). Is 2FA mandatory for staff (admin app)?

Answers go here with date, and into the relevant ticket's "Notes / answers from owner".
