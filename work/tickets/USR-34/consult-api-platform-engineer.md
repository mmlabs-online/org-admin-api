# Consult — api-platform-engineer — USR-34 (2026-11-26)

**Findings**
- `app/modules/users/router.py:26-41`: `list_users` → `Page[UserRead]`, params `email`, `status`; no `sort`; fixed order `created_at desc, id` (`repository.py:89`). USR-35 adds conditions at `repository.py:80-85`.
- `schemas.py:11-22`: `UserRead` is also the response of `GET /users/{id}` and PATCH — adding fields would leak `access` there.
- `Page[T]` generic, limit ≤ 100; 100 rows × cap 20 ≈ 2100 rows max.
- Spec §5.15 leaves to PLAN: absent vs `null` without `include`, and the 422 body for a bad `include`.

**Recommendations**
1. `UserListRead(UserRead)` with `access: list[AppAccess]` and `access_truncated: bool`; `UserRead` untouched; `response_model=Page[UserRead] | Page[UserListRead]` (anyOf), `response_model_exclude_unset=True`, build `UserListRead` only with `include=access`; never `exclude_none` (would drop `display_name: null`, `expires_at: null`).
2. Without `include` the fields are absent — byte-identical to today.
3. `include: list[IncludeField] = Query([])`, `IncludeField` StrEnum("access"): repeatable, extensible; unknown → standard 422 pointing at `include`; `include=` empty → 422; duplicates idempotent.
4. Schemas: `AccessRole {assignment_id, role_id, role_key, org_id | None, expires_at | None}`; `AppAccess {app, app_status, roles}` (reuse the app-status enum if one exists); nullable fields always present.
5. Order: apps by key, roles by `role_key`, app scope before org scope, then `org_id` (nulls first), `assignment_id`; cap via `row_number()` per user in that order, fetch cap+1, `access_truncated` if row 21 exists; group after the cap (document partial groups).
6. One shared `active_assignment_predicate()` next to the assignment model (USR-34/35); batch read taking `page_ids` → `dict[user_id, (rows, truncated)]`; users with no rows `[]` / `false`; router HTTP-only (R1); check whether touching rbac makes the review High.
7. Additive (R9), no version bump; closed `access` shape, later names (USR-27) additive.
8. No new permission (R3 unchanged); 401/403 tests in the route-class style.
9. Tests: query count via SQL event counter at page sizes 1 and 50; snapshot that no-`include` responses have no `access` key.

**Risks**
- Sharing `UserRead` leaks fields to other routes (avoided by 1).
- Union `response_model` gives two page types in generated SDKs — tell admin-pan.
- Window query cost with large pages — fine with `uq_role_assignments_user_role_org` leading `user_id` (p95 < 200 ms target).
- `now()` is the transaction start time.

**Open questions for Ming (defaults)**
1. Without `include`: absent.
2. `include=access` + USR-35 filters: show all the user's access (§5.15 proposal).
3. No access → `access: []`, `access_truncated: false`.
4. `include` repeatable, only `access` valid for now.
