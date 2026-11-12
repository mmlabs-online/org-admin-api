# Security review — USR-02c

## Attempt 1

**Scope:** `git diff c2a3a0f...HEAD`, one commit `debae92`. Files reviewed: `app/main.py`, `app/modules/rbac/{assignments_router,repository,schemas,service}.py`, `tests/test_rbac_assignments_api.py`, `tests/test_rbac_locking.py`, `tests/rbac_factories.py`.
**`make check`:** green. ruff, format and mypy pass, and pytest shows 499 passed.

**Verdict:** no 🔴 blockers. One 🟡 should-fix; the rest are notes.

### Findings

#### 🟡 S1: `admin`-app roles, including `admin:super-admin`, can be assigned with an `org_id`
- **Where:**
  - `app/modules/rbac/service.py:531-537` (`_grant_locked`) refuses `org_id` only when `app.org_mode == OrgMode.NONE`.
  - The `admin` app's `org_mode` can be changed. `AppUpdate.org_mode` exists (`app/modules/rbac/schemas.py:61`), and the admin-app protection in `AppService.update` (`app/modules/rbac/service.py:192-197`) covers only `join_policy`, `default_role_id` and `status`.
- **Risk:** a caller can `PATCH /api/v1/apps/admin {"org_mode":"required"}` and then `POST …/role-assignments {"role_id": <admin:super-admin>, "org_id": …}` to create an org-scoped admin assignment.
  - The last-super-admin guard ignores such rows (`_counting_super_admin` requires `org_id IS NULL`, `repository.py:384-393`). That direction is safe.
  - The risk is for ADM-002. Org-scoped `admin:*` has no defined meaning, and depending on how the permission resolver treats `org_id`, it could become a hidden or ambiguous staff-privilege grant.
  - Rows written while the flag is on stay after the flag is turned off.
  - Exploitability today is low, because the flag is refused in staging and production (`config.py:97-109`). The new code path still makes the bad state reachable.
- **Fix, inside this diff:** in `_grant_locked`, before the `org_mode` check, refuse any org scope on the `admin` app.
  ```python
  if org_id is not None:
      if app.key == ADMIN_APP_KEY or app.org_mode == OrgMode.NONE:
          raise _assignment_unprocessable(
              "This app does not use organizations", "org_id", "org_id_not_allowed"
          )
  ```
  Add a test that sets the admin app's `org_mode` to `required` in the DB, posts the super-admin role with an `org_id`, and expects 422 `org_id_not_allowed`.
- **Not in this diff:** pinning `org_mode = none` on the `admin` app in `AppService.update` belongs to the module that owns it (follow-up F1).

#### 💭 N1: The flag gate is untouched and still holds
`config.py:97-109` refuses `ADMIN_RBAC_API_ENABLED` in `staging`/`production`, and requires `APP_ENV` to be set explicitly when the flag is on. `app_env` is a `Literal` (`config.py:23`), so there is no unknown-env bypass.

The new router is mounted only inside the existing `if settings.admin_rbac_api_enabled:` (`app/main.py:43-45`), and `test_flag_off_404` covers it. As the identity consult accepted, anyone can grant `admin:super-admin` over unauthenticated HTTP while the flag is on (local/test only). That is acceptable only as long as this gate holds.

#### 💭 N2: Locking and race handling are correct
- **Order:** both writes take the role's app lock (`FOR NO KEY UPDATE`) before deciding any guard (`service.py:513`, `service.py:584`).
- **Re-reads:** after the lock, the role is re-read (`get_role(app.id, …)`) and so is the assignment row (`populate_existing`).
- **Super-admin revoke:** it holds the `admin` lock, so two concurrent revokes are serialized. The count runs after the lock under READ COMMITTED (assumption A8).
- **Org check:** it uses `FOR KEY SHARE`, so the org cannot be deleted mid-transaction.
- **Inserts:** a racing insert hits the savepoint `IntegrityError` backstop and gets 409.
- **Tests:** `test_assignment_writes_lock_role_app_first` and `test_super_admin_revoke_holds_admin_lock` cover this.
  - Minor gap: `_is_guard_count` matches only `count(`. The `is_counting_super_admin` EXISTS query (`repository.py:406-410`) is not asserted to run after the lock. The code order makes this correct today, so it is a note only.

#### 💭 N3: No mass assignment and no IDOR
- `AssignmentCreate` uses `extra="forbid"`, and `granted_by` is never input (`schemas.py:195-203`). `test_assign_extra_field_422` covers it.
- DELETE is scoped by `user_id` (`get_assignment_row`, `repository.py:346-357`), so another user's assignment returns 404 (`test_revoke_other_users_assignment_404`).
- All queries go through SQLAlchemy expressions, so there is no SQL injection surface. The `app` filter is validated against `APP_KEY_RE`.

#### 💭 N4: Errors don't echo submitted values (R6/R11)
- Details are `{field, reason}` only, and messages are static.
- The `RequestValidationError` handler strips `input`/`ctx` (`app/core/errors.py:91-93`).
- No email or token appears in any response. `AssignmentRead` has no PII.

#### 💭 N5: User enumeration through GET and POST 404s
GET and POST answer 404 "User not found" for unknown ids (`service.py:497`, `service.py:507`). That is a user-existence oracle by UUID. The risk is negligible: the ids are random UUIDs, the endpoints are staff-only after ADM-002, and the flag is local-only now. No change needed.

#### 💭 N6: Small contract wobble on assign
If the role's app is deleted between the unlocked `role_with_app` and `_locked_app`, assign returns 404 "App not found" (`service.py:83-87`, `service.py:513`) instead of 422 `unknown_role`. This is not a security issue and leaks nothing.

#### 💭 N7: ADM-002 and R4 markers
The `# ADM-002 guard` markers are present in assign (`service.py:545-547`) and revoke (`service.py:600-601`). The `# R4` markers are present on the insert, replace and revoke branches. Both match plan §6.

### Follow-ups (out of scope for this diff)
- **F1 (USR-02b/ADM-002):** add `org_mode` to the immutable fields of the `admin` app in `AppService.update` (`service.py:192-197`). Before ADM-002, check for existing org-scoped `admin`-app assignments and remove or migrate them.
- **F2 (ADM-002, = plan FU1):** the last-super-admin guard can be bypassed by disabling a user (`PATCH /api/v1/users/{id}`) or deleting one (cascade). Neither path takes `lock_app("admin")`. Also turn the `# ADM-002 guard` markers into enforced checks:
  - no self-grant of `admin`-app roles;
  - no granting permissions the actor does not hold;
  - `admin:*` required for any `admin`-app or `is_admin` grant.
- **F3 (ADM-002):** before the API is enabled with auth, audit every `role_assignments` row of the `admin` app written while the flag was on in any shared database.
- **F4 (USR-03 / join flow):** any future writer of `role_assignments` (join assigns the default role) must take the app lock first. Otherwise the find-then-insert duplicate check relies only on the unique-constraint backstop.
- **F5 (USR-02c tests, optional):** widen `_is_guard_count` in `tests/test_rbac_locking.py` to also match the EXISTS guard query, so the lock-first assertion covers both guard statements.
- **F6 (USR-04, = plan FU4):** decide whether unverified accounts count toward the last-super-admin guard.
