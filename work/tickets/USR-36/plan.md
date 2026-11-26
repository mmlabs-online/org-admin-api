Status: READY

> READY on the defaults in `## Blocking questions for Ming` (Q1–Q16). The ticket is **High** (`app/modules/rbac/**`,
> Gate `plan-approval`), so it stops here for Ming's approval anyway and is never merged by an agent. Batch 2026-11-26-2.
> Built **after USR-37** (READY, merged first): this plan uses USR-37's `REASON_ASSIGNMENT_EXISTS`,
> `REASON_CHANGED_CONCURRENTLY` and `_assignment_conflict()` in `app/modules/rbac/service.py`.
> ⚠️ **Ticket vs ADR conflict (Q1):** ADR-0029 §3 (USR-38 amendment) and ADR-0030 §4 require a request-level 422
> `super_admin_role`; the ticket Scope (line 28) and AC 2 (line 39) omit it. The ADR wins; this plan adds it to scope
> and AC 2. The orchestrator should add it to `ticket.md` (the planner edits only this file).

# Plan — USR-36: Bulk-assign one role to up to 100 users (`assignment-batches`)

## Scope summary
Spec `docs/spec-v1.md` §5.15 Part 3 (lines 368-387), quoted:
- Line 369: "`POST /api/v1/apps/{key}/roles/{role_id}/assignment-batches` … It answers **200**, not 201: an R9 exception
  (D41 …). R3 class 1 (Bearer)." Line 372: "**Permission:** `admin:roles:assign`."
- Line 370: "**Body:** `{user_ids: [uuid] (1..100, unique), org_id?, expires_at?}`; `org_id` and `expires_at` apply to
  every user."
- Line 371: "**Response 200 (always, even if every user is `failed`)** `{batch_id, results: [{user_id, status: created |
  unchanged | failed, assignment_id?, expires_at?, error?: {code, message}}]}`, one result per requested user, in
  request order. `created` and `unchanged` carry `assignment_id` and `expires_at`; `failed` carries `error`."
- Lines 373-377: "**Request-level errors** — nothing is written: 401 …; **403** `permission_denied` …; **403**
  `admin_wildcard_required` — the role holds an `is_admin` permission and the caller lacks `admin:*` …; **404** unknown
  app, unknown role, or a role of another app; **422** `unprocessable`: bad body (`user_ids` empty, duplicated or more
  than 100, malformed ids, unknown fields); `admin_app_role` (any role of the `admin` app, `admin:super-admin`
  included; field `role_id`); and … `org_id_not_allowed`, `unknown_org`, `expires_in_past` — as **one** request-level
  422 with the single POST's reasons."
- Lines 378-381: "**Per-user outcomes**, by reusing `AssignmentService`'s grant logic, not a copy: `created` — a new
  assignment, or an **expired** duplicate re-activated in place with its old `assignment_id` …; `unchanged` — the user
  already holds the role actively in that scope, **even with a different `expires_at`**: the existing assignment is not
  touched …; the result returns its `assignment_id` and `expires_at` …; `failed` — `not_found` (unknown user) and, once
  USR-25 lands, `self_assignment_forbidden` …; any other rule the single POST applies per user."
- Line 382: "**Target state:** exactly as the single POST today — disabled users, users with an unverified email and
  disabled apps are accepted."
- Line 383: "**one transaction and one `lock_app` for the whole batch**, **one savepoint per user** …, user ids
  processed in sorted order (results still in request order); same lock order as the single POST (app → org → rows)
  … An unexpected error rolls back the whole batch (5xx, nothing written). Documented in OpenAPI."
- Line 384: "**Audit (R4):** identical to the single POST, per user — one `role_assignment.granted` row per `created`
  result, in the same transaction; a per-user refusal writes a `denied` row exactly when the single POST would
  (`self_assignment_forbidden`; a request-level `admin_wildcard_required` writes one, as the single POST does);
  `unchanged`, `not_found` and 422s write nothing. **`batch_id` = the request's `request_id`**."
- Line 385: "`AuditLogger.record_denied` / `record_failure` roll back the whole pending transaction and commit the row
  alone …, so they cannot be called in the middle of a batch as they are. The USR-36 plan must solve this without
  changing what a `denied` row contains."
- Line 386: "**Limits:** 100 users per request (owner). No extra rate limit (Bearer admin route)."
- Line 393 (out of scope): "bulk revoke …; tightening the target-state rules (OQ78 …). Also not proposed: bulk across
  several roles or apps, CSV import, assigning `admin`-app roles in bulk, notifying users."

ADR-0029 §3: "422 bad body, `admin_app_role` (any `admin`-app role), `super_admin_role` (the app's designated
super-admin role, ADR-0030; USR-36 implements it)". ADR-0030 §4: "The bulk route (ADR-0029) refuses it at request level
with 422 `super_admin_role`." Spec §8 OQ81 (line 559): "never through the §5.15 bulk route (request-level 422, like
`admin_app_role`)". → **`super_admin_role` is in scope** (Q1).

ADR-0029 §4-§6: one transaction / one `lock_app` / savepoint per user / sorted ids; "`batch_id` = `request_id` … no
schema change"; "The grant rules are reused from `AssignmentService`, not copied."

Owner (ticket Scope reference): "The API owns all business rules; reuse the existing single-assignment logic, do not
duplicate it."

`CLAUDE.md` R9 / R6 (D41, ADR-0029): `POST /api/v1/apps/{key}/roles/{role_id}/assignment-batches` "answers 200 with
`{batch_id, results[]}`, per-item results, not 201". R3 class 1 (Bearer + `require_permission`).

Reading of "nothing is written" (line 373) vs line 384: no assignment and no `granted` row is written on any
request-level error; the only rows a request-level error writes are the `denied` rows line 384 asks for on 403
`admin_wildcard_required` (same as ADR-0029 §3 vs §6). `work/decisions.md`: nothing on bulk assignment.

## Acceptance criteria → verification
Tests in `tests/test_rbac_assignment_batches_api.py` unless another file is named. "Writes nothing" = assignment count
and audit-row count unchanged (asserted with `make_strict_client`, which commits/rolls back like production).

| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Happy path and mixed created / unchanged / failed results, **in request order**, with `batch_id` equal to the audit rows' `request_id`; idempotent retry → all `unchanged` with ids and expiry; active holder with a different `expires_at` stays unchanged; expired duplicate → `created` with its old id; all-failed → 200; disabled / unverified users and a disabled app accepted. | `test_batch_all_created_200`, `test_batch_mixed_results_in_request_order`, `test_batch_id_equals_audit_request_id`, `test_batch_retry_all_unchanged`, `test_batch_active_holder_other_expiry_unchanged`, `test_batch_expired_duplicate_created_with_old_id`, `test_batch_all_failed_200`, `test_batch_disabled_unverified_users_and_disabled_app_created`, `test_batch_self_non_privileged_role_created`. |
| 2 | Request-level refusals, each writing nothing: 404 unknown app / unknown role / role of another app; 422 body (0 or > 100 ids, duplicates → `duplicate_user_ids`, bad UUID, unknown field, naive `expires_at`); 422 `admin_app_role` (incl. `admin:super-admin`); **422 `super_admin_role`** (added, Q1); 422 `org_id_not_allowed` / `unknown_org` / `expires_in_past`; 403 `permission_denied`; 403 `admin_wildcard_required` (writes only the `denied` rows of AC 4); 401; 411 / 413 / 415 (Q7). | `test_batch_404_unknown_app`, `test_batch_404_unknown_role`, `test_batch_404_role_of_other_app`, `test_batch_admin_app_unknown_role_404`, `test_batch_body_422` (parametrized), `test_batch_duplicate_user_ids_422`, `test_batch_admin_app_role_422` (parametrized `admin:auditor`, `admin:super-admin`), `test_batch_super_admin_role_422`, `test_batch_org_id_not_allowed_422`, `test_batch_unknown_org_422`, `test_batch_expires_in_past_422`, `test_batch_permission_denied_403`, `test_batch_admin_wildcard_required_403`, `test_batch_body_411_413_415`; 401 by the registry sweep `tests/test_route_auth_classes.py › test_every_bearer_route_401_without_token` / `_with_cookie_only`. |
| 3 | The single POST keeps its current behaviour and the batch reuses its grant logic (no duplicated rule code); existing single-assign tests stay green. | `make check` green with **no edit** to `tests/test_rbac_assignments_api.py`, `tests/test_audit_denied.py` existing cases, `tests/test_rbac_super_admin_role.py`, `tests/test_rbac_locking.py`. `test_batch_reuses_single_grant_logic` (spy on `AssignmentService._grant_user_locked` and `_check_grant_request`: called once per existing user / once per batch). Diff review: org / expiry / privileged / self / recipient / upsert rules exist once, in the helpers of Approach step 2. |
| 4 | One `role_assignment.granted` row per created assignment (renewal included), none for `unchanged` / `not_found` / 422; `denied` rows exactly when the single POST writes them, without rolling back the batch's other rows. | `test_batch_audit_one_granted_row_per_created`, `test_batch_admin_wildcard_required_403` (one `denied` row per **existing** requested user, same columns as the single POST's row; unknown ids get none — Q2), `test_batch_mid_batch_denied_keeps_other_grants` (stubbed per-user `SelfAssignmentForbiddenError`, Q3), `tests/test_audit_denied.py › test_denied_writes_one_row[batch_wildcard]` (new SITES entry), `tests/test_audit_logger.py › test_record_denied_in_tx_*`; `tests/test_audit_coverage.py › test_success_writes_one_row` / `test_failure_writes_no_row` for the new route. |
| 5 | A concurrent single assign on the same app neither deadlocks nor is lost; a stuck app lock gives the chosen timeout answer (Q5). | `tests/test_rbac_assignment_batches_concurrency.py › test_batch_and_single_assign_same_user_one_wins`, `test_batch_and_single_assign_other_users_both_win`, `test_batch_lock_timeout_409_nothing_written`. |
| 6 | Unexpected error rolls back the whole batch (5xx, nothing written). | `test_batch_unexpected_error_rolls_back_all` (`make_strict_client`, `_record_grant` raises `RuntimeError` on the 2nd user → 500, zero new assignments, zero new rows). |
| 7 | Lock is short: preloads, bounded query count. | `test_batch_query_count_bounded`: per-user statement delta is constant (statements(10 users) − statements(1 user) == 9 × (statements(2) − statements(1))), ≤ 8 statements per created user, exactly one `users … = ANY` select, one `role_assignments … = ANY` select, one `FOR NO KEY UPDATE` on `apps`. |
| 8 | Route registered in `tests/test_route_auth_classes.py`; OpenAPI documents the transaction / lock behaviour and all codes. | `tests/test_route_auth_classes.py` BEARER entry (all class tests incl. CORS matcher, sweep); `tests/test_openapi_contract.py › test_assignment_batches_documented` (responses exactly `{200, 401, 403, 404, 409, 411, 413, 415, 422}`, envelope `$ref` on every 4xx, description mentions `one transaction`, `lock`, `savepoint`, `sorted`, `5xx`; 422 text lists `validation_error`, `duplicate_user_ids`, `admin_app_role`, `super_admin_role`, `org_id_not_allowed`, `unknown_org`, `expires_in_past`; requestBody schema has `user_ids` `minItems 1`, `maxItems 100`); `_SUPER` and `_EXPECTED_ERRORS` entries (`test_requires_super_extension`, `test_admin_declared_error_responses`). Locally: `make dev`, `/docs`, operation under tag `apps`. |

## Ming verification after merge
- After the next release: `GET https://api.mmlabs.online/openapi.json` lists
  `POST /api/v1/apps/{key}/roles/{role_id}/assignment-batches` with 200 and the codes above (admin-pan PAN-033
  regenerates its types).
- One real bulk assign from admin-pan through Cloudflare on staging/production answers 200 (not 411): confirms the
  browser → Cloudflare → origin path keeps `Content-Length` (Q7 risk). Then `GET /api/v1/audit-logs` shows the
  `granted` rows; their `request_id` equals the response's `batch_id`.

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/modules/rbac/service.py` | Add `GrantOutcome` StrEnum; split `_grant_locked` (lines 1664-1731) into `_check_grant_request` + `_grant_user_locked`, `_grant_locked` becomes a thin wrapper with **unchanged signature and behaviour**; split `_guard_privileged` (1733-1750) into `_require_privileged_grant` + `_forbid_self` (revoke keeps the combined `_guard_privileged`); add `AssignmentService.assign_batch` and small private helpers (`_batch_item_failed`, `_record_batch_wildcard_denied`). | AC 1-7; owner "reuse, do not duplicate" | **High** (`app/modules/rbac/**`) |
| `app/modules/rbac/repository.py` | Add `ASSIGNMENT_BATCH_LOCK_TIMEOUT = "5s"`, `set_lock_timeout(value)`, `existing_user_ids(ids) -> set[UUID]`, `find_assignments(user_ids, role_id, org_id) -> dict[UUID, RoleAssignment]` (`= ANY`, `IS NOT DISTINCT FROM`, `populate_existing`), `savepoint()` (returns `session.begin_nested()`), static `is_lock_timeout(exc) -> bool` (SQLSTATE `55P03`). Nothing existing changes. | AC 5, 7; R1 (service never touches the session) | **High** |
| `app/modules/rbac/schemas.py` | Add `ASSIGNMENT_BATCH_MAX_USERS = 100`, `AssignmentBatchCreate`, `BatchItemStatus`, `AssignmentBatchError`, `AssignmentBatchItem`, `AssignmentBatchRead` (after `AssignmentRead`, line 308). | API contract | **High** |
| `app/modules/rbac/router.py` | New route `POST /{key}/roles/{role_id}/assignment-batches` after `replace_role_permissions` (~line 401+); body via `read_json_body` (16 KiB cap), hand-written `requestBody` (`inline_schema`), `status_code=200`, `require_permission("admin:roles:assign", requires_super="conditional", …)`, OpenAPI description. Imports only. | AC 2, 8; R3, R9 | **High** |
| `app/modules/audit/service.py` | Add `AuditLogger.record_denied_in_tx(...)` (same signature and row content as `record_denied`, never commits / rolls back, raises on error). `record_denied` / `record_failure` unchanged. | AC 4; spec line 385 | no (medium) |
| `tests/test_rbac_assignment_batches_api.py` | New: API tests of AC 1-4, 6-8. | R8 | no (low) |
| `tests/test_rbac_assignment_batches_concurrency.py` | New: committed-data race and lock-timeout tests (pattern `tests/test_org_member_remove_concurrency.py`). | AC 5 | no (low) |
| `tests/test_route_auth_classes.py` | BEARER entry `("POST", f"{V1}/apps/{{key}}/roles/{{role_id}}/assignment-batches"): "admin:roles:assign"` (near line 124-ff apps entries). | AC 8; R3 registry | no (low) |
| `tests/test_audit_coverage.py` | `AUDITED` entry (`{"role_assignment.granted"}`, success = batch of one user, failure = unknown role → 404); `_SUCCESS_STATUS_BY_ROUTE` → 200 (line 637); count `34` → `35` (line 668). | AC 4; existing coverage test fails otherwise | no (low) |
| `tests/test_audit_denied.py` | New SITES case `_batch_wildcard` (one existing user, `is_admin` role, `admin:roles:assign` only → 403, one row, target `("user", uid)`, `changes={"reason": "admin_wildcard_required", "role_key": …}`), modelled on `_assign_org_scoped` (line 238). | AC 4 | no (low) |
| `tests/test_audit_logger.py` | `test_record_denied_in_tx_same_content_as_record_denied`, `test_record_denied_in_tx_keeps_pending_work`, `test_record_denied_in_tx_system_actor_writes_nothing`. | AC 4 helper | no (low) |
| `tests/test_openapi_contract.py` | `_EXPECTED_ERRORS` entry `{"404", "409"}`, `_SUPER` entry `"conditional"` (lines 315-373); new `test_assignment_batches_documented`. | AC 8; `test_requires_super_extension` is exhaustive | no (low) |
| `docs/spec-v1.md` | §5.15 Part 3 line 377: add `super_admin_role` (ADR-0029/0030); one sentence "PLAN (USR-36): …" recording Q5/Q6/Q7 defaults. | Keep spec in sync with ADR and PLAN choices | no (low) |

Not touched: `app/main.py` (the route goes into the already-included `rbac/router.py`), `app/core/**` (no new error
class: `UnprocessableError`, `ConflictError`, `NotFoundError` reused; no new settings — the cap and timeout are module
constants, like `MAX_INFLIGHT_UPLOADS`), `app/modules/admin_auth/deps.py`, `app/modules/releases/body.py` (imported
as-is, precedent `app/modules/usage/app_router.py:22`), migrations (none).

**Risk tier: High** (`app/modules/rbac/**` in `.github/risk-tiers.yml` `high:`; `app/modules/audit/service.py` is
default/medium; `tests/**`, `docs/**` low; PR tier = max = high). Never auto-merged; Ming approves this plan and merges.
`appsec-engineer` review applies (rbac/auth). Confirm with
`python3 -I .github/scripts/risk_tier.py --files app/modules/rbac/service.py app/modules/rbac/repository.py app/modules/rbac/schemas.py app/modules/rbac/router.py app/modules/audit/service.py tests/test_rbac_assignment_batches_api.py docs/spec-v1.md`
(expected: `high`).

## Data model / migration
None. `batch_id` = existing `audit_logs.request_id` (ADR-0029 §5). The unique constraint
`uq_role_assignments_user_role_org` already has `postgresql_nulls_not_distinct=True` (`rbac/models.py:222-228`), so an
app-scope duplicate also fails at the DB (database-optimizer risk 1 checked).

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/apps/{key}/roles/{role_id}/assignment-batches` | `AssignmentBatchCreate` `{user_ids: [uuid] (1..100, unique), org_id?: uuid \| null, expires_at?: aware datetime \| null}`, `extra=forbid`; `Content-Type: application/json`, `Content-Length` ≤ 16 KiB | 200 `AssignmentBatchRead` `{batch_id: uuid, results: [AssignmentBatchItem]}` | 200; 401; 403 `permission_denied` / `admin_wildcard_required`; 404 `not_found`; 409 `conflict` `details=[{"reason": "changed_concurrently"}]` (lock timeout, Q5); 411 `length_required`; 413 `payload_too_large`; 415 `unsupported_media_type`; 422 `validation_error` (shape) / `unprocessable` with `details=[{field, reason}]`, reasons `duplicate_user_ids` (field `user_ids`), `admin_app_role`, `super_admin_role` (field `role_id`), `org_id_not_allowed`, `unknown_org` (field `org_id`), `expires_in_past` (field `expires_at`), `incomplete` (field `body`, from `read_json_body`) | `admin:roles:assign`; `admin:*` when the role holds an `is_admin` permission (`x-requires-super: conditional`, `super_when="Role holding an is_admin permission"`) |

`AssignmentBatchItem` — every key always present (null when not applicable), so typed clients need no optional keys:
`{user_id: uuid, status: "created" | "unchanged" | "failed", assignment_id: uuid | null, expires_at: datetime | null,
error: {code: str, message: str} | null}`. `created` / `unchanged`: `assignment_id` set, `expires_at` = the row's value
(null = never expires), `error` null. `failed`: `assignment_id` and `expires_at` null, `error` set. Per-item codes are
the single POST's envelope codes: `not_found` ("User not found"), `self_assignment_forbidden` (USR-25 / stub),
`unprocessable` (`recipient_not_staff_super_admin`, unreachable here), `conflict` (racing insert / renew, USR-37
messages). Fixed messages only: no email, status or other user state (security rec 3).

Request-level check order (matches the single POST where both have the rule):
auth (401/403 `permission_denied`) → body (411 → 415 → 413 → 422 `validation_error`) → 422 `duplicate_user_ids` →
404 app → 404 role → 422 `admin_app_role` → 422 `super_admin_role` → 422 `org_id_not_allowed` → 422 `unknown_org` →
422 `expires_in_past` → 403 `admin_wildcard_required` → per user.

OpenAPI description (wording is the implementer's; must state): one transaction and one app lock for the whole batch,
held until commit, bounded by a 5 s lock wait (→ 409 `changed_concurrently`); one savepoint per user, so a per-user
failure undoes only that user; users processed in sorted id order, results in request order; an unexpected error rolls
back the whole batch (5xx, nothing written); `batch_id` equals the `request_id` of every audit row of the batch and the
`X-Request-ID` response header; `unchanged` never changes `expires_at`; idempotent retry gives `unchanged`.

## Tests to write
API tests use the existing fixtures/factories (`make_client`, `make_strict_client`, `tests/rbac_factories.py`:
`make_app`, `make_role`, `make_permission`, `grant`, `assign`, `make_user`, `super_admin_role`; USR-38 helpers in
`tests/test_rbac_super_admin_role.py` for the designated role — import or copy the small setup). Request order in the
mixed tests is deliberately **not** sorted.

| Test (file › name) | Covers |
|---|---|
| `test_rbac_assignment_batches_api.py › test_batch_all_created_200` | 3 new users → 200, all `created`, ids exist, `expires_at` from request; `granted_by` = actor. |
| `› test_batch_mixed_results_in_request_order` | [new, active holder, unknown id, expired holder] in a non-sorted order → statuses `created, unchanged, failed(not_found), created`, `results[i].user_id == user_ids[i]`. |
| `› test_batch_id_equals_audit_request_id` | `batch_id == X-Request-ID header ==` every new row's `request_id`. |
| `› test_batch_retry_all_unchanged` | Same body twice → second all `unchanged` with the first call's `assignment_id`/`expires_at`; no new audit rows. |
| `› test_batch_active_holder_other_expiry_unchanged` | Holder with `expires_at=None`, request `expires_at=future` → `unchanged`, `expires_at` null, DB row untouched (and reverse case). |
| `› test_batch_expired_duplicate_created_with_old_id` | Expired row → `created`, same id, request `expires_at` (incl. null), `granted_by` updated, one `granted` row. |
| `› test_batch_all_failed_200` | Only unknown ids → 200, all `failed/not_found`, nothing written. |
| `› test_batch_disabled_unverified_users_and_disabled_app_created` | OQ78 target state. |
| `› test_batch_self_non_privileged_role_created` | Caller's own id in a non-privileged role → `created` (today's rule; USR-25 may change it). |
| `› test_batch_404_unknown_app` / `_unknown_role` / `_role_of_other_app` / `test_batch_admin_app_unknown_role_404` | 404 `not_found`, nothing written. |
| `› test_batch_body_422` (param: `[]`, 101 ids, `"x"` id, unknown field, naive `expires_at`) | 422 `validation_error`, nothing written. |
| `› test_batch_duplicate_user_ids_422` | 422 `unprocessable`, `details == [{"field": "user_ids", "reason": "duplicate_user_ids"}]`. |
| `› test_batch_admin_app_role_422` (param `admin:auditor`, `admin:super-admin`) | 422 `admin_app_role` field `role_id`; nothing written; caller with `admin:*`. |
| `› test_batch_super_admin_role_422` | Designated role → 422 `super_admin_role` field `role_id`, nothing written (Q1). |
| `› test_batch_org_id_not_allowed_422` / `_unknown_org_422` / `_expires_in_past_422` | One request-level 422 with the single POST's reason, nothing written. |
| `› test_batch_permission_denied_403` | Caller with `admin:users:read` only → 403 `permission_denied`, no row. |
| `› test_batch_admin_wildcard_required_403` | `make_strict_client`, caller `admin:roles:assign`, `is_admin` role, ids [u1, unknown, u2] → 403; zero assignments; exactly two `denied` rows (u1, u2), each with `action=role_assignment.granted`, `outcome=denied`, target `("user", uid)`, `app_key`, `org_id`, `changes={"reason": "admin_wildcard_required", "role_key": …}`, shared `request_id` (Q2). |
| `› test_batch_body_411_413_415` | No `Content-Length` (generator body) → 411; 16 KiB + 1 → 413; `text/plain` → 415; nothing written (Q7). |
| `› test_batch_reuses_single_grant_logic` | Spy (wraps, not replaces) `_check_grant_request` called once, `_grant_user_locked` once per existing user. |
| `› test_batch_audit_one_granted_row_per_created` | [new, expired holder, active holder, unknown] → exactly 2 `granted` rows, targets = their assignment ids, `changes` as `assignment_changes`. |
| `› test_batch_mid_batch_denied_keeps_other_grants` | `make_strict_client`; monkeypatch `AssignmentService._forbid_self` to raise `SelfAssignmentForbiddenError` for one uid → that item `failed/self_assignment_forbidden`, one `denied` row with the single POST's content; the other users `created` with their `granted` rows committed (Q3). |
| `› test_batch_unexpected_error_rolls_back_all` | `make_strict_client`; `_record_grant` raises `RuntimeError` on 2nd call → 500; zero new assignments and rows. |
| `› test_batch_query_count_bounded` | AC 7 (pattern `_statements` in `tests/test_auth_orgs_super_admin.py:286-299`; warm-up request first). |
| `test_rbac_assignment_batches_concurrency.py › test_batch_and_single_assign_same_user_one_wins` | Committed data (`SessionLocal`), `asyncio.gather` of batch (20 users incl. X) and single POST for X, `wait_for` timeout → no deadlock; exactly one assignment for X and one `granted` row for X; either single 201 + batch X `unchanged`, or batch X `created` + single 409 `assignment_exists`. |
| `› test_batch_and_single_assign_other_users_both_win` | Batch and single on different users of the same app → both succeed (nothing lost). |
| `› test_batch_lock_timeout_409_nothing_written` | Hold `SELECT … FROM apps … FOR NO KEY UPDATE` in a second session; monkeypatch `ASSIGNMENT_BATCH_LOCK_TIMEOUT` to `"200ms"` → 409 `conflict`, `details == [{"reason": "changed_concurrently"}]`, nothing written (Q5). |
| `test_route_auth_classes.py` (registry entry) | R3: class 1, `admin:roles:assign`; 401 sweeps; CORS matcher. |
| `test_audit_coverage.py` (AUDITED entry) | One row on success, none on 404. |
| `test_audit_denied.py › test_denied_writes_one_row[batch_wildcard]` | Single-user batch denied row identical in shape to `_assign_org_scoped`. |
| `test_audit_logger.py › test_record_denied_in_tx_same_content_as_record_denied` | Rows from `record_denied` and `record_denied_in_tx` with equal inputs have equal columns (except id/time). |
| `test_audit_logger.py › test_record_denied_in_tx_keeps_pending_work` | A pending row added before stays after the call; nothing committed until the caller commits; an outer rollback removes both. |
| `test_audit_logger.py › test_record_denied_in_tx_system_actor_writes_nothing` | Same `AdminPrincipal` guard as `record_denied`. |
| `test_openapi_contract.py › test_assignment_batches_documented` | AC 8. |

## Approach
1. **Schemas** (`rbac/schemas.py`). `AssignmentBatchCreate(extra="forbid")`: `user_ids: list[uuid.UUID] =
   Field(min_length=1, max_length=ASSIGNMENT_BATCH_MAX_USERS)`, `org_id: uuid.UUID | None = None`, `expires_at:
   AwareDatetime | None = None`. Duplicates are **not** a Pydantic validator (that would give `validation_error` with a
   free-text msg); the service raises `UnprocessableError` with reason `duplicate_user_ids` (Q6). Response models as in
   the API contract.
2. **Split the grant logic** (`rbac/service.py`; line numbers as on `main` before USR-37, which only touches lines
   1614/1709/1716/1730):
   - `class GrantOutcome(StrEnum): CREATED, RENEWED, UNCHANGED`.
   - `async def _check_grant_request(self, app, role, org_id, expires_at, *, actor, now) -> None` = lines 1675-1695
     **verbatim** in the same order (designated + `org_id` before the org lookup; `org_mode`; `unknown_org` with
     `org_exists_key_share`; `expiry_not_allowed`; `expires_in_past` against `now`), then
     `await self._require_privileged_grant(app, role.id, actor)`.
   - `async def _require_privileged_grant(self, app, role_id, actor) -> None` = lines 1739-1746 (privileged ⇒
     `require_super`). `def _forbid_self(self, app, role_id, user_id, actor) -> None` = lines 1747-1750 (no query).
     `_guard_privileged` = both, unchanged for `revoke` (identical: `_forbid_self`'s conditions — admin app or
     designated — imply privileged). USR-25 will widen `_forbid_self`.
   - `async def _grant_user_locked(self, app, role, user_id, org_id, expires_at, *, actor, now, existing:
     RoleAssignment | None) -> tuple[RoleAssignment, GrantOutcome]` = `_forbid_self` (self check **before** the
     existing-row check, Q9), lines 1697-1703 (designated recipient check), then the upsert of lines 1704-1731 with the
     active duplicate returning `(existing, UNCHANGED)` instead of raising; renewal (sets `expires_at`, `granted_by`,
     `repo.save()`) → `RENEWED`; insert → `CREATED`. IntegrityError mapping unchanged (USR-37 `_assignment_conflict`
     reasons).
   - `_grant_locked(...)` keeps its signature `-> tuple[RoleAssignment, bool]` and docstring: `now = await
     self.repo.db_now()`; `_check_grant_request`; `existing = await self.repo.find_assignment(user_id, role.id,
     org_id)`; `_grant_user_locked`; `UNCHANGED` → `raise _assignment_conflict("Role is already assigned",
     REASON_ASSIGNMENT_EXISTS)`; return `(ra, outcome is GrantOutcome.CREATED)`. Observable behaviour of the single POST
     and `bootstrap_super_admin` is identical (same checks, order, errors, messages, details, audit); only the SQL order
     changes (`db_now` once up front — same value, `now()` is transaction start; `find_assignment` before the self check,
     a read). `assign`, `bootstrap_super_admin`, `revoke` bodies unchanged (Q12).
3. **Repository** (`rbac/repository.py`): the six additions in the Files table. `set_lock_timeout` runs
   `text(f"SET LOCAL lock_timeout = '{value}'")` (precedent `usage/meter_repository.py:77-79`; `value` is a module
   constant, never input). `is_lock_timeout(exc: DBAPIError) -> bool`: `getattr(exc.orig, "sqlstate", None) ==
   "55P03"` (implementer confirms the attribute on the asyncpg adapter via `test_batch_lock_timeout_409_nothing_written`;
   no precedent in the code base). `find_assignments` uses `RoleAssignment.user_id.in_(ids)` (renders `= ANY` with
   asyncpg arrays or `IN`; either is fine — the test counts statements, not syntax), `role_id ==`,
   `org_id.is_not_distinct_from(org_id)`, `populate_existing=True`.
4. **Audit helper** (`audit/service.py`, after `record_denied`, line 222): `record_denied_in_tx(action, *, exc, actor,
   ctx, target=None, app_key=None, org_id=None, facts=None)`: same `AdminPrincipal` guard; `await
   self.repo.add(_build(action, outcome=AuditOutcome.DENIED, …, changes={"reason": exc.code, **(facts or {})}))`; no
   rollback, no commit, errors propagate (like `record`, so a failed write is a 5xx and the batch rolls back — R4
   atomicity). Docstring: "inside a caller-owned transaction (bulk endpoints, ADR-0029); the row is committed with the
   caller's work and lost if it rolls back". To guarantee identical content, extract `_denied_changes(exc, facts)` used
   by both `record_denied` and `record_denied_in_tx` (pure refactor, `record_denied` behaviour unchanged).
5. **`AssignmentService.assign_batch(key, role_id, data, *, actor, ctx) -> AssignmentBatchRead`**:
   1. Duplicates: `len(set(data.user_ids)) != len(data.user_ids)` → 422 `duplicate_user_ids` (before any SQL).
   2. `batch_id = uuid.UUID(ctx.request_id)`; `ctx.request_id is None` → `RuntimeError` (5xx; the middleware always
      sets a server-generated UUID4, `app/core/request_id.py:24`) (Q13).
   3. Wrap steps 4-10 in `try … except DBAPIError as exc: if RbacRepository.is_lock_timeout(exc): raise
      _assignment_conflict("Assignment was changed concurrently", REASON_CHANGED_CONCURRENTLY) from exc; raise` (Q5).
   4. `await repo.set_lock_timeout(ASSIGNMENT_BATCH_LOCK_TIMEOUT)` (before the lock, so it bounds the app-lock wait and
      every later lock wait of the transaction: org `KEY SHARE`, FK `KEY SHARE` on users during inserts).
   5. App: `key == ADMIN_APP_KEY` → unlocked `repo.get_app_by_key` (never lock `admin` here, ADR-0030 §6 spirit; nothing
      is written on this path); otherwise `repo.lock_app(key)`. `None` → `NotFoundError("Unknown app")`.
      `repo.get_role(app.id, role_id)` `None` → `NotFoundError("Unknown app, or role not of that app")` (covers role of
      another app). Disabled apps are not refused (OQ78).
   6. `app.key == ADMIN_APP_KEY` → 422 `admin_app_role` (field `role_id`); `is_designated_super_admin_role(app,
      role.id)` → 422 `super_admin_role` (field `role_id`) (Q1). Both via `_assignment_unprocessable`.
   7. `now = await repo.db_now()`; `await self._check_grant_request(app, role, data.org_id, data.expires_at,
      actor=actor, now=now)` inside `try … except DENIED_ERRORS as exc: await self._record_batch_wildcard_denied(exc,
      app, role, data, actor, ctx); raise`. `_record_batch_wildcard_denied`: `ids = await
      repo.existing_user_ids(data.user_ids)`; for each in `sorted(ids)`: `await self.audit.record_denied(...)` with the
      single POST's exact arguments (lines 1601-1610: target `(USER, uid)`, `app_key`, `org_id=data.org_id`, `facts
      {"role_key"}`) (Q2). Unknown ids get no row, exactly as the single POST answers them 404 before any guard.
   8. Preload: `existing_users = await repo.existing_user_ids(data.user_ids)`; `held = await
      repo.find_assignments(existing_users, role.id, data.org_id)` (Q4).
   9. `for uid in sorted(data.user_ids)` (deterministic row-lock order, ADR-0029 §4): unknown → item
      `failed(NotFoundError("User not found"))`; else `try: async with repo.savepoint(): ra, outcome = await
      self._grant_user_locked(app, role, uid, data.org_id, data.expires_at, actor=actor, now=now,
      existing=held.get(uid)); if outcome is not UNCHANGED: await self._record_grant(ra, app, role, actor, ctx)` —
      `except DENIED_ERRORS as exc:` (the savepoint has already rolled back) `await
      self.audit.record_denied_in_tx(ROLE_ASSIGNMENT_GRANTED, exc=exc, …same args as the single POST…)`, item failed;
      `except AppError as exc:` item failed (`{exc.code, exc.message}`); else item `created` (CREATED / RENEWED, Q11) or
      `unchanged` with `ra.id`, `ra.expires_at` (Q10). Any other exception propagates → `get_session` rolls back all
      (5xx). Order of `except` clauses: `DENIED_ERRORS` first (they are `AppError` subclasses).
   10. Return `AssignmentBatchRead(batch_id, [items[uid] for uid in data.user_ids])` (request order).
6. **Router** (`rbac/router.py`): `_BATCH_KWARGS = require_permission("admin:roles:assign", errors={404: _ROLE_404,
   409: …changed_concurrently…, 411: …, 413: …, 415: …, 422: …all reasons + validation_error…},
   requires_super="conditional", super_when="Role holding an is_admin permission")`, then
   `_BATCH_KWARGS["openapi_extra"]["requestBody"] = {"required": True, "content": {"application/json": {"schema":
   inline_schema(AssignmentBatchCreate)}}}` (fresh dict per call, as USR-37 Approach 3). Handler
   `create_assignment_batch(request, session, principal, ctx, key, role_id)`: `body = await read_json_body(request,
   AssignmentBatchCreate, cap=ASSIGNMENT_BATCH_MAX_BYTES (16 * 1024), timeout_s=ASSIGNMENT_BATCH_READ_TIMEOUT_S
   (10.0))`, then the service. Auth dependencies run before the body is read (no buffering for unauthenticated callers).
   `status_code=200`, `response_model=AssignmentBatchRead`, `summary`, `description` (API contract text).
7. **Tests** as listed, then `make check`.
8. **Docs**: spec line 377 adds `super_admin_role`; one "PLAN (USR-36)" sentence: lock timeout 5 s → 409
   `changed_concurrently`; `duplicate_user_ids`; 16 KiB body cap (411/413/415).

Rules: **R1** router reads the body and calls the service; service raises `AppError`s and owns the savepoint
through `repo.savepoint()`; the only SQLAlchemy names the service uses are the exception types (`IntegrityError`
already, `DBAPIError` new — same precedent). **R2** no schema change. **R3** class 1, registry entry. **R4** `granted`
rows in the same transaction; `denied` rows as the single POST (Q2/Q3). **R5** no new env keys (constants). **R6**
envelope, codes 200/401/403/404/409/411/413/415/422 all in the R6 list. **R7** OQ79 filter, bulk revoke, G5 race,
`forbidden=` param are follow-ups. **R8** tests above. **R9** D41 exception (200 + `{batch_id, results[]}`); additive
only. **R11** no email in results, logs or rows.

Expected diff: app code ~300 lines (service ~150 incl. ~40 moved, repo ~45, schemas ~45, router ~45, audit ~20);
tests ~550 lines; docs 2 lines.

## Specialist input used
- **software-architect**: adopted rec 1 (split inside `AssignmentService`, names `_check_grant_request` /
  `_grant_user_locked` / `GrantOutcome`, `_grant_locked` wrapper raising 409 on `UNCHANGED`; `_guard_privileged` split
  with `revoke` keeping the combined one), rec 2 flow, rec 3 per-user error mapping, rec 4 non-committing helper (name
  `record_denied_in_tx`), rec 6 `batch_id`, rec 7 concurrency test, Q1 default (one denied row per user), Q2, Q3, Q4.
  Rejected rec 5 "no preload" in favour of the ticket's note and the database-optimizer (Q4; no user-delete path exists
  in `app/`, org deletion is blocked by our `KEY SHARE`, all assignment writers take `lock_app`). Adjusted: `require_super`
  stays **after** the org/expiry 422s, as in the single POST (keeps the wrapper identical). Optional `bootstrap_super_admin`
  use of `UNCHANGED` not done (R7).
- **database-optimizer**: adopted recs 1-6, 8, 9 (no migration, preload with `populate_existing`, `db_now` once,
  `lock_timeout` before `lock_app`, no `ON CONFLICT`), risk 1 verified (NULLS NOT DISTINCT). Rec 4 "batched
  `is_active_staff_super_admin`" dropped: the designated role is refused at request level, so the check never runs in a
  batch. Rec 7 helper adopted under the architect's name `record_denied_in_tx`.
- **api-platform-engineer**: adopted rec 1 (`duplicate_user_ids` as `unprocessable`), rec 2 (models, 200, registry),
  rec 4, 5, 6, 7, 8; finding 10 (ADR conflict → Q1). Rejected `too_many_users` reason (Pydantic `max_length` →
  `validation_error`, as every list cap in the API) and per-item `details` (spec line 371 fixes `error: {code,
  message}`; Q15). Rec 3 adjusted: per-item codes are the single POST's envelope codes; USR-37's reasons are reused,
  not avoided (USR-37 lands first).
- **security-architect**: adopted rec 1 (request-level checks once, `record_denied` safe at that point), rec 2, rec 3
  (fixed messages), rec 4, rec 5 (16 KiB cap, 411/413 — Q7), rec 6, rec 7 tests. Q3 (rate limit) answered by spec line
  386 ("No extra rate limit") — Q8. Q4 (OQ79, bulk revoke) → follow-ups.
- **identity-access-engineer**: adopted rec 1, 2 (order), 3 (self check before existing-row check), 4, 6, 7, 8,
  finding 1 (Q1). Rec 5 `record_denied(..., commit=False)` rejected in favour of a separate method (no new flag on a
  method every service calls). Q1 default "one row per batch targeting the role" rejected for Q2's default (spec line
  384 "identical to the single POST, per user"); offered as Q2 alternative. Order note: body 422s come before the 404s
  (FastAPI / `read_json_body` reads the body before the handler; Q16).

## Split proposal
None by default (Q14). App code ~300 lines, the total exceeds the ~400-line guide only because of tests (~550). If Ming
prefers smaller PRs: slice A = grant split + `record_denied_in_tx` + their tests (no new route, single POST unchanged);
slice B = schemas, repository preloads, route, batch tests. The audit change alone (~20 lines) is too small to be its
own ticket.

## Blocking questions
None that block — see `## Blocking questions for Ming`; every question has a default and this plan is written on the
defaults.

## Blocking questions for Ming
Confirm or override at the plan-approval gate. "(spec decides)" = already answered by the spec/ADR; listed to confirm the
reading only.
1. **`super_admin_role` (ticket vs ADR).** Add request-level 422 `super_admin_role` (field `role_id`) to Scope and AC 2?
   **Default: yes** — ADR-0029 §3, ADR-0030 §4 and OQ81 require it; without it the batch could bulk-grant the designated
   role (only the per-user `recipient_not_staff_super_admin` would remain).
2. **Audit of request-level 403 `admin_wildcard_required`.** **Default: one `denied` row per existing requested user**,
   with exactly the single POST's content (target user, `role_key`, `app_key`, `org_id`), written by calling the
   unchanged `record_denied` per user (≤ 100 small commits on a rare refusal path); unknown ids get no row (the single
   POST answers them 404 before the guard). Alternatives: (b) same rows in one commit via a new
   `record_denied_many` helper; (c) one row per batch targeting the role with `{role_key, user_count}` (identity
   consultant) — changes the row shape, so not "identical".
3. **Mid-batch `denied` rows.** Via the new non-committing `AuditLogger.record_denied_in_tx` (same content, written in
   the outer transaction after the user's savepoint rolled back, committed with the batch, lost on a 5xx — documented).
   Built now and tested with a stub, although no per-user denied path is reachable until USR-25. **Default: yes; do not
   wait for USR-25.**
4. **Preload existing assignments** with one `= ANY` query under the lock (ticket technical note) vs a per-user
   `find_assignment`. **Default: preload** (fewer round trips while holding the lock; stale only if a row is deleted
   without the app lock, which no code path does — a vanished row then fails the whole batch as a 5xx).
5. **Lock wait.** `SET LOCAL lock_timeout = '5s'` before `lock_app`, bounding every lock wait of the batch; on timeout
   **default: 409 `conflict`, `details=[{"reason": "changed_concurrently"}]`, nothing written** (client may retry).
   Alternatives: 503 `service_unavailable`; or `2s` like the other `lock_timeout` uses in the code base.
6. **Duplicate `user_ids`.** **Default: 422 `unprocessable`, `details=[{"field": "user_ids", "reason":
   "duplicate_user_ids"}]`** (no silent de-duplication). Other shape errors (empty, > 100, bad UUID, unknown field) stay
   `validation_error` like every route.
7. **Body cap.** Read the body with `read_json_body` (`app/modules/releases/body.py`, reused as-is): `Content-Length`
   required (411), > 16 KiB → 413, non-JSON → 415. **Default: yes.** Risk: a proxy that drops `Content-Length` would make
   admin-pan get 411 — checked in "Ming verification". Alternative: no cap (like other admin routes).
8. **Per-staff rate limit.** **(spec decides) No** — spec line 386 "No extra rate limit (Bearer admin route)".
   Follow-up only together with OQ79 if wanted.
9. **Self check before the existing-row check** (a held self-target shows `failed self_assignment_forbidden`, not
   `unchanged`, as the single POST answers 403 not 409). **Default: yes.**
10. **`unchanged` returns the existing `assignment_id` and `expires_at`**, never changes them. **(spec decides) yes.**
11. **Renewal of an expired duplicate = `created`** with its old id, writes one `granted` row, sets `expires_at` (incl.
    null) and `granted_by` from the request, as the single POST. **Default: yes.**
12. **Split of `_grant_locked`** into `_check_grant_request` + `_grant_user_locked` (and `_guard_privileged` into
    `_require_privileged_grant` + `_forbid_self`), single POST behaviour identical; only the SQL order changes
    (`db_now` fetched once up front, `find_assignment` before the self check). **Default: yes.**
13. **`batch_id = request_id`** (server-generated UUID4, never client input). **(spec decides) yes.**
14. **Split the ticket** (audit refactor or slices A/B in `## Split proposal`)? **Default: no** — one PR, size M.
15. **Per-item `error` = `{code, message}` only** (spec line 371), no `details`. **Default: yes**; adding
    `details.reason` later is additive.
16. **Body 422s before 404s** (the body is read and validated before the service looks up the app/role, as on every
    route). **Default: yes.**

## Assumptions
- **USR-37 is built and merged before USR-36** (same batch, USR-37 plan READY): `REASON_ASSIGNMENT_EXISTS`,
  `REASON_CHANGED_CONCURRENTLY` and `_assignment_conflict()` exist in `app/modules/rbac/service.py`, and the four
  `ConflictError`s of `_grant_locked` / `assign` carry `details=[{"reason": …}]`. USR-36 rebases onto it; the moved
  upsert code keeps USR-37's calls unchanged. If USR-37 slips, the implementer stops and tells the orchestrator.
- **Hotspots:** `app/modules/rbac/service.py` (`_grant_locked`, `_guard_privileged`) and
  `app/modules/rbac/assignments_router.py` are edited by USR-37 (merged first); `assignments_router.py` is **not**
  touched here. USR-25 (BACKLOG) will edit `_forbid_self` after this split — its plan should target the new helper.
  USR-35 (PLANNING, users module) shares no file. `tests/test_audit_coverage.py` (`AUDITED` count) and
  `tests/test_route_auth_classes.py` are append-only registries other open tickets may also touch — trivial rebase.
- `ctx.request_id` is always set by `RequestIdMiddleware` for HTTP requests; the CLI path never calls `assign_batch`.
- Under `lock_app` every role-assignment writer (single POST, revoke, join/invite accept, org member removal) is
  serialized per app; no user-delete path exists in `app/` (checked: no `delete(User)`), and an org cannot be deleted
  while the batch holds its `KEY SHARE` (FK `RESTRICT`).
- The batch never locks the `admin` app (admin-app keys are refused before locking), so the lock order app → org →
  user FK rows (sorted) matches ADR-0003 §10 / ADR-0030 §6; `users.set_status`' `FOR UPDATE` on a user can delay a
  batch insert, bounded by the 5 s `lock_timeout` → 409.
- `httpx` and admin-pan send `Content-Length` for JSON bodies; the 411 test uses a generator body.
- A 100-user batch holds the app lock ~100-300 ms (consultant estimate), acceptable at ≤ 100 and documented in OpenAPI.
- Follow-ups (not in this diff, R7): OQ79 audit filter / index on `request_id`; bulk revoke (§5.7 Later); the
  pre-existing G5 race (`set_status` does not take the target app lock, architect risk 4); optional
  `bootstrap_super_admin` use of `GrantOutcome.UNCHANGED`; per-item `details.reason` if admin-pan needs it.
