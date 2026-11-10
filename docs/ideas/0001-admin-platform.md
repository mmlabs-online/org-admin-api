# 0001 — mmlabs admin platform (foundation)

Status: Draft
Owner: _TBD_
Date: 2026-11-10

> Starting draft written while bootstrapping the repo. Everything here is a proposal for the owner to confirm, cut or change — especially the **Open questions**.

## Problem

mmlabs runs several products/services. Admin tasks (managing customer orgs, users, which products an org can use, support actions) are today spread across each product, with no single place, no shared permission model, and no audit trail of who changed what.

## Goals

- One admin API that the mmlabs admin web UI (and internal tools) use for cross-product administration.
- A shared permission model for internal staff (who can see/do what).
- A complete, queryable audit log of admin actions.

## Non-goals (for now)

- End-user (customer) login/auth for the products themselves.
- Billing/payments.
- Replacing each product's own internal data model.

## Users & use cases

| Actor | Wants to… | So that… |
|---|---|---|
| Super admin | manage staff accounts and their roles | access follows least privilege |
| Support staff | look up an org and its users, suspend/reactivate | they can resolve tickets quickly |
| Ops / product owner | enable/disable a product for an org, set plan/limits | customers get the right entitlements |
| mmlabs services | read an org's status and entitlements | products enforce what admin decided |
| Auditor | see who did what and when | changes are accountable |

## Proposed modules (MVP → later)

1. **Organizations** — CRUD, status (active/suspended). _(reference module already in code)_
2. **Admin auth** — staff login (SSO via Google Workspace/OIDC is the likely fit), JWT/session, service API keys.
3. **RBAC** — roles, permissions, role assignments for staff.
4. **Audit log** — append-only record of every mutating admin action.
5. **Products registry & entitlements** — list of mmlabs products; which org has which product, plan, limits.
6. **Org members** — users belonging to an org (or a read-through view into each product).
7. _Later:_ feature flags per org, announcements, impersonation (with heavy audit), webhooks to products.

## Success metrics

- All admin actions for MVP products go through this API (no direct DB edits).
- 100% of mutating endpoints write an audit entry.
- p95 latency < 200 ms for read endpoints.

## Risks & assumptions

- Assumes products can call this API (or consume events) to read entitlements.
- Source of truth for users is unclear (see questions) — biggest design risk.

## Open questions

- [ ] Which mmlabs products are in scope first?
- [ ] Staff login: Google Workspace SSO, another IdP, or email/password + 2FA?
- [ ] Is this API the **source of truth** for orgs/users, or does it aggregate data owned by each product?
- [ ] How do products consume admin decisions — pull (API), push (webhooks/events), or both?
- [ ] Deployment target (cloud, k8s, VPS) and environments (staging/prod)?
- [ ] Any compliance requirements (data retention for audit logs, PII handling)?

**Next step:** owner answers the questions above → `/design docs/ideas/0001-admin-platform.md` (likely split into designs per module: auth+RBAC first, then audit log, then entitlements).
