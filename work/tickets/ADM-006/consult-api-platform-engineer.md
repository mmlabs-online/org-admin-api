ADM-006 consult (api-platform-engineer, read-only). Nothing edited.

FINDINGS (current code)
- Org router: /api/v1/organizations, `READ`/`WRITE` = admin:organizations:read/write via `require_permission`, router-level `current_admin`. Service raises NotFoundError/ConflictError, writes audit in the same transaction (R4). It uses `diff.snapshot` with field tuples (ORG, APP in app/modules/audit/diff.py) and skips the audit row when nothing changed. Org status: active|suspended. App status: active|disabled.
- Apps are addressed by immutable `key` under /apps/{key}/… (R9/D18). Existing sub-collections: /apps/{key}/credentials (POST 201, GET Page, DELETE 204) and /apps/{key}/join-requests.
- Admin permissions: seeded by data-only migrations. 5e2a9c7d1f04 seeds organizations/users/apps/roles; c3f1a9d27b60 seeds admin:audit:read. All is_admin=true and are not granted to any role (only admin:*). There is no admin:entitlements:* yet.
- tests/test_route_auth_classes.py is deny-by-default. Every new route must be added to the `BEARER` map (exact method+path -> permission), or `test_bearer_route_permission_map`, `test_every_route_is_classified` and the 401 sweeps fail. The `_fill()` helper only substitutes the placeholders it knows: `{key}`, `org_id`, `user_id`, `assignment_id`, `permission_id`, `role_id`, `credential_id`, `request_id`. A new path param name such as `app_key` would raise KeyError. Reuse `{key}` or add the name to `_fill`. The APP_CREDENTIAL allow-list is asserted exact (only PUT /apps/{key}/manifest). A new class-4 route needs an ADR-0016 amendment and a test change.
- Audit catalogue (app/modules/audit/actions.py) has no entitlement action or target. Add AuditTarget.ENTITLEMENT and AuditAction ENTITLEMENT_GRANTED / _UPDATED / _REVOKED, and a diff field tuple (`app_key`, `plan`, `limits`, `enabled`). Keep org_id on the audit row.
- Error codes (app/core/errors.py): not_found, conflict, unprocessable, etc. Existing 409s carry `details=[{"field","reason"}]`.
- §5.6 says only "readable by mmlabs services (service-to-service auth, §8 Q4)". Q4 (pull vs push) is open, and so is Q3 (source of truth).

RECOMMENDATIONS

1. Paths (org-scoped, apps by key in the path):
   - GET    /api/v1/organizations/{org_id}/entitlements -> Page[EntitlementRead] (limit<=100, offset). Optional filter `enabled=true|false`. Order by app key, stable.
   - GET    /api/v1/organizations/{org_id}/entitlements/{key} -> EntitlementRead. (Use `{key}` as the param name for the app, so `_fill` already works.)
   - POST   /api/v1/organizations/{org_id}/entitlements -> 201, body {app_key, plan, limits?, enabled?=true}.
   - PATCH  /api/v1/organizations/{org_id}/entitlements/{key} -> 200 (partial: plan, limits, enabled).
   - DELETE /api/v1/organizations/{org_id}/entitlements/{key} -> 204 (revoke).
   This is POST + PATCH, not PUT. Reasons: it matches R9 ("PATCH for partial updates", 201 on create), the organizations module and the 409-on-duplicate acceptance criterion. PUT-upsert would make "duplicate 409" impossible and hide accidental overwrites in the audit trail. A "revoke" is DELETE (row removed); a temporary "disable" is PATCH enabled=false. Say this distinction in the docs. Keep both: the criteria say enable/disable and revoke.
   Alternative considered: POST taking app_key in the body vs PUT /entitlements/{key}. The POST-with-body shape is the safer fit. Do not use `/apps/{key}/entitlements` as the primary write path (the org is the resource owner). A reverse view "which orgs have app X" is out of scope; list it as a follow-up.

2. Status codes:
   - 201 POST. 200 PATCH and GET. 204 DELETE.
   - 404 unknown org, unknown app key, or (for GET/PATCH/DELETE) no entitlement for that pair. Use distinct messages but the same `not_found` code. In POST, unknown `app_key` in the body: the ticket says 404; a body reference is arguably 422, but the criteria say "unknown org/product 404", so follow the ticket and return 404 `not_found` with details[0].field="app_key". Flag this in the plan.
   - 409 duplicate (org, app). Catch IntegrityError on the unique constraint as well as the pre-check (same pattern as OrganizationService.create). details reason e.g. `entitlement_exists`.
   - 409 for granting on the `admin` app or a disabled app (mirror issue_credential's 409 with details[0].reason `app_not_grantable`). Decide: `admin` is a staff app and not a product, so reject with 409.
   - 422 for validation (Pydantic). Empty PATCH body is a no-op 200 with no audit row (consistent with the org update).
   - 401/403 per the BEARER sweep.
   Suspended org: recommend that staff can still grant/update/revoke/list on a suspended org (support must be able to fix things), and the read endpoint for services reports it with an org-level flag (see 4). Do not silently hide entitlements. Say this explicitly in plan.md (criterion 4). Disabled app: entitlement row stays, reads show `app_status`; granting new on a disabled app -> 409.

3. Schema/limits:
   - EntitlementRead: id (uuid), org_id, app_key, plan (str), limits (object), enabled (bool), created_at, updated_at (timestamptz). Keep field names snake_case and ISO-8601, same as the rest of v1.
   - `plan`: free-form slug string, 1..64, pattern like ^[a-z0-9][a-z0-9_-]*$. No plan catalogue in v1 (billing is a non-goal, R10; plans are labels, not prices). Do not enum it: an enum is hard to extend and a tightening is breaking.
   - `limits`: JSON object (jsonb, NOT NULL, default {}). Without a per-app limits schema, do generic validation: keys matching ^[a-z][a-z0-9_]{0,63}$, values int>=0, or bool, or str? Recommend v1 = values are non-negative integers or null (null = unlimited) plus booleans disallowed; max 32 keys; total serialized size cap (e.g. 4 KiB); reject floats and nested objects with 422. State that apps own the meaning of keys; per-app validation against the manifest is a later ticket. Keep it permissive enough to avoid later tightening (tightening is breaking, loosening is additive).
   - PATCH `limits` semantics: replace whole object (not merge). Document it; merge semantics are ambiguous with null meaning unlimited. Explicit null for `plan`/`enabled` = no change (as in org update) or 422; pick one and test it.
   - DB: UNIQUE(org_id, app_id), FKs with ON DELETE CASCADE for org (revoking is moot when the org goes), and RESTRICT for app unless apps delete already cleans up. Check what DELETE /apps/{key} does today and whether it should 409 on existing entitlements or cascade. CHECK on plan length. Hand-written migration with downgrade (R2), model imported in app/db/models.py.
   - Note org delete already 409s on role assignments only; decide whether entitlements block org deletion (recommend cascade-delete, with the audit row on the org-delete carrying the count, or 409 like assignments. Plan must pick one).

4. Admin permissions:
   - Recommend new `admin:entitlements:read` and `admin:entitlements:write` (data-only seed migration like 5e2a9c7d1f04, is_admin=true, not granted to roles; downgrade deletes them). Reason: the ticket persona is "Ops / product owner" (spec §4), distinct from whoever manages org records. Reusing admin:organizations:write would let anyone who can rename an org grant a product, and it would block later separation without a breaking permission change. Entitlements are a different resource and the existing key set already follows one namespace per resource (users, apps, roles, audit).
   - Fallback if the owner wants minimal: reuse organizations:read/write. Not recommended. Adding the new keys is additive and cheap.
   - Update BEARER map in tests/test_route_auth_classes.py with all five routes. Update docs/identity-architecture.md permission catalogue if it lists them.

5. "Service read" endpoint (§8 Q4 open). Recommend: defer the service-facing endpoint, and do not invent a new auth class in ADM-006.
   - Reason: R3 class 4 is deliberately limited to PUT /apps/{key}/manifest (ADR-0016), and the test asserts the allow-list is exact. A service read is a new auth surface (an app credential for org X's data, authorisation question: may app A read org's entitlement for app B? Only its own?). That needs an ADR amendment and owner decision on Q4 and Q3, which are unanswered. Also, spec §5.6 lists it as a goal only.
   - What to ship now to satisfy criterion 3 in a defensible way: the staff list GET (with `enabled=true` filter) covers "an org's active entitlements" for staff/admin UI. Mark the criterion as partially met and ask the owner to move the service read to a follow-up ticket (e.g. ADM-006b) once Q4 is answered. Do not mark the AC done by silently reusing admin permissions for services.
   - If the owner wants it now, the least-risk design is: GET /api/v1/apps/{key}/org-entitlements/{org_id} or GET /api/v1/organizations/{org_id}/entitlements/{key} ... authenticated with the app credential (class 4), only for its own app (another app -> 403), single document `{org_id, org_status, app_key, enabled, plan, limits}`, rate-limited per-IP, no CORS, no list across apps. Requires: ADR-0016 amendment, extend APP_CREDENTIAL set and test_app_credential_allow_list_is_exact, R9 exception entry. Response contains `org_status` so services enforce suspension. Return 404 for unknown org/entitlement and for a non-member org without distinguishing (no existence oracle, as in tokens/errors.py).
   - Push/webhooks: out of scope until Q4. Do not add speculative event tables.
   - Alternative, also staff-token only: services could fetch via a user token with an `org` claim. Not appropriate; tokens carry roles, not entitlements, and adding claims changes the JWT contract.

6. Audit and tests:
   - Each mutation: grant -> entitlement.granted, PATCH -> entitlement.updated (no row on no-op), DELETE -> entitlement.revoked, all with target=(ENTITLEMENT, id), org_id, and changes diff.
   - Tests (R8): grant 201 + audit row; duplicate 409 (including concurrent path); unknown org 404; unknown app 404 on POST body and on path; admin/disabled app 409; limits validation 422 (negative, float, nested, too many keys, bad key); PATCH replaces limits; DELETE 204 then GET 404; list pagination + `enabled` filter; suspended org behaviour as chosen; 401/403 via the route-class sweep; org delete behaviour with entitlements.

RISKS
- Adding paths under /organizations/{org_id}/... with `{key}` for an app is slightly inconsistent in naming vs `app_key` in the body. Pick `{key}` for path (matches R9 "addressed by key") and `app_key` in body/response; document it.
- `limits` shape is a long-lived contract: once apps read it, tightening it is breaking. Keep v1 loose but bounded; wide jsonb with no app-specific schema may let staff store garbage that apps misread.
- The permissions seed migration (is_admin=true) means only admin:* holders can use these until a role is given the permission; super-admin gets them via the wildcard. Confirm that is intended (same as existing keys).
- Deferring the service read leaves the spec persona "mmlabs services read entitlements" unmet; flag to the owner explicitly rather than closing the AC.
- App deletion / org deletion interplay (FK cascade vs 409) can silently destroy entitlement data if cascade is chosen; audit it.
- Granting on a suspended org or disabled app: behaviour must be documented, because services will read `enabled=true` rows of suspended orgs unless `org_status` is exposed.

OPEN QUESTIONS (for owner / planner)
1. Q4 (and Q3): pull with app credentials, push, or both? Until answered, approve deferring the service read (recommended), or authorise an ADR-0016 amendment for a class-4 read route?
2. New permissions admin:entitlements:read/write (recommended) or reuse admin:organizations:*?
3. Suspended org: may staff still grant/modify entitlements (recommended yes)? Do services see them (with org_status) or must they treat suspended as no access?
4. Do entitlements block org deletion (409) or cascade? Same for app deletion.
5. Should `plan` be a free-form slug (recommended) or validated against a per-app plan list in the manifest later?
6. `limits` value types: non-negative integers/null only (recommended), or also bools/strings?
7. Is an entitlement disabled by default on grant (enabled=false) or enabled=true? Recommend true, since a grant is meant to enable.
8. Unknown app_key in POST body: 404 (per ticket) or 422? Recommend 404 per the acceptance criteria.

Files read: /home/user/org-admin-api/work/tickets/ADM-006/ticket.md, /home/user/org-admin-api/app/modules/organizations/{router,service}.py, /home/user/org-admin-api/app/modules/app_credentials/router.py, /home/user/org-admin-api/app/modules/audit/actions.py, /home/user/org-admin-api/migrations/versions/20261115_5e2a9c7d1f04_seed_admin_permissions.py, /home/user/org-admin-api/tests/test_route_auth_classes.py, /home/user/org-admin-api/docs/spec-v1.md (§5.6, §8 Q3/Q4).
