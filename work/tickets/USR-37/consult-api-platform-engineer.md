# Consult — api-platform-engineer — USR-37 (2026-11-26)

**Findings**
- Router `app/modules/rbac/assignments_router.py:45-58`: POST declares only 404 and 409 via `require_permission(errors=...)`; the 409 text (53-54) is wrong ("app disabled, user unverified or disabled"). Conditional-super 403 text from `admin_auth/deps.py:140-143` doesn't name `self_assignment_forbidden`. `require_permission` rejects 401/403 in `errors` (deps.py:128). 422 not in `errors`; generic 422 covered by `test_422_is_envelope_validation_error` (tests/test_openapi_contract.py:146).
- Service `_grant_locked` (`rbac/service.py:1664-1731`): bare `ConflictError` without details at 1709 (active duplicate), 1716 ("Assignment was changed concurrently", re-activating an expired row), 1730 (racing insert; also covers "user deleted meanwhile").
- 422s via `_assignment_unprocessable` (service.py:1479), `details=[{field, reason}]`: `unknown_role` (1591, 1595), `org_id_not_allowed` (1678 designated role; 1683 `org_mode=none`), `unknown_org` (1686), `expiry_not_allowed` (1690), `expires_in_past` (1694), **USR-38 `recipient_not_staff_super_admin` (1702, field `user_id`) — omitted by the ticket**.
- 403s: `admin_wildcard_required` (1746); `self_assignment_forbidden` (1748 own admin-app role; 1750 own designated super-admin role — not only admin-app now); `permission_denied` (missing `admin:roles:assign`).
- 404 `not_found` = unknown user; unknown role is 422.
- `_grant_locked` shared with other callers (service.py:1597, 1654) — added details invisible to them.
- Contract test `_EXPECTED_ERRORS[("post", _RA)] == {"404","409"}` (test_openapi_contract.py:344) checks only keys and the envelope `$ref`.
- Spec §5.15 Part 4 (spec-v1.md:338-339) still says "409 without details" and omits `recipient_not_staff_super_admin` — update.
- Precedent: `AppKeyTakenError` (errors.py:91-98) `details=[{"field","reason"}]`; USR-26 names the concurrent reason `changed_concurrently` (work/tickets/USR-26/ticket.md:21).

**Recommendations**
1. Concurrent reason `changed_concurrently` (matches USR-26), not `concurrent_change`.
2. 409s keep `code: conflict`; `details=[{"field": "role_id", "reason": "assignment_exists"}]` at 1709 and 1730; `[{"reason": "changed_concurrently"}]` at 1716 (alternative: `{reason}` only, as in the ticket). Additive.
3. Messages unchanged; clients map on `details[0].reason`.
4. 409 description: "Role already assigned (assignment_exists) or assignment changed concurrently (changed_concurrently)"; remove the wrong text.
5. Declare 403 and 422 explicitly: add an optional parameter to `require_permission` (cleaner, keeps `test_require_permission_rejects_bad_arguments` valid) or extend `responses` after the call; envelope `$ref`; 422 lists all 6 reasons.
6. 403 description names `permission_denied`, `admin_wildcard_required`, `self_assignment_forbidden` (own admin-app or designated super-admin role).
7. Tests: duplicate → `details[0].reason == "assignment_exists"`; `changed_concurrently` by monkeypatching `repo.save` on an expired duplicate; same for `add_assignment` for a racing `assignment_exists`; OpenAPI test: keys {201,401,403,404,409,422}, envelope refs, descriptions contain each reason and none of "disabled"/"unverified"; keep `_EXPECTED_ERRORS` for 404/409.
8. For USR-36: reason strings as constants in service.py (`REASON_ASSIGNMENT_EXISTS`, …); record codes (`not_found`, `self_assignment_forbidden`, `admin_wildcard_required`). Bulk `already_assigned` is not a failure (ADR-0029), so `assignment_exists` isn't used there.
9. R9: additive (status, code, message unchanged; admin-pan checks status 409); update spec §5.15 and the docs/consumers.md reason list.

**Risks**
- Both files are High paths — owner approval.
- OpenAPI diff (descriptions + new 403/422 entries) — admin-pan type regeneration sees new codes; harmless.
- Old message text kept.
- Racing-insert catch (1729) can also be a deleted user labelled `assignment_exists` — slightly inaccurate; FK vs unique distinction out of scope.

**Open questions for Ming (defaults)**
1. `changed_concurrently` (aligned with USR-26).
2. `assignment_exists` carries `field: role_id`; `changed_concurrently` reason only.
3. Declare `recipient_not_staff_super_admin` in the 422 list — yes ("exactly the real outcomes").
