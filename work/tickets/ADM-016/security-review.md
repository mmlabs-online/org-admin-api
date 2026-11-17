## Attempt 1

Security review for ADM-016 (`git diff origin/main...HEAD`). I read the code only and did not run `make check` or pytest, as you asked.

**Findings**

No 🔴 blockers and no 🟡 should-fix items.

- 💭 **Query cost:** `/home/user/org-admin-api/app/modules/rbac/repository.py:581-602`
  - The count is one grouped query per request, not one per app. The list endpoint passes at most 100 page ids through `IN (...)` (`router.py:53`); detail, POST and PATCH pass a single id. There is no N+1.
  - The cost grows with the number of `role_assignments` rows joined to `roles` and `users`, which is fine at staff-only volume.
  - Fix (only if assignment volume grows): make sure `role_assignments(role_id)` and `roles(app_id)` are indexed. This is a follow-up, not part of this diff.
- 💭 **Read after write:** `/home/user/org-admin-api/app/modules/rbac/router.py:73,98`
  - POST and PATCH now run one extra read-only count after the service call, in the same session and transaction.
  - It does not change the audit entry (R4) or what gets written, and a count on a new app correctly returns 0 through `.get(app.id, 0)`. No action needed.

**Focus checks — all pass**

- **Authz unchanged:** the decorators are still `admin:apps:read` on list and detail and `admin:apps:write` on POST and PATCH. The diff only touches the handler bodies. No new route and no change of auth class (R3).
- **No PII:** the new field is a bare `int` (`ge=0`) with no user ids, emails or names. It counts only users whose status is `active` and whose assignments have not expired. It is visible only to staff who already have `admin:apps:read`.
- **Layering (R1):** router → `AppService.member_counts` (`service.py:336`) → `RbacRepository.member_counts`. The router only uses the ORM `App` as a type for `_app_read`. It runs no queries itself, the same as the existing `model_validate` pattern.
- **No breaking change:** `AppRead` and `AppFields` are used only in `rbac/router.py` and `rbac/schemas.py`. The public role catalog and other schemas do not use them, so `member_count` cannot leak onto public or app-credential routes. Splitting `AppRead` into `AppFields` + `AppRead` keeps the existing fields identical, so v1 compatibility holds (R9).
- **Injection:** none. The query is built with SQLAlchemy Core using bound UUID parameters.

**Follow-ups (out of scope)**
- Confirm the indexes on `role_assignments.role_id` and `roles.app_id` exist. The same query backs the ADM-015 dashboard.

**Verdict: PASS (security), with no blockers.**
