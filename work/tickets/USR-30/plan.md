Status: BLOCKED

> Blocked only on owner choices at the plan gate (High paths: `app/core/`, `app/modules/rbac/`, `.env.example`).
> Every question in `## Blocking questions` has a default; "defaults" from Ming makes this plan READY as written.

# Plan — USR-30: Org owners remove a member's org roles from the app (requested by simap)

## Scope summary
Scope sources relied on (quoted):

- Ticket goal: "An org member holding a new org-level permission (simap grants it to its org owner role `simap:admin`)
  can remove another person from the org for that app, by removing that person's org-scoped roles of the app in that
  org — without staff and without touching the account."
- Ticket scope: "Auth class: cookie (R3 class 2), like invitations … `POST /api/v1/auth/orgs/{org_id}/members/{user_id}/remove`
  with body `{account_id}`, `mm_device` cookie + `Origin` check + `X-MM-App: <app>`." "Permission: … `<app>:org:remove-member`
  … Checked org-exact in the service, as `<app>:org:invite` is; no `require_permission`." "Effect: deletes the target's
  non-expired org-scoped role assignments of `<app>` in that org (all of them). Never deletes or disables the account,
  never touches app-scope roles, other apps or other orgs."
- `docs/spec-v1.md` §5.3 (line 51): org-level permissions are `permissions.scope = 'org'` and are never in the default
  role of `org_mode = required` apps (ADR-0015).
- `docs/spec-v1.md` §5.8 (line 82): "Per-app access tokens … lifetime `ACCESS_TOKEN_TTL_SECONDS`, up to 12 h; 12 h in
  production"; (line 83) `org_roles` "role keys held in that org only; org apps authorize org data from it".
- ADR-0003 (`docs/adr/0003-rbac-keys-and-matching.md:22`): each key segment matches `[a-z][a-z0-9-]{0,47}`, exactly
  `<app>:<resource>:<action>` — `<app>:org:remove-member` is valid, simap's four-segment key is not.
- ADR-0014 §1 (`docs/adr/0014-org-invitations.md:12-14`): "Unknown, suspended and not-a-member orgs share one 403
  `org_access_denied`." §2 (:15-17): subset rule + "no `is_admin` permission"; "(no member removal in v1; staff only)".
  Consequences (:60): "Follow-ups: … member management."
- ADR-0015 §1–§5: `org_roles` claim; reserved scopes (`<app>:org:invite` is `org`); default role never holds `scope='org'`.
- ADR-0003 §9/§10 (in code, `app/modules/rbac/repository.py:81-93`): "Every RBAC writer must call [`lock_app`] first".

**Scope conflict (flagged, needs Ming — Q1):** `docs/spec-v1.md` §5.7 Later (line 322) lists "Org members view (member
management …)" as later, and ADR-0014 §2 says "no member removal in v1; staff only". The ticket asks for member removal
now. Per CLAUDE.md the spec wins until Ming approves; this plan adds a dated spec line, a new ADR-0026 (D35) and
"Amended by ADR-0026" pointers in ADR-0014, effective only on Ming's approval at this gate.

**Ticket deviations fixed by this plan (defaults, see Blocking questions):** unknown / suspended / not-a-member org
answers **403 `org_access_denied`**, not "404 as USR-29" (ADR-0014 §1, `/auth/token` at
`app/modules/tokens/service.py:111-113`, invitations at `app/modules/invitations/service.py:165-167`); success is
**204**; a rank rule is added (403 `member_outranks_caller`).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | A holder of `<app>:org:remove-member` removes a member: the member's `<app>` org roles in that org are gone; app-scope roles, other orgs and other apps untouched; **204** with `Cache-Control: no-store` | `tests/test_org_member_remove.py › test_owner_removes_member_204`, `test_only_this_app_and_org_touched`, `test_all_org_roles_of_target_removed`, `test_owner_removes_co_owner_204`, `test_delegate_removes_lower_member_204` |
| 2a | No cookie → 401; bad `Origin` / missing `X-MM-App` → 403 | `test_no_cookie_401`, `test_bad_origin_403`, `test_missing_app_header_403`, `test_disabled_app_403` |
| 2b | Caller unverified → 403 `email_unverified`; caller account not on this device (body/path swap) → 401 `session_invalid` | `test_unverified_caller_403`, `test_account_id_is_caller_not_target` |
| 2c | Unknown / suspended / not-a-member org → 403 `org_access_denied` (one answer) | `test_org_access_denied_one_answer` (parametrized: unknown uuid, suspended org, org where caller holds no role) |
| 2d | Caller lacks the permission in that org → 403 `org_remove_member_forbidden`, including when held only at app scope or in another org | `test_member_without_permission_403`, `test_permission_at_app_scope_or_other_org_does_not_count` |
| 2e | Self-removal → 403 `self_removal_forbidden` (also for a sole owner: 403 before 409) | `test_self_removal_403`, `test_sole_owner_self_removal_is_403_not_409` |
| 2f | Rank rule → 403 `member_outranks_caller` (target role holds `is_admin`, or target org permissions ⊄ caller org permissions) | `test_delegate_cannot_remove_owner_403`, `test_target_with_admin_permission_403` |
| 2g | Last org owner → 409 `last_org_owner`; other owners with an expiring assignment, or disabled/unverified, do not count | `test_last_owner_409`, `test_expiring_or_inactive_owner_does_not_count` |
| 2h | Unknown target / target without an unexpired `<app>` org role / retry after success → 404 `member_not_found` | `test_unknown_target_404`, `test_target_only_expired_roles_404`, `test_target_roles_in_other_app_only_404`, `test_retry_after_success_404` |
| 2i | `org_mode = none` → 422 `org_not_supported`; owner role unset → 403 `org_member_remove_unavailable`; extra body field / bad uuid → 422 | `test_org_mode_none_422`, `test_owner_role_unset_403`, `test_body_validation_422` |
| 3 | Two owners removing each other concurrently leaves at least one owner | `tests/test_org_member_remove_concurrency.py › test_two_owners_remove_each_other_one_wins` (committed data, real Postgres; statuses `[204, 409]`, exactly one owner-role row left) |
| 4 | One audit row per removal (actor, target user, org, app, role keys, count; no email); denied attempts per the existing pattern (no row for end users) | `test_success_writes_one_audit_row_without_email`, `test_refusals_write_no_audit_row`; `tests/test_audit_coverage.py › test_every_mutating_identity_route_is_classified` + `test_identity_success_writes_one_row` (new `IDENTITY_AUDITED` entry) |
| 4b | (Q8 default) The target's pending invitations to that org/app are revoked, one `organization.invitation_revoked` row each, reason `member_removed` | `test_pending_invitations_to_target_revoked` |
| 5 | After removal `/auth/token` with that `org_id` no longer carries the roles (403 `org_access_denied`); a token without `org` still works; lag documented | `test_token_after_removal`; reviewer reads `docs/consumers.md` "Removing a member" + ADR-0026 §Consequences |
| 6 | Route in the cookie class registry; CLAUDE.md R3 class 2 list mentions member removal | `tests/test_route_auth_classes.py` (`COOKIE` set + existing `test_every_route_is_classified`); `grep -n "member removal" CLAUDE.md` |
| 7 | Rate limits per IP and per user → 429 with `Retry-After` | `test_rate_limit_per_ip_429`, `test_rate_limit_per_user_429` |
| 8 | `<app>:org:remove-member` has the reserved scope `org` (Q11) | `tests/test_rbac_scopes.py › test_reserved_scope_remove_member_is_org` |
| 9 | Pure rules | `tests/test_org_member_rules.py` (see Tests) |
| — | `make check` green | `make check` |

## Ming verification after merge
- simap grants the permission: `simap:admin` is simap's **org owner role**, and the manifest sync refuses to change the
  owner role (`app/modules/rbac/manifest.py:159-164`, `role_not_manifest_managed`). So simap declares
  `simap:org:remove-member` (`scope: "org"`, `is_admin: false`) in its manifest, and **staff** add it to `simap:admin`
  in the admin portal (`PUT /api/v1/apps/simap/roles/{role_id}/permissions`). Then, on the deployed API, an owner in a
  simap org removes a test member from the simap UI and the member's next `/auth/token?org_id=` is 403. The implementer
  adds this step to `work/ming-todo.md`.

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/modules/organizations/member_service.py` (new) | `OrgMemberService.remove(...)`: check order, guards, delete, invitation revoke, audit | Business rules (R1) | no |
| `app/modules/organizations/member_rules.py` (new) | Pure `outranks(...)`, `leaves_no_owner(...)` (reuses `invitations.rules.is_subset`) | Pure logic first, unit-tested | no |
| `app/modules/organizations/auth_router.py` | New route `POST /{org_id}/members/{user_id}/remove`, 204, `rate_limit("org_member_remove")`, `_finish` | HTTP layer (R1) | no |
| `app/modules/organizations/schemas.py` | `OrgMemberRemove {account_id}` (`extra="forbid"`) | Request body | no |
| `app/modules/organizations/errors.py` | `OrgRemoveMemberForbiddenError` (403 `org_remove_member_forbidden`), `OrgMemberRemoveUnavailableError` (403 `org_member_remove_unavailable`), `SelfRemovalForbiddenError` (403 `self_removal_forbidden`), `MemberOutranksCallerError` (403 `member_outranks_caller`), `MemberNotFoundError` (404 `member_not_found`), `LastOrgOwnerError` (409 `last_org_owner`) | Error codes (R6); module-local, not `app/core/errors.py` | no |
| `app/modules/rbac/repository.py` | `org_roles_held(user_id, app_id, org_id) -> list[Role]`, `count_other_org_owners(owner_role_id, org_id, exclude_user_id) -> int`, `delete_org_assignments(user_id, app_id, org_id) -> list[str]` | SQLAlchemy only in repositories (R1) | **yes** |
| `app/modules/rbac/scopes.py` | `reserved_scope`: `<app>:org:remove-member` → `ORG` | Q11; default role can never hold it (ADR-0015 §3) | **yes** |
| `app/cli.py` | `MSG_RESERVED_SCOPE` text (line 87-90) names `org:remove-member` too | Message matches the rule; test imports the constant | no |
| `app/modules/audit/actions.py` | `ORGANIZATION_MEMBER_REMOVED = "organization.member_removed"` next to line 43 | Audit (R4) | no |
| `app/core/rate_limit.py` | Scope `"org_member_remove"` in `Scope` (line 71-92) and `_LIMITS` (line 94-133) | Per-IP limit | **yes** |
| `app/core/config.py` | `org_member_remove_rate_limit=30`, `_window_seconds=3600`, `org_member_remove_user_rate_limit=10`, `_window_seconds=600` (after line 100) | R5 | **yes** |
| `.env.example` | The four keys above, no values (near lines 46/56) | R5 | **yes** |
| `docs/adr/0026-org-member-removal.md` (new) | ADR "Org member removal (cookie class)" (content in Approach §9) | Decision outlives the ticket | no |
| `docs/adr/0014-org-invitations.md` | "Amended by ADR-0026" at §2 (line 17) and Consequences (line 60) | Supersedes "no member removal in v1" | no |
| `docs/identity-architecture.md` | §4 table row after line 177; decision log row **D35** after D34 (USR-29) | Decision log + API surface | no |
| `docs/spec-v1.md` | Dated line under §5.8 (after line 86) and §5.7 Later (line 322) pointer | Scope change (Q1) | no |
| `docs/consumers.md` | New `### Removing a member (USR-30, ADR-0026)` after USR-29's subsection; fix line 180 "there is no member removal in v1, staff only"; line 172 points to the lag note | Consumer contract | no |
| `CLAUDE.md` | R3 class 2 list: "… bug reports — D28; org member removal — D35" | AC 6 | no |
| `work/ming-todo.md` | Staff step: add `simap:org:remove-member` to `simap:admin` | Owner role is not manifest-managed | no |
| `tests/org_member_factories.py` (new) | Builds on `tests/invitation_factories.setup_calechip`: adds `calechip:org:remove-member` (scope org) to the owner role, a `calechip:org-manager` role (remove-member + data:read), helpers `remove_url`, `remove_body` | Shared setup without changing invitation tests | no |
| `tests/test_org_member_remove.py` (new) | API tests (see Tests) | R8 | no |
| `tests/test_org_member_remove_concurrency.py` (new) | Committed-data race | AC 3 | no |
| `tests/test_org_member_rules.py` (new) | Unit tests of `member_rules` | Pure logic | no |
| `tests/test_rbac_scopes.py` | Reserved-scope case | AC 8 | no |
| `tests/test_route_auth_classes.py` | Add `("POST", f"{V1}/auth/orgs/{{org_id}}/members/{{user_id}}/remove")` to `COOKIE` (line 49-61) | AC 6 | no |
| `tests/test_audit_coverage.py` | `IDENTITY_AUDITED` entry (line 913-920) with scenario `_id_member_remove`, action `organization.member_removed`, status 204 | Audit coverage registry | no |

Tier: **High** (rbac, `app/core/`, `.env.example`). No other file may change without a note in impl-log.

## Data model / migration
None. Verified:
- `<app>:org:invite` is not seeded per app by migration: `migrations/versions/20261117_d6f03e8b2a15_usr06c_permission_scope.py:12-13,56-58`
  only backfills the `scope` of existing rows. Permissions are app data, created by manifest / staff / `register-app`.
- `<app>:org:remove-member` arrives via the app's manifest (`scope='org'`, `is_admin=false`) or staff; reserving its
  scope is a pure-function change (`app/modules/rbac/scopes.py`), no stored data changes.
- Queries use existing indexes: `uq_role_assignments_user_role_org`, `role_assignments.role_id`, `.org_id`
  (`app/modules/rbac/models.py:200,207-208`).
- If review finds a migration is needed after all, its `down_revision` is USR-29's head revision.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/orgs/{org_id}/members/{user_id}/remove` | Cookie `mm_device`; headers `Origin` (an origin of the app), `X-MM-App: <app>`; body `{"account_id": uuid}` (`extra="forbid"`) — `account_id` = the **caller's** signed-in account on this device; `{user_id}` = the **target** user; `{org_id}` = the org | **204**, empty body, `Cache-Control: no-store`, rotated cookie when due | 204; 401 `session_invalid`; 403 `origin_not_allowed` (bad Origin / missing or unknown `X-MM-App` / disabled app), `email_unverified`, `org_access_denied`, `org_remove_member_forbidden`, `org_member_remove_unavailable`, `self_removal_forbidden`, `member_outranks_caller`; 404 `member_not_found`; 409 `last_org_owner`; 422 `org_not_supported` / request validation; 429 `rate_limited` | Cookie class (R3 class 2). No `require_permission`; the service checks `<app>:org:remove-member` **org-exact** (`authz.org_permissions`). `<app>` comes only from `AppContextDep` |

Error bodies use the R6 envelope; `details` never carry the target's email or account fields (`org_access_denied` keeps
`details={}` like invitations). `GET`-less: CORS for cookie routes already allows POST + `X-MM-App` with credentials
(`app/core/cors.py:34-38`), nothing to change.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_org_member_rules.py › test_outranks_false_when_subset_and_no_admin` | rank rule happy path |
| `… › test_outranks_true_when_target_has_admin_permission` | `is_admin` targets |
| `… › test_outranks_true_when_target_permission_not_held` | ⊄ |
| `… › test_wildcard_caller_covers_everything_but_target_wildcard_needs_wildcard` | `<app>:*` both sides (via `permits`) |
| `… › test_co_owners_equal_sets_do_not_outrank` | co-owner removal allowed |
| `… › test_leaves_no_owner_only_when_target_is_owner_and_no_other` | last-owner rule (truth table) |
| `tests/test_rbac_scopes.py › test_reserved_scope_remove_member_is_org` | reserved scope; other app prefix free |
| `tests/test_org_member_remove.py › test_owner_removes_member_204` | AC1, 204, no-store, roles gone |
| `… › test_only_this_app_and_org_touched` | target keeps app-scope role, roles in another org of same app, and another app's roles in the same org |
| `… › test_all_org_roles_of_target_removed` | target with two org roles (owner + viewer, another owner remains) |
| `… › test_owner_removes_co_owner_204` | co-owners |
| `… › test_delegate_removes_lower_member_204` | manager removes viewer |
| `… › test_no_cookie_401` / `test_bad_origin_403` / `test_missing_app_header_403` / `test_disabled_app_403` | AC2a |
| `… › test_unverified_caller_403` | AC2b |
| `… › test_account_id_is_caller_not_target` | body `account_id` = target's account (not on caller device) → 401; path `user_id` = caller → 403 self; nothing deleted |
| `… › test_org_access_denied_one_answer` (parametrized) | AC2c, identical body for the 3 cases |
| `… › test_member_without_permission_403` | AC2d (viewer calls) |
| `… › test_permission_at_app_scope_or_other_org_does_not_count` | org-exact: role with remove-member assigned at app scope, or in org B → 403 in org A |
| `… › test_self_removal_403` / `test_sole_owner_self_removal_is_403_not_409` | AC2e |
| `… › test_delegate_cannot_remove_owner_403` / `test_target_with_admin_permission_403` | AC2f |
| `… › test_last_owner_409` | caller holds `calechip:*` org role (not owner role), target is the only owner → 409, nothing deleted |
| `… › test_expiring_or_inactive_owner_does_not_count` | other owner has `expires_at` set / is disabled / unverified → still 409 (Q5 default) |
| `… › test_unknown_target_404` / `test_target_only_expired_roles_404` / `test_target_roles_in_other_app_only_404` / `test_retry_after_success_404` | AC2h |
| `… › test_org_mode_none_422` / `test_owner_role_unset_403` / `test_body_validation_422` | AC2i |
| `… › test_success_writes_one_audit_row_without_email` | one row: action `organization.member_removed`, `actor_user_id` = caller, target `("user", target_id)`, `app_key`, `org_id`, `changes == {"role_keys": [...], "count": n, "was_owner": bool}`, neither email in `changes` |
| `… › test_refusals_write_no_audit_row` | 403/404/409 cases add no row (Q7 default) |
| `… › test_pending_invitations_to_target_revoked` | target's pending invitation in this org/app revoked (+ one revoke row, reason `member_removed`); invitation to the same email in another org untouched (Q8) |
| `… › test_token_after_removal` | `/auth/token` with `org_id` → 403 `org_access_denied`; without `org_id` → 200 |
| `… › test_rate_limit_per_ip_429` / `test_rate_limit_per_user_429` | AC7 (settings override to 1) |
| `tests/test_org_member_remove_concurrency.py › test_two_owners_remove_each_other_one_wins` | AC3: two committed owners A, B; A removes B and B removes A via `asyncio.gather` (own connections, pattern of `tests/test_invitations_accept.py:525-607`); statuses sorted `[204, 409]`, loser code `last_org_owner`, exactly one owner-role row left; cleanup fixture |
| `tests/test_route_auth_classes.py` (registry entry) | AC6 |
| `tests/test_audit_coverage.py` (`IDENTITY_AUDITED` entry) | one row on success via the strict client |

## Approach
1. **Pure rules** (`member_rules.py`): `outranks(target_permission_keys, target_has_admin, caller_permissions) -> bool`
   = `target_has_admin or not is_subset(target_permission_keys, caller_permissions)` (reuse
   `app/modules/invitations/rules.py:14-16`); `leaves_no_owner(target_is_owner, other_owners) -> bool`. Unit tests.
2. **Reserved scope** (`rbac/scopes.py:6-12`): add `remove-member` → `ORG`; update `MSG_RESERVED_SCOPE` (`app/cli.py:87-90`).
   Manifest/staff paths already use `reserved_scope` (`rbac/manifest.py:73,122,156`, `rbac/service.py:219-228`).
3. **Repository** (`rbac/repository.py`, near lines 663-732; database clock via `func.now()`, pattern of `_is_active` line 547):
   - `org_roles_held(user_id, app_id, org_id)`: `Role` rows of unexpired assignments with `org_id = :org`, `Role.app_id = :app`.
   - `count_other_org_owners(owner_role_id, org_id, exclude_user_id)`: `role_id = owner AND org_id = :org AND user_id <> :target
     AND expires_at IS NULL` joined to `users` with `status = 'active' AND email_verified_at IS NOT NULL` (mirrors
     `_counting_super_admin`, line 704-715; Q5).
   - `delete_org_assignments(user_id, app_id, org_id)`: one `DELETE … WHERE user_id = :t AND org_id = :org AND role_id IN
     (SELECT id FROM roles WHERE app_id = :app) AND (expires_at IS NULL OR expires_at > now()) RETURNING role_id`, mapped to
     sorted role keys (core statement, `synchronize_session=False`). Docstring: "caller holds `lock_app`".
4. **Service** `OrgMemberService.remove(calling_app, device, org_id, target_user_id, account_id, *, ctx)` — check order
   (cheap, no oracle; consults identity rec. 2 / security "Check order"):
   1. `app = lock_app(calling_app.key)`; missing or not active → 403 `origin_not_allowed` (as `invitations/service.py:117-122`).
   2. `app.org_mode != required` → 422 via `organizations/auth_service._org_not_supported()` (line 45-49).
   3. `user = _user(device, account_id)` (copy of `invitations/service.py:103-115`: 401 `session_invalid`, 403 `email_unverified`).
   4. Per-user limit: `limiter.hit("org_member_remove_user", str(user.id), user_limit)` → `RateLimitedError` (pattern
      `bug_reports/service.py:121-123`; limiter + limit passed in by the router as `bug_reports/auth_router.py:56-62`).
   5. `authz.org_role_keys(user.id, app.key, org_id)` empty → 403 `OrgAccessDeniedError(details={})`.
   6. `caller_perms = authz.org_permissions(...)`; `not permits(caller_perms, f"{app.key}:org:remove-member")` → 403 `org_remove_member_forbidden`.
   7. `app.org_owner_role_id` unset or role missing → 403 `org_member_remove_unavailable` (warning log, fail closed).
   8. `target_user_id == user.id` → 403 `self_removal_forbidden` (compare user ids, before any target read).
   9. `roles = rbac.org_roles_held(target, app.id, org_id)` empty → 404 `member_not_found`.
   10. `outranks(authz.org_permissions(target, app.key, org_id), any(role_has_admin_permission(r.id)), caller_perms)` → 403 `member_outranks_caller`.
   11. `was_owner = owner_role.id in {r.id}`; `leaves_no_owner(was_owner, count_other_org_owners(...))` → 409 `last_org_owner`.
   12. `role_keys = delete_org_assignments(...)`; revoke the target's pending invitations: `InvitationRepository.supersede_pending(org_id, app.id, email_hasher.hmac(normalize_email(target.email)))`
       (`invitations/repository.py:55-71`), one `ORGANIZATION_INVITATION_REVOKED` row each, `changes={"reason": "member_removed"}`.
   13. `audit.record(ORGANIZATION_MEMBER_REMOVED, actor=EndUser(user.id), target=(AuditTarget.USER, target), app_key, org_id,
       changes={"role_keys": role_keys, "count": len(role_keys), "was_owner": was_owner})` — same transaction (R4), no email.
   14. `logger.info("org_member_removed org_id=%s app=%s target=%s count=%s", …)` (ids only, R11).
   Refusals at steps 6–11 log `logger.warning("org_member_remove_denied reason=%s org_id=%s app=%s", …)` and write no
   audit row (`record_denied` writes nothing for non-staff, `audit/service.py:210-212`; Q7).
   **Locking:** `lock_app` (`FOR NO KEY UPDATE` on `apps`) is the only lock and the first statement; every
   `role_assignments` writer takes it (consult-database-optimizer findings), so two owners removing each other queue on
   the app row and the second re-counts after the first commits → 409. No org-row lock, no advisory lock. Comment the
   invariant at the call site. Lock order app → users → org_invitations → role_assignments matches invitations/org create.
5. **Router** (`organizations/auth_router.py`): `@router.post("/{org_id}/members/{user_id}/remove", status_code=204,
   response_class=Response, dependencies=[rate_limit("org_member_remove")], responses={401,403,404,409,422,429})`,
   deps `AppContextDep`, `DeviceCookieDoc`, `DeviceDep`, `AuditContextDep`, body `OrgMemberRemove`; builds the service
   with `get_email_hasher(request)`, the limiter and the user `RateLimit`; then `_finish(request, response, device)` (line 26-30).
   No ORM in the router (R1). Pydantic validates both path uuids.
6. **Config / limits** (R5): four settings + `.env.example` keys; scope `org_member_remove` in `rate_limit.py`.
7. **Audit action** in `audit/actions.py`.
8. **Tests** as listed; register the route in `COOKIE` and `IDENTITY_AUDITED` (scenario: setup from
   `tests/org_member_factories.py`, owner removes a viewer, no pending invitations → exactly one row).
9. **ADR-0026 "Org member removal (cookie class)"** — Status "Proposed (owner approval at the USR-30 plan gate)", D35:
   Context (simap request; ADR-0014 deferred it). Decision: (1) cookie class route and body/path roles; app only from
   `X-MM-App`; (2) permission `<app>:org:remove-member`, reserved scope `org`, org-exact, declared by the app, granted by
   staff to the owner role (owner role is not manifest-managed); (3) check order and codes incl. 403 for unknown/suspended/
   non-member orgs (ADR-0014 §1); (4) rank rule (no `is_admin` target, target ⊆ caller; co-owners may remove each other);
   (5) last-owner rule and counting definition; `lock_app` only; (6) effect: non-expired org roles of `<app>` in that
   org, pending invitations to the target revoked; account, app-scope roles, other apps/orgs untouched; (7) audit: one
   success row, no denied rows for end users; (8) revocation lag: live tokens keep `org_roles` until `exp` (≤12 h prod),
   apps re-check via the USR-29 member read (or force a token refresh) before destructive org actions; (9) 204, retry 404.
   Rejected: app-credential variant (app would name the actor); `DELETE` verb (CORS allows only GET/POST for cookie
   routes, `cors.py:36`); token version / deny list (breaks offline JWKS verification); 200 with summary.
   Consequences: staff revoke of the last owner stays unguarded (staff action); co-owner takeover possible by design,
   recovery through staff; app-wide serialization of removals; removal leaves no row, so USR-29 incremental sync cannot
   see it (full re-read); follow-ups below.
10. **Docs**: ADR-0014 pointers; identity-architecture §4 row + D35; spec §5.8 dated line
    "> 2026-11-2x (USR-30, ADR-0026, owner-approved at the plan gate): org members holding `<app>:org:remove-member` in an
    org remove another member's org roles of that app (cookie class) …" and §5.7 pointer; consumers.md subsection (request,
    who may remove whom, error table, 204, retry → 404, token lag, re-check advice, USR-29 full re-read note, staff setup
    step); CLAUDE.md R3 class 2 wording.

Rules: R1 (router/service/repo split), R3 (cookie class, registry), R4 (row in the same transaction), R5 (limits from
config), R6 (envelope; 204/401/403/404/409/422/429 only), R7 (follow-ups below, not in diff), R8 (all classes of tests),
R9 (POST action route; deliberate like `/invitations/accept`; ADR records it), R11 (no email/tokens in logs/errors;
verified email required; `Origin` + `X-MM-App`).

## Specialist input used
- **identity-access-engineer**: adopted check order (rec. 2), 403 `org_access_denied` (finding / Q-A), rank rule (rec. 3),
  ADR-0026/D35 + ADR-0014 pointers (rec. 8), lag accepted (rec. 6), no "leave org" (Q-G), `optional` mode → 422 (Q-F; note
  `org_mode` is only `none|required` in code). Rejected: org row `FOR NO KEY UPDATE` (rec. 4) — `lock_app` already serializes
  every writer (db + security agree); delete expired rows too (rec. 5) — ticket says non-expired, db consult agrees (Q6);
  denied rows via `record_failure` (rec. 7) — ticket says "existing pattern", which writes none for end users (Q7).
- **security-architect**: adopted app only from `AppContextDep`, path/body swap test, rank rule, owner count excluding
  expiring assignments (`expires_at IS NULL`), revoke pending invitations (Q8), per-user limit, 204 + no-store + `_finish`,
  no denied rows (warning log). Code name `member_remove_forbidden` replaced by `member_outranks_caller` (distinct from the
  missing-permission code `org_remove_member_forbidden`).
- **database-optimizer**: adopted `lock_app` only, one-statement delete with RETURNING, mirroring the super-admin counting
  shape (plus user active+verified), non-expired delete, no migration, USR-29 `updated_since` limitation documented.
  Adjusted: owner count excludes expiring owners (security), not just expired ones (Q5). Verified no user hard-delete path
  in `app/` bypasses the lock (`grep "delete(User" app/` → none). `record_denied` suggestion rejected (writes nothing for
  end users). `last_org_owner` is not added to `DENIED_ERRORS` (staff-only tuple in `app/core/errors.py:102`).
- **api-platform-engineer**: adopted route shape, `account_id` = caller, `_finish`, rate-limit wiring, audit action name,
  docs contract, retry → 404. Rejected: 200 with summary (3 consults favour 204; R6 lists 204 for deletes; logout is a 204
  cookie POST), 404 for unrelated org (ADR-0014 consistency). Per-account bucket exists as a pattern (`bug_report_user`).

## Assumptions
- **USR-29 merges first.** Shared hotspot files (rebase expected): `app/core/rate_limit.py` (USR-29 adds a scope and,
  per its consults, a per-key hook — USR-30 only appends one scope literal + `_LIMITS` entry and must rebase onto it),
  `app/core/config.py`, `.env.example`, `app/modules/audit/actions.py`, `app/modules/rbac/repository.py`,
  `tests/test_route_auth_classes.py`, `tests/test_audit_coverage.py`, `docs/consumers.md` "Organizations" (USR-30's
  subsection goes after USR-29's), `docs/identity-architecture.md` (D35 after D34), `docs/spec-v1.md`, `CLAUDE.md` R3.
  ADR numbers: USR-29 = ADR-0025 / D34, USR-30 = ADR-0026 / D35. `work/tickets/USR-29/plan.md` did not exist when this
  plan was written.
- `ACCESS_TOKEN_TTL_SECONDS` default 900 s, production 43200 s (`app/core/config.py:124`, spec §5.8).
- The `admin` app has `org_mode = none`, so it gets 422 at step 2; nothing else needed.
- A disabled target user with org roles is removable (target status is not checked); a target that does not exist gets 404.
- `_user` is duplicated a fourth time (as in invitations, org self-service, tokens); de-duplication is a follow-up.
- Follow-ups (not in this diff): self-service "leave org"; cookie-side member list; guard staff revoke of the last org
  owner; push/webhook on removal; refuse app-scope grants of roles holding `org` permissions (ADR-0015 follow-up).

## Blocking questions
1. **Scope:** spec §5.7 and ADR-0014 §2 defer member management. Approve member removal in v1 (dated §5.8 line, §5.7 pointer,
   ADR-0026/D35, "Amended by ADR-0026" in ADR-0014)? — **Default: yes.**
2. **Unknown / suspended / not-a-member org:** 403 `org_access_denied` (ADR-0014, `/auth/token`, invitations) or 404 (ticket)? —
   **Default: 403; the ticket AC wording is corrected.**
3. **Rank rule:** refuse with 403 `member_outranks_caller` if any target org role holds an `is_admin` permission or the
   target's org permissions are not a subset of the caller's; co-owners may remove each other? — **Default: yes.**
4. **Response:** 204 (db, identity, security) or 200 `{org_id, user_id, removed_roles, removed_count}` (api)? —
   **Default: 204; a retry after success gets 404 `member_not_found` (documented: treat 404 after a timeout as done).**
5. **Last-owner counting and lock:** count only other owner-role holders in that org with `expires_at IS NULL`, user
   active and verified, under `lock_app` only (no org-row lock)? — **Default: yes.**
6. **What is deleted:** only the target's non-expired `<app>` org assignments (ticket, db) or expired ones too (identity)? —
   **Default: non-expired only.**
7. **Denied attempts:** no audit row for end-user refusals (existing pattern; warning log line without email) or a `denied`
   row via `record_failure` for confirmed members (identity)? — **Default: no row.**
8. **Pending invitations:** revoke the target's pending invitations to that org/app on removal (audited, reason
   `member_removed`)? — **Default: yes, in this ticket** (otherwise the removed person can rejoin by accepting).
9. **Revocation lag:** accept that live tokens keep `org_roles` until `exp` (≤12 h in production), documented, with apps
   advised to re-check via the USR-29 read before destructive org actions? — **Default: yes.**
10. **Rate limits:** per IP 30/hour and per user 10/10 min, in config? — **Default: yes.**
11. **Reserved scope:** fix `<app>:org:remove-member` to `scope = org` like `<app>:org:invite` (a contradicting declaration →
    422 `reserved_scope`)? — **Default: yes.**
12. **Out of scope confirmations:** `org_mode` other than `required` → 422 `org_not_supported`; self-service "leave org" is a
    follow-up ticket. — **Default: yes.**

## Split proposal
None. One endpoint; product code is about 350 changed lines (service ~170, repository ~70, router/schemas/errors ~80,
rules/config/scopes ~40); tests and docs add more but belong to the same cycle.
