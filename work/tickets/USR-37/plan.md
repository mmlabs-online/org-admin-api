Status: READY

> READY on the defaults in `## Blocking questions for Ming` below. The spec leaves the exact names to PLAN
> ("Exact names at PLAN, aligned with the reason names USR-26 uses", spec §5.15 Part 4), so no question blocks the
> plan. The ticket is High (`app/modules/rbac/**`), so it stops at the plan-approval gate anyway: Ming confirms or
> overrides the four defaults there.

# Plan — USR-37: Single role-assignment POST — correct OpenAPI errors and add stable 409 reasons

## Scope summary
`docs/spec-v1.md` §5.15 Part 4 (owner, 2026-11-26, OQ77), lines 389-391:
> - Correct the OpenAPI responses of `POST /api/v1/users/{user_id}/role-assignments` to the real outcomes listed above
>   (404 `not_found`; 409 `conflict`; 422 reasons; 403 `admin_wildcard_required` and `self_assignment_forbidden`), and
>   make sure the deployed version publishes 404/409 (the next release after the fix; admin-pan regenerates its types
>   from it).
> - **Add stable `details.reason` values to the 409s** (owner's examples: `assignment_exists` for an active duplicate
>   or a racing insert, `concurrent_change` for a concurrent change), keeping `code: conflict`. Additive behaviour
>   change; it does not break admin-pan, which checks only status 409. Exact names at PLAN, aligned with the reason
>   names USR-26 uses for the same situation on role definitions.

"The real outcomes listed above" = spec line 338 (current contract, checked 2026-11-25): 404 `not_found` (unknown
user); 422 `unprocessable` with `unknown_role`, `org_id_not_allowed`, `unknown_org`, `expiry_not_allowed`,
`expires_in_past`; 403 `admin_wildcard_required`, `self_assignment_forbidden`; 409 `conflict` ("Role is already
assigned" for an active duplicate or a racing insert; "Assignment was changed concurrently"). Line 339: the current 409
text "names 'app disabled, user unverified or disabled' and `details[0].reason`, none of which the code produces — and
the 403 text does not name `self_assignment_forbidden`."

USR-26 (`work/tickets/USR-26/ticket.md:21-22`, BACKLOG, not yet in code): the concurrent reason on role definitions is
`changed_concurrently`, shape `details: [{"reason": …}]`.

Ticket out of scope: "Any other behaviour change on this endpoint; target-state checks (OQ78)."

Since the spec was written, USR-38 (merged) added one more real 422 on this POST: `recipient_not_staff_super_admin`
(field `user_id`, `service.py:1698-1703`), and widened `self_assignment_forbidden` to the caller's own designated
super-admin role (`service.py:1749-1750`). Declaring them follows from "the real outcomes" in AC 1; see question 3.

Decisions this plan makes (delegated to PLAN by the spec):
- D-a: concurrent-change reason = **`changed_concurrently`** (USR-26), not the owner's example `concurrent_change`.
- D-b: 409 `details` = `[{"reason": <reason>}]`, no `field` (ticket text, USR-26 shape).
- D-c: the reasons are module constants in `app/modules/rbac/service.py` that USR-36 imports.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | OpenAPI for the POST lists exactly the real 401/403/404/409/422 outcomes with the reasons above; no mention of checks the code does not make. | `tests/test_openapi_contract.py › test_assign_role_post_documents_real_outcomes` (response keys exactly `{201, 401, 403, 404, 409, 422}`, envelope `$ref` on every 4xx, each reason in its description, no "disabled"/"unverified" anywhere in the operation's responses). Existing `test_admin_declared_error_responses`, `test_requires_super_extension`, `test_every_error_response_references_envelope`, `test_x_request_id_documented_on_every_response` stay green. Locally: `make dev`, open `/openapi.json`, path `/api/v1/users/{user_id}/role-assignments` → `post` → `responses`. |
| 2 | Active duplicate → 409 `conflict` with `details[0].reason = assignment_exists`; concurrent change → 409 with the chosen concurrent-change reason. | `tests/test_rbac_assignments_api.py › test_assign_active_duplicate_409` (extended), `test_assign_racing_insert_409_assignment_exists`, `test_assign_expired_duplicate_race_409_changed_concurrently`, `test_assign_row_vanished_409_changed_concurrently`. |
| 3 | All other responses unchanged (additive, R9); existing tests stay green. | `make check` green with no edit to any existing assertion except the added `details` assertion in `test_assign_active_duplicate_409`. Diff review: 409 status, `code` and `message` strings unchanged; no change to 201/404/422/403 bodies; DELETE and GET routes untouched. |
| 4 | Tests assert each 409 reason and the OpenAPI responses of the route. | The tests named in rows 1-2, plus `test_assignment_conflict_reason_constants` pinning the constant values. |

## Ming verification after merge
- After the next release (Ming pushes `release/X.Y.Z`), `GET https://api.mmlabs.online/openapi.json` shows the POST's
  404/409/403/422 with the new descriptions, and `info.version` is the new version, so admin-pan can regenerate its
  types (spec Part 4: "make sure the deployed version publishes 404/409").

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/modules/rbac/service.py` | Add `REASON_ASSIGNMENT_EXISTS`, `REASON_CHANGED_CONCURRENTLY` constants and `_assignment_conflict()` helper next to `_assignment_unprocessable` (line 1479); pass `details` at lines 1614, 1709, 1716, 1730. Nothing else. | AC 2 (409 details) | **High** (`app/modules/rbac/**`) |
| `app/modules/rbac/assignments_router.py` | POST only (lines 45-58): new 404/409/422 descriptions via `errors=`, 403 description overridden on the returned kwargs; import the two reason constants for the 409 text. GET and DELETE untouched. | AC 1 (OpenAPI) | **High** (`app/modules/rbac/**`) |
| `tests/test_rbac_assignments_api.py` | Extend `test_assign_active_duplicate_409` (line 304); add 3 race tests and the constants test. | AC 2, 4 | no (low) |
| `tests/test_openapi_contract.py` | Add `test_assign_role_post_documents_real_outcomes`. `_EXPECTED_ERRORS` (line 344) unchanged. | AC 1, 4 | no (low) |
| `docs/spec-v1.md` | §5.15 Part 4 (line 391): one sentence recording the chosen names and the 422 list (question 4). The dated "Current contract" block (line 338) is a fact snapshot and stays as is. | Spec delegated the names to PLAN; keep spec in sync | no (low) |

Not touched: `app/modules/admin_auth/deps.py` (High, `app/modules/admin_auth/**`) — see Approach step 3 for why the
403 override stays in the router; `app/core/errors.py` (High) — no new error class; `docs/consumers.md` — it has no
reason list for this route (only an example at line 239), so nothing to update.

**Risk tier: High.** I could not run `python3 -I .github/scripts/risk_tier.py --files …` (no shell in this planner
session); by `.github/risk-tiers.yml`: `app/modules/rbac/service.py` and `app/modules/rbac/assignments_router.py`
match `high: "app/modules/rbac/**"`; `tests/**` and `docs/**` are `low`; PR tier = max = **high**. Never auto-merged
(`--auto-merge` does not apply); Ming approves this plan and merges the PR. The `appsec-engineer` review trigger
(`**/rbac*`) applies. The orchestrator / implementer should confirm with
`python3 -I .github/scripts/risk_tier.py --files app/modules/rbac/service.py app/modules/rbac/assignments_router.py tests/test_rbac_assignments_api.py tests/test_openapi_contract.py docs/spec-v1.md`
(expected output: `high`).

## Data model / migration
None.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/users/{user_id}/role-assignments` | unchanged `AssignmentCreate` `{role_id, org_id?, expires_at?}` | unchanged `AssignmentRead` (201) | 201; 401; 403 `permission_denied` / `admin_wildcard_required` / `self_assignment_forbidden`; 404 `not_found`; 409 `conflict` + `details: [{"reason": "assignment_exists" \| "changed_concurrently"}]` (**new details**, was `null`); 422 `validation_error` / `unprocessable` + `details[].reason` ∈ {`unknown_role`, `org_id_not_allowed`, `unknown_org`, `expiry_not_allowed`, `expires_in_past`, `recipient_not_staff_super_admin`} | `admin:roles:assign`; `admin:*` when the role is privileged (`x-requires-super: conditional`, unchanged) |

409 bodies after the change (message strings unchanged):
- `{"error": {"code": "conflict", "message": "Role is already assigned", "details": [{"reason": "assignment_exists"}]}}`
  — active duplicate (`service.py:1709`) and racing insert / `IntegrityError` on insert (`service.py:1730`).
- `{"error": {"code": "conflict", "message": "Assignment was changed concurrently", "details": [{"reason": "changed_concurrently"}]}}`
  — `IntegrityError` while re-activating an expired duplicate (`service.py:1716`) and the row-vanished guard in
  `assign` (`service.py:1614`, unreachable under the lock but on the same route).

OpenAPI descriptions (exact wording is the implementer's, but each must contain the listed tokens and nothing about
app/user status or email verification):
- 403: `permission_denied` (missing `admin:roles:assign`), `admin_wildcard_required` (privileged role without
  `admin:*`), `self_assignment_forbidden` (the caller's own `admin`-app role or the app's designated super-admin role).
- 404: "Unknown user (not_found); an unknown role is 422 unknown_role".
- 409: `code=conflict`, `details[0].reason`: `assignment_exists` (role already actively held in this scope, or a racing
  insert), `changed_concurrently`.
- 422: `validation_error` (malformed body) or `unprocessable` with the six reasons above.

Stable constants for USR-36 (in `app/modules/rbac/service.py`, public names, `typing.Final`):
```python
REASON_ASSIGNMENT_EXISTS: Final = "assignment_exists"
REASON_CHANGED_CONCURRENTLY: Final = "changed_concurrently"
```
Codes USR-36 maps per item are the existing envelope codes (`not_found`, `self_assignment_forbidden`,
`admin_wildcard_required`, `conflict`, `unprocessable`); USR-37 adds no new error code. The 422 reason strings stay as
literals in `_grant_locked` (no constants: out of this ticket's minimal scope; USR-36 may hoist them with its split).

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_rbac_assignments_api.py › test_assign_active_duplicate_409` (extend, both `org_scope` params) | AC 2: second POST → 409, `code == "conflict"`, `message == "Role is already assigned"`, `details == [{"reason": "assignment_exists"}]`; total still 1. |
| `tests/test_rbac_assignments_api.py › test_assign_racing_insert_409_assignment_exists` | AC 2: `monkeypatch.setattr(RbacRepository, "add_assignment", raise IntegrityError(...))` (precedent: `tests/test_app_credential_auth.py:382`) → 409 `conflict`, `details == [{"reason": "assignment_exists"}]`. |
| `tests/test_rbac_assignments_api.py › test_assign_expired_duplicate_race_409_changed_concurrently` | AC 2: expired row via `assign(db_session, user, viewer, expires_at=_past())` (as line 323), `monkeypatch.setattr(RbacRepository, "save", raise IntegrityError(...))` → 409, `message == "Assignment was changed concurrently"`, `details == [{"reason": "changed_concurrently"}]`. Implementer first greps that `RbacRepository.save` is not called earlier on this request path. |
| `tests/test_rbac_assignments_api.py › test_assign_row_vanished_409_changed_concurrently` | AC 2 (line 1614): `monkeypatch.setattr(RbacRepository, "get_assignment_row", async → None)` → 409 `details == [{"reason": "changed_concurrently"}]`. |
| `tests/test_rbac_assignments_api.py › test_assignment_conflict_reason_constants` | AC 4 / USR-36 contract: `REASON_ASSIGNMENT_EXISTS == "assignment_exists"`, `REASON_CHANGED_CONCURRENTLY == "changed_concurrently"`. |
| `tests/test_openapi_contract.py › test_assign_role_post_documents_real_outcomes` | AC 1: `op = paths["/api/v1/users/{user_id}/role-assignments"]["post"]`; `set(op["responses"]) == {"201","401","403","404","409","422"}`; every 4xx `content.application/json.schema == {"$ref": REF}`; 403 desc ⊇ {`permission_denied`, `admin_wildcard_required`, `self_assignment_forbidden`}; 404 desc ∋ `not_found`; 409 desc ⊇ {`conflict`, `assignment_exists`, `changed_concurrently`}; 422 desc ⊇ {`validation_error`, `unknown_role`, `org_id_not_allowed`, `unknown_org`, `expiry_not_allowed`, `expires_in_past`, `recipient_not_staff_super_admin`}; `"disabled"` and `"unverified"` appear in no response description of the operation. Use literal strings, not the constants (the test pins the public contract). |

Build `IntegrityError` as `IntegrityError("INSERT …", {}, Exception("duplicate"))`. Tests use the existing
`rbac_client` (`admin:*`) fixture; no new fixtures.

Not added (already covered, unchanged): 401/403/404/422 behaviour tests for this POST.

## Approach
1. **Service (R1, R6).** In `app/modules/rbac/service.py` next to `_assignment_unprocessable` (line 1479), add the
   two `Final` constants and
   `def _assignment_conflict(message: str, reason: str) -> ConflictError: return ConflictError(message, details=[{"reason": reason}])`.
   Replace the four bare raises: line 1614 and 1716 → `_assignment_conflict("Assignment was changed concurrently",
   REASON_CHANGED_CONCURRENTLY)`; lines 1709 and 1730 → `_assignment_conflict("Role is already assigned",
   REASON_ASSIGNMENT_EXISTS)` (keep `from exc` on 1716/1730). Messages unchanged. No other change in `_grant_locked`:
   USR-36's split (`_check_grant_request` / per-user upsert) is **not** done here. `bootstrap_super_admin` (line 1654)
   also calls `_grant_locked`; it never reaches the active-duplicate branch (checked at 1649-1652) and the CLI shows
   only the message, so the added `details` are invisible to it. `ConflictError` is not in `DENIED_ERRORS`
   (`errors.py:102`), so audit behaviour is unchanged (R4: 409 writes nothing, as today).
2. **Router 404/409/422.** In `assignments_router.py` POST, pass `errors={404: …, 409: …, 422: …}` to
   `require_permission`. 422 is already accepted by `errors` (precedent `rbac/router.py:150`, `audit/router.py:86`);
   because the description is not one of `_GENERIC_DESCRIPTIONS`, `app/core/openapi.py:39` keeps it, so it must name
   `validation_error` itself. Build the 409 text from the two imported constants (router importing service constants
   is within R1). Keep `_USER_404` for GET and DELETE unchanged.
3. **Router 403 — mechanism.** `require_permission` raises on 401/403 in `errors` (`admin_auth/deps.py:128`) and, with
   `requires_super`, writes a fixed 403 text (deps.py:140-143) that omits `self_assignment_forbidden`. Chosen: build
   the kwargs once at module level, `_ASSIGN_KWARGS = require_permission(…)`, then
   `_ASSIGN_KWARGS["responses"][403] = {"description": _ASSIGN_403}`, and use `**_ASSIGN_KWARGS` in the decorator.
   Safe: `require_permission` returns a fresh `responses` dict per call (asserted by
   `test_require_permission_rejects_bad_arguments`, lines 425-431, and `test_schema_generation_does_not_mutate_shared_dicts`).
   The text keeps `admin_wildcard_required` (required by `test_requires_super_extension`, line 403). Rejected
   alternative: a new `forbidden=` parameter on `require_permission` — cleaner long term, but it edits
   `app/modules/admin_auth/deps.py` (High path, every admin route) for one route; a follow-up if a second route needs it.
4. **Docs.** `docs/spec-v1.md` line 391: append "PLAN (USR-37): `assignment_exists` and `changed_concurrently`,
   `details: [{reason}]`; the 422 list also includes `recipient_not_staff_super_admin` (USR-38)." (question 4).
5. **Tests**, then `make check`.

Rules: R1 (service raises `ConflictError`, router only declares docs), R6 (same envelope and codes; `details` is a list
of `{reason}`, no values), R7 (DELETE's 409 at `service.py:1789` and GET's 404 text are not touched → follow-ups),
R8 (tests above; `make check`), R9 (additive: `details` goes from `null` to a list; status, `code`, `message`
unchanged; OpenAPI gains 422 and changes descriptions only — the 403 was already declared), R3 unchanged (Bearer,
`admin:roles:assign`). Expected diff: ~25 lines app code, ~90 lines tests, 1 line docs.

## Specialist input used
`consult-api-platform-engineer.md` (USR-37):
- Adopted: rec 1 `changed_concurrently`; rec 3 messages unchanged; rec 4 409 text and removal of the wrong text; rec 5
  declare 403 and 422 (chose the "extend `responses` after the call" variant, see Approach 3); rec 6 403 names three
  codes incl. designated super-admin; rec 7 tests (monkeypatch `save` / `add_assignment`) and keep `_EXPECTED_ERRORS`;
  rec 8 constants in `service.py` with the `REASON_*` names; finding on `recipient_not_staff_super_admin` (question 3);
  finding that `_grant_locked` is shared (Approach 1).
- Adjusted: rec 2 — `assignment_exists` without `field: role_id` (question 2): the ticket and USR-26 specify
  `[{reason}]`, and the conflict is on the (user, role, org) triple, not on `role_id` alone.
- Adjusted: rec 9 / finding 11 — spec update limited to Part 4 (line 391); the "Current contract" block at line 338 is
  a dated snapshot. `docs/consumers.md` has no reason list to update.
- Added: line 1614 also gets `changed_concurrently` (same route, same situation).
`work/tickets/USR-36/consult-*.md`: read for the shared refactor only. Their `_grant_locked` split
(`_check_grant_request` / `_upsert_locked`, software-architect, database-optimizer, identity-access-engineer) is
**rejected for USR-37** (orchestrator: keep USR-37 minimal); USR-36 does it and reuses the constants. Software-architect
rec 3 (lock timeout → 409 `changed_concurrently`) matches the chosen name.

## Split proposal
None (size S, ~115 changed lines).

## Blocking questions for Ming
Each has a default; the plan is written on the defaults. Confirm or override at the plan-approval gate.
1. **Concurrent-change reason name:** `changed_concurrently` (aligned with USR-26 and USR-36's architect consult) or
   your example `concurrent_change`? **Default: `changed_concurrently`.** Override = one constant value plus test
   literals.
2. **`field` in the 409 details:** `[{"reason": "assignment_exists"}]` only, or `[{"field": "role_id", "reason":
   "assignment_exists"}]` like `app_key_taken`? **Default: reason only** for both reasons (ticket text, USR-26 shape;
   clients map on `details[0].reason` either way).
3. **Declare `recipient_not_staff_super_admin`** (USR-38, field `user_id`) in the 422 list, although the ticket's list
   predates USR-38? **Default: yes** — AC 1 says "exactly the real outcomes", and the code returns it.
4. **Spec §5.15 Part 4 text:** record the chosen names and the six 422 reasons in one sentence at line 391 in this PR?
   **Default: yes**, Part 4 only; the dated "Current contract" block stays as is.

## Blocking questions
None that block (see `## Blocking questions for Ming`; all have defaults and the spec delegates the names to PLAN).

## Assumptions
- USR-26 (BACKLOG) will use the same `changed_concurrently` string; if it later wants its own constants, it may move
  both to a shared place (e.g. `app/modules/rbac/reasons.py`) — not needed now.
- **Hotspot:** USR-36 (PLANNING, same batch) edits `app/modules/rbac/service.py` `_grant_locked` (lines 1664-1731)
  and rbac routers; it is planned after this ticket and must rebase onto USR-37. USR-35 (PLANNING) is in the users
  module, no shared file expected. No open IN_PROGRESS/REVIEW/DONE ticket edits these files.
- The racing-insert `IntegrityError` at line 1730 can also be a user deleted meanwhile (FK violation); it is labelled
  `assignment_exists`, as the spec says ("a racing insert"). Distinguishing FK from unique violations is out of scope.
- The single POST's audit behaviour does not change (409 writes no row, `ConflictError` not in `DENIED_ERRORS`).
- Adding `422` with a custom description replaces FastAPI's auto "Validation Error" entry for this route; the
  description names `validation_error`, so `_VALIDATION_DESCRIPTION` is not needed here.
- Follow-ups (not in this diff, R7): DELETE `…/role-assignments/{assignment_id}` 409 "Assignment was changed
  concurrently" (`service.py:1789`) has no reason; a `forbidden=` parameter on `require_permission` if more routes need
  a custom 403 text; GET's `_USER_404` text could name `not_found`.
