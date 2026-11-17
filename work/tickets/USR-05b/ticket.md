# USR-05b: Manifest push endpoint (app credential auth)

| Field | Value |
|---|---|
| State | REVIEW |
| Priority | P2 |
| Areas | api, auth, security |
| Attempts | 1 |
| Created | 2026-11-17 |
| PR | — |

## Goal
Second slice of USR-05: apps push their permission manifest with their app credential (`PUT /api/v1/apps/{key}/manifest`), so adding or changing an app's permissions is a deploy of that app.

## Scope
The design is fixed in `work/tickets/USR-05/plan.md` ("Split proposal", slice 2) and must be followed: `current_app_credential` dependency (`Authorization: Bearer mmak_…`), `ManifestService.sync` reusing `_build_for_app`, `PermissionService._create`, `scopes.py`, `lock_app`; `APP_CREDENTIAL` route class; rate limit scope `app_manifest`; `docs/auth-integration.md`.

## Spec reference
§5.3; ADR-0001 §6; ADR-0016 (from USR-05a); ADR-0015; identity-architecture.md §4; decision D9

## Acceptance criteria
- [ ] App credential accepted only on the manifest route and only for its own app (wrong app → 403 `app_credential_mismatch`; missing/invalid/revoked → 401; disabled app → 403)
- [ ] Manifest = permissions (key, description, is_admin, scope) + optional extra roles + optional member override; only the `<app>:` namespace accepted (422 otherwise); `scope` required on `org_mode = required` apps (422)
- [ ] Idempotent push; new permissions added; `<app>:member` rebuilt via `_build_for_app` unless overridden; no `is_admin`/scope flips (409); removed permissions reported, not dropped from roles
- [ ] Role catalog `ETag` changes when catalog-visible content changes
- [ ] One audit row per changing push; none for a no-op
- [ ] `docs/auth-integration.md`: manifest format and pushing it at deploy time
- [ ] Tests: namespace, idempotency, is_admin excluded from member, pushing twice never adds `scope = org` to member, ETag, wrong app credential → 403, route-class tests

## Dependencies
USR-05 (slice 05a: app credentials) merged.

## Notes / answers from owner
- Created 2026-11-17 by the orchestrator from the USR-05 planner's split proposal. Owner decisions listed in USR-05 plan.md ("Needs owner's decision") apply.
- 2026-11-17 (orchestrator): consult step reuses the USR-05 consult notes (`work/tickets/USR-05/consult-*.md`), which already cover the manifest endpoint; the design is fixed in USR-05 plan.md (split proposal, slice 2).
