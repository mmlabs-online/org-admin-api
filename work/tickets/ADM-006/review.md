## Attempt 1 — 2026-11-17
Verdict: PASS

### Checks (run by reviewer)
- `service postgresql start`; `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff "All checks passed!", "299 files already formatted", mypy "Success: no issues found in 139 source files", pytest "2242 passed, 1 warning in 211.99s", exit 0. The known flake did not occur. Matches impl-log.md (2242 passed).
- `alembic upgrade head && alembic check`: "No new upgrade operations detected."; `alembic heads` = `c8a1f4e72d95 (head)` (single head, `down_revision = a9d4c27e6b13`).
- Downgrade/upgrade round trip is exercised by `tests/test_entitlements_migration.py › test_downgrade_drops_table_and_permissions` (green).

### Acceptance criteria
- [x] AC1: entitlements table + migration (org, app, plan, limits, enabled), unique per (org, app), apps from the USR-02 registry. Evidence: `app/modules/entitlements/models.py` (FK `app_id` → `apps.id`, `uq_org_entitlements_org_id_app_id`, CHECKs), `migrations/versions/20261117_c8a1f4e72d95_adm006_org_entitlements.py` (CHECK text identical to the model, working `downgrade()`), model registered in `app/db/models.py`; `test_constraints` (unique, plan CHECK, limits-object CHECK, defaults, RESTRICT on org and app) and `test_downgrade_drops_table_and_permissions` pass; `alembic check` clean.
- [x] AC2: grant, update, revoke, list endpoints. Evidence: `app/modules/entitlements/router.py` (4 routes); `test_grant_returns_201_and_audit`, `test_grant_defaults`, `test_update_plan_limits_enabled`, `test_patch_replaces_limits`, `test_patch_null_is_no_change`, `test_noop_patch_no_audit`, `test_revoke_204_then_gone`, `test_list_paginates_ordered_by_app_key` pass.
- [x] AC3: read of an org's active entitlements (owner note 2026-11-17: staff read). Evidence: `GET …/entitlements?active=true` using the single `ACTIVE` expression in `app/modules/entitlements/repository.py`; `test_active_filter_definition`, `test_active_field_matches_filter` pass.
- [x] AC4: grant/revoke, duplicate 409, unknown org/product 404, suspended org behaviour. Evidence: `test_duplicate_grant_409`, `test_duplicate_grant_integrity_409`, `test_unknown_org_404` (all 4 routes), `test_unknown_app_key_404`, `test_unknown_entitlement_404` (incl. another org's id), `test_suspended_org_behaviour`, `test_ungrantable_app_409` (admin_app / app_disabled / org_mode_none), 422 validation tests, `test_read_permission_cannot_write` (403), `test_org_delete_with_entitlements_409`, `test_app_delete_with_entitlements_409`; 401 and permission mapping via the updated BEARER map in `tests/test_route_auth_classes.py`; audit success/failure via `tests/test_audit_coverage.py`.

### Scope
Every changed file is in the plan's file list (plus the workflow files `work/backlog.md`, `work/tickets/ADM-006/*`). `EntitlementRepository.exists_for_org` from the plan was dropped (unused; `OrganizationRepository.has_entitlements` covers it), which shrinks the diff. No unjustified out-of-plan change.

### Issues
None.

### Rules R1–R11
No violations found.
- R1: router does HTTP only and validates `EntitlementView` dataclasses; service raises only `NotFoundError`/`ConflictError`; ORM only in repositories.
- R2: hand-written migration with explicit names, matching CHECK texts and server defaults, working lossy downgrade (documented); `alembic check` clean.
- R3: class 1 only; all 4 routes in the BEARER map with `require_permission("admin:entitlements:read|write")`; no class 4 change.
- R4: one audit row per grant/update/revoke in the same transaction; none on no-op or refusal (audit coverage map now 23 entries).
- R5/R11: no config, no secrets touched. R6: envelope via `AppError` subclasses, 201/204/404/409/422. R9: plural path, UUID ids, `Page[T]`, PATCH. R10: `plan` is a label only.

### Nits (do not block)
- `app/modules/entitlements/service.py:88`: the `IntegrityError` catch maps every integrity failure to `entitlement_exists`, including an FK violation from an org/app deleted concurrently. Status is still 409; only the reason may mislead in a rare race.
- `tests/test_entitlements_api.py:254`: `updated_at` equality is a weak check inside one test transaction (`now()` is the transaction start time); the no-audit-row assertion is the real guard.
- `tests/test_entitlements_api.py:300` (`_four_states`): builds three rows in one org, not the plan's four (the suspended-org case is covered separately by `test_suspended_org_behaviour`). The helper name is a bit misleading.
- `app/modules/entitlements/repository.py`: `_select()` returns `Any` (stubs limitation, recorded in impl-log).

### Follow-ups (out of scope)
- Create the proposed ticket ADM-018 (service read of org entitlements, spec §8 Q4). The spec §5.6 note already refers to ADM-018, but there is no backlog row yet.
- App PATCH `org_mode required → none` while entitlements exist: decide 409 or accept.
- `docs/deploy.md` admin-permission list still lacks `admin:audit:read` (pre-existing; solo fix).
- Owner decisions listed as non-blocking in plan.md ("Needs owner's decision" 1–8) are approved by merging.
