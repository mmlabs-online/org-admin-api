# consult-api-platform-engineer — USR-02c

## Findings
- The contract is already fixed in `work/tickets/USR-02/plan.md` → "USR-02c". There are three routes, a `Page[AssignmentRead]` list, and the permissions `admin:users:read` and `admin:roles:assign`.
- `users_router` (`/users`, prefix `/users`) is mounted only under `ADMIN_USERS_API_ENABLED`. The new routes belong to the **rbac** flag, so they need their own router in `app/modules/rbac/` with prefix `/users`, mounted in `create_app` under `admin_rbac_api_enabled`. Both flags can be on at once. FastAPI accepts the two routers side by side as long as no path repeats.
- ADR-0003 §10 says every RBAC writer locks the app row first. `lock_app` takes a key, but a POST only carries `role_id`. A by-id variant, or resolving role → app key and then locking, is needed.
- The last-super-admin guard has a race. Two concurrent DELETEs of different super-admin assignments would each count 2 and both succeed. Locking the `admin` app row closes it.
- No email-masking helper exists in `app/`.
- `argparse` exits with code **2** on usage errors. That clashes with the ticket's "exit 2 = unknown email".

## Recommendations
- **Router (R1/R3):** add `app/modules/rbac/assignments_router.py` with `openapi_extra=permission(...)` on each route. Add a route → permission test copied from `test_rbac_apps_api.py:77`, and a test that every path returns 404 when the flag is off.
- **GET:** the optional `app` filter is checked against `APP_KEY_RE` (bad format → 422 `validation_error`). An unknown but well-formed key returns an empty page, the same as the `email` filter on `/users`. Unknown user → 404. Compute `active` in SQL with the database clock, like `_active_assignments`, never with Python's clock. Order by `created_at desc, id`.
- **POST order of checks:** user 404 → role 422 (`reason: unknown_role`, matching `default_role_id`) → lock the role's app and re-read the role → `org_id` 422 (`org_id_not_allowed` / `unknown_org`) → `expires_at` → 409.
  - The schema uses `extra="forbid"` and `AwareDatetime`, so a naive datetime gets the framework's 422 `validation_error`.
  - A past `expires_at` gets 422 `unprocessable` with reason `expires_in_past`, compared against the database's `now()`.
  - `org_id: null` on an `org_mode=required` app is valid (app scope, as join does).
- **Duplicates:** one atomic `INSERT … ON CONFLICT ON CONSTRAINT uq_role_assignments_user_role_org DO UPDATE SET expires_at, granted_by WHERE role_assignments.expires_at <= now() RETURNING`. No returned row means an active duplicate → 409. Wrap it in a savepoint and map `IntegrityError` → 409. A replaced row returns 201, as the plan says.
- **DELETE:** load the assignment scoped to `user_id` (another user's id → 404, no leak). Lock the app, re-read the assignment, then run the guard: 409 when the assignment is `admin:super-admin`, active and held by an active user, and no other such assignment exists. Second DELETE → 404. Add `# R4` markers and an unused `actor` parameter, as in 02b/02d.
- **CLI:** `app/cli.py` (or `app/cli/__main__.py`) using standard-library `argparse`, so no new dependency.
  - Override `parser.error` to exit 64 (`EX_USAGE`).
  - Exit codes: 0 for "granted" or "already held" (replacing an expired assignment counts as granted), 2 unknown email, 3 disabled user, 1 generic for unexpected errors with no traceback and no DSN printed.
  - Reuse the service's grant path (lock `admin` → upsert) in one transaction.
  - Output only the user id and a masked email, e.g. `j***@e***.com`. Put the helper in `app/core/text.py` and test it.
  - Add `make grant-super-admin email=...` (quoted). Add the runbook entry using `docker compose -p org-admin-api run --rm org-admin-api python -m app.cli …`, noting that the CLI loads the full Settings, so `TRUSTED_PROXY_CIDRS` is needed.
- **Size:** roughly router 60, schemas 45, service 140, repository 90, CLI 100, helper/main/Makefile 25 ≈ **460 production lines**, plus about 550 test lines. That is over ~400. Clean split: **02c-1** the API (~330 lines); **02c-2** the CLI, masking helper, Makefile target and runbook (~130 lines), sharing the service's grant method.

## Risks
- The routes are unauthenticated until ADM-002. The flag is refused in staging and production; keep it that way.
- `PATCH /users/{id}` (status `disabled`) can disable the last super-admin user, bypassing the guard. It is in another module and does not take the `admin` lock. This is a follow-up for ADM-002, not in this diff (R7).
- `org_id` has no foreign key until USR-06, so an org deleted between the check and the insert leaves an orphan. Accept and note for USR-06.
- An in-place replacement leaves `created_at` unchanged, so it is invisible in the list until audit lands (ADM-004).

## Open questions
1. Assigning to a **disabled user**, a **disabled app**, or an **unverified** account: allow (current default) or 422?
2. The CLI on an unverified account: grant with a warning (default, since D16 rules out a "mark verified" path), or a separate exit code?
3. Do you accept splitting into 02c-1 (API) and 02c-2 (CLI)?
