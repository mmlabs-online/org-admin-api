Status: READY

# Plan — ADM-004b: Audit identity events, denied privileged attempts (retention split to ADM-004c)

## Spec summary
- spec §5.4: "Append-only record of every mutating admin action: actor, action, target type/id,
  before/after (or diff), timestamp, request id. Read-only, filterable listing for auditors."
- spec §2 goal: "A complete, queryable audit log of admin actions."
- spec §6: "100% of mutating endpoints write an audit entry (once §5.4 lands)."
- spec §5.8 / identity-architecture S8: "Login (success/failure), logout, reuse revocation, password
  reset, email verification, denied privileged attempts (`outcome`) and retention are ADM-004b."
- spec Q6 (open): "Compliance needs (audit retention period, PII handling)?" Retention moves to
  ADM-004c (see Split proposal). The defaults are written down there as owner decisions, not as
  blockers.
- ADR-0009 §7 decided one failure-path mechanism, `record_failure`. **This plan supersedes §7 with
  one mechanism per path.** All four consults agree that a single rollback-first mechanism would undo
  the reuse revocation, and that it can't apply to login before Argon2. The ADR-0009 amendment
  records this (ticket wording conflict flagged under Assumptions, A1).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Each covered identity event writes exactly one row: `auth.login_succeeded`, `auth.login_failed` (credential-checked only), `auth.logged_out`, `auth.logged_out_all`, `device_session.reuse_revoked`, `auth.password_reset_completed`, `auth.email_verified`. A 429, a Turnstile-required/failed refusal or a reserve-`None` writes no row. | `tests/test_audit_identity.py` (one test per event; `test_login_refusals_write_no_row` parametrized over blocked-429, turnstile_required, turnstile_failed, reserve_none). `tests/test_audit_coverage.py::test_every_mutating_identity_route_is_classified`. |
| 2 | Denied privileged attempts (the exact list below) write one row each with `outcome=denied`, `action` = the attempted action, `changes={"reason": <code>, …catalog keys}`. The HTTP status, code and envelope are unchanged, and business state is unchanged. Plain `require_permission` 403s and `SystemActor` denials write nothing. | `tests/test_audit_denied.py::test_denied_writes_one_row[...]` (one case per site), `test_plain_permission_403_writes_no_row`, `test_denied_leaves_state_unchanged`. The inverted `tests/test_audit_coverage.py::test_denied_guard_writes_row`. |
| 3 | An audit write failure on a failure path never turns the response into a 500 (test). | `tests/test_audit_denied.py::test_audit_add_fails_denied_still_403` and `test_audit_commit_fails_denied_still_409`. `tests/test_audit_identity.py::test_audit_add_fails_login_still_401` and `test_audit_add_fails_reuse_still_401_and_revoked`. Each asserts the same body as without the fault and an `audit_write_failed` log record carrying no email. |
| 4 | No password, token, cookie, hash or email of an unknown account appears in any row (test). | `tests/test_audit_identity.py::test_no_secret_in_identity_rows` searches every row's `changes`, `actor_label`, `user_agent` and `request_id` for the test emails (known and unknown), passwords, raw cookie values, reset/verify tokens and the email HMAC hex. |
| 5 | `outcome` column + `outcome` filter on `GET /api/v1/audit-logs` (additive). | `tests/test_audit_schema.py::test_outcome_default_and_check`, `test_outcome_partial_index_used`. `tests/test_audit_logs_api.py::test_outcome_field_present`, `test_filter_by_outcome`, `test_invalid_outcome_422`. `alembic check` is clean, and `alembic downgrade -1 && upgrade head` passes in CI. |
| 6 | Carried hardening: `_ALLOWED_KEYS` scoped to `email_retention.purged` with int values; scoped IPv6 rejected and IPv4-mapped normalised; C0+DEL stripped from user agent; `X-Request-ID` kept only from trusted proxies. | `tests/test_audit_logger.py::test_allowed_keys_only_for_retention_ints`, `test_scoped_ipv6_dropped`, `test_ipv4_mapped_normalised`, `test_user_agent_control_chars_stripped` (a NUL in the UA still gives a row on the denied and login-failure paths). `tests/test_request_id.py::test_inbound_ignored_from_untrusted_peer`, `test_inbound_kept_from_trusted_peer`. |
| 7 | `make check` green. | CI / `make check`. |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/core/errors.py` | Add `DENIED_ERRORS = (AdminWildcardRequiredError, SelfAssignmentForbiddenError, SelfStatusChangeForbiddenError, LastSuperAdminError)` | One definition of what counts as "denied" (AC2) |
| `app/core/client_ip.py` | Extract `peer_is_trusted(host, cidrs) -> bool`, used by `client_ip()` | Shared by the request-id hardening (AC6) |
| `app/core/request_id.py` | `RequestIdMiddleware(app, trusted_proxy_cidrs=...)`: keep a valid inbound id only when `scope["client"]` is a trusted peer, otherwise generate one | Security carried item (AC6) |
| `app/main.py` | Pass `trusted_proxy_cidrs=settings.trusted_proxy_cidrs` to the middleware | Wiring |
| `app/modules/audit/actions.py` | `AuditOutcome` StrEnum; 7 identity actions; `AuditTarget.DEVICE_SESSION`; update the `ANONYMOUS` comment | Catalogue (AC1, AC5) |
| `app/modules/audit/context.py` | `AnonymousActor` frozen sentinel + `ANONYMOUS` instance | Unknown/unauthenticated actors (AC1, AC4) |
| `app/modules/audit/service.py` | `record()` accepts `AnonymousActor` and flat `changes`; new `record_failure`, `record_denied`, `record_in_savepoint`; a shared private row builder; `_valid_ip`/UA/`_ALLOWED_KEYS` hardening | The three failure-path mechanisms (AC2, AC3, AC6) |
| `app/modules/audit/repository.py` | `rollback()`, `commit()`, `begin_nested()` passthroughs (no row mutation); `outcome` in `AuditLogFilter` + where clause; docstring update | R1: services stay ORM-free; filter (AC5) |
| `app/modules/audit/models.py` | `outcome` column, `outcome_valid` CHECK, partial `ix_audit_log_outcome` | AC5 (texts must match the migration) |
| `app/modules/audit/schemas.py` | `outcome: AuditOutcome` (always present) | AC5 |
| `app/modules/audit/router.py` | `outcome: AuditOutcome \| None` query filter | AC5 |
| `migrations/versions/20261115_<rev>_add_audit_log_outcome.py` | Additive migration (see Data model) | R2 |
| `app/modules/rbac/service.py` | Explicit `try/except DENIED_ERRORS` + `record_denied` + `raise` at the public mutators: `AppService.update`, `PermissionService.create/update/delete`, `RoleService.create/update/delete/replace_permissions`, `AssignmentService.assign/revoke` | AC2 |
| `app/modules/users/service.py` | Same pattern in `set_status` | AC2 |
| `app/modules/sessions/repository.py` | `revoke_device` → `int \| None` (accounts revoked; `None` when the device UPDATE matched nothing); `revoke_account` → `uuid.UUID \| None` (`RETURNING user_id`); `revoke_all_accounts_of_user` → `int` (rowcount) | Write rows only for real revocations (AC1) |
| `app/modules/sessions/service.py` | Constructor takes `audit_ctx: AuditContext = AuditContext.NONE` and builds an `AuditLogger`. Reuse row: `record_in_savepoint` before the commit when `commit_on_reuse=True`, plain `record` otherwise. `revoke_account`/`revoke_device` pass through the return values | AC1, AC3 |
| `app/modules/auth/service.py` | `LoginService`: `AuditLogger`; `login(..., app_key, ctx)`; success `record`; failure `record_failure` after Argon2. `LogoutService(session, devices)`: `logout(..., app_key, ctx)` writes `auth.logged_out` / `auth.logged_out_all` | AC1 |
| `app/modules/auth/password_reset.py` | `reset(token, pw, *, ctx)` writes `auth.password_reset_completed` | AC1 |
| `app/modules/auth/email_verification.py` | `verify(token, *, ctx)` writes `auth.email_verified` only when `mark_email_verified` ran | AC1 |
| `app/modules/auth/deps.py` | `get_device_sessions` depends on `audit_context` and passes `audit_ctx` | Reuse rows on every cookie route |
| `app/modules/auth/router.py` | login/logout/verify-email/password-reset take `ctx: AuditContextDep` (logout also `session: SessionDep`) and pass `ctx` and `_app.key` on | R1 (router reads HTTP, service gets context) |
| `docs/adr/0009-audit-log.md` | `## Amendment — failure paths, identity events, outcome (ADM-004b, 2026-11-15)` | Rule 7: the decision outlives the ticket |
| `docs/identity-architecture.md` | Rewrite S8 to list the identity events, denied rows, `outcome`; retention → ADM-004c | Keep the design doc true |
| `docs/deploy.md` | Audit section: `outcome`, denied/failed rows, `X-Request-ID` is kept only from `TRUSTED_PROXY_CIDRS` peers (correlation, not evidence); retention still indefinite until ADM-004c | Ops truth |
| `tests/test_audit_identity.py` (new) | Identity event tests (see Tests) | AC1, AC3, AC4 |
| `tests/test_audit_denied.py` (new) | Denied-attempt tests | AC2, AC3 |
| `tests/test_audit_coverage.py` | Invert `test_denied_guard_writes_no_row` → `test_denied_guard_writes_row`; add the identity route classification; commit setup before denied calls; trusted-proxy override in `test_row_carries_request_context` | AC1, AC2 |
| `tests/test_audit_logger.py` | Anonymous actor, flat changes, hardening, `record_failure` / `record_in_savepoint` unit tests (CancelledError propagates) | AC3, AC6 |
| `tests/test_audit_schema.py` | Outcome default/CHECK/index tests | AC5 |
| `tests/test_audit_logs_api.py` | `outcome` field + filter tests | AC5 |
| `tests/test_request_id.py`, `tests/test_auth_join.py` | Clients that expect an inbound id echoed use `trusted_proxy_cidrs=["127.0.0.0/8"]`; new untrusted/trusted tests | AC6 (existing behaviour now needs a trusted peer) |
| `tests/test_admin_guards.py`, `tests/test_rbac_assignments_api.py` | `await db_session.commit()` setup before each request that hits a denied guard or `last_super_admin`. `record_failure`'s rollback (= `ROLLBACK TO SAVEPOINT` under `create_savepoint`) would otherwise erase flushed setup | Test-harness consequence (DB consult finding 5) |
| `tests/test_auth_logout.py`, `tests/test_device_sessions.py`, `tests/test_auth_login*.py` (only if they break) | Adjust to new service signatures / return types | Signature changes only, no behaviour change |

No other files. If another existing test breaks only because setup was not committed, or because of a signature change, the implementer fixes it and records the file in `impl-log.md`.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `test_audit_identity.py › test_login_success_row` | 1 row: actor `user`=user.id, target user, `app_key`, changes `{account_session_id, device_session_id}`, outcome success |
| `› test_login_failed_unknown_email_row` | 1 row: `actor_kind=anonymous`, no actor id/label, no target, changes `{"reason": "unknown_account"}`, outcome failure; no email, no HMAC |
| `› test_login_failed_wrong_password_row` | anonymous actor, target user, `{"reason": "invalid_credentials", "failures": n}` |
| `› test_login_failed_disabled_user_row` | correct password + disabled → `reason=user_disabled`; response identical to the wrong-password 401 |
| `› test_login_refusals_write_no_row[blocked\|turnstile_required\|turnstile_failed\|reserve_none]` | AC1 negative |
| `› test_logout_one_account_row` / `› test_logout_unknown_account_no_row` | `auth.logged_out` only when a row was revoked (N1 / foreign id → 0 rows) |
| `› test_logout_all_row` | `auth.logged_out_all`, anonymous actor, target `device_session`, `{"accounts_revoked": n}` |
| `› test_reuse_on_cookie_route_row` | `device_session.reuse_revoked`, `{"accounts_revoked": n, "during_login": false}`, committed despite the 401 |
| `› test_reuse_during_login_row` | same action, `during_login: true`, in the login transaction |
| `› test_reuse_already_revoked_no_second_row` | a second reuse attempt (device already revoked) writes nothing |
| `› test_reuse_during_login_audit_failure_is_500` | pins the success-path atomic behaviour (identity consult) |
| `› test_password_reset_row` | `auth.password_reset_completed`, actor user, `{"account_sessions_revoked": n, "verified": bool}`; no separate `auth.email_verified` row |
| `› test_verify_email_row` / `› test_verify_email_idempotent_no_row` / `› test_bad_token_no_row` | `auth.email_verified` only when it changed state; failed verify/reset tokens write nothing |
| `› test_audit_add_fails_login_still_401` | monkeypatch `AuditRepository.add` to raise → same 401 body; reservation counter still incremented; `audit_write_failed` logged |
| `› test_audit_add_fails_reuse_still_401_and_revoked` | add raises inside the savepoint → 401; device and accounts `revoked_at` set (fresh query) |
| `› test_no_secret_in_identity_rows` | AC4 |
| `test_audit_denied.py › test_denied_writes_one_row[<site>]` | one case per entry in the denied list below: status/code/envelope equal to today's, one row with action, `outcome=denied`, actor id, target, `changes` exactly as specified |
| `› test_denied_leaves_state_unchanged` | e.g. admin-app PATCH denied → name unchanged; revoke 409 → assignment still exists |
| `› test_plain_permission_403_writes_no_row` | route-level `require_permission` 403 → 0 rows |
| `› test_non_guard_409_writes_no_row` | e.g. `role_has_assignments` 409 → 0 rows |
| `› test_audit_add_fails_denied_still_403` / `› test_audit_commit_fails_denied_still_409` | AC3 (fault injection on `add` and on `commit`) |
| `› test_denied_row_with_nul_user_agent` | UA with `\x00` → row still written (AC6, security finding 5) |
| `test_audit_coverage.py › test_denied_guard_writes_row` | inverted former `test_denied_guard_writes_no_row` |
| `› test_every_mutating_identity_route_is_classified` | `IDENTITY_AUDITED ∪ IDENTITY_NOT_AUDITED == {non-GET routes in PUBLIC ∪ COOKIE}`, the two sets disjoint |
| `› test_identity_success_writes_one_row[route]` | one success scenario per `IDENTITY_AUDITED` route |
| `test_audit_logger.py › test_anonymous_actor_row`, `› test_record_failure_rolls_back_pending_work`, `› test_record_failure_swallows_and_logs`, `› test_record_failure_cancelled_propagates`, `› test_record_in_savepoint_keeps_outer_work`, `› test_record_denied_skips_system_actor`, `› test_allowed_keys_only_for_retention_ints`, `› test_scoped_ipv6_dropped`, `› test_ipv4_mapped_normalised`, `› test_user_agent_control_chars_stripped` | Logger units |
| `test_audit_schema.py › test_outcome_default_and_check`, `› test_outcome_partial_index_used` | `INSERT` without outcome → `success`; `'other'` → CHECK violation; `EXPLAIN` under `SET LOCAL enable_seqscan=off` for `outcome='denied'` names `ix_audit_log_outcome` |
| `test_audit_logs_api.py › test_outcome_field_present`, `› test_filter_by_outcome`, `› test_invalid_outcome_422` | API (AC5) |
| `test_request_id.py › test_inbound_ignored_from_untrusted_peer`, `› test_inbound_kept_from_trusted_peer` | AC6 |

**Harness rules for all new tests.** Tests that reach `record_failure` use `make_strict_client` and
`await db_session.commit()` the setup first. Under `create_savepoint`, the rollback inside
`record_failure` is a `ROLLBACK TO SAVEPOINT` and erases uncommitted setup. Rows are read back
through `db_session` after the request.

## Approach
1. **Catalogue + context** (`actions.py`, `context.py`).
   - `AuditOutcome = success|failure|denied`.
   - Actions:
     - `AUTH_LOGIN_SUCCEEDED="auth.login_succeeded"`
     - `AUTH_LOGIN_FAILED="auth.login_failed"`
     - `AUTH_LOGGED_OUT="auth.logged_out"`
     - `AUTH_LOGGED_OUT_ALL="auth.logged_out_all"`
     - `DEVICE_SESSION_REUSE_REVOKED="device_session.reuse_revoked"`
     - `AUTH_PASSWORD_RESET_COMPLETED="auth.password_reset_completed"`
     - `AUTH_EMAIL_VERIFIED="auth.email_verified"`

     All past tense and all match `action_format`. `auth.logged_out`/`auth.logged_out_all` replace
     the ticket's `auth.logout`/`auth.logout_all`, for consistency (API consult, Assumption A3).
   - `AuditTarget.DEVICE_SESSION="device_session"`.
   - `@dataclass(frozen=True) class AnonymousActor` plus the module constant `ANONYMOUS = AnonymousActor()`.
2. **Logger** (`audit/service.py`). Type `AuditActor = Actor | EndUser | AnonymousActor`.
   - Private `_build(action, outcome, actor, ctx, target, app_key, org_id, changes) -> AuditLog`:
     - It runs the key check: `_FORBIDDEN_KEY` on every key. `_ALLOWED_KEYS` is exempt **only** when
       `action == EMAIL_RETENTION_PURGED` and the value is `{"from": None, "to": int}` (not bool).
     - It maps the actor: `SystemActor` → system; `AnonymousActor` → anonymous with no id or label;
       anything else → user.
     - It sets `outcome=outcome.value` explicitly.
     - `ip` goes through the hardened `_valid_ip`: `None` on `ValueError` or when an IPv6 address has
       a `scope_id`; an IPv4-mapped IPv6 address becomes its `ipv4_mapped`.
     - The UA has `re.sub(r"[\x00-\x1f\x7f]", "", ua)` applied first, is then cut to 512, and an
       empty result becomes `None`.
   - `changes: Mapping[str, Any]`: admin mutations keep `{field: {from, to}}`; identity and failure
     rows use a flat object of allowlisted scalars (ADR amendment).
   - `record(...)` stays as it is: build → `repo.add` (flush). It never commits, and it raises on a
     bug (success path, atomic, R4).
   - `record_failure(action, *, outcome, actor, ctx, target=None, app_key=None, org_id=None, changes=None) -> None`:
     - It takes **primitives only** (UUIDs, strings, ints, frozen dataclasses), never ORM objects.
       Callers copy `app.key`, `user.id` and similar values *before* the call.
     - It asserts `outcome != SUCCESS`.
     - It runs `try: row = _build(...); await repo.rollback(); await repo.add(row); await repo.commit()`.
     - `except Exception: logger.warning("audit_write_failed action=%s outcome=%s", action.value, outcome.value)`,
       followed by a best-effort `try: await repo.rollback() except Exception: pass`.
     - It never catches `BaseException`, so `CancelledError` propagates, and it never re-raises.
       The caller re-raises the original exception object.
   - `record_denied(action, *, exc: AppError, actor, ctx, target=None, app_key=None, org_id=None, facts=None)`:
     - If `actor` is not an `AdminPrincipal`, it returns (no row, no rollback).
     - Otherwise it calls `record_failure(..., outcome=DENIED, changes={"reason": exc.code, **(facts or {})})`.
   - `record_in_savepoint(action, *, actor, ctx, target=None, app_key=None, changes=None) -> None`:
     - It runs `try: async with repo.begin_nested(): await repo.add(_build(...))` and catches
       `Exception` to log `audit_write_failed`. It never commits and never rolls back the outer
       transaction.
     - It is used only for reuse with `commit_on_reuse=True`.
3. **Repository**.
   - `AuditRepository` gains `rollback()`, `commit()` and `begin_nested()`. They touch session state
     only, never rows; update the module docstring to say so.
   - `AuditLogFilter.outcome` plus the matching where clause.
4. **Model + migration + API** (see Data model / API contract).
5. **Denied attempts (R1, ADR-0009 §3).**
   - Write the pattern explicitly in each public mutator, never in `_guard_privileged`,
     `_grant_locked` or `_update`/`_create`, and never in a global handler:
     ```python
     try:
         <guard call or private helper that contains the guard>
     except DENIED_ERRORS as exc:
         await self.audit.record_denied(<action>, exc=exc, actor=actor, ctx=ctx, target=..., app_key=key, facts=...)
         raise
     ```
   - Every value passed is evaluated before the call, so before the rollback.
   - Only server-resolved values go in, plus `reason`. Never the request body, the error message or
     an email.
   - Exact list (security consult):

   | Public mutator | Guard(s) | action | target | facts |
   |---|---|---|---|---|
   | `AppService.update` (wraps `_update`) | G1 `require_super` | `app.updated` | `None` (only the key is known in the public method; `app_key=key` identifies the app, D18) | — |
   | `PermissionService.create` (wraps `_create`) | `require_super` | `permission.created` | `None` | — (the key is client input) |
   | `PermissionService.update` | `require_super` | `permission.updated` | `(PERMISSION, permission_id)` | — |
   | `PermissionService.delete` | `require_super` | `permission.deleted` | `(PERMISSION, permission_id)` | — |
   | `RoleService.create` | `require_super` | `role.created` | `None` | — |
   | `RoleService.update` (rename) | `require_super` | `role.updated` | `(ROLE, role_id)` | `role_key` |
   | `RoleService.delete` | `require_super` | `role.deleted` | `(ROLE, role_id)` | `role_key` |
   | `RoleService.replace_permissions` | `require_super` (G2) | `role.permissions_replaced` | `(ROLE, role_id)` | `role_key`, `permission_keys_added`, `permission_keys_removed` (sorted, from `added`/`removed`) |
   | `AssignmentService.assign` (wraps `_grant_locked`) | `require_super`, `self_assignment_forbidden` | `role_assignment.granted` | `(USER, user_id)` (no assignment exists) | `role_key`; `org_id=data.org_id` (validated before the guard) |
   | `AssignmentService.revoke` | `_guard_privileged` + `last_super_admin` 409 | `role_assignment.revoked` | `(ROLE_ASSIGNMENT, assignment_id)` | `role_key`; `org_id=ra.org_id` |
   | `UserService.set_status` | `self_status_change_forbidden`, G5 `require_super`, `last_super_admin` 409 | `user.disabled`/`user.enabled` (requested status) | `(USER, user_id)` | — |

   - Keys must pass `_FORBIDDEN_KEY`. `role_key` and the `permission_keys_*` keys do.
   - Invariant: no listed site writes anything before its guard (security consult finding 1). The
     rollback is the backstop.
   - `record_failure` commits early, which releases the app lock before the 403. This is accepted
     (security consult, risks).
6. **Login** (`LoginService.login(data, *, cookies, remote_ip, user_agent, app_key, ctx)`).
   - Phase 1, Turnstile and reserve-`None` are unchanged and write nothing.
   - After the Argon2 check, track `password_ok` and `active` separately. On failure, copy
     `user_id = None if user is None else user.id`, then call
     `record_failure(AUTH_LOGIN_FAILED, outcome=FAILURE, actor=ANONYMOUS, ctx=ctx, app_key=app_key, ...)`:
     - unknown email: target `None`, `changes={"reason": "unknown_account"}`;
     - known email: target `(USER, user_id)`,
       `changes={"reason": "invalid_credentials" | "user_disabled", "failures": failures}`;
       `user_disabled` only when the password was correct.

     Then `raise InvalidCredentialsError(...)` with the same message.
   - The rollback only drops the `FOR SHARE` read. The reservation is already committed, so the
     counter survives.
   - Success: `record(AUTH_LOGIN_SUCCEEDED, actor=EndUser(user.id), target=(USER, user.id), app_key=app_key, changes={"account_session_id": account_id, "device_session_id": device.device_id})`
     in the request transaction, after `add_account`.
7. **Reuse** (`DeviceSessionService.resolve`).
   - Copy `device_id = device.id`, then run `revoked = await repo.revoke_device(device_id)`.
   - With `commit_on_reuse=True`:
     - if `revoked is not None`, call
       `record_in_savepoint(DEVICE_SESSION_REUSE_REVOKED, actor=ANONYMOUS, ctx=self.audit_ctx, target=(DEVICE_SESSION, device_id), changes={"accounts_revoked": revoked, "during_login": False})`;
     - then `repo.commit()` as before.
   - With `commit_on_reuse=False` (login), if `revoked is not None`, a plain `record(...)` with
     `during_login: True`.
   - **Never use rollback-first here.** It would undo the revocation.
   - `app_key` is `NULL`, because `resolve` has no app context.
8. **Logout** (`LogoutService(session, devices).logout(data, device, *, app_key, ctx)`).
   - `all`: `n = revoke_device(...)`. If `n is not None`, write `auth.logged_out_all` with actor
     `ANONYMOUS`, target `(DEVICE_SESSION, device.device_id)` and `{"accounts_revoked": n}`.
   - `account_id`: `uid = revoke_account(...)`. If `uid is not None`, write `auth.logged_out` with
     actor `EndUser(uid)`, target `(USER, uid)` and `{"account_session_id": data.account_id}`.
   - A no-op logout writes no row (N1, no oracle; the response is unchanged).
9. **Reset / verify.**
   - Reset: capture `was_unverified = user.email_verified_at is None` and
     `n = await sessions.revoke_all_accounts_of_user(user.id)`. At the end, write
     `record(AUTH_PASSWORD_RESET_COMPLETED, actor=EndUser(user.id), target=(USER, user.id), changes={"account_sessions_revoked": n, "verified": was_unverified})`.
     The key is `verified`: `email_verified` would trip `_FORBIDDEN_KEY`.
   - Verify: write `record(AUTH_EMAIL_VERIFIED, actor=EndUser(user.id), target=(USER, user.id))`
     only inside the `if user.email_verified_at is None:` branch. The idempotent early return writes
     nothing.
   - `app_key` is `NULL` on both, because they have no app context.
10. **Routers/deps.**
    - `get_device_sessions(request, session, ctx: AuditContextDep)` passes `audit_ctx=ctx`.
    - login, logout, verify-email and password-reset take `ctx: AuditContextDep` and pass it with
      `_app.key` where `AppContextDep` exists. `user_agent`/`remote_ip` stay as they are for
      device-session storage.
    - Not audited, with no change: signup, token, verify-email/resend, password/forgot, GET accounts.
11. **Request id.**
    - `RequestIdMiddleware(app, *, trusted_proxy_cidrs: Sequence[str] = ())` parses the networks once.
    - It keeps the inbound value only if `scope.get("client")` is a trusted peer
      (`peer_is_trusted`) and the value matches `_SAFE`. Otherwise it generates a UUID4.
12. **Docs.**
    - ADR-0009 amendment:
      - supersedes §6/§7 with the per-path table (`record` / `record_failure` / `record_in_savepoint` /
        `record_denied`);
      - the `outcome` column;
      - flat `changes` for identity and failure rows;
      - the denied list and call-site rule;
      - the identity event table (actor, target, changes);
      - not audited: signup, token, forgot, resend;
      - the X-Request-ID trust rule;
      - retention → ADM-004c.
    - identity-architecture S8.
    - deploy.md audit section.

**Rules.**
- R1: every SQLAlchemy call stays in repositories, including the session passthroughs on
  `AuditRepository`. Services take `AuditContext`, never `Request`.
- R2: one migration with a working downgrade, and no drift.
- R3: no new routes; auth classes are unchanged.
- R4: success identity rows are written in the same transaction. Failure rows go through the
  documented failure-path mechanisms.
- R5: no new config. `trusted_proxy_cidrs` already exists.
- R6: error bodies are unchanged (asserted).
- R7: retention is split out, and signup/token audit is a follow-up.
- R8: tests per above.
- R9: additive field and filter only.
- R11: no password, token, cookie, hash or full email in rows or logs. `audit_write_failed` logs the
  action and outcome only.

## Specialist input used
- **security-architect.**
  - Adopted:
    - the exact denied list and the AdminPrincipal-only rule;
    - attempted action + `outcome=denied`;
    - `{"reason": code}` + catalog keys;
    - explicit public-mutator call sites;
    - copy primitives before the rollback (finding 2);
    - login row only after Argon2;
    - no per-IP or per-actor audit cap;
    - all carried items (UA strip is required, scoped IPv6, `_ALLOWED_KEYS`, X-Request-ID trust);
    - retention defaults and the purge design, moved to ADM-004c.
  - Adjusted: for app/permission/role *create* denials, the plan omits the key and target. The key
    isn't server-resolved (it is client input) and no object exists yet.
- **identity-access-engineer.**
  - Adopted:
    - `AnonymousActor`;
    - actors and targets per event;
    - `DEVICE_SESSION` target;
    - logout only when a row was revoked (`RETURNING`);
    - reuse row only when the UPDATE matched;
    - the `begin_nested` reuse path;
    - context injected into `DeviceSessionService`;
    - signup and token not audited.
  - Adjusted:
    - `reason` values follow the orchestrator's guidance: `invalid_credentials|user_disabled`
      instead of `bad_password|inactive`, and `unknown_account` for unknown emails.
    - `email_verified` → `verified`, because of `_FORBIDDEN_KEY`.
    - `new_device` dropped, because `ResolvedDevice` cannot tell a mint from a rotate without more
      change.
- **database-optimizer.** Adopted:
  - metadata-only `ADD COLUMN` + named CHECK + partial index;
  - downgrade drops the index, then the column;
  - no UPDATE;
  - the `record_failure` sequence;
  - commit test setup under `create_savepoint`;
  - reuse via `begin_nested`;
  - GUC purge design and partitioning rejected (→ ADM-004c).
- **api-platform-engineer.**
  - Adopted:
    - `outcome` always present + enum filter;
    - past-tense `auth.logged_out(_all)`;
    - `AuditTarget.DEVICE_SESSION`;
    - identity route classification sets;
    - inverting the denied test;
    - a separate `audit-purge` CLI (→ ADM-004c).
  - Rejected:
    - "don't add a standalone index": the DB consult's *partial* index is cheap and serves the
      selective filters;
    - "known-account failed login as `actor_kind=user`": the orchestrator and identity consult chose
      anonymous, because the caller proved nothing;
    - "add the row before the reservation commit" for login: the outcome isn't known yet there.

## Assumptions
- **A1. Ticket wording superseded.** The ticket asks for "one failure-path mechanism" and to "add the
  row before that commit" for login. Both are superseded by the consults, which agree. Reuse uses
  `record_in_savepoint`, and login uses `record_failure` after the check. This is recorded in the
  ADR-0009 amendment (scope conflict flagged per CLAUDE.md).
- **A2. Known-user login failure.** The actor is anonymous, the target is the user and the reason is
  `invalid_credentials|user_disabled`. The `user_disabled` reason is visible only to `admin:*`
  holders, and never in the response.
- **A3. Action names.** The names are `auth.logged_out` / `auth.logged_out_all`, not the ticket's
  `auth.logout` / `auth.logout_all`. The catalogue is past tense, and these become frozen filter values.
- **A4. Not audited here.** Signup and token issuance are not audited. A follow-up offers
  `auth.signed_up` to the owner.
- **A5. Reuse audit failure inside login.** A failure there gives a 500. It is part of the success
  transaction, consistent with ADM-004, and pinned by a test.
- **A6. X-Request-ID behind cloudflared.** The proxy peer is trusted and Cloudflare forwards client
  request headers. So in production a client-supplied `X-Request-ID` still passes the trusted-peer
  check. The value is safe (regex) but remains client-influenced. deploy.md and the ADR say it is a
  correlation id, not evidence. Follow-up: derive the id from `CF-Ray` from trusted peers, or always
  generate one.
- **A7. Retention.** Retention stays indefinite until ADM-004c ships. Recorded owner defaults for
  ADM-004c (not blockers):
  - keep 365 days, with the full IP stored;
  - a DB-enforced purge floor of 90 days;
  - read-time IP masking (IPv4 /24, IPv6 /48, UA hidden) before `admin:audit:read` is widened (a
    follow-up).
- **A8. Abuse resistance.** There is no audit-write rate cap (security consult). Volume is bounded by
  the existing login limits. A metric or alert on `login_failed`/`denied` per hour is a follow-up
  (deployment monitoring).

Follow-ups (not in this diff): `auth.signed_up`; audit reads of the audit log; the runtime DB role
split (INSERT/SELECT); an alert on failure/denied volume; request id from `CF-Ray`; read-time IP
masking before widening `admin:audit:read`; a rate limiter for admin endpoints (security finding 7).

## Blocking questions
None.

## Split proposal
Estimated production lines (app/ + migration): about **470**. Docs add about 90 and tests about 650.
Adding the retention purge would push production lines past ~700 and add a second migration (planner
rule 2), so it is split:

- **ADM-004b (this plan):** `outcome` + the API filter, identity events, denied attempts, the three
  failure-path mechanisms, and the carried hardening.
- **ADM-004c: Audit retention purge** (P1, areas db, security; depends on ADM-004b). Scope:
  - **Migration:** `CREATE OR REPLACE FUNCTION audit_log_immutable()`.
    - UPDATE and TRUNCATE are always refused.
    - DELETE is allowed only when `current_setting('audit.allow_purge', true) = 'on'` **and**
      `OLD.occurred_at < now() - interval '90 days'` (the floor is hard-coded).
    - The downgrade restores the original function text from `c3f1a9d27b60` verbatim.
  - **CLI:** `python -m app.cli audit-purge --older-than-days N [--dry-run]`.
    - It refuses a missing N or N < 90 (exit code 2).
    - It deletes in batches: `SET LOCAL audit.allow_purge='on'`, `lock_timeout`,
      `FOR UPDATE SKIP LOCKED` on `ix_audit_log_occurred_at_id`.
    - Each batch transaction writes one `audit_log.purged` row (`system:cli`,
      `{"cutoff": iso, "deleted": int}`).
    - There is never an HTTP route.
  - **Bypass statement:** it lives only in a CLI-only repository module. A grep test asserts that
    `audit.allow_purge` appears nowhere else in `app/` except that module and the migration. Tests
    show that DELETE without the GUC, and DELETE with the GUC on a recent row, still raise.
  - **Email cleanup:** a per-batch summary row (carried item).
  - **Ops:** add `audit-purge` to the deployment repo's ops workflow allowlist behind its own
    approval (ops input); write the deploy.md runbook.
  - **Owner decisions to confirm** (spec Q6): 365 days, full IP, 90-day floor.
  - **Open question for the owner:** does Vietnam Decree 13/2023 require a shorter retention period?

## Data model / migration
Migration `migrations/versions/20261115_<rev>_add_audit_log_outcome.py`, `down_revision = "c3f1a9d27b60"`.

**upgrade():**
- `op.add_column("audit_log", sa.Column("outcome", sa.Text(), server_default=sa.text("'success'"), nullable=False))`.
  This changes metadata only on PG17: no table rewrite and no backfill. Existing rows read
  `success`, and the append-only trigger is not fired (no UPDATE).
- `op.create_check_constraint(op.f("ck_audit_log_outcome_valid"), "audit_log", "outcome IN ('success', 'failure', 'denied')")`.
- `op.create_index("ix_audit_log_outcome", "audit_log", ["outcome", "occurred_at", "id"], postgresql_where=sa.text("outcome IN ('failure', 'denied')"))`.

**downgrade():**
- `op.drop_index("ix_audit_log_outcome", table_name="audit_log")`;
- `op.drop_column("audit_log", "outcome")` (the CHECK goes with the column);
- no data is touched.

**Model** (`AuditLog`):
- `outcome: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'success'"))`;
- `CheckConstraint("outcome IN ('success', 'failure', 'denied')", name="outcome_valid")`;
- `Index("ix_audit_log_outcome", "outcome", "occurred_at", "id", postgresql_where=text("outcome IN ('failure', 'denied')"))`.

Texts must match the migration exactly. Alembic doesn't compare CHECKs or (here) server defaults,
so the reviewer checks them by hand. The `alembic check` and downgrade/upgrade round-trip run in CI.

There are no other schema changes. `actor_consistent` already allows anonymous rows (no id, no
label), and `target_type` is free text, so `device_session` needs no constraint change.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/audit-logs` | Existing filters + `outcome` (`success`\|`failure`\|`denied`, optional). The `action` enum gains the 7 identity actions and `target_type` gains `device_session` (additive) | `Page[AuditLogRead]`, where each item gains `outcome: "success"\|"failure"\|"denied"` (always present, never null) | 200, 401, 403, 422 (invalid enum, `from`>`to`) | `admin:audit:read` |

There are no new endpoints. The existing identity and admin endpoints keep their exact status
codes, error codes and bodies (asserted in tests). Their only change is the audit rows they write.
