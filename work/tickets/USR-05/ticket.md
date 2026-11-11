# USR-05: Apps self-register their permission manifest

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P2 |
| Areas | api, auth, security |
| Attempts | 0 |
| Created | 2026-11-11 |
| PR | — |

## Goal
Each mmlabs app declares its own permissions (with `is_admin` flags) and default role through a manifest, so adding or changing an app's permissions is a deploy of that app, not a change here.

## Scope
App credential (type decided in the plan: API key vs client credentials); `PUT /api/v1/apps/{key}/manifest`; idempotent sync; default-role rebuild per D9; `mmlabs-auth` / docs support for pushing the manifest at deploy time.

## Spec reference
§5.3; ADR-0001 §6; identity-architecture.md §4; decision D9

## Acceptance criteria
- [ ] App credential issued/rotated by staff; stored hashed; only accepted for its own app
- [ ] Manifest = permissions (key, description, is_admin) + optional extra roles; only the app's `<app>:` namespace accepted (422 otherwise)
- [ ] Idempotent push; new permissions added; `<app>:member` rebuilt as "all non-`is_admin`" unless the app overrides it; removed permissions reported (not silently dropped from roles that hold them)
- [ ] Role catalog `ETag` changes when the manifest changes
- [ ] `docs/auth-integration.md`: manifest format and pushing it at deploy time
- [ ] Tests: namespace enforcement, idempotency, is_admin excluded from member role, ETag change, wrong app credential → 403

## Dependencies
USR-02, USR-03b.

## Notes / answers from owner
- 2026-11-11: until this lands, app permissions are seeded by staff through USR-02 endpoints.
