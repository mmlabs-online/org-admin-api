# Consult — api-platform-engineer — USR-35 (2026-11-26)

**Findings**
- Users list: router `app/modules/users/router.py:27-34` (`email` Query 1–254, `status` enum) → `UserService.list` (`users/service.py:41`) → `UserRepository.list` (`users/repository.py:77-88`); one `conditions` list for count and page.
- `app` filter convention is by KEY: `Query(pattern=APP_KEY_RE)` (`rbac/assignments_router.py:37`; `audit/router.py:37` `app_key`) — matches spec and R9.
- Reason convention: `UnprocessableError(msg, details=[{"field","reason"}])` (`rbac/service.py:1479`, `:1591`); `unknown_role` already exists.
- Spec §5.15 Part 2 (spec-v1.md:360-366) fixes the 422 reasons; disabled apps are known values. USR-35 is the first list filter that validates existence (others filter and return empty).

**Recommendations**
1. Router: `app: str | None = Query(pattern=APP_KEY_RE)`, `role_id: uuid.UUID | None`; malformed → standard FastAPI 422 in the R6 envelope.
2. Existence checks in `UserService.list` (R1), through `RbacRepository` (as USR-18): app by key with no status filter; role by id.
3. Order: unknown `app` → `unknown_app` (field `app`); unknown `role_id` → `unknown_role` (field `role_id`); both exist but `role.app_id != app.id` → `role_not_in_app`; unknown app + role given → `unknown_app` first. One 422, not aggregated.
4. Repo: `app_id` / `role_id` params; one shared `active_assignment_predicate()` (also for USR-34) — ideally in the users repository (rbac is High); `EXISTS(... ra.user_id = User.id AND active ...)` in `conditions`.
5. Resolve key → id in the service so the repo compares ids only (no join to `apps`).
6. OpenAPI: declare 422 with `ErrorEnvelope`, list the three reasons; describe both params (`app` is the key; disabled apps accepted). Additive (R9).
7. `include=access` not narrowed by the filters.
8. Bundle with USR-34 only if the predicate helper lands in one PR; otherwise USR-35's plan notes USR-34 supplies it.

**Risks**
- Two extra indexed lookups only when filters are present.
- Possible role_assignments scan for the app-via-roles join — verify with EXPLAIN.
- 422 `details` shape must match exactly or admin-pan's display breaks.

**Open questions for Ming (defaults)**
- `role_id` given and `app` unknown → `unknown_app`.
- Empty-string `app` / `role_id` → malformed 422.
