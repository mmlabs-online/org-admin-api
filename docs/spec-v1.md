# org-admin-api — Product spec v1

Status: **Draft** — written while bootstrapping the repo (2026-11-10). Everything below is a proposal for the owner to confirm, cut or change, especially §8 Open questions.
Source of truth for scope: if `CLAUDE.md`, a ticket and this spec disagree on scope, **this spec wins** — flag the conflict.

## §1 Problem

mmlabs runs several products/services. Admin tasks (managing customer orgs, users, which products an org can use, support actions) are spread across each product, with no single place, no shared permission model, and no audit trail of who changed what.

## §2 Goals

- One admin API that the mmlabs admin web UI (and internal tools) use for cross-product administration.
- A shared permission model for internal staff.
- A complete, queryable audit log of admin actions.

## §3 Non-goals (v1)

- End-user (customer) login/auth for the products themselves.
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

### §5.2 Staff authentication
Only authenticated mmlabs staff can call `/api/v1/*`. Health endpoints stay public. Approach (SSO/OIDC vs. other) is an open question (§8 Q2).

### §5.3 RBAC
Roles, permissions and role assignments for staff. Every `/api/v1` endpoint declares the permission it needs. A bootstrap super-admin exists.

### §5.4 Audit log
Append-only record of every mutating admin action: actor, action, target type/id, before/after (or diff), timestamp, request id. Read-only, filterable listing for auditors.

### §5.5 Products registry
List of mmlabs products/services (key, name, status, base URL).

### §5.6 Org product entitlements
Which org can use which product, with plan and limits; enable/disable per org. Readable by mmlabs services (service-to-service auth, §8 Q4).

### §5.7 Later (not v1)
Org members view, per-org feature flags, announcements, impersonation (heavily audited), webhooks/events to products.

## §6 Non-functional

- p95 latency < 200 ms for reads at expected admin load.
- 100% of mutating endpoints write an audit entry (once §5.4 lands).
- No secrets in code or logs; config via env only.

### §6.1 Deployment
- Public hostname: **`api-admin.mmlabs.online`** (owner, 2026-11-10).
- Release/deploy follows the mmlabs model used by stoktok: release branch → image on Docker Hub → PR on `mmlabs-online/deployment` → owner merges → deploy on the self-hosted runner, exposed through Cloudflare Tunnel (to confirm, §8 Q5).
- No Cloudflare Access gate is required before staff authentication (§5.2) ships (owner, 2026-11-10). Accepted risk: until ADM-002 lands, anyone who can reach the hostname can call the admin endpoints.

## §7 Success metrics

- All admin actions for in-scope products go through this API (no direct DB edits).

## §8 Open questions (owner)

- [ ] Q1. Which mmlabs products are in scope first?
- [ ] Q2. Staff login: Google Workspace SSO, the mmlabs "Authen" service, another IdP, or email/password + 2FA?
- [ ] Q3. Is this API the **source of truth** for orgs/users, or does it aggregate data owned by each product?
- [ ] Q4. How do products consume admin decisions — pull (API with service keys), push (webhooks/events), or both?
- [ ] Q5. Deployment target: same Mac self-hosted runner + Docker Compose + Cloudflare Tunnel as stoktok? Postgres in the compose stack on that host, or an existing/managed Postgres? (Hostname decided: `api-admin.mmlabs.online`, 2026-11-10.)
- [ ] Q6. Compliance needs (audit retention period, PII handling)?

Answers go here with date, and into the relevant ticket's "Notes / answers from owner".
