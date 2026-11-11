# ADR-0001: Multi-app authorization (app-scoped RBAC, per-app tokens)

Status: Accepted (owner, 2026-11-11)
Date: 2026-11-11

## Context
org-admin-api is also **mmlabs Identity** (spec §5.8): one user account for every mmlabs app (stoktok,
calechip, the admin UI, future apps). Each app needs its own permissions, and new apps must be added
without changing this service's code. The admin API itself needs staff permissions (spec §5.3).

## Decision
1. **Authentication and coarse authorization are central; business authorization stays in each app.**
   This service answers "who is this user" and "which roles/permissions do they have in app X".
   Ownership and data rules ("is this portfolio theirs") stay in the app's own database.
2. **Every app is a registered client**, including the admin API (`admin`). The `apps` registry replaces
   the separate "products registry" (former ADM-005).
3. **Permissions are namespaced per app:** `<app>:<resource>:<action>`, e.g. `stoktok:portfolio:read`,
   `admin:users:disable`. Roles belong to one app (`stoktok:member`). Wildcard `<app>:*` is allowed for
   that app's super-admin role. No deny rules in v1.
4. **Data model**
   ```
   apps              (id, key, name, status, created_at, updated_at)
   permissions       (id, app_id → apps, key, description)            unique (app_id, key)
   roles             (id, app_id → apps, key, name, is_system)        unique (app_id, key)
   role_permissions  (role_id → roles, permission_id → permissions)   pk (role_id, permission_id)
   role_assignments  (id, user_id → users, role_id → roles, org_id → organizations NULL,
                      granted_by → users NULL, expires_at NULL, created_at)
                      unique (user_id, role_id, org_id) — org_id NULL = app-wide
   ```
   A role and its permissions must belong to the same app. A user can use an app if they hold at least
   one non-expired role in it.
5. **Per-app access tokens.** Login gives a refresh token (identity). An app gets its own access token
   with `POST /api/v1/auth/token {"audience": "<app>"}`: `aud=<app>`, `sub`, `roles` (only that app's),
   `org` when scoped. Refused if the user has no role in that app. Apps verify locally via JWKS and map
   roles to permissions from a cached catalog (`GET /api/v1/apps/{key}/roles`, with ETag). A token for
   one app is useless at another (audience check).
6. **Apps declare their catalog** (permissions + default roles) through an API call or a manifest at
   deploy time. This service stores and assigns them; it does not interpret them.

## Consequences
- Adding an app is data, not code: register the app, push its catalog, assign roles.
- Tokens stay small. Role changes take effect at the next access-token refresh (≤ access-token TTL,
  ~15 min); immediate cut-off = revoke the user's refresh tokens (USR-03).
- Apps must verify `aud` and must not trust permissions from another app's token.
- The admin API is the first client: its endpoints require `admin:*` permissions (ADM-002).

## Alternatives considered
- **All permissions of all apps in one token:** large tokens, leaks one app's permissions to others,
  every catalog change bloats tokens. Rejected.
- **Central policy check on every request (`/authz/check`):** strong consistency, but a network call
  per request and a single point of failure for every app. Kept as a possible later addition for
  sensitive actions, not the default.
- **Global (non-namespaced) roles:** "admin" means different things per app; collisions. Rejected.

## Later, additive
Groups (assign roles to groups), ABAC conditions on assignments, client credentials for
service-to-service calls, full OIDC provider (currently a non-goal, R10).
