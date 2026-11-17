Status: READY

# Plan — ADM-006: Org product entitlements

## Spec summary
- §5.6: "Which org can use which product, with plan and limits; enable/disable per org. Readable by mmlabs services (service-to-service auth, §8 Q4)."
- §5.5: "The list of mmlabs products is the `apps` table of §5.3 (key, name, status)." So "product" means a row in `apps`.
- §4 actors: "Ops / product owner | enable/disable a product for an org, set plan/limits" and "mmlabs services | read an org's status and entitlements".
- §3 non-goals: "Billing / payments." A `plan` is only a label. It has no price and no catalogue.
- §8 Q4 (still open): "How do products consume admin decisions — pull (API with service keys), push (webhooks/events), or both?"
- Ticket, owner note 2026-11-17: "v1 ships staff (Bearer) endpoints only. The service read path (spec §8 Q4 …) is deferred to a follow-up ticket; the AC 'read endpoint suitable for services' is met in v1 by a staff read of an org's active entitlements."

So this ticket adds only R3 class 1 routes (Bearer `aud=admin` plus `require_permission`). It adds no class 4 route, no token claim and no push.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Entitlements table and migration (org, app, plan, limits, enabled), unique per (org, app); apps come from the USR-02 registry | `tests/test_entitlements_migration.py › test_downgrade_drops_table_and_permissions`, `› test_constraints` (unique, plan CHECK, limits object CHECK, FK RESTRICT on org and app). `make check` runs `alembic check`, which must report no drift. `app_id` is an FK to `apps.id`. |
| 2 | Endpoints to grant, update, revoke and list an org's entitlements | `tests/test_entitlements_api.py`: `test_grant_returns_201_and_audit`, `test_update_plan_limits_enabled`, `test_patch_replaces_limits`, `test_noop_patch_no_audit`, `test_revoke_204_then_gone`, `test_list_paginates_ordered_by_app_key`. Also the BEARER map in `tests/test_route_auth_classes.py`. |
| 3 | Read endpoint suitable for services: an org's active entitlements (owner: met by a staff read) | `GET /api/v1/organizations/{org_id}/entitlements?active=true`. Tests `test_active_filter_definition` (enabled AND org active AND app active) and `test_active_field_matches_filter`. Every item also carries a computed `active` field. |
| 4 | Tests: grant/revoke, duplicate 409, unknown org/product 404, suspended org behaviour as defined in the plan | `test_duplicate_grant_409`, `test_unknown_org_404` (all 4 routes), `test_unknown_app_key_404`, `test_unknown_entitlement_404` (also another org's id), `test_suspended_org_behaviour` (grant/update/revoke still allowed, the list shows rows with `active=false`, `?active=true` returns an empty page, reactivating brings them back). Plus `test_admin_app_409`, `test_disabled_app_409`, `test_org_mode_none_409`, `test_limits_validation_422`, `test_plan_validation_422`, and the delete guards `test_org_delete_with_entitlements_409` and `test_app_delete_with_entitlements_409`. Auth: 401/403 sweeps in `tests/test_route_auth_classes.py`, plus `test_read_permission_cannot_write` (403). |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/entitlements/__init__.py` | create (empty) | new domain module (reference-module shape) |
| `app/modules/entitlements/models.py` | create `OrgEntitlement` | AC1 table |
| `app/modules/entitlements/schemas.py` | create `EntitlementCreate`, `EntitlementUpdate`, `EntitlementRead`, `PLAN_PATTERN`, `LIMIT_KEY_PATTERN` | API contract, input validation |
| `app/modules/entitlements/repository.py` | create; holds the single `ACTIVE` SQL expression | R1 ORM only here; one "active" definition |
| `app/modules/entitlements/service.py` | create `EntitlementService` (list, grant, update, revoke, plus audit) | R1 business rules, R4 audit |
| `app/modules/entitlements/router.py` | create 4 Bearer routes | AC2/AC3, R3 class 1 |
| `app/api/router.py` | include `entitlements_router` | mount the routes |
| `app/db/models.py` | import and export `OrgEntitlement` | R2 (Alembic metadata) |
| `app/modules/audit/actions.py` | add `AuditTarget.ENTITLEMENT`, `AuditAction.ENTITLEMENT_GRANTED/UPDATED/REVOKED` | R4 |
| `app/modules/audit/diff.py` | add `ENTITLEMENT = ("app_id", "plan", "limits", "enabled")` | R4 allowlisted diff |
| `app/core/errors.py` | add `OrgHasEntitlementsError(ConflictError)`, code `org_has_entitlements` | org-delete guard (RESTRICT FK) |
| `app/modules/organizations/service.py` | `delete`: after the assignments check, 409 `org_has_entitlements` when rows exist | RESTRICT FK, so the delete must answer a clear 409 and not raise an IntegrityError |
| `app/modules/organizations/repository.py` | add `has_entitlements(org_id)` (an `exists` query) | same |
| `app/modules/rbac/service.py` | `AppService.delete`: after the assignments check, `ConflictError("App has entitlements; disable it instead", details=[{"field":"key","reason":"app_has_entitlements"}])` | RESTRICT FK on `app_id` |
| `app/modules/rbac/repository.py` | add `app_has_entitlements(app_id)` | same |
| `migrations/versions/20261117_<rev>_adm006_org_entitlements.py` | create: table, index, permission seed, downgrade | R2 |
| `tests/test_entitlements_api.py` | create | R8 |
| `tests/test_entitlements_migration.py` | create | R2 constraints and downgrade |
| `tests/test_route_auth_classes.py` | add 4 BEARER entries; add `"entitlement_id"` to `_fill` | R3 deny-by-default map |
| `tests/test_audit_coverage.py` | add 3 `AUDITED` entries and their scenarios; `len(AUDITED) == 23` | R4 coverage |
| `tests/test_rbac_schema.py` | add the 2 keys to `ADMIN_LEAF_PERMISSIONS` | seeded permission catalogue |
| `docs/spec-v1.md` | §5.6: add a dated note (see Approach step 9) | record scope decision |
| `docs/identity-architecture.md` | §4 API table: one "Admin: `/api/v1/organizations/{org_id}/entitlements`" row; §3 data model: one `org_entitlements` line | permission and API catalogue |
| `docs/deploy.md` | lines 435–436: add `'admin:entitlements:read','admin:entitlements:write'` to the expected admin-permission list | the runbook query must not flag seeded keys |
| `work/tickets/ADM-006/impl-log.md` | implementer log | workflow |

Prod code estimate: about 420–480 lines including the migration (models 45, schemas 55, repository 90, service 140, router 75, guards/errors/audit 30, migration 90). This is under the split threshold, so the plan has no split.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_entitlements_api.py › test_grant_returns_201_and_audit` | POST 201; body has `app_key`, `plan`, `limits`, `enabled=true`, `active=true`; one `entitlement.granted` row with target `("entitlement", id)`, `org_id`, `app_key`, and `changes` holding plan/limits/enabled/app_id |
| `› test_grant_defaults` | `limits` omitted gives `{}`; `enabled` omitted gives `true` |
| `› test_duplicate_grant_409` | second POST for the same (org, app) gives 409 `conflict`, details reason `entitlement_exists`, no audit row |
| `› test_duplicate_grant_integrity_409` | pre-check bypassed (monkeypatch the repo lookup to return None) gives IntegrityError, mapped to 409 `entitlement_exists` |
| `› test_unknown_org_404` | GET/POST/PATCH/DELETE with a random `org_id` give 404 `not_found` |
| `› test_unknown_app_key_404` | POST with an unregistered (well-formed) `app_key` gives 404, details `[{"field":"app_key","reason":"unknown_app"}]` |
| `› test_unknown_entitlement_404` | PATCH/DELETE with a random id give 404; an id belonging to **another org** also gives 404 |
| `› test_admin_app_409` | `app_key="admin"` gives 409 reason `admin_app` |
| `› test_disabled_app_409` | disabled app gives 409 reason `app_disabled` |
| `› test_org_mode_none_409` | `org_mode=none` app gives 409 reason `org_mode_none` |
| `› test_plan_validation_422` | empty, uppercase, too long (64 chars), leading `-` give 422 `validation_error` |
| `› test_limits_validation_422` | negative, float (`1.5`), bool, string, null, nested object, bad key (`Seats`, `1x`), 33 keys, value > 2^53−1 give 422 |
| `› test_unknown_field_422` | `extra="forbid"` on create and update (e.g. `{"limit": {}}`) gives 422 |
| `› test_update_plan_limits_enabled` | PATCH 200; `entitlement.updated` row with only the changed fields in `changes` |
| `› test_patch_replaces_limits` | `{"a":1,"b":2}` patched with `{"b":3}` gives `{"b":3}` (replace, not merge) |
| `› test_patch_null_is_no_change` | `{"plan": null}` is 200, nothing changes, no audit row |
| `› test_noop_patch_no_audit` | PATCH with the same values gives 200, no audit row, `updated_at` unchanged |
| `› test_patch_on_now_disabled_app_allowed` | app disabled after the grant: PATCH and DELETE still work (only grant is refused) |
| `› test_revoke_204_then_gone` | DELETE 204; `entitlement.revoked` row whose `changes` hold the before snapshot; the list no longer contains it; a re-grant gives 201 |
| `› test_list_paginates_ordered_by_app_key` | `Page` shape, `limit`/`offset`, `total`, order by app key then id; only this org's rows |
| `› test_active_filter_definition` | four rows: enabled+active app; `enabled=false`; disabled app; and the same for a suspended org. `?active=true` returns only the first; `?active=false` returns the rest; no param returns all |
| `› test_active_field_matches_filter` | every item's `active` equals membership in `?active=true` |
| `› test_suspended_org_behaviour` | suspended org: grant 201, PATCH 200, DELETE 204, the list shows rows with `active=false`, `?active=true` returns `total=0`; reactivating (PATCH org `status=active`) makes them `active=true` |
| `› test_read_permission_cannot_write` | a `make_client(admin_permissions=["admin:entitlements:read"])` client can GET; POST/PATCH/DELETE give 403 `permission_denied`, `details.required == "admin:entitlements:write"` |
| `› test_org_delete_with_entitlements_409` | DELETE org with an entitlement (no assignments) gives 409 `org_has_entitlements`; after revoke, org delete gives 204 |
| `› test_app_delete_with_entitlements_409` | DELETE app with an entitlement gives 409 `conflict`, reason `app_has_entitlements` |
| `tests/test_entitlements_migration.py › test_downgrade_drops_table_and_permissions` | downgrade to `a9d4c27e6b13` removes `org_entitlements` and the 2 permissions; upgrade restores them (pattern of `test_join_requests_migration.py`) |
| `› test_constraints` | raw SQL: duplicate (org, app) fails; plan CHECK fails; `limits='[]'::jsonb` fails `ck_org_entitlements_limits_object`; server default `limits='{}'`, `enabled=true`; deleting a referenced org or app raises IntegrityError (RESTRICT) |
| `tests/test_route_auth_classes.py` (existing tests, map updated) | all 4 routes classified; permissions match `x-permission`; 401 without token / cookie only / app credential |
| `tests/test_audit_coverage.py` (existing tests, map updated) | success writes exactly one row; failure (404) writes none; no secret or email in rows |
| `tests/test_rbac_schema.py › test_admin_leaf_permissions_seeded` (set updated) | both keys seeded, `is_admin=true`, not granted to `admin:super-admin` |

## Approach
1. **Model** (`entitlements/models.py`). `OrgEntitlement(UUIDPkMixin, TimestampMixin, Base)`, table `org_entitlements`; see the data model below. CHECK texts must match the migration character for character (Alembic does not compare CHECKs; add the module docstring note as in `join_requests/models.py`). Register it in `app/db/models.py`.
2. **Migration** (one file). Generate the revision id with `make migration m="adm006 org entitlements"` and then hand-edit it. `down_revision = "a9d4c27e6b13"`; the chain was verified, and that is the current single head. It is expand-only (new table, no locks on existing tables). It seeds both permissions with the exact `INSERT … ON CONFLICT (app_id, key) DO NOTHING` used in `5e2a9c7d1f04` (`ADMIN_APP_ID = "00000000-0000-4000-8000-0000000000a1"`, `is_admin=true`, granted to no role). `downgrade()` deletes the 2 permissions first (their `role_permissions` cascade), then drops the index and the table. The docstring states that it is lossy.
3. **Schemas.** Create and update use `model_config = ConfigDict(extra="forbid")`.
   - `PLAN_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,62}$"` (max 63).
   - `Limits = dict[Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")], Annotated[StrictInt, Field(ge=0, le=2**53 - 1)]]` with `Field(max_length=32)`. `StrictInt` rejects bool, float and str.
   - `EntitlementCreate{app_key: str (APP_KEY_RE, max 32), plan, limits: Limits = {}, enabled: bool = True}` (use `Field(default_factory=dict)`).
   - `EntitlementUpdate{plan: str | None, limits: Limits | None, enabled: StrictBool | None}`. An explicit `null` means no change, as in `OrganizationService.update`.
   - `EntitlementRead{id, org_id, app_key, plan, limits, enabled, active, created_at, updated_at}`.
4. **Repository.** A module-level `ACTIVE = and_(OrgEntitlement.enabled.is_(True), Organization.status == "active", App.status == "active")` is the **only** definition of "active". It is used both as a selected column (`ACTIVE.label("active")`) and as the `?active` filter (`ACTIVE` / `not_(ACTIVE)`). The future service-read ticket must reuse it.
   - Query shape: `select(OrgEntitlement, App.key, ACTIVE)` joined to `App` and `Organization`, filtered on `org_id`, ordered by `App.key, OrgEntitlement.id`, with a count query using the same joins and filter. No N+1.
   - Methods: `get_view(org_id, ent_id)` (same select, single row), `get_for_org(org_id, ent_id)` (ORM row), `find(org_id, app_id)`, `add`, `delete`, `exists_for_org`.
5. **Service** (raises `AppError` subclasses only, R1).
   - `list(org_id, active, limit, offset)`: org must exist, else 404.
   - `grant(org_id, data)`: run the checks in this order.
     1. Org exists, else 404 `Organization … not found`.
     2. App exists by key (`RbacRepository.get_app_by_key`), else `NotFoundError("App not found", details=[{"field":"app_key","reason":"unknown_app"}])`.
     3. `app.key == ADMIN_APP_KEY` gives 409 reason `admin_app`.
     4. `app.status == disabled` gives 409 reason `app_disabled`.
     5. `app.org_mode != required` gives 409 reason `org_mode_none`.
     6. An existing (org, app) row gives 409 reason `entitlement_exists`.
     7. Insert. Catch `IntegrityError` and map it to the same 409.
     8. Audit `entitlement.granted`.

     All 409s are `ConflictError(msg, details=[{"field":"app_key","reason":…}])`.
   - `update`: load by (org_id, id), else 404 (org first, so an unknown org is also 404). Take `before = diff.snapshot(ent, diff.ENTITLEMENT)` and apply the non-None fields; **assign a new dict** to `limits`, because there is no MutableDict. Flush and refresh, then write an audit row only when `changes` is non-empty.
   - `revoke`: load by (org_id, id), else 404. Take the before snapshot, delete, then audit `entitlement.revoked` with `diff.diff(before, None)`.
   - Every audit call passes `target=(AuditTarget.ENTITLEMENT, ent.id)`, `org_id=org_id` and `app_key=app.key`, in the same transaction (R4).
   - Suspended org: no special case in writes; this is deliberate (see Assumptions).
   - Disabled app or `org_mode` change after a grant: update and revoke stay allowed.
6. **Router.** `APIRouter(prefix="/organizations/{org_id}/entitlements", tags=["entitlements"], dependencies=[Depends(current_admin)])`. Use `READ = "admin:entitlements:read"` and `WRITE = "admin:entitlements:write"` through `require_permission` (R3 class 1). Copy the `_with_409` helper pattern from `app_credentials/router.py` for POST, documenting "admin app, disabled app, org_mode none or duplicate (details[0].reason)", and do the same for nothing else. The router does HTTP only and builds `EntitlementRead` from the repository view rows (via the service).
7. **Delete guards** (needed because of RESTRICT).
   - Org delete: after `has_role_assignments`, call `has_entitlements`, which raises `OrgHasEntitlementsError("Organization has entitlements; revoke them first", details=[{"field":"org_id","reason":"org_has_entitlements"}])`.
   - App delete: after `app_has_assignments`, call `app_has_entitlements`, which raises a 409 with reason `app_has_entitlements`.
   - The existing `IntegrityError` fallbacks stay unchanged. In a concurrent race they can return the assignments message; the status is still 409 (see Assumptions).
8. **Tests and maps.** Update `test_route_auth_classes.py`, `test_audit_coverage.py` and `test_rbac_schema.py` as listed, and write the two new test files. Audit-coverage scenarios:
   - `_ent_grant`: org plus `make_app(s, _key(), org_mode="required")`, body `{"app_key": key, "plan": "pro"}`. Failure: `_ent_grant_unknown_org` → 404.
   - `_ent_update`: existing row, body `{"plan": "max"}`. Failure: unknown id → 404.
   - `_ent_revoke`: existing row. Failure: unknown id → 404.

   Add an `ENT = f"{V1}/organizations/{{org_id}}/entitlements"` constant. Keep plan values free of the words `token`, `password`, `cookie`, `_hash` and `@` (the no-secret sweep).
9. **Docs.**
   - Spec §5.6 note: `> 2026-11-17 (ADM-006): staff Bearer endpoints under /api/v1/organizations/{org_id}/entitlements (admin:entitlements:read|write). Active = enabled AND org active AND app active (?active=true). Only non-admin, active, org_mode=required apps can be granted. Service-to-service read (Q4) deferred to ADM-018.`
   - `identity-architecture.md` §4 row and §3 data-model line.
   - `deploy.md` permission list.
10. Run `make check` (ruff, format, mypy --strict, pytest, `alembic check`).

Rules:
- **R1**: layering as above.
- **R2**: hand-reviewed migration, working downgrade, model registered, no drift.
- **R3**: class 1 only, and every route is in the BEARER map. `APP_CREDENTIAL` stays exactly `{PUT manifest}`; `test_app_credential_allow_list_is_exact` is untouched.
- **R4**: one row per mutation, none on failure or no-op.
- **R5**: no new config.
- **R6**: 201/200/204/404/409/422 through the error envelope.
- **R7**: no service auth, no token change, no app-PATCH org_mode guard.
- **R8**: tests above.
- **R9**: `/api/v1/organizations/{org_id}/entitlements[/{entitlement_id}]` with UUID ids, `Page[T]`, PATCH. No new R9 exception.
- **R10**: a plan is a label, not billing.
- **R11**: not touched.

## Specialist input used
Adopted:
- Architect R-A, DB: new module `app/modules/entitlements/` and table `org_entitlements`; `plan` as a free slug (no enum); `limits` as JSONB `NOT NULL DEFAULT '{}'`; `enabled` bool default true; `UNIQUE(org_id, app_id)` with an explicit name; index on `app_id`.
- Architect R-C, API, DB: revoke is a hard DELETE (204) with an audited before snapshot; disable is PATCH `enabled=false` (keeps plan and limits).
- Architect R-D, API §4: new `admin:entitlements:read|write` (`is_admin`, seeded, granted to no role). Org-record editing and commercial grants are separate duties, and adding keys is additive.
- Architect R-E, DB: one active definition (`enabled AND org active AND app active`) in a single repository expression with one JOIN query.
- Architect R-F, API §2: suspension does not touch rows, staff writes stay allowed, and the active view of a suspended org is empty.
- API §2: POST plus PATCH (not a PUT upsert), so "duplicate → 409" holds; pre-check plus an IntegrityError catch; an empty or identical PATCH writes no audit row; unknown `app_key` in the body gives 404 (the ticket AC says 404).
- API §1/§5: no class 4 route and no ADR-0016 change (the owner decided this).
- API §6, architect: audit actions `entitlement.granted|updated|revoked`, target `entitlement`.
- DB: the Alembic pitfalls (identical CHECK text, JSONB server_default `text("'{}'::jsonb")` in both places, explicit FK and unique names, `ondelete` in both places).

Disagreements resolved:
- **FK ON DELETE: RESTRICT for both `org_id` and `app_id`** (architect), not CASCADE (DB, API). Reason: the ADR-0013 §4 house rule says "Cascading would drop grants without per-row audit". An entitlement is a grant. Org delete and app delete already 409 on assignments, so two parallel guards (`org_has_entitlements`, `app_has_entitlements`) keep the behaviour uniform.
- **Item addressed by UUID `{entitlement_id}`** (architect), not `{key}` (DB, API). Reason: the D18 exception covers only `/api/v1/apps/{key}/…` paths. Using an app key under `/organizations/...` would need a new R9 exception, which means a CLAUDE.md change this ticket cannot make. The cost is one `_fill` entry. Responses carry `app_key`.
- **`limits` values: non-negative strict integers only** (architect), not int/float/bool/str/null (DB) and not int-or-null (API). Reason: the most conservative choice. Loosening later is additive in v1, while tightening would break it. Absent key = "app default", and the app owns the meaning. Max 32 keys, key regex `^[a-z][a-z0-9_]{0,63}$`, values ≤ 2^53−1 (JS-safe for admin-pan).
- **`org_mode = none` apps are refused** (architect, DB) with 409 `org_mode_none`. Reason: no token or request of such an app carries an org, so the grant could never be enforced. Lifting the refusal later is additive. 409 (not 422) matches the other grantability refusals (`admin_app`, `app_disabled`), as in `AppCredentialService._refusal`.
- **Disabled apps are refused on grant** (DB, API) and not pre-provisionable (architect). Reason: this is the conservative choice and mirrors credential issue. Existing rows stay editable and revocable after an app is disabled.
- **Active view = `?active=true` filter on the list**, not a separate `/entitlements/active` document route. Reason: it needs no R9 exception and no extra route. It is still a `Page[T]`, and the same `ACTIVE` expression also fills the per-item `active` field.
- **No `created_by`/`updated_by` columns** (DB: optional). The audit log already records the actor (R4), so they would be redundant.
- **No `GET …/{entitlement_id}`** (API proposed it). It is not in AC2, and R7 keeps scope tight.
- **No `pg_column_size`/`octet_length` CHECK** (DB). Pydantic bounds the size (32 keys × short keys × ints), so only `jsonb_typeof(limits) = 'object'` is enforced in the DB.

Rejected:
- Architect R-E Options 1–3 (class 4 read, `ent` token claim, push). The owner deferred them; they go into the follow-up text below.
- Architect's ADR-0017 in this ticket. Deferred to the follow-up, which is where Q4 gets answered.

## Assumptions
- **Suspended org.** Rows are not changed. Grant, update, revoke and list stay allowed, so staff can prepare reactivation. The rows report `active=false`, and `?active=true` returns an empty page (not 403/404, since the staff caller already knows the org).
- **Disabled app.** Existing rows stay and report `active=false`, and they remain editable. New grants give 409.
- **Later `org_mode` change** (app PATCH `required` → `none`) does not touch existing rows and does not change `active`. Guarding that PATCH is a follow-up (R7).
- **Token issuance and app join are not gated** by entitlements in v1 (architect risk: gating would lock calechip out). Products enforce entitlements themselves.
- **Concurrent races.** A grant racing an org or app delete is caught by the RESTRICT FK. The existing `IntegrityError` fallback in `delete` may then answer with the assignments 409 message/code instead of the entitlements one. Accepted: the status is still 409 and the data is intact.
- **Enabled default.** `enabled` defaults to `true` on grant, because a grant is meant to enable.
- **PATCH limits** replaces the whole object.
- **PATCH nulls.** An explicit `null` for any PATCH field means "no change", consistent with the org update.
- **Refusals are not audited.** 404/409 refusals write no audit row (they are not `DENIED_ERRORS` guards; consistent with org/app CRUD and `test_failure_writes_no_row`).
- **Who holds the permissions.** The new permissions are held only via `admin:*` until staff grant them to a role (same as the existing admin keys).
- **Re-grant after revoke** is allowed (hard delete, so the unique constraint is free).

### Needs owner's decision (non-blocking; defaults applied, approve by merging)
1. **FK RESTRICT** on org and app delete (409 `org_has_entitlements` / `app_has_entitlements`), not cascade. Default: RESTRICT (ADR-0013 §4).
2. **New permissions** `admin:entitlements:read|write`, not reuse of `admin:organizations:*`. Default: new keys.
3. **Grantable apps**: only active, non-`admin`, `org_mode = required` apps. Default: yes. Alternative: allow `org_mode = none` and/or pre-provisioning on disabled apps (both additive later).
4. **`limits` shape**: flat `{key: non-negative int}`, ≤ 32 keys. Default: yes. Alternative: allow null (= unlimited), booleans or strings (additive later).
5. **`plan`**: required free-form slug, no per-app plan catalogue. Default: yes.
6. **Suspended org**: staff writes allowed, active view empty. Default: yes.
7. **No entitlement gate** on token issuance or join in v1. Default: none.
8. **Service read deferred** to ADM-018 (owner note 2026-11-17, already decided; listed for traceability).

### Follow-ups (not in this diff)
- **Proposed ticket ADM-018: Service read of org entitlements (spec §5.6, §8 Q4).** Areas: architecture, auth, security, api.
  - Goal: mmlabs services can read an org's active entitlements for their own app.
  - Owner decides Q4 first (pull with an app credential, an `ent` token claim, or both; push is §5.7 Later). Record the decision in a new ADR ("Entitlement consumption") that amends R3/ADR-0016 if class 4 is widened.
  - If pull: `GET /api/v1/apps/{key}/entitlements/{org_id}`, app credential, own app only (another app gives 403). It returns a single document `{org_id, app_key, active, plan, limits}` built from the ADM-006 `ACTIVE` expression. Unknown, suspended and not-entitled orgs give one indistinguishable answer (no existence oracle), with a per-IP rate limit and no CORS.
  - Decide whether the `mmak_` key needs a scope (`manifest` | `entitlements:read`), since today it also holds manifest-write power.
  - If a token claim: an additive `ent` claim only when the `org` claim is present, with the 12 h staleness accepted.
  - AC: R3 class-4 list and `test_app_credential_allow_list_is_exact` updated, audit/rate-limit tests, a consumers doc in `docs/consumers.md`.
- App PATCH `org_mode required → none` while entitlements exist: decide 409 or accept (small ticket).
- Reverse view "which orgs hold app X" (`GET /api/v1/apps/{key}/entitlements` for staff), if admin-pan needs it.
- Per-app plan/limit catalogue declared in the manifest (validation of `plan` and `limits` keys).
- `docs/deploy.md` line 435 list also lacks the existing `admin:audit:read`. It was not fixed here (R7); this is a separate solo fix.

## Blocking questions
None

## Split proposal
None. About 450 lines of prod code including the migration, one spec feature.

## Data model / migration
Table `org_entitlements` (migration `20261117_<rev>_adm006_org_entitlements.py`, `down_revision = "a9d4c27e6b13"`):

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | `uuid` | NOT NULL | app-side `uuid4` | PK `pk_org_entitlements` |
| `org_id` | `uuid` | NOT NULL | — | FK `fk_org_entitlements_org_id_organizations` → `organizations.id` **ON DELETE RESTRICT** |
| `app_id` | `uuid` | NOT NULL | — | FK `fk_org_entitlements_app_id_apps` → `apps.id` **ON DELETE RESTRICT** |
| `plan` | `varchar(63)` | NOT NULL | — | CHECK `ck_org_entitlements_plan_format`: `plan ~ '^[a-z0-9][a-z0-9_-]{0,62}$'` |
| `limits` | `jsonb` | NOT NULL | `'{}'::jsonb` (`server_default=sa.text("'{}'::jsonb")` in model and migration) | CHECK `ck_org_entitlements_limits_object`: `jsonb_typeof(limits) = 'object'` |
| `enabled` | `boolean` | NOT NULL | `true` (`server_default=sa.true()` / `text("true")`, the same in both) | |
| `created_at` | `timestamptz` | NOT NULL | `now()` | TimestampMixin |
| `updated_at` | `timestamptz` | NOT NULL | `now()` (onupdate now) | TimestampMixin |

Constraints and indexes:
- `UniqueConstraint("org_id", "app_id", name="uq_org_entitlements_org_id_app_id")`. It also serves list-by-org (leading `org_id`) and the `org_id` FK check.
- `Index("ix_org_entitlements_app_id", "app_id")` serves the RESTRICT check on app delete and `app_has_entitlements`.
- No partial or JSONB indexes (few rows per org; no query by limit).

Model CHECK names are passed as `name="plan_format"` / `name="limits_object"` (naming convention). The migration uses `op.f("ck_org_entitlements_…")`. FK and unique names are explicit and identical in both.

Data seed in the same migration: `admin:entitlements:read` ("Read organization entitlements") and `admin:entitlements:write` ("Grant, update and revoke organization entitlements"), on app `admin` (`00000000-0000-4000-8000-0000000000a1`), `is_admin=true`, `ON CONFLICT (app_id, key) DO NOTHING`, granted to no role.

`downgrade()`:
1. `DELETE FROM permissions WHERE app_id = … AND key IN (the 2 keys)` (their `role_permissions` cascade; same known limitation as `5e2a9c7d1f04`).
2. `op.drop_index("ix_org_entitlements_app_id", table_name="org_entitlements")`.
3. `op.drop_table("org_entitlements")`.

The downgrade is lossy: every entitlement is dropped. The docstring says so.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/organizations/{org_id}/entitlements` | query `limit` (1–100, default 20), `offset` (≥0), `active` (bool, optional: `true` = only active, `false` = only inactive) | `Page[EntitlementRead]`, ordered by app key, id | 200, 401, 403, 404 (org), 422 | `admin:entitlements:read` |
| POST | `/api/v1/organizations/{org_id}/entitlements` | `{app_key, plan, limits?={}, enabled?=true}` (extra forbidden) | `EntitlementRead` | 201, 401, 403, 404 (org; `app_key` reason `unknown_app`), 409 (`admin_app`, `app_disabled`, `org_mode_none`, `entitlement_exists`), 422 | `admin:entitlements:write` |
| PATCH | `/api/v1/organizations/{org_id}/entitlements/{entitlement_id}` | `{plan?, limits?, enabled?}` (null = no change; `limits` replaces; extra forbidden) | `EntitlementRead` | 200, 401, 403, 404 (org or entitlement not in org), 422 | `admin:entitlements:write` |
| DELETE | `/api/v1/organizations/{org_id}/entitlements/{entitlement_id}` | — | — | 204, 401, 403, 404 | `admin:entitlements:write` |

`EntitlementRead`: `{id: uuid, org_id: uuid, app_key: str, plan: str, limits: {str: int}, enabled: bool, active: bool, created_at: timestamptz, updated_at: timestamptz}`. `active` = `enabled AND organizations.status='active' AND apps.status='active'` (the single repository expression).

Changed behaviour of existing endpoints:
- `DELETE /api/v1/organizations/{org_id}` now also gives 409 `org_has_entitlements`.
- `DELETE /api/v1/apps/{key}` now also gives 409 `conflict` with reason `app_has_entitlements`.

Audit actions: `entitlement.granted`, `entitlement.updated`, `entitlement.revoked` (target `entitlement`).
