Status: READY

# Plan — USR-03g: Join approval flow (join_requests + staff approve/reject)

## Stale ticket text (flagged, planned against current code)
The ticket was written on 2026-11-13, before ADM-002 and ADM-004 landed. Three points no longer match the code. This plan follows the code and `CLAUDE.md` R3/R4 as they stand on 2026-11-17:
1. **"behind the admin RBAC flag"**: there is no such flag. `ADMIN_RBAC_API_ENABLED` is gone and every staff router is mounted unconditionally in `app/main.py`. The new staff routes are plain R3 class-1 Bearer routes: `current_admin` plus exactly one `require_permission(...)`, as in `app/modules/rbac/assignments_router.py`.
2. **"USR-03b `AuditRecorder`"**: this is now `AuditLogger.record(...)` in `app/modules/audit/service.py` (ADM-004).
3. **Migration head**: the head is `f3a8d51c9e27` (USR-05a). I checked the `down_revision` chain in `migrations/versions/`, and no file revises `f3a8d51c9e27`. The implementer must run `uv run alembic heads` before writing the revision, because other tickets land on the same day.

Two more things the ticket did not anticipate:
- The ORM class name `JoinRequest` is already taken by the request-body schema in `app/modules/membership/schemas.py`. The ORM class here is `AppJoinRequest`.
- `JoinApprovalUnavailableError` is still used by the CLI `create-account --join` (`app/cli.py:400`). It stays for the CLI (see Approach §3).

## Spec summary
- spec §5.3: "each app has a `join_policy` (`open` | `approval` | `closed`; default `open`, `admin` = `closed`), a default role `<app>:member` … Joining assigns the default role at app scope (`org_id NULL`). Policy and role contents are data."
- spec §5.8 (per-app tokens): "Not a member → `not_member` with `join_available`; joining follows the app's `join_policy` (D8–D9)."
- identity-architecture.md §3: "`join_requests (id, app_id, user_id, status pending|approved|rejected, created_at, decided_by NULL)` — only needed for join_policy = approval (USR-03g)".
- identity-architecture.md D8: "`POST /auth/apps/{key}/join {account_id}` applies `join_policy` (open/approval/closed), assigns the default role at app scope, writes audit. 'Continue as <account>' = consent."
- CLAUDE.md R3 class 1 (Bearer `aud=admin` + `require_permission`), R4 (audit in the same transaction), R9 (`Page[T]`, `/api/v1/<plural-noun>`).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Migration creates `join_requests` with CHECK `(status = 'pending') = (decided_at IS NULL)`, a status CHECK, partial unique `(app_id, user_id) WHERE status = 'pending'`, the list and user indexes, FKs, and a working `downgrade()`; the model is registered; there is no drift | `tests/test_join_requests_migration.py` › `test_downgrade_drops_table`, `test_constraints`; `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head && uv run alembic check` |
| 2 | Join on an `approval` app → 202 `{"status":"pending","request_id"}`. Idempotent while pending (same id, one row, one audit row). A member still gets 200 `member`. `/token` `join_available` reflects it (true while joinable or pending, false inside the rejection cooldown) | `tests/test_join_requests_join.py` › `test_join_approval_202_pending`, `test_join_approval_replay_same_request`, `test_join_approval_member_200`, `test_token_join_available_approval`; updated `tests/test_auth_token.py` › `test_token_not_member` |
| 3 | Approve takes `lock_app`, re-checks app active + policy `approval`, active user, verified email and a usable default role, then grants the default role at app scope (`granted_by` = staff). Reject records `decided_by`/`decided_at`. Non-pending → 409, unknown/cross-app → 404 | `tests/test_join_requests_api.py` (see Tests) |
| 4 | Every create/approve/reject writes exactly one audit row via `AuditLogger` in the same transaction; replays and failures write none | `tests/test_audit_coverage.py` (approve/reject rows in `AUDITED`), `tests/test_join_requests_join.py` › `test_join_approval_audit_once` |
| 5 | Rejection cooldown (env): join inside the cooldown → 403 `join_rejected`, and a new pending request is allowed after it | `tests/test_join_requests_join.py` › `test_join_rejected_cooldown_403`, `test_join_after_cooldown_new_request` |
| 6 | The R3 classification holds and the routes are in the permission map | `tests/test_route_auth_classes.py` (whole file green) |
| 7 | `make check` is green | `make check` |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `migrations/versions/20261117_<rev>_usr03g_join_requests.py` | **new** revision, `down_revision = "f3a8d51c9e27"` (confirm with `alembic heads`) | AC1, R2 |
| `app/modules/join_requests/__init__.py` | new (empty) | new domain module (house layout) |
| `app/modules/join_requests/models.py` | `AppJoinRequest` ORM plus a `JoinRequestStatus` StrEnum | AC1 |
| `app/modules/join_requests/repository.py` | SQLAlchemy-only queries (see Approach §5) | R1 |
| `app/modules/join_requests/schemas.py` | `JoinRequestRead`, `JoinRequestStatusFilter` | staff API contract |
| `app/modules/join_requests/service.py` | `JoinRequestService.list/approve/reject` | AC3, R1, R4 |
| `app/modules/join_requests/router.py` | 3 Bearer routes, `prefix="/apps"`, `dependencies=[Depends(current_admin)]` | AC3, R3 |
| `app/db/models.py` | import and export `AppJoinRequest` | R2 |
| `app/main.py` | `app.include_router(join_requests_router, prefix=settings.api_prefix)` | mount the routes |
| `app/core/config.py` | `join_reject_cooldown_seconds: int = Field(default=604800, ge=0)` | AC5, R5 |
| `.env.example` | `JOIN_REJECT_COOLDOWN_SECONDS=` with a one-line comment | R5 |
| `app/modules/membership/service.py` | extract `usable_default_role`; add the `approval` branch to `JoinService.join`; extend `JoinResult`; add the `join_available(...)` helper; `JoinService` takes `reject_cooldown_seconds` | AC2, AC5 |
| `app/modules/membership/errors.py` | add `JoinRejectedError` (403 `join_rejected`); update the `JoinApprovalUnavailableError` docstring to "CLI `create-account --join` only" | AC5 |
| `app/modules/membership/schemas.py` | add `JoinPendingResponse(status: Literal["pending"], request_id: UUID)`; leave `JoinResponse` (member) byte-identical; drop the "USR-03g" comment | AC2 |
| `app/modules/auth/router.py` | join handler: pass the cooldown, return 202 + `JoinPendingResponse` when pending, document 200/202/`join_rejected`; token handler: pass the cooldown to `TokenService` | AC2 |
| `app/modules/tokens/service.py` | `TokenService(..., join_reject_cooldown_seconds)`; use `join_available(...)` instead of the inline expression | AC2 |
| `app/modules/organizations/auth_service.py` | `OrgSelfService(..., join_reject_cooldown_seconds)`; use `join_available(...)` | AC2 (the second call site, which must not diverge) |
| `app/modules/organizations/auth_router.py` | pass the cooldown into both `OrgSelfService(...)` constructions | AC2 |
| `app/modules/audit/actions.py` | `AuditTarget.JOIN_REQUEST = "join_request"`; actions `APP_JOIN_REQUESTED = "app.join_requested"`, `APP_JOIN_REQUEST_APPROVED = "app.join_request_approved"`, `APP_JOIN_REQUEST_REJECTED = "app.join_request_rejected"` | AC4, R4 |
| `tests/join_request_factories.py` | new: `make_approval_app(s, key=None)` (unique key, `join_policy="approval"`, member default role, login origin), `make_join_request(s, app, user, status="pending", decided_at=None)` | shared by 3 test files |
| `tests/test_join_requests_migration.py` | new | AC1 |
| `tests/test_join_requests_join.py` | new | AC2, AC4, AC5 |
| `tests/test_join_requests_api.py` | new | AC3 |
| `tests/test_auth_join.py` | drop the `("approval", "join_approval_unavailable")` case from `test_join_policy_refused_403` (closed only; fix the docstring) | the behaviour changed |
| `tests/test_auth_token.py` | `test_token_not_member` case `("approval", True, False)` → `("approval", True, True)`; add `("approval", False, False)` | `join_available` changed |
| `tests/test_auth_orgs.py` | one case: an `approval` non-member gets `not_member` with `join_available: true` | second call site |
| `tests/test_route_auth_classes.py` | 3 `BEARER` entries; `"request_id"` in `_fill()` | R3 test harness |
| `tests/test_audit_coverage.py` | approve/reject in `AUDITED`, per-route 200 success status, `len(AUDITED) == 20` | R4 harness |
| `docs/consumers.md` | Joining section: 202 pending, polling guidance, error table | consumer contract |
| `docs/identity-architecture.md` | §2 Join, §3 data model, §4 API table, S8 audit list | design doc kept true |

The implementer must not touch other files without recording why in `impl-log.md`. In particular, the ticket's `ticket.md` is the orchestrator's to update (stale text, see above).

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `test_join_requests_migration.py` › `test_downgrade_drops_table` | downgrade to `f3a8d51c9e27` drops the table; upgrade restores it (pattern: `tests/test_app_credentials_migration.py`) |
| `test_join_requests_migration.py` › `test_constraints` | `IntegrityError` for: approved with `decided_at NULL`; pending with `decided_at` set; `status='bogus'`; a second pending row for the same (app, user). A new pending row is allowed after the first is rejected. The user delete cascades. `decided_by` is set NULL when the staff user is deleted. (CHECK drift is invisible to `alembic check`, so these tests guard it.) |
| `test_join_requests_join.py` › `test_join_approval_202_pending` | 202, body `{"status":"pending","request_id":<uuid>}`, one row with `status=pending`, no role assignment, `Cache-Control: no-store`, the cookie re-set on rotation |
| › `test_join_approval_replay_same_request` | second join → 202, same `request_id`, still one row |
| › `test_join_approval_audit_once` | exactly one `app.join_requested` row (actor user = the joiner, target `join_request`, `app_key`) across two joins; no `app.joined` |
| › `test_join_approval_member_200` | an existing member on an `approval` app → 200 `{"status":"member"}`, no row |
| › `test_join_approval_unusable_default_role_403` | NULL default role, and a default role holding an `is_admin` permission → 403 `join_unavailable`, no row |
| › `test_join_approval_unverified_403` | 403 `email_unverified`, no row |
| › `test_join_approval_disabled_app_403` | 403 `origin_not_allowed`, no row |
| › `test_join_rejected_cooldown_403` | a rejected row with `decided_at = now()` → 403 `join_rejected`, no new row, no audit row |
| › `test_join_after_cooldown_new_request` | rejected row `decided_at` older than the cooldown (or `make_client(join_reject_cooldown_seconds=0)`) → 202 with a new `request_id` |
| › `test_token_join_available_approval` | `/token` for a non-member: `join_available` true before the join, true while pending, false inside the cooldown after a reject |
| `test_join_requests_api.py` › `test_list_pending_default` | `Page` shape, default `status=pending` only, order `created_at, id`, fields incl. `email`, `user_status`, `email_verified`; `Cache-Control: no-store` |
| › `test_list_status_filter` | `?status=rejected` returns only rejected; `?status=bogus` → 422; `limit=101` → 422 |
| › `test_list_unknown_app_404` / `test_list_admin_app_404` | 404 `not_found` |
| › `test_approve_grants_default_role` | 200, `status=approved`, `decided_by` = staff id, `decided_at` set; one app-scope assignment of the default role with `granted_by` = staff; `/token` then succeeds |
| › `test_approve_already_member_no_grant` | the user holds another app-scope role → 200 approved, no new assignment, no 409 |
| › `test_approve_uses_current_default_role` | default role changed after the request → the new default role is granted; the audit row `role_key` shows it |
| › `test_approve_refusals_409` (parametrized) | 409 `conflict` with `details[0].reason` ∈ `app_disabled`, `join_policy_changed` (now `open` and `closed`), `user_disabled`, `email_unverified`, `default_role_unusable` (NULL, and `is_admin`); the request stays pending; no assignment; no audit row |
| › `test_approve_not_pending_409` / `test_reject_not_pending_409` | an already decided request (incl. a second approve) → 409 reason `not_pending` |
| › `test_approve_unknown_404` / `test_approve_cross_app_404` / `test_reject_cross_app_404` | unknown id, and an id belonging to another app → the same 404 |
| › `test_reject_records_decision` | 200, `status=rejected`, `decided_by`/`decided_at` set, no assignment |
| › `test_reject_allowed_when_app_disabled_or_policy_changed` | reject still succeeds (queue cleanup; it grants nothing) |
| › `test_approve_lock_order` | statement recording (pattern `tests/test_auth_join.py::test_join_locks_app_before_reads_and_writes`): `apps … FOR NO KEY UPDATE` precedes the `users … FOR SHARE`, which precedes `join_requests … FOR UPDATE` and the `INSERT INTO role_assignments` |
| › `test_staff_routes_401_403` (parametrized over the 3 routes) | no token → 401; a token with only `admin:apps:read` → 403 |
| `test_route_auth_classes.py` (existing tests) | the new routes are classified, with `x-permission`, 401/403 in openapi and the runtime sweeps |
| `test_audit_coverage.py` › `test_every_mutating_bearer_route_is_mapped`, `test_success_writes_one_row`, `test_failure_writes_no_row`, `test_no_secret_or_email_in_any_row` | approve/reject write exactly one row (actor = staff user, target `join_request`, non-empty `changes`, no email); unknown id → 404 with no row |
| `test_auth_token.py` › `test_token_not_member` (updated) | the approval cases |
| `test_auth_orgs.py` › `test_create_org_not_member_approval_join_available` | the second `join_available` site |

## Approach
1. **Migration and model (R2).** Copy the `app/modules/invitations/models.py` and `c5e91d3a7b24` patterns.
   - Write the CHECK texts and the `postgresql_where` text byte-identical in the model and in the migration.
   - Constraint names come from `op.f(...)` / `NAMING_CONVENTION`. FK names are explicit.
   - Register the model in `app/db/models.py`.
   - Verify with upgrade → downgrade -1 → upgrade → `alembic check`.
2. **Errors and config (R5, R6).**
   - Add `JoinRejectedError(PermissionDeniedError)` with code `join_rejected` and empty details. It reveals no reason, no decider and no date.
   - Staff-side state refusals use `ConflictError(message, details=[{"field": "request_id", "reason": <reason>}])`, the same pattern as `app_credentials/service.py::_refusal`.
   - Do **not** reuse the 403 `PermissionDeniedError` subclasses on staff routes. On a Bearer route, 403 means "missing permission".
   - Add the setting `join_reject_cooldown_seconds` (default 604800 = 7 days, `ge=0`; 0 turns the cooldown off).
3. **`membership/service.py`.**
   - `usable_default_role(rbac, app) -> Role`: the NULL / missing / `is_admin` checks moved out of `join_default_role`, raising 403 `join_unavailable` with the same log line.
   - `join_default_role` keeps its policy branch, so the CLI (`MSG_JOIN_APPROVAL`) and the invitation path are unchanged. It then calls `usable_default_role`.
   - `JoinResult` gains `request_id: uuid.UUID | None = None` (pending iff not None).
   - In `JoinService.join`, the order is unchanged up to the member check: mismatch → lock app → account → active → verified → member (200, nothing written).
   - Then, **only if `app.join_policy == "approval"`**:
     1. `usable_default_role` (403 `join_unavailable`; don't queue requests that can never be approved).
     2. Existing pending row → `JoinResult(created=False, request_id=row.id)`, no write, no audit.
     3. If `reject_cooldown_seconds > 0` and a rejected row has `decided_at > now() - cooldown` → `JoinRejectedError`.
     4. Otherwise insert the pending row and write the audit row `app.join_requested` (actor `EndUser(user.id)`, target `(JOIN_REQUEST, id)`, `app_key`, `changes={"status": change(None, "pending")}`) → `JoinResult(created=True, request_id=id)`.
   - Every other policy keeps calling `join_default_role` (closed → 403 `join_closed`, open → grant).
   - The app lock serialises join, approve and reject per app, so select-then-insert is race-free. The partial unique index is the backstop: an `IntegrityError` there is unreachable; the insert runs in a savepoint (`begin_nested`) and any `IntegrityError` re-raises as `ConflictError`.
   - `async def join_available(session, app, user_id, *, reject_cooldown_seconds) -> bool`:
     - `open` → `app.default_role_id is not None` (unchanged);
     - `approval` → `default_role_id is not None` and not inside the cooldown (pending counts as available, because join answers 202 idempotently);
     - `closed` → False.
   - `tokens/service.py:76-79` and `organizations/auth_service.py:89-91` call `join_available`. Both services take the cooldown as a required keyword (passed from `request.app.state.settings` by the routers; `get_settings()` would bypass test overrides).
4. **Join router.**
   - When `result.request_id` is set: `response.status_code = 202` and return `JoinPendingResponse`. Otherwise return `JoinResponse(status="member")`.
   - `response_model=JoinResponse | JoinPendingResponse`.
   - Add `responses={202: {"model": JoinPendingResponse, ...}, ...}`.
   - Cookie rotation and `Cache-Control: no-store` apply on both paths.
   - Update the docstring.
   - `join_approval_unavailable` is no longer emitted over HTTP. That 403 was documented as provisional ("until USR-03g"), so the change is not a v1 break; state this in `consumers.md`.
5. **Repository (`JoinRequestRepository`, SQLAlchemy only):**
   - `get_pending(app_id, user_id)`
   - `rejected_within(app_id, user_id, seconds) -> bool` (`decided_at > now() - make_interval(secs => :s)`, DB clock)
   - `add(row)` (savepoint)
   - `get_in_app(app_id, request_id)` and `get_in_app_for_update(app_id, request_id)` (`FOR UPDATE`, `populate_existing`)
   - `page(app_id, status, limit, offset) -> (rows of (AppJoinRequest, User), total)`: join `users`, order `created_at, id`
   - `decide(row, status, decided_by)`: set `decided_at = func.now()`, flush, refresh.
6. **`JoinRequestService` (R1: raises `AppError` only; returns `JoinRequestRead`):**
   - **list(key, status, limit, offset):** `key == "admin"` or unknown app → 404 (`rbac.get_app_by_key`, no lock).
   - **approve(key, request_id, actor, ctx)**, in one transaction, in this lock order: app → user → request. This is the ADR-0003 §10 order, and it matches `UserService.set_status`, which takes the user `FOR NO KEY UPDATE`, so the user `FOR SHARE` here blocks a concurrent disable.
     1. `key == "admin"` → 404 before any read.
     2. `rbac.lock_app(key)`; None → 404.
     3. `get_in_app(app.id, request_id)` (plain read, to learn `user_id`); None → 404. Cross-app ids give the same 404.
     4. `users.get_for_share(row.user_id)`.
     5. `get_in_app_for_update(...)`; None → 404.
     6. Not pending → 409 `not_pending`.
     7. App not active → 409 `app_disabled`; policy ≠ `approval` → 409 `join_policy_changed`.
     8. User missing or not active → 409 `user_disabled`; `email_verified_at` NULL → 409 `email_unverified`.
     9. `usable_default_role` → on `JoinUnavailableError`, raise 409 `default_role_unusable`. Catch and convert; never surface the 403.
     10. If `authz.effective_roles(user.id, app.key)` is non-empty → no grant. Otherwise `AssignmentService(session)._grant_locked(app, role, user.id, None, None, actor=actor)` (app scope, `granted_by` = staff, `_guard_privileged` still applies).
     11. `repo.decide(row, "approved", actor_user_id(actor))`.
     12. Write one audit row: `app.join_request_approved`, actor = staff, target `(JOIN_REQUEST, row.id)`, `app_key`, `changes={"status": change("pending","approved")}` plus `"role_key": change(None, role.key)` only when a role was granted.
     - Refusals leave the request pending and write no row.
     - `_guard_privileged` cannot fire, because `is_admin` default roles are refused at step 9, so no `record_denied` wrapper is needed.
   - **reject(key, request_id, actor, ctx):**
     - Steps 1–3 and 5–6 as for approve (no user lock, no grant; app status and policy are not checked, so staff can always clean the queue).
     - `decide(row, "rejected", ...)`.
     - Audit row `app.join_request_rejected` with `changes={"status": change("pending","rejected")}`.
   - Logging: only `join_request_<event> request_id= app= user_id=`, never an email or display name (R11).
7. **Router.** These are POST actions returning 200 with the updated resource, not PATCH/DELETE and not 201/204, because they are state transitions. Each has `**require_permission(...)` with a 409 description added (`_with_409` pattern). The list sets `Cache-Control: no-store`.
8. **Test harness edits.**
   - `test_route_auth_classes.py`: add the 3 `BEARER` entries and `"request_id"` in `_fill()`.
   - `test_audit_coverage.py`:
     - add `JREQ = f"{V1}/apps/{{key}}/join-requests"`;
     - add `AUDITED` entries `("POST", f"{JREQ}/{{request_id}}/approve")` → `({"app.join_request_approved"}, _jr_approve, _jr_unknown_approve, 404)` and the same for reject;
     - success scenarios create a fresh approval app (unique key), a verified active user and a pending request;
     - replace `_SUCCESS_STATUS[method]` with a helper that returns 200 for these two routes (`_SUCCESS_STATUS_BY_ROUTE`), used in both `test_success_writes_one_row` and `test_no_secret_or_email_in_any_row`;
     - change `len(AUDITED) == 18` to `20`.
   - The identity harness keeps its `open` join scenario. The pending path's single row is covered in `test_join_requests_join.py`; add a one-line comment next to the join entry in `IDENTITY_AUDITED` saying so.
9. **Docs.**
   - `consumers.md` Joining section:
     - 202 `{"status":"pending","request_id"}` means "waiting for staff approval"; re-posting is idempotent;
     - after a 202, poll `/token` (not join) at a modest interval or on next app open; the join rate limit applies;
     - in the table, add rows 202 and 403 `join_rejected` ("rejected recently; try later; no reason given");
     - remove the `join_approval_unavailable` row and note the change;
     - `not_member.join_available` is now true for approval apps.
   - `identity-architecture.md`:
     - §2 Join text: the approval branch, the cooldown and the staff decision;
     - §3 data model: the `join_requests` line gains `decided_at`, the constraints and the indexes;
     - §4: update the join row; add a row for the 3 staff routes with their permissions;
     - S8: add the 3 audit actions.

Rules: R1 (router → service → repository; the service never raises `HTTPException`), R2 (§1), R3 (§7, harness §8), R4 (§3/§6 audit in the same transaction, after the last guard), R5 (§2), R6 (`ConflictError`/`NotFoundError`/`PermissionDeniedError` only), R7 (see Follow-ups), R8 (Tests), R9 (`/apps/{key}/join-requests` plural noun under the D18 key exception, `Page[T]`, UUID ids, timestamptz), R11 (no email in logs or audit; the verified email is re-checked at approval).

**Size estimate (prod code, excluding docs/tests):** migration ~75, model ~45, repository ~85, service ~150, router ~65, schemas ~35, membership changes ~60, tokens/orgs/routers/config/actions/errors ~45, for a total of **~560**, of which ~120 is migration and model boilerplate. Hand-written logic is ~440, which fits one cycle. See the Split proposal for the fallback.

## Specialist input used
- **api-platform-engineer.**
  - Adopted:
    - 202 on the same handler, idempotent same `request_id`;
    - member check before policy;
    - default role validated at request time;
    - approve uses the default role at approval time;
    - 409 (not 403) for staff-side state refusals;
    - POST actions returning 200;
    - cross-app id → 404;
    - one `join_available` helper;
    - `_fill()` / `BEARER` edits;
    - `granted_by` = staff.
  - Rejected:
    - **`details.join_pending`**: deferred to a follow-up (it changes an exact-match `details` contract that several tests pin; a re-POST of join already answers "pending").
    - **Removing `JoinApprovalUnavailableError`**: the CLI still uses it.
    - **Audit action name `join_request.created`**: I chose `app.join_requested` to sit with `app.joined`.
    - **`granted_role_key` in the read model**: there is no stored column. The audit row records `role_key`, and the user's role-assignments show the grant.
- **identity-access-engineer.**
  - Adopted:
    - name `AppJoinRequest`;
    - lock order app → user `FOR SHARE` → request `FOR UPDATE`;
    - the 409 reasons (`join_policy_changed` etc.);
    - cooldown from env, 7 days, 403 `join_rejected`;
    - already-member → approved without grant;
    - `join_available` false inside the cooldown;
    - no deny-list.
  - Rejected:
    - **`cancelled` status and the disable hook (R-f)**: R7. Approve-time re-checks already refuse disabled users, and staff can reject. This avoids a write in `UserService.set_status`.
    - **Approve-/request-time `role_has_org_permission` check**: the `open` join path does not do it either, and the write-time guards of ADR-0015 §3 already keep org permissions off default roles. Follow-up.
    - **`retry_after` detail**: not needed.
- **security-architect.**
  - Adopted:
    - permissions `admin:users:read` / `admin:roles:assign` (no seed migration, and nothing staff couldn't already grant);
    - `admin` key → 404 first;
    - deny-by-default 409 when the app is not active or the policy is not `approval`;
    - reject allowed regardless of app state;
    - no reject reason;
    - one audit row per decision with no email;
    - `Cache-Control: no-store` on the list;
    - `display_name` shown as untrusted text (a note in the docs for the admin UI).
  - Rejected:
    - **Allowing approve under `open`** (their Q3 suggestion): the owner default is "refused unless policy still approval", which is more conservative.
    - **Pending cap per app**: follow-up.
    - **`ON CONFLICT DO NOTHING`**: unnecessary under the app lock. The index is the backstop.
- **database-optimizer.**
  - Adopted:
    - columns, no `updated_at`, `String(20)` status with no server default;
    - both CHECKs; FKs (CASCADE / CASCADE / SET NULL); no "decided_by NOT NULL when decided" CHECK;
    - partial unique `uq_join_requests_pending`;
    - `ix_join_requests_app_id_status_created_at_id`;
    - `ix_join_requests_user_id_app_id_created_at` instead of a plain `user_id` index (it serves the cascade and the cooldown lookup);
    - no `decided_by` index;
    - DB-level CHECK tests;
    - a lossy downgrade docstring.
  - Rejected:
    - **Grant "upsert ON CONFLICT"**: `_grant_locked` is reused instead, after the already-member check, so its 409 cannot fire for a member. It still handles an expired duplicate in place.
- **Disagreements resolved:**
  - (a) **Approve under a changed policy**: refuse (owner default; identity agrees) over security's/db's "allow under open".
  - (b) **Status set**: no `cancelled` (db, R7) over identity.
  - (c) **409 shape**: `conflict` + `details[0].reason` (the house pattern in `app_credentials`) over api's separate codes. It is equally stable for clients and avoids new error classes.
  - (d) **Cooldown order**: an existing pending request is returned before the cooldown check, because it is idempotent and truthful.

## Assumptions
- `make_member_role` sets `apps.default_role_id`, and an app with no `org_mode` override is `none`. Org apps behave the same way: approval grants app scope only, as `open` join does (identity consult R-b).
- Org invitations stay unavailable on `approval` apps (`InvitationService._unavailable_reason`, the existing S1 guard). This ticket does not change that.
- `rate_limit("join")` (per IP) is the only request-rate control; the cooldown bounds the re-request loop.
- Staff may approve their own request on a non-admin app (consistent with `_guard_privileged`, which blocks self-assignment only on `admin`; the `admin` app never has requests because it is `closed` and its key → 404).
- When the user already has a membership at approve time, the request is still marked approved (the audit row has no `role_key`).

### Needs owner's decision (non-blocking; defaults applied, approve by merging)
1. **Permissions**: list = `admin:users:read` (rows show emails), approve/reject = `admin:roles:assign` (approve is a role grant). No new `admin:join-requests:*` keys and no seed migration.
2. **What approve grants**: the app's default role **at approval time**, app scope (`org_id NULL`), with no role in the approve body. Unusable default role (NULL, missing or `is_admin`) → 409 `default_role_unusable`.
3. **Approve preconditions**: refused (409) unless the app is `active` **and** `join_policy` is still `approval`. Reject is always allowed.
4. **Already a member at approve time** → marked approved, no grant, 200.
5. **Rejection cooldown**: `JOIN_REJECT_COOLDOWN_SECONDS`, default 7 days (0 = off). Inside it, join → 403 `join_rejected` (explicit code, no reason) and `join_available=false`.
6. **No permanent deny-list** (USR-03b F6) in v1.
7. **No reject reason** field in v1.
8. **Pending requests never expire**; there is no retention purge in this ticket.
9. **`join_approval_unavailable` disappears from the HTTP API** (it was documented as provisional). It remains only as the CLI `create-account --join` refusal for approval apps.

## Blocking questions
None.

## Split proposal
None. The plan is one feature (spec §5.3 `approval` policy) and fits one cycle (about 440 lines of hand-written logic plus ~120 lines of migration/model boilerplate).

Fallback, if the implementer's diff exceeds ~600 prod lines, is a split into two slices:
- **USR-03g-1**: migration, model, the cookie-side join 202, cooldown and `join_available`, plus `GET` list.
- **USR-03g-2**: approve/reject.

The implementer records this in `impl-log.md` and does not grow the diff. This plan covers both.

## Data model / migration
Table `join_requests` (new revision; `down_revision = "f3a8d51c9e27"`, confirm with `uv run alembic heads`):

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | `uuid` PK (`pk_join_requests`) | no | Python-side (`UUIDPkMixin`) |
| `app_id` | `uuid` FK `fk_join_requests_app_id_apps` → `apps.id` ON DELETE CASCADE | no | — |
| `user_id` | `uuid` FK `fk_join_requests_user_id_users` → `users.id` ON DELETE CASCADE | no | — |
| `status` | `varchar(20)` | no | none (service sets it) |
| `created_at` | `timestamptz` | no | `now()` (model `server_default=func.now()`, migration `sa.text("now()")`) |
| `decided_at` | `timestamptz` | yes | — |
| `decided_by` | `uuid` FK `fk_join_requests_decided_by_users` → `users.id` ON DELETE SET NULL | yes | — |

Constraints:
- `ck_join_requests_status_valid`: `status IN ('pending', 'approved', 'rejected')`
- `ck_join_requests_decided_iff_not_pending`: `(status = 'pending') = (decided_at IS NULL)`

Indexes:
- `uq_join_requests_pending` UNIQUE `(app_id, user_id)` `WHERE status = 'pending'`
- `ix_join_requests_app_id_status_created_at_id` `(app_id, status, created_at, id)`: the staff list
- `ix_join_requests_user_id_app_id_created_at` `(user_id, app_id, created_at)`: the user cascade and the cooldown lookup

`downgrade()`: drop the two plain indexes, drop `uq_join_requests_pending` (passing `postgresql_where`), then `drop_table`. The docstring states it is lossy (all join requests are dropped). The upgrade only creates a new table, so there are no lock concerns.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/apps/{key}/join` (cookie class, existing) | `{account_id}` | 200 `{"status":"member"}` \| **202 `{"status":"pending","request_id":uuid}`** | 200, 202, 401 `session_invalid`, 403 `email_unverified` / `join_closed` / `join_unavailable` / **`join_rejected`** / `app_mismatch` / `origin_not_allowed`, 422, 429 | cookie + Origin + `X-MM-App` (R3 class 2) |
| POST | `/api/v1/auth/token` (existing) | unchanged | `403 not_member` `details.join_available` is now true for `approval` apps with a default role, outside the cooldown | unchanged | cookie |
| GET | `/api/v1/apps/{key}/join-requests?status=pending\|approved\|rejected&limit&offset` | — | 200 `Page[JoinRequestRead]`, default `status=pending`, order `created_at, id`, `Cache-Control: no-store` | 200, 401, 403, 404 (unknown or `admin` app), 422 | `admin:users:read` |
| POST | `/api/v1/apps/{key}/join-requests/{request_id}/approve` | no body | 200 `JoinRequestRead` | 200, 401, 403, 404 (unknown app / id / other app's id), 409 `conflict` reason `not_pending` / `app_disabled` / `join_policy_changed` / `user_disabled` / `email_unverified` / `default_role_unusable`, 422 (bad uuid) | `admin:roles:assign` |
| POST | `/api/v1/apps/{key}/join-requests/{request_id}/reject` | no body | 200 `JoinRequestRead` | 200, 401, 403, 404, 409 `conflict` reason `not_pending`, 422 | `admin:roles:assign` |

`JoinRequestRead`: `id`, `app_key`, `user_id`, `email`, `display_name` (untrusted user text: the admin UI renders it as text only), `email_verified` (bool), `user_status`, `status`, `created_at`, `decided_at`, `decided_by`.

## Follow-ups (out of scope, R7)
- Cancel pending requests when a user is disabled, adding a `cancelled` status (identity R-f).
- Retention purge of decided `join_requests` in the `cleanup` CLI, keeping the newest rejection per (app, user) for at least the cooldown.
- Expiry of stale pending requests.
- Pending cap per app (security R4 backstop).
- Permanent deny-list / ban (USR-03b F6).
- Staff-only reject reason.
- `details.join_pending` on `not_member`.
- `role_has_org_permission` defense-in-depth in `usable_default_role` for every join path (open, approval, invitation).
- The open join reads the user without a lock (`membership/service.py:94`); use `get_for_share` (security F5).
- Optional per-account join rate limit.
- Update the stale `ticket.md` wording ("admin RBAC flag", "AuditRecorder").
