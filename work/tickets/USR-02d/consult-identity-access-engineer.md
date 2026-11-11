# consult-identity-access-engineer — USR-02d

## Findings
1. **The TOCTOU is real and three-sided, not one.** ADR-0003 §9 ("a default role never holds an `is_admin` permission") breaks under READ COMMITTED for any concurrent pair of: (a) PUT adding an `is_admin` permission to role R ‖ `PATCH /apps/{key}` setting `default_role_id=R` (02b-1 `AppService.update` checks `role_has_admin_permission` then writes — both pass); (b) `PATCH permission is_admin=true` ‖ PUT adding that permission to the default role; (c) `_build_for_app` reading non-admin permissions ‖ PATCH flipping one of them to `is_admin`.
2. **A check "in the same statement" is not enough.** `INSERT … SELECT … WHERE NOT EXISTS` uses a statement snapshot and can't see the other request's uncommitted change. A per-row `FOR UPDATE` on the role alone misses (b) and (c), whose conflicting write is on `permissions` or `apps`.
3. **Other guards share the race:** "delete a permission held by any role" races a concurrent PUT (`role_permissions.permission_id` is `ON DELETE CASCADE`, so the grant would be silently dropped); "PUT keeps `admin:*` in `admin:super-admin`" races deleting/flipping the permission.
4. **Already DB-backed:** default-role delete → `fk_apps_default_role_same_app` (NO ACTION); role with assignments → `role_assignments.role_id` (NO ACTION); `<app>:*` ⇒ `is_admin` CHECK; cross-app via composite FKs. With savepoints these surface as `IntegrityError` → 409.

## Recommendations
- **R1. One per-app row lock, taken first by every RBAC mutation (AC4, carry-over 1).** `RbacRepository.lock_app(key)`:
  ```python
  select(App).where(App.key == key)
    .with_for_update(key_share=True)            # FOR NO KEY UPDATE
    .execution_options(populate_existing=True)  # refresh the identity map
  ```
  Call it at the start of all 7 mutating 02d routes, of `AppService.update` (replacing `get`), and of `_build_for_app` (lock, then read permissions). Roles and permissions are bound to one app by the composite FKs, so this single lock serializes every invariant above; lock order is always app → rows (no deadlock); `NO KEY UPDATE` doesn't block FK `KEY SHARE` checks from 02c assignment inserts. Rejected: a DB trigger (must lock across 3 tables, invisible to `alembic check`, overkill — follow-up for USR-05/CLI paths that skip the service); SERIALIZABLE (needs retries + 40001 mapping); advisory lock (equivalent but less visible).
- **R2. Guards, all after `lock_app` (AC2, AC4):** prefix ≠ app key → 422 `unprocessable` (field `key`); `<app>:*` with `is_admin` absent → forced true; `is_admin=false` on create or PATCH → 422; PATCH `is_admin=true` → 409 if `app.default_role_id` holds the permission; PUT → 409 if the target set adds any `is_admin` permission and `role.id == app.default_role_id`, and 409 if the role is `admin:super-admin` and `admin:*` is not in the target; DELETE permission → 409 for `admin:*` or when held by any role; DELETE role → 409 if `is_system`, the app's default role, or it has assignments (FK-backed `IntegrityError` → 409 as a second line).
- **R3. Shared diff (AC3, carry-over 3):** one `_set_role_permissions(role, app, target_ids) -> (added, removed)` wrapping insert+delete in `begin_nested()`, mapping `IntegrityError` → 409; used by the builder and PUT. PUT dedupes; an empty list is allowed.
- **R4. Member role (`is_system`) is PUT-editable but not deletable** (D9: tightening is data); the default-role guard still blocks adding `is_admin` permissions. Role `name` PATCH allowed on system roles; `key`/`is_system` stay `extra="forbid"`.
- **R5. Tests:** sequential tests for each guard and each race side (a/b/c). The per-test rollback fixture can't run two connections on shared data, so also capture SQL (event listener) and assert `FOR NO KEY UPDATE` is issued on each mutating path; record that in impl-log.
- **R6. ADR-0003 amendments:** §9 — the invariant also holds on role/permission paths, enforced under a per-app `FOR NO KEY UPDATE` lock (list the three sides); new §10 guard catalogue (409 vs 422; system rows: `admin:*` not deletable, `admin:super-admin` must keep `admin:*`, `is_system` roles PUT-editable but not deletable); §7 `_set_role_permissions` shared by builder and PUT, builder takes the lock; §1 wildcard forcing rule.

## Risks
- A later writer (02c assignments touching `admin:super-admin`, USR-05 manifest, CLI) that skips `lock_app` reopens the invariant. Guards live in the service only — add a code comment and a follow-up for a DB trigger.
- If 02b-1's `AppService.update` and `_build_for_app` aren't changed, race sides (a) and (c) stay open — they're inside this ticket's carry-over scope.
- AC3's unknown keys in 422 `details` conflict with 02b-1's "never echo submitted values" rule. Acceptable only for keys that already passed `PERMISSION_KEY_RE` (format errors stay Pydantic 422 with no echo), capped (≤ 200).

## Open questions
1. Force every `admin`-app permission `is_admin=true`? Recommended yes — harmless, and it makes ADM-002's "grant `is_admin` needs `admin:*`" guard cover all admin permissions.
2. Reserve the role key `<app>:member` so staff can't create it (e.g. `admin:member`) → 409/422? Recommended yes.
3. `admin:super-admin` PUT-editable beyond keeping `admin:*`? Default yes until ADM-002 adds `admin:*`-only.
