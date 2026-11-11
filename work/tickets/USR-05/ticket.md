# USR-05: App catalog registration and cached role catalog for apps

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P2 |
| Areas | api, auth, security |
| Attempts | 0 |
| Created | 2026-11-11 |
| PR | — |

## Goal
Each mmlabs app declares its own permissions and default roles, and can fetch its role → permission catalog to check tokens locally.

## Spec reference
§5.3 Authorization; ADR-0001 §5–§6

## Acceptance criteria
- [ ] An app pushes its catalog (permissions + default roles) idempotently with an app credential; only its own `<app>:` namespace is accepted; removed permissions are reported, not silently deleted from assigned roles
- [ ] `GET /api/v1/apps/{key}/roles` returns role → permissions with an `ETag`; `If-None-Match` → 304
- [ ] `docs/auth-integration.md` gains a section: register the app, push the catalog, verify tokens (`aud`), map roles to permissions, cache with ETag
- [ ] Tests: namespace enforcement, idempotent re-push, ETag/304, unknown app 404

## Notes / answers from owner
- Depends on USR-02 and USR-03. App credential type (API key vs client credentials) decided in the plan.
