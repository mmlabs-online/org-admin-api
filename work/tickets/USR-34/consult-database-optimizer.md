# Consult — database-optimizer — USR-34 (2026-11-26)

**Findings**
- Only the users list changes: `app/modules/users/repository.py:77-93` (count + page; no access data today).
- `RoleAssignment` (`rbac/models.py:217-251`): `uq_role_assignments_user_role_org` leading `user_id`, indexes on `role_id`, `org_id` — `user_id = ANY(page_ids)` is an index scan; no migration.
- `Role` `uq_roles_app_id_key` (`models.py:176`), `apps.key` unique — joins by primary key.
- DB-clock expiry predicate exists at `rbac/repository.py:479` inside private `_active_assignments` (:466-480); USR-35 needs the same.
- `apps.status` (`models.py:114`): active / disabled.
- At most 2100 rows at limit 100 (cap 20 + 1 probe).

**Recommendations**
1. One batched query per page, only with `include=access`: 3 queries (count, page, access), constant in page size.
2. CTE selecting `ra.user_id, ra.id, ra.role_id, ra.org_id, ra.expires_at, a.key, a.status, r.key` with `row_number() OVER (PARTITION BY ra.user_id ORDER BY a.key, r.key, (ra.org_id IS NOT NULL), ra.org_id, ra.id)`, filter `ra.user_id = ANY(:ids) AND <active>`, outer `rn <= 21`.
3. `access_truncated = rows > 20` in Python, drop row 21; no `count(*) OVER`.
4. One shared helper `active_assignment_clause()` for USR-34 and USR-35, `func.now()` only (transaction start — keep test `expires_at` well away from it).
5. Access read in `users/repository.py` querying `RoleAssignment`/`Role`/`App` directly — stays off the rbac High path.
6. R1: router parses `include` as `Literal["access"]` (else 422); service: page → access → group by app key; separate `UserWithAccessRead` response model (R9, additive).
7. Query-count test with `before_cursor_execute`, same count at page size 1 and 5.
8. No index now; USR-35 uses `EXISTS` / `IN (subquery)` for distinct `total`.

**Risks**
- A plain `LIMIT` caps the whole result, not per user — use per-user `row_number`.
- `(org_id IS NOT NULL)` sorts false first (app scope first) — test it.
- Without the `ra.id` tiebreak order is unstable.

**Open questions for Ming (defaults)**
1. Without `include`, `access` / `access_truncated` absent (separate response model).
2. Order within the same role and scope: `org_id`, then `id`.
3. Tier Medium if the access read lives in the users repository.
