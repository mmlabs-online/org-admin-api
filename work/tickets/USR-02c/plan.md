Status: READY

# Plan — USR-02c: Role assignments staff API (flag-gated)

> **Split.** This plan covers the **assignments HTTP API only** (list, assign, revoke, the last-super-admin guard and the locking rules). The bootstrap super-admin CLI, the email-masking helper, the Make target and the runbook move to a follow-on ticket, **USR-02f** (see **Split proposal**). The ticket's CLI criterion (AC4) and the CLI tests are planned there, not here.

## Spec summary
- spec §5.3: "users get roles per app, optionally limited to one organization. Every admin `/api/v1` endpoint declares the `admin:*` permission it needs. A bootstrap super-admin (`admin:*`) exists."
- spec §5.3 (membership): "Joining assigns the default role at app scope (`org_id NULL`)."
- ADR-0001 §4: `role_assignments (id, user_id → users, role_id → roles, org_id → organizations NULL, granted_by → users NULL, expires_at NULL, created_at)`, "unique (user_id, role_id, org_id) — org_id NULL = app-wide".
- ADR-0003 §10: "Every RBAC mutation … starts with `SELECT … FROM apps WHERE key = … FOR NO KEY UPDATE` and decides every guard after it … the order is always app → rows (no deadlocks)."
- CLAUDE.md R3 class 1 (Bearer, admin endpoints under `/api/v1/*` with a declared `require_permission("admin:…")`; until ADM-002, endpoints note the permission).
- USR-02 plan → Split proposal → "USR-02c": the endpoint table, "An expired duplicate is replaced in place (new `expires_at`, `granted_by`) → 201", "Last-super-admin guard: revoking the last active, non-expired `admin:super-admin` assignment held by an active user → 409", "Same `ADMIN_RBAC_API_ENABLED` gate".

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | `GET/POST /api/v1/users/{user_id}/role-assignments` and `DELETE /api/v1/users/{user_id}/role-assignments/{assignment_id}` exist only when `ADMIN_RBAC_API_ENABLED` is on | `test_rbac_assignments_api.py › test_flag_off_404` (default app: all three → 404); the happy-path tests run with the flag on |
| 2 | Each route declares its permission: GET `admin:users:read`, POST and DELETE `admin:roles:assign` | `› test_route_permission_map` (OpenAPI `x-permission` map equals the expected dict exactly) |
| 3 | GET lists a user's assignments as `Page[AssignmentRead]`, ordered `created_at desc, id`, optional `app` filter, `active` computed with the database clock | `› test_list_happy_path`, `› test_list_filter_by_app`, `› test_list_unknown_app_empty_page`, `› test_list_bad_app_key_422`, `› test_list_active_flag`, `› test_list_unknown_user_404` |
| 4 | POST assigns a role (201) at app scope or in one org | `› test_assign_app_scope_201`, `› test_assign_org_scope_201` |
| 5 | 422 for unknown role, past or naive `expires_at`, `org_id` on an `org_mode = none` app, unknown `org_id`; extra fields refused | `› test_assign_unknown_role_422`, `› test_assign_naive_expires_at_422`, `› test_assign_past_expires_at_422`, `› test_assign_org_id_on_none_app_422`, `› test_assign_unknown_org_422`, `› test_assign_extra_field_422` (e.g. `granted_by` in the body) |
| 6 | 409 for an active duplicate; an expired duplicate is replaced in place → 201, same `id` | `› test_assign_active_duplicate_409` (app scope and org scope), `› test_assign_expired_duplicate_replaced` |
| 7 | `expires_at` on `admin:super-admin` → 422 (the last holder cannot silently lapse) | `› test_assign_super_admin_with_expiry_422` |
| 8 | DELETE revokes (204); unknown assignment, another user's assignment, unknown user → 404; second DELETE → 404 | `› test_revoke_204`, `› test_revoke_other_users_assignment_404`, `› test_revoke_unknown_404`, `› test_revoke_twice_404` |
| 9 | Last-super-admin guard: revoking the last active, non-expired `admin:super-admin` held by an active user → 409; revoking one of two → 204; revoking a non-counting one (expired, or held by a disabled user) → 204 | `› test_revoke_last_super_admin_409`, `› test_revoke_one_of_two_super_admins_204`, `› test_revoke_super_admin_other_holder_disabled_409`, `› test_revoke_expired_super_admin_204` |
| 10 | POST and DELETE lock the role's app first (ADR-0003 §10); revoking a super-admin locks `admin`, so concurrent revokes are serialized | `test_rbac_locking.py › test_assignment_writes_lock_role_app_first` (SQL capture: the `FROM apps … FOR NO KEY UPDATE` statement comes before any write to `role_assignments` and before the guard count), `› test_super_admin_revoke_holds_admin_lock` (after the request, a second connection's `FOR NO KEY UPDATE NOWAIT` on `admin` fails with 55P03) |
| 11 | Error envelope, codes and no value echo (R6, R11) | Asserted in each 4xx test: `error.code` is `not_found` / `conflict` / `unprocessable` / `validation_error`; `details` carry `{field, reason}` only |
| 12 | `make check` green, incl. `ruff format --check` on this plan | `make check` |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/rbac/assignments_router.py` | **new**: `APIRouter(prefix="/users", tags=["role-assignments"])` with the three routes, `openapi_extra=permission(...)` | R1/R3; the routes belong to the rbac flag, not to `users_router` (USR-01b flag) |
| `app/modules/rbac/schemas.py` | add `AssignmentCreate`, `AssignmentRead` | request/response contract |
| `app/modules/rbac/service.py` | add `AssignmentService` (list, assign, revoke, guard) + `_assignment_unprocessable` helper | business rules (R1) |
| `app/modules/rbac/repository.py` | add assignment queries (see Approach §3) | SQLAlchemy only in the repository (R1) |
| `app/main.py` | include `assignments_router` inside the existing `if settings.admin_rbac_api_enabled:` block | flag gating |
| `tests/test_rbac_assignments_api.py` | **new**: API tests | R8 |
| `tests/test_rbac_locking.py` | add the two assignment locking tests | AC10 |
| `tests/rbac_factories.py` | add `make_assignment(session, user, role, *, org_id=None, expires_at=None)` and `super_admin_role(session)` (loads the seeded role) | test setup |

No migration, no new config key, no new dependency. `app/db/models.py` is unchanged (`RoleAssignment` is already registered).

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `test_rbac_assignments_api.py › test_flag_off_404` | AC1 (GET, POST, DELETE → 404 on the default app) |
| `› test_route_permission_map` | AC2 (paths starting with `/api/v1/users/{user_id}/role-assignments`; copy the shape of `test_rbac_apps_api.py` route map) |
| `› test_list_happy_path` | AC3: fields `{id, app, role_id, role_key, org_id, granted_by, expires_at, active, created_at}`, order, `total` |
| `› test_list_filter_by_app` / `› test_list_unknown_app_empty_page` / `› test_list_bad_app_key_422` | AC3 filter: well-formed unknown key → empty page; `app=Bad!` → 422 `validation_error` |
| `› test_list_active_flag` | AC3: expired row → `active: false`, open-ended and future rows → `true` |
| `› test_list_unknown_user_404` / `› test_list_pagination_limit_over_100_422` | 404 user; `Page` limits |
| `› test_assign_app_scope_201` | AC4: 201, body matches GET, `granted_by` null, `org_id` null |
| `› test_assign_org_scope_201` | AC4: `org_mode = required` app + existing organization → 201 with `org_id`; also `org_id: null` on a `required` app → 201 (A4) |
| `› test_assign_unknown_user_404` | 404 before any 422 |
| `› test_assign_unknown_role_422` | AC5 `{field: role_id, reason: unknown_role}` |
| `› test_assign_naive_expires_at_422` / `› test_assign_past_expires_at_422` | AC5: naive → `validation_error`; past → `unprocessable` `expires_in_past` |
| `› test_assign_org_id_on_none_app_422` / `› test_assign_unknown_org_422` | AC5 `org_id_not_allowed` / `unknown_org` |
| `› test_assign_extra_field_422` | `extra="forbid"` (`granted_by` in the body) |
| `› test_assign_active_duplicate_409` | AC6, parametrized app scope / org scope (NULLS NOT DISTINCT) |
| `› test_assign_expired_duplicate_replaced` | AC6: same `id` and `created_at`, new `expires_at`, `active: true`, one row in the list |
| `› test_assign_super_admin_with_expiry_422` | AC7 `expiry_not_allowed`; without `expires_at` → 201 |
| `› test_assign_disabled_user_and_disabled_app_allowed` | A2/A3 defaults (pins the documented behaviour) |
| `› test_revoke_204` / `› test_revoke_twice_404` / `› test_revoke_unknown_404` / `› test_revoke_other_users_assignment_404` | AC8 |
| `› test_revoke_last_super_admin_409` | AC9: the seed has no holder; give one user super-admin, revoke → 409 `conflict` |
| `› test_revoke_one_of_two_super_admins_204` | AC9: then revoking the remaining one → 409 |
| `› test_revoke_super_admin_other_holder_disabled_409` | AC9: the other holder is `disabled` → does not count → 409 |
| `› test_revoke_expired_super_admin_204` | AC9: target row inserted with past `expires_at` via the factory → not counting → 204, even when it is the only row |
| `› test_role_delete_with_assignment_409` | regression: `DELETE /apps/{key}/roles/{id}` of a role with an assignment made through the new POST → 409 |
| `test_rbac_locking.py › test_assignment_writes_lock_role_app_first` | AC10 POST (stoktok role) and DELETE (stoktok and admin role) |
| `test_rbac_locking.py › test_super_admin_revoke_holds_admin_lock` | AC10 race: lock held until the transaction ends (pattern of `test_lock_app_blocks_lockers_not_key_share`) |

## Approach
1. **Schemas** (`schemas.py`).
   - `AssignmentCreate`: `model_config = ConfigDict(extra="forbid")`; `role_id: uuid.UUID`; `org_id: uuid.UUID | None = None`; `expires_at: AwareDatetime | None = None`. Naive datetimes get the framework 422 `validation_error`. `granted_by` is never input.
   - `AssignmentRead`: `id`, `app: str` (app key), `role_id`, `role_key`, `org_id`, `granted_by`, `expires_at`, `active: bool`, `created_at`. Built by the service from a repository row, not from the ORM object (it needs two joined keys and the computed flag).
2. **Router** (`assignments_router.py`, R1/R3/R9). Thin, same style as `router.py`; module docstring says "Admin (Bearer) endpoints — R3 class 1. Unauthenticated until ADM-002; mounted only when `admin_rbac_api_enabled`".
   - `GET ""` path `/{user_id}/role-assignments`: `user_id: uuid.UUID`, `page: PageDep`, `app: Annotated[str | None, Query(pattern=APP_KEY_RE)] = None` → `Page[AssignmentRead]`, `permission("admin:users:read")`.
   - `POST /{user_id}/role-assignments` → 201 `AssignmentRead`, `permission("admin:roles:assign")`.
   - `DELETE /{user_id}/role-assignments/{assignment_id}` → 204, `permission("admin:roles:assign")`.
   - Mounted in `create_app` next to `rbac_router` in the same `if`. `users_router` (`/users`, `/users/{user_id}`) has no overlapping path, so both flags can be on together.
3. **Repository** (`repository.py`, SQLAlchemy only). New methods:
   - `user_exists(user_id) -> bool` (`select(exists().where(User.id == ...))`; imports `User` model only).
   - `role_with_app(role_id) -> tuple[Role, App] | None`: unlocked read used to find which app to lock.
   - `org_exists_key_share(org_id) -> bool`: `select(Organization.id).where(...).with_for_update(key_share=True)`, so the organization cannot be deleted before this transaction ends (no FK until USR-06).
   - `db_now() -> datetime`: `select(func.now())` (transaction clock, same as `_active_assignments`).
   - `page_assignments(user_id, *, app_key, limit, offset) -> (rows, total)`: rows are `(RoleAssignment, app_key, role_key, active)`, `active` = `or_(expires_at IS NULL, expires_at > func.now())` as a labelled column; join `Role`, `App`; order `RoleAssignment.created_at.desc(), RoleAssignment.id`.
   - `get_assignment_row(user_id, assignment_id)`: same row shape, scoped to `user_id` (another user's id → None → 404, no leak).
   - `find_assignment(user_id, role_id, org_id) -> RoleAssignment | None`: `org_id IS NULL` when `org_id` is None (`is_not_distinct_from`).
   - `add_assignment(ra)`: insert in a savepoint (`begin_nested`), as `add_role`.
   - `save()` (exists) for the in-place replacement; `delete_assignment(ra)` in a savepoint.
   - `count_other_active_super_admins(role_id, exclude_id) -> int`: `role_id = :super_admin`, `org_id IS NULL`, `expires_at IS NULL OR > now()`, join `User` with `status = 'active'`, `id != exclude_id`.
   - `is_counting_super_admin(assignment_id) -> bool`: same predicate for the target row (keeps both checks in SQL with the DB clock).
4. **Service** (`AssignmentService`, R1, raises `AppError` subclasses only). Every method takes `actor: uuid.UUID | None = None` (unused until ADM-002; `granted_by = actor`).
   - `list(user_id, *, app_key, limit, offset)`: user missing → `NotFoundError("User not found")`; else page.
   - `assign(user_id, data, actor)`, in this order:
     1. user missing → 404.
     2. `role_with_app(role_id)` is None → 422 `{field: role_id, reason: unknown_role}`.
     3. `_locked_app(repo, app.key)` — **the first locking statement and before any guard decision** (ADR-0003 §10). Re-read the role with `repo.get_role(app.id, role_id)`; gone → 422 `unknown_role` (deleted between the read and the lock).
     4. `org_id`: `app.org_mode == none` and `org_id` set → 422 `org_id_not_allowed`. Set and `not org_exists_key_share` → 422 `unknown_org`. `org_id` None is always allowed (app scope; A4).
     5. `expires_at`: on `admin:super-admin` (app `admin`, role key `SUPER_ADMIN_ROLE`) and not None → 422 `expiry_not_allowed`. Else `expires_at <= repo.db_now()` → 422 `expires_in_past`.
     6. `# ADM-002 guard:` comment block (see §6).
     7. Duplicate: `find_assignment`. Active (`expires_at` None or > db now) → `ConflictError("Role is already assigned")`. Expired → set `expires_at`, `granted_by`, `repo.save()` (in-place replacement, keeps `id` and `created_at`). None → `add_assignment`; `IntegrityError` (racing insert outside the lock, or user deleted meanwhile) → 409.
     8. `# R4: audit entry in the same transaction once ADM-004 lands.` on both the insert and the replacement branch.
     9. Return the `AssignmentRead` built from `get_assignment_row`.
     The steps after the lock (3–7) are one private method, `_grant_locked(app, role, user_id, org_id, expires_at, actor) -> (RoleAssignment, created: bool)`, so USR-02f's CLI can reuse it under the same lock.
   - `revoke(user_id, assignment_id, actor)`:
     1. `get_assignment_row` None (also covers unknown user) → 404 `Assignment not found`.
     2. `_locked_app(repo, app_key)` of the assignment's role. For an `admin:super-admin` row this **is** the `admin` lock, so two concurrent revokes are serialized. Re-read the row; gone → 404.
     3. Guard: if the role is `admin:super-admin` and `is_counting_super_admin(target)` and `count_other_active_super_admins(...) == 0` → `ConflictError("Cannot revoke the last active super-admin")`. Under PostgreSQL's default READ COMMITTED each statement takes a new snapshot, so the count after the lock sees a concurrent revoke that committed first (A8).
     4. `# ADM-002 guard` and `# R4` comments; `delete_assignment`; `IntegrityError` → 409.
5. **Flag gating.** No config change: `admin_rbac_api_enabled` already exists and is refused in staging/production with an explicit `APP_ENV` required (`config.py` `_admin_apis_gate`). Nothing in this diff may touch that validator (security consult finding 1).
6. **`# ADM-002 guard` markers** (comments only, in `assign` and `revoke`): "granting or revoking an `admin`-app role, or a role holding any `is_admin` permission, requires `admin:*`; an actor may not grant themselves any `admin`-app role; an actor may not grant permissions they do not hold; self-revoke still goes through the last-super-admin guard."
7. **Rules check.** R1 (layers as above), R2 (no schema change; `alembic check` unaffected), R3 (Bearer class, permissions declared), R4 (markers), R5 (no config), R6 (`NotFoundError`, `ConflictError`, `UnprocessableError` with `{field, reason}` details, no values echoed), R7 (follow-ups below), R8 (tests above), R9 (`/api/v1/users/{user_id}/role-assignments`, UUID ids, `Page[T]`), R11 (no email or token in any response or error).

## Specialist input used
**api-platform-engineer**
- Adopted: separate router in `app/modules/rbac/` mounted under the rbac flag; route→permission test; flag-off 404 test; `app` filter validated with `APP_KEY_RE`, unknown well-formed key → empty page; `active` computed in SQL; order `created_at desc, id`; check order user → role → lock → org → expiry → duplicate; `AwareDatetime` + `extra="forbid"`; `expires_in_past` against DB `now()`; `org_id: null` valid on `required` apps; DELETE scoped by `user_id`; `# R4` markers and unused `actor`.
- Adopted: the split, as 02c = API and 02f = CLI + masking + Make target + runbook.
- Modified: duplicate handling. Instead of one `INSERT … ON CONFLICT … DO UPDATE … WHERE expires_at <= now()`, a find-then-write under the app lock (the lock already serializes every writer of that role's assignments, including the 02f CLI), with a savepoint + `IntegrityError` → 409 backstop. Same behaviour, simpler to read and test.
**identity-access-engineer**
- Adopted: lock the role's app after an unlocked read, re-read under the lock; super-admin revoke locks `admin`; org check with `FOR KEY SHARE`; guard definition (app scope, not expired, user `active`, others than the target); `expires_at` on `admin:super-admin` → 422; `# ADM-002 guard` contents; `granted_by` never input.
- Answered as assumptions: Q2 (disabled users) → A2; Q3 (app scope on `required` apps) → A4; Q4 (unverified super-admin counts) → A5. Q1, Q5 (CLI) → USR-02f.
**security-architect**
- Adopted: the flag gate stays untouched; locking + a lock test; `expires_at` on super-admin refused (option 1 of Q1); `# ADM-002 guard` / `# R4` markers.
- Modified: "every assignment POST/DELETE takes `lock_app('admin')` first" → lock the **role's** app. Only `admin:super-admin` rows affect the guard and they live in the `admin` app, so the role's app lock is the `admin` lock exactly when it matters; locking `admin` for every stoktok assignment would serialize unrelated apps for no gain.
- Deferred: user-disable/delete guard (FU1), CLI items incl. `hide_parameters`, pre-registration hijack runbook and `email_verified` output (USR-02f).

## Assumptions
For the owner to confirm in the PR.
- **A1 — Split.** ~460 production lines in total (api consult) is over the ~400 guide. This ticket ships the API (~300 lines); USR-02f ships the CLI. ADM-002 must also depend on USR-02f.
- **A2 — Disabled users may be assigned roles** (any app, `admin` included). Disabled users cannot log in or get tokens (USR-03 checks status, USR-02a A7), and the grant becomes effective if staff re-enable them. A disabled holder does not count for the last-super-admin guard.
- **A3 — Disabled apps may receive assignments.** Assignments are data; USR-03 answers 403 for a disabled app.
- **A4 — `org_id: null` on an `org_mode = required` app is allowed** (app scope = every org of that app), the same scope join uses (spec §5.3). `org_id` on an `org_mode = none` app → 422.
- **A5 — Email verification is ignored** for assigning and for the guard count. Before USR-04 nobody is verified (D16), so requiring it would make the API and the guard useless. Revisit in USR-04/ADM-002.
- **A6 — `expires_at` on `admin:super-admin` → 422** (`expiry_not_allowed`), so the last holder cannot lapse silently. Rows with an expiry inserted outside the API simply do not count once expired.
- **A7 — Replacement of an expired duplicate** keeps `id` and `created_at`, sets the new `expires_at` and `granted_by`, and returns 201 (USR-02 plan). It is invisible in the list order until audit lands (ADM-004).
- **A8 — The race argument relies on READ COMMITTED** (PostgreSQL's default, not changed by `app/db/session.py`). If the isolation level is ever raised to REPEATABLE READ, the guard must retry on serialization failure.
- **A9 — `expires_at` must be later than the transaction's `now()`**; there is no upper bound.
- **A10 — `GET` needs nothing extra beyond `admin:users:read`** (it shows roles, not PII; no email in the response).

## Blocking questions
None.

## Split proposal
Slices: **USR-02c (this plan)** → **USR-02f**. Backlog edits for the orchestrator: add USR-02f (P1, areas `auth, security`, depends on USR-02c); add USR-02f to ADM-002's hard dependencies; trim USR-02c's AC4 (CLI) and its CLI tests to "moved to USR-02f".

### USR-02f — Bootstrap super-admin CLI (`grant-super-admin`) with masked output and runbook
- **Goal:** the first super-admin can be granted from the server's command line, never over HTTP, without the RBAC flag.
- **Spec reference:** §5.3 ("A bootstrap super-admin (`admin:*`) exists"); D16; R11; USR-02 plan → USR-02c CLI bullet; USR-02c consults (CLI parts).
- **Acceptance criteria:**
  - `python -m app.cli grant-super-admin --email <email>` (standard-library `argparse`, no new dependency) grants `admin:super-admin` at app scope with `granted_by` NULL and `expires_at` NULL, in one transaction: `lock_app("admin")` → load the seeded role (missing → exit 1 "run migrations") → find the user by email normalized as sign-up does (`strip().lower()`, CITEXT) → reuse `AssignmentService._grant_locked`.
  - Idempotent: active assignment → exit 0 "already held"; none or expired → exit 0 "granted" (an expired row is replaced in place). Running twice leaves one row.
  - Exit codes: 0 granted / already held; 2 unknown email; 3 disabled user; 1 any unexpected error; argparse usage errors → 64 (`parser.error` overridden). Every failure prints one generic line, never a traceback or DSN.
  - Output shows only the user id, a masked email (`j***@e***.com`, helper in `app/core/text.py` with unit tests), `created_at` and `email_verified: yes/no`. The full email never appears on stdout or stderr, including on a database error (the CLI's engine uses `hide_parameters=True` and refuses `DB_ECHO=true`).
  - Works with `ADMIN_RBAC_API_ENABLED` off and in production; never imported from `app.main` (test: no route reaches it). No user creation, no password input, no env-var bootstrap, no "mark verified" path (D16).
  - `make grant-super-admin email=...` (quoted variable) and a runbook entry in `docs/deploy.md`: `docker compose -p org-admin-api run --rm --no-deps org-admin-api python -m app.cli grant-super-admin --email …` on the server only; the full Settings load, so `TRUSTED_PROXY_CIDRS` and `APP_ENV` must be set; sign up the account yourself and confirm you can log in before granting (pre-registration hijack, security consult finding 3); after USR-04, reset the password; never run with `DB_ECHO=true`.
  - Tests: exit codes 0/0/2/3/64/1, idempotency, expired-row replacement, no full email in captured stdout/stderr (incl. a forced database error), mask helper cases.
  - `# R4` marker (once ADM-004 lands the CLI writes an audit entry with actor `system:cli`).
- **Owner questions (non-blocking defaults):** refuse unverified accounts after USR-04 with `--allow-unverified`? (default: grant and print `email_verified: no`); read the email from stdin instead of argv? (default: argv, low risk).

## Data model / migration
None. Uses the existing `role_assignments` table and `uq_role_assignments_user_role_org` (NULLS NOT DISTINCT) from USR-02a.

## API contract
All Bearer class (R3 class 1), unauthenticated until ADM-002, mounted only when `ADMIN_RBAC_API_ENABLED` (refused in staging/production).

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/users/{user_id}/role-assignments` | query `app` (key, `APP_KEY_RE`, optional), `limit` ≤ 100, `offset` | 200 `Page[AssignmentRead]`, order `created_at desc, id` | 200; 404 user; 422 `validation_error` (bad `app`, bad paging, bad UUID) | `admin:users:read` |
| POST | `/api/v1/users/{user_id}/role-assignments` | `{role_id: uuid, org_id?: uuid \| null, expires_at?: aware datetime \| null}`, `extra="forbid"` | 201 `AssignmentRead` (new row, or the expired duplicate replaced in place) | 201; 404 user; 409 active duplicate or racing write; 422 `validation_error` (naive `expires_at`, extra field) or `unprocessable` with `details[].reason` ∈ `unknown_role`, `org_id_not_allowed`, `unknown_org`, `expires_in_past`, `expiry_not_allowed` | `admin:roles:assign` |
| DELETE | `/api/v1/users/{user_id}/role-assignments/{assignment_id}` | — | 204 | 204; 404 (unknown user, unknown assignment, another user's assignment); 409 last active super-admin | `admin:roles:assign` |

`AssignmentRead` = `{id: uuid, app: str, role_id: uuid, role_key: str, org_id: uuid | null, granted_by: uuid | null, expires_at: datetime | null, active: bool, created_at: datetime}`.

## Follow-ups (not in this diff)
- **FU1 — ADM-002:** the last-super-admin guard can be bypassed by `PATCH /api/v1/users/{id}` (`status=disabled`) and by deleting a user (cascade). Apply the same check under `lock_app("admin")` in those paths. Also turn the `# ADM-002 guard` markers into checks, and review `role_assignments` rows of the `admin` app written while the flag was on.
- **FU2 — USR-06:** `org_id` has no FK; the `FOR KEY SHARE` read closes the race inside a request, but an org deleted later leaves orphans. Add the FK with an orphan check (USR-02 FU3).
- **FU3 — ADM-004:** audit entries at every `# R4` marker (assign, replace, revoke); the replacement keeps `created_at`, so only the audit shows when it happened.
- **FU4 — USR-04:** decide whether unverified accounts count for the guard (A5).
- **FU5 — Orchestrator:** create USR-02f from the Split proposal; update ADM-002's dependencies and USR-02c's AC4.
