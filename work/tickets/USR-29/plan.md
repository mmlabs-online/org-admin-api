Status: READY

> BLOCKED only for owner choices at the High-path plan gate. Every question below has a default, and the plan is written on the defaults: if Ming answers "all defaults", flip this line to `READY` and nothing else changes. A non-default answer changes only the sections named in that question.

# Plan — USR-29: App-facing read of an org's members for org-mode apps (requested by simap)

## Scope summary
Ticket `work/tickets/USR-29/ticket.md`, "Scope":
> Server-to-server read authenticated with an app credential (`Authorization: Bearer mmak_…`), e.g. `GET /api/v1/apps/{key}/orgs/{org_id}/members`. The credential's app must equal `{key}` (403 `app_credential_mismatch` otherwise). Built on the scoped app credentials of ADR-0021 / ADM-034 with a new read scope …
> Response `Page[T]` (same pagination as other lists, stable sort), `Cache-Control: no-store`: `user_id` (= token `sub`), `email`, `email_verified`, `display_name`, `status` (`active` | `disabled`), `org_roles` (role keys of `{key}` held in that org, non-expired), `joined_at`. Only users holding at least one org-scoped role of `{key}` in that org; disabled users are listed with `status = disabled`.
> Optional filters: `updated_since` …, `status`.
> Errors: one 404 answer (`org_access_denied`) for an unknown org, an org with no relation to `{key}`, or a suspended org; 422 `org_not_supported` for `org_mode = none` apps; per-credential (and per-IP) rate limit; no CORS.
> Audit: one row per call (actor `SystemActor("app:{key}/{key_id}")`), ids and counts only …

Scope sources it rests on:
- Spec §5.3: "users get roles per app, optionally limited to one organization"; `org_mode` (`none` | `required`).
- Spec §5.8: "**Organizations:** optional per app (`org_mode`); one `organizations` table owned by this Identity module (D10, D15, USR-06)." and "Claims: `sub`, … `org_roles` (if scoped: role keys held in that org only …)".
- Spec §5.7 Later: "Org members view (member management; the read-only staff view of an org's members and invitations ships in v1 as USR-23 …)".
- ADR-0021 §1: "App credentials gain a **`scope`** … Each key has **exactly one** scope … at most **2 active keys per (app, scope)**".
- ADR-0022 §3: "Runtime scopes do not take `lock_app` … `last_used_at` is written by a conditional `UPDATE ... WHERE revoked_at IS NULL AND (last_used_at IS NULL OR last_used_at < now() - 60 s)`. … A request already in flight during a revoke may complete; this is accepted."
- ADR-0022 §7: "Three buckets: a generous per-IP bucket before auth, … and a per-key bucket after auth. Values live in `app/core/config.py` (R5)".
- CLAUDE.md R3 class 4 (a): app credential "accepted only on the routes its ADR lists for it (registry: `tests/test_route_auth_classes.py`)".

**Spec conflict (flagged, needs Ming at the gate):** no spec section covers an app-facing member read. Spec §5.7 lists "Org members view" as Later (USR-23 is the staff read only), and spec §8 Q4 says "No pull endpoint and no app-credential widening for now". That sentence is about entitlements (ADR-0017 §2), but it is a pull endpoint and a class-4 widening. Per CLAUDE.md the spec wins, so this ticket must not ship without Ming's confirmation (Q1). The plan adds dated spec lines. It does not rewrite §5.7 or §8 Q4.

## Acceptance criteria → verification
| # | Criterion (ticket) | How the reviewer verifies it |
|---|---|---|
| 1 | simap's credential (right scope) gets the members of an org where people hold simap org roles; paging and stable order tested | `tests/test_app_org_members.py::test_lists_members_one_row_per_user`, `::test_paging_is_stable_by_user_id`, `::test_total_counts_distinct_users`, `::test_limit_over_100_is_422` |
| 2 | Another app's credential → 403 `app_credential_mismatch` (audited denied); wrong scope → 403 `app_credential_scope`; admin JWT or deployer key → 401 | `test_app_org_members.py::test_other_app_credential_403_mismatch_audited`, `::test_manifest_and_releases_keys_403_scope`; parametrized registry tests in `tests/test_route_auth_classes.py` (`test_admin_jwt_on_app_credential_route_401`, `test_deployer_key_on_app_credential_route_401`, `test_app_credential_route_401_without_credential`, `test_app_credential_allow_list_is_exact`) pick the route up from `APP_CREDENTIAL` |
| 3 | No `{key}` org role in that org → not listed; disabled user listed with `status = disabled`; expired assignments do not count | `::test_user_without_key_role_not_listed` (app-scope role, other app's org role, other org), `::test_expired_assignment_ignored`, `::test_disabled_user_listed_minimised` |
| 4 | Unknown, unrelated and suspended orgs → same 404 `org_access_denied`; `org_mode = none` app → 422 `org_not_supported` | `::test_unknown_unrelated_suspended_org_same_404` (bodies compared byte for byte), `::test_org_mode_none_422` |
| 5 | One audit row per call without emails or the body; no email in logs; `Cache-Control: no-store` | `::test_success_writes_one_audit_row_counts_only`, `::test_404_writes_one_denied_row`, `::test_no_email_in_logs` (caplog at DEBUG), `::test_cache_control_no_store`; `tests/test_audit_coverage.py::test_app_credential_routes_are_classified` + new `::test_org_members_read_route_is_audited` |
| 6 | `updated_since` and `status` filters tested; contract documented in `docs/consumers.md` "Organizations" | `::test_status_filter`, `::test_status_filter_invalid_422`, `::test_updated_since_user_change_and_new_grant`, `::test_updated_since_requires_timezone_422_no_echo`; reviewer reads the new subsection in `docs/consumers.md` |
| 7 (plan) | Runtime-key path: no `lock_app`, throttled `last_used_at`, per-key bucket; scope refused for `admin` | `tests/test_app_credential_auth.py::test_runtime_scope_takes_no_app_lock`, `::test_runtime_touch_throttled_60s`, `::test_revoked_runtime_key_401`; `test_app_org_members.py::test_per_key_rate_limit_429`, `::test_per_ip_rate_limit_429_before_auth`; `tests/test_app_credentials_api.py::test_issue_org_members_scope`, `::test_org_members_scope_refused_for_admin_app` |
| 8 (plan) | Migration widens the CHECK reversibly; the model CHECK text is byte-identical | `tests/test_app_credential_scope_migration.py::test_org_members_scope_round_trip`; `uv run alembic check` clean |

## Ming verification after merge
- Issue an `org-members:read` key for `simap` in staging/production (admin API or admin-pan) and store it as `MMLABS_ORG_MEMBERS_CREDENTIAL` in simap's backend deploy environment only. Then call the route once from the simap backend.
- Run `alembic upgrade head` on production as a deploy step (Ming's; `docs/deploy.md`).

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `migrations/versions/20261121_<rev>_usr29_org_members_read_scope.py` | new: `down_revision = "911eb99ddf8b"`; drop and recreate `ck_app_credentials_scope_valid` with `'org-members:read'`; downgrade deletes those keys first | R2, new scope value | **High** |
| `app/modules/app_credentials/models.py` | `AppCredentialScope.ORG_MEMBERS_READ = "org-members:read"`; `RUNTIME_SCOPES` frozenset; CHECK text (lines 31-32) updated byte-identically; docstring (line 18) | new scope; shared runtime-scope set for ADM-042 | **High** |
| `app/modules/app_credentials/repository.py` | new `touch_last_used_throttled(credential_id, *, interval_seconds)` next to `touch_last_used` (line 77) | ADR-0022 §3 throttled touch | **High** |
| `app/modules/app_credentials/service.py` | `authenticate` (152-194): for a scope in `RUNTIME_SCOPES`, call the throttled touch and return, with no `confirm`/`lock_app`. `RUNTIME_TOUCH_INTERVAL_SECONDS = 60` | runtime hot path | **High** |
| `app/modules/app_credentials/deps.py` | `RequireAppCredential.__init__` refuses `lock=True` for a runtime scope (ValueError at import); new `OrgMembersCredentialDep` (scope `ORG_MEMBERS_READ`, denied action `ORGANIZATION_MEMBERS_READ`, `lock=False`); module docstring | route dependency | **High** |
| `app/core/rate_limit.py` | generic post-auth per-subject bucket: `SubjectScope` Literal, `_SUBJECT_LIMITS`, `enforce_subject_limit(request, scope, subject)`. IP scope `"app_org_members"` added to `Scope`/`_LIMITS` | per-key limit (ADR-0022 §7), reusable by ADM-042 | **High** |
| `app/core/config.py` | `app_org_members_rate_limit=120`, `app_org_members_rate_window_seconds=60`, `app_org_members_key_rate_limit=60`, `app_org_members_key_rate_window_seconds=60` (after line 115) | R5 | **High** |
| `.env.example` | the four keys, no values (after line 78) | R5; `tests/test_config.py::test_env_example_keys_only` | **High** |
| `app/modules/rbac/repository.py` | new `org_related_to_app(org_id, app_id) -> bool` and `page_app_org_members(...)` next to `page_org_members` (591-626), reusing `_is_active()` (547-549); row type `AppOrgMemberRow` | the one-row-per-user query | **High** |
| `app/modules/audit/actions.py` | `ORGANIZATION_MEMBERS_READ = "organization.members_read"` | audit action | no |
| `app/modules/organizations/errors.py` | `OrgNotVisibleToAppError(NotFoundError)` with `code = "org_access_denied"` | 404 variant of the code (Q4) | no |
| `app/modules/organizations/schemas.py` | `AppOrgMemberRead` | response item | no |
| `app/modules/organizations/app_service.py` | new `AppOrgMemberService.list(caller, org_id, *, status, updated_since, limit, offset, ctx)`: org-mode check, relation check, page, audit | R1 service layer | no |
| `app/modules/organizations/app_router.py` | new `APIRouter(prefix="/apps", tags=["app-orgs"])`, `GET /{key}/orgs/{org_id}/members` | route (R3 class 4 (a)) | no |
| `app/api/router.py` | include `organizations.app_router` (next to `releases_app_router`, line 29) | mount | no |
| `tests/test_app_org_members.py` | new API tests | R8 | no |
| `tests/test_app_credential_auth.py` | runtime-path tests | runtime path | no |
| `tests/test_app_credentials_api.py` | issuance with the new scope; `admin` refusal | ADR-0021 §1 limit, allow-list | no |
| `tests/test_app_credential_scope_migration.py` | new round-trip test | R2 | no |
| `tests/test_route_auth_classes.py` | `APP_CREDENTIAL[("GET", ORG_MEMBERS)] = "org-members:read"`; `test_app_credential_allow_list_is_exact` asserts `.lock is False` for it | R3 registry | no |
| `tests/test_audit_coverage.py` | `APP_CREDENTIAL_AUDITED` entry (`{"organization.members_read"}`) and an audited-route test | audit registry (line 968-978 asserts equality with `APP_CREDENTIAL_ROUTES`) | no |
| `tests/test_rate_limit.py` | `enforce_subject_limit` unit tests | new core helper | no |
| `docs/adr/0025-org-members-read-scope.md` | new ADR (amends ADR-0016 §1, ADR-0021 §1/§3, ADR-0022 §1/§10) | decision outlives the ticket | no |
| `docs/adr/0016-…md`, `docs/adr/0021-…md`, `docs/adr/0022-usage-key-scopes.md` | one header pointer line each: "Amended by ADR-0025 (…)" | ADR pointer convention (ADR-0021 line 4) | no |
| `docs/identity-architecture.md` | decision-log row D34 after D33 (line 329) | decision log | no |
| `CLAUDE.md` | R3 class 4: add D34 / ADR-0025 to the citation, add the `org-members:read` scope sentence to (a) | R3 text must list the route (Ming approves at gate) | no (but owner-owned rules) |
| `docs/consumers.md` | new subsection "### Reading an org's members from your backend (USR-29, ADR-0025)" after "Invitations" (before line 215) | ticket AC6 | no |
| `docs/spec-v1.md` | dated lines under §5.8 (after line 86) and §5.7 (line 322), plus a dated note under §8 Q6 (line 350) | spec has no section (Q1) | no |

Tier: **High** (migrations, `app/core/`, `app/modules/app_credentials/`, `app/modules/rbac/`, `.env.example`). Never agent-merged.

## Data model / migration
- Table `app_credentials`: no new column or index. The existing partial index `ix_app_credentials_app_id_scope_active` already serves the "2 active per (app, scope)" count.
- Constraint `ck_app_credentials_scope_valid`:
  - before: `scope IN ('manifest', 'releases')`
  - after: `scope IN ('manifest', 'releases', 'org-members:read')`
- Migration `usr29_org_members_read_scope`, `down_revision = "911eb99ddf8b"`:
  - `upgrade()`: `op.drop_constraint(op.f("ck_app_credentials_scope_valid"), "app_credentials", type_="check")`, then `op.create_check_constraint(op.f("ck_app_credentials_scope_valid"), "app_credentials", "scope IN ('manifest', 'releases', 'org-members:read')")`.
  - `downgrade()`: `op.execute("DELETE FROM app_credentials WHERE scope = 'org-members:read'")` FIRST. This revokes by removal and avoids a CHECK violation, the same reasoning as ADM-034's downgrade. Then drop and recreate with the old list.
  - The scope texts are literals in the migration (history is frozen). Do not import the model constant.
- Model `CheckConstraint("scope IN ('manifest', 'releases', 'org-members:read')", name="scope_valid")` must be byte-identical to the migration text. Alembic does not compare CHECKs; the round-trip test does.
- ADM-042 sequencing: ADM-042 will recreate this CHECK starting from this list. That is recorded in the ADR and in the model comment (line 31): "ADM-042 recreates it from the USR-29 list".
- Query (no schema change, no new index). Inner aggregate per user, then join to `users`:
  ```
  agg = SELECT ra.user_id,
               array_agg(r.key ORDER BY r.key)  AS org_roles,   -- (user, role, org) is unique: no DISTINCT needed
               min(ra.created_at)               AS joined_at,
               max(ra.created_at)               AS last_granted
        FROM role_assignments ra JOIN roles r ON r.id = ra.role_id
        WHERE ra.org_id = :org AND r.app_id = :app_id AND <_is_active()>
        GROUP BY ra.user_id
  rows  = SELECT u.id, u.email, u.email_verified_at IS NOT NULL, u.display_name, u.status,
                 agg.org_roles, agg.joined_at
          FROM agg JOIN users u ON u.id = agg.user_id
          WHERE [u.status = :status] [AND (u.updated_at > :since OR agg.last_granted > :since)]
          ORDER BY u.id LIMIT :limit OFFSET :offset
  total = SELECT count(*) FROM agg JOIN users u … same WHERE
  ```
  - Two statements (existing pattern). Explicit `User` columns only, never the entity, so `password_hash` cannot reach the result.
  - `array_agg(... ORDER BY ...)` uses `sqlalchemy.dialects.postgresql.aggregate_order_by`.
  - The query filters on `r.app_id` (the authenticated app's id) rather than joining `apps.key`.
- Relation check (one statement, run before paging, the same code path for every 404 case):
  `SELECT EXISTS (role_assignments ra JOIN roles r JOIN organizations o ON o.id = ra.org_id WHERE ra.org_id = :org AND r.app_id = :app_id AND o.status = 'active' AND <_is_active()>)`.
  It returns false for an unknown org, an unrelated org and a suspended org alike.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/apps/{key}/orgs/{org_id}/members` | `Authorization: Bearer mmak_…` (`org-members:read`). Query: `limit` (≤100), `offset`, `status` (`active`\|`disabled`, optional), `updated_since` (RFC 3339 with offset, optional) | 200 `Page[AppOrgMemberRead]`, `Cache-Control: no-store` | 200; 401 (missing/malformed/unknown/revoked key, `mmdk_` key, admin JWT: the single ADR-0016 §4 answer with `WWW-Authenticate: Bearer realm="app"`); 403 `app_credential_mismatch` / `app_credential_scope` / `app_disabled`; 404 `org_access_denied`; 422 `org_not_supported`, invalid query (no values echoed); 429 `rate_limited` + `Retry-After` | R3 class 4 (a), scope `org-members:read`; no `admin:*` |

`AppOrgMemberRead`:
| Field | Type | Meaning |
|---|---|---|
| `user_id` | UUID | = access-token `sub` |
| `email` | string \| null | account email; `null` when `status = disabled` (Q3) |
| `email_verified` | bool | `email_verified_at IS NOT NULL` (kept: staff-granted roles can reach unverified accounts; Q3) |
| `display_name` | string \| null | `null` when unset or when `status = disabled` (Q3) |
| `status` | `active` \| `disabled` | account status |
| `org_roles` | list[str] | full role keys (`simap:manager`) of `{key}` held in that org, not expired, sorted |
| `joined_at` | timestamptz | earliest creation time among those non-expired assignments. It is not "joined the org", and it moves if the earliest role expires or is removed |

Order of checks:
1. Per-IP bucket `app_org_members` (route dependency, before auth).
2. Credential (401, then `app_disabled`, then mismatch (denied row), then scope (denied row)). Runtime path: throttled `last_used_at`.
3. Per-key bucket `app_org_members_key` (subject = `key_id`).
4. `org_mode != required` → 422 `org_not_supported` (details `[{"field": "app", "reason": "org_mode_none"}]`, as `auth_service._org_not_supported`).
5. Relation check → 404 `org_access_denied` (denied audit row).
6. Page, then the success audit row.

OpenAPI `responses=` declares 401, 403, 404, 422 and 429 (`test_openapi_bearer_scheme` checks 401/403 on app-credential operations).

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_app_org_members.py › test_lists_members_one_row_per_user` | a user with two simap org roles appears once with both keys sorted; fields exact; no `password_hash`/extra keys (`set(item) == {...}`) — AC1 |
| › `test_total_counts_distinct_users` | `total` = distinct users, not assignments — AC1 |
| › `test_paging_is_stable_by_user_id` | 5 users, `limit=2` over 3 pages = all ids once, ascending — AC1 |
| › `test_limit_over_100_is_422` | pagination bound (R9) |
| › `test_user_without_key_role_not_listed` | app-scope (`org_id NULL`) simap role, another app's role in the same org, simap role in another org: none listed — AC3 |
| › `test_expired_assignment_ignored` | user with only an expired role absent; user with one expired + one active lists only the active key; `joined_at` from active only — AC3 |
| › `test_disabled_user_listed_minimised` | disabled user listed, `status=disabled`, `email` and `display_name` null — AC3 / Q3 |
| › `test_unknown_unrelated_suspended_org_same_404` | random UUID, org with only other-app roles, suspended org with simap roles: identical status + body `org_access_denied` — AC4 |
| › `test_org_with_only_expired_key_roles_is_404` | relation needs a non-expired role — AC4 |
| › `test_org_mode_none_422` | `org_mode = none` app with its own key → 422 `org_not_supported` — AC4 |
| › `test_other_app_credential_403_mismatch_audited` | calechip key on `/apps/simap/...` → 403, one `denied` row `organization.members_read` reason `app_credential_mismatch` — AC2 |
| › `test_manifest_and_releases_keys_403_scope` | both → 403 `app_credential_scope` (denied row) — AC2 |
| › `test_disabled_app_403` | app disabled → 403 `app_disabled` |
| › `test_status_filter` | `status=disabled` / `active`; `total` follows the filter — AC6 |
| › `test_status_filter_invalid_422` | `status=nope` → 422 — AC6 |
| › `test_updated_since_user_change_and_new_grant` | with `since` = T: unchanged user excluded; user with `updated_at > T` (status change) included; user with a new grant after T included; `total` follows — AC6 |
| › `test_updated_since_requires_timezone_422_no_echo` | naive datetime → 422; response text does not contain the submitted value — AC6 |
| › `test_success_writes_one_audit_row_counts_only` | exactly one row: action `organization.members_read`, outcome success, actor `app:simap/<key_id>`, target org, `changes` keys ⊆ {returned,total,limit,offset,status_filter,updated_since_set}; no `@` in the row JSON — AC5 |
| › `test_404_writes_one_denied_row` | denied row with reason `org_access_denied`, no email |
| › `test_no_email_in_logs` | `caplog.set_level(DEBUG)`; after a successful call and a 404, no fixture email appears in `caplog.text` — AC5 |
| › `test_cache_control_no_store` | header on 200 — AC5 |
| › `test_per_ip_rate_limit_429_before_auth` | `make_client(app_org_members_rate_limit=1)`, second call without a key → 429 + `Retry-After` |
| › `test_per_key_rate_limit_429` | `make_client(app_org_members_key_rate_limit=1)`, same key twice → 429; a second key of the same app is still allowed |
| `tests/test_app_credential_auth.py › test_runtime_scope_takes_no_app_lock` | holding `SELECT … FOR UPDATE` on the `apps` row in another connection, the call still returns 200 (with a timeout guard) |
| › `test_runtime_touch_throttled_60s` | first call sets `last_used_at`; a second call within 60 s leaves it unchanged; with `last_used_at` set 61 s back, the call updates it |
| › `test_revoked_runtime_key_401` | revoked `org-members:read` key → 401 |
| › `test_require_app_credential_refuses_lock_for_runtime_scope` | `RequireAppCredential(ORG_MEMBERS_READ, …, lock=True)` raises ValueError |
| `tests/test_app_credentials_api.py › test_issue_org_members_scope` | staff issues `scope=org-members:read` (201, listed with scope); third active → 409 `credential_limit` |
| › `test_org_members_scope_refused_for_admin_app` | `admin` app → 409 reason `admin_app` |
| `tests/test_app_credential_scope_migration.py › test_org_members_scope_round_trip` | at head insert `org-members:read` + `manifest` keys. Downgrade to `911eb99ddf8b`: the org-members key is gone, the manifest key stays, inserting `org-members:read` fails on `ck_app_credentials_scope_valid`. Upgrade head: insert succeeds. Cleanup in `finally` |
| `tests/test_route_auth_classes.py` (registry entry) | all parametrized class-4 tests: no CORS, 401 without a key, admin JWT 401, `mmdk_` 401, allow-list exact, `lock is False` |
| `tests/test_audit_coverage.py › test_org_members_read_route_is_audited` | success → one `organization.members_read` row; classification test passes |
| `tests/test_rate_limit.py › test_subject_limit_counts_per_subject` / `› test_unknown_subject_scope_refused` | `enforce_subject_limit` raises `RateLimitedError` after the limit, keys independent; unknown scope → ValueError |

## Approach
1. **Scope + migration (R2).**
   - Add `ORG_MEMBERS_READ` and `RUNTIME_SCOPES = frozenset({AppCredentialScope.ORG_MEMBERS_READ})` to `models.py`, with the comment "ADM-042 adds `usage:*`/`limits:write` here".
   - Write the migration and check it by hand (`op.f` full name, downgrade deletes first). Run `uv run alembic check`.
   - Issuance needs no change: the allow-list at `service.py:121-123` already refuses every non-`releases` scope for `admin`, and the schema enum widens on its own.
2. **Runtime path (shared with ADM-042).**
   - Repository: `touch_last_used_throttled` is one `UPDATE … WHERE id = :id AND revoked_at IS NULL AND (last_used_at IS NULL OR last_used_at < now() - make_interval(secs => :s))`, returning nothing.
   - Service `authenticate`: after the scope check, `if scope in RUNTIME_SCOPES: await self.repo.touch_last_used_throttled(...); return caller`.
   - The manifest path (`lock=True` → `confirm`) and the releases path (`lock=False`, then `confirm` in its service) stay byte-for-byte as they are.
   - `deps.py` refuses `lock=True` for runtime scopes and adds `OrgMembersCredentialDep`.
3. **Per-key limiter (core, shared).**
   - In `rate_limit.py`, add `SubjectScope = Literal["app_org_members_key"]` and the `_SUBJECT_LIMITS` map from settings.
   - `enforce_subject_limit(request, scope, subject)` uses `request.app.state.rate_limiter.hit(scope, f"k:{subject}", …)` and raises `RateLimitedError`. This is the same pattern as the bug-report per-user bucket (`bug_reports/service.py:121`), made generic.
   - Add the IP scope `app_org_members` to `Scope`/`_LIMITS`, and the config keys plus `.env.example` (R5). In-process, so the effective limit is N workers × the setting (ADR-0002).
4. **Query (R1 repository).** Add `org_related_to_app` and `page_app_org_members` to `RbacRepository` as in "Data model". Reuse `_is_active()`.
5. **Service.** `organizations/app_service.py` (HTTP-free, raises `AppError`s):
   - Load the app by `caller.app_key` with no lock; if `org_mode != required`, raise 422.
   - If `not org_related_to_app`, write a `record_failure(... DENIED, target=(ORGANIZATION, org_id), org_id=org_id, changes={"reason": "org_access_denied"})` row and raise `OrgNotVisibleToAppError("Organization not found")`.
   - Page, then map rows to `AppOrgMemberRead`, nulling `email`/`display_name` for disabled users.
   - `audit.record(ORGANIZATION_MEMBERS_READ, actor=caller.actor, target=(ORGANIZATION, org_id), org_id=org_id, app_key=caller.app_key, changes={"returned", "total", "limit", "offset", "status_filter", "updated_since_set"})` in the request transaction. Key names avoid the forbidden `email|token|…` pattern (`audit/service.py:28`).
   - No logging of rows. Log lines carry `key_id`/`org_id` only.
6. **Router** (`organizations/app_router.py`, shape of `releases/app_router.py`):
   - `dependencies=[rate_limit("app_org_members")]`, `caller: OrgMembersCredentialDep`, `page: PageDep`.
   - `status: UserStatus | None`, `updated_since: AwareDatetime | None` (naive → 422; the envelope never echoes values, `core/errors.py:215`).
   - Call `enforce_subject_limit(request, "app_org_members_key", caller.key_id)` first, then the service. Set `response.headers["Cache-Control"] = "no-store"`, return `Page(...)`.
   - Mount in `app/api/router.py`. CORS: `BearerRouteMatcher` only covers Bearer routes and the path is outside `/auth/`, so no CORS (asserted by `test_app_credential_route_no_cors`).
7. **Registries.** Update `APP_CREDENTIAL` and `APP_CREDENTIAL_AUDITED`, and add the `lock is False` assertion.
8. **Docs.**
   - ADR-0025 records:
     - the scope;
     - the runtime key `MMLABS_ORG_MEMBERS_CREDENTIAL` (backend deploy env only, never CI, never in browser or mobile code; never shares a value with a CI scope);
     - refused for `admin`;
     - no `lock_app`, and the in-flight-revoke race is accepted again;
     - the per-IP + per-key buckets;
     - the relation rule;
     - 404 here vs 403 on cookie routes, and why;
     - field minimisation;
     - `updated_since` is additive only;
     - audit one row per call;
     - blast radius (all member emails of every org of that app);
     - rejected alternatives: `name` token claim alone, `org_entitlements` relation, multi-scope key, keyset cursor, tombstones.
   - Pointer lines in ADR-0016/0021/0022. D34 row. CLAUDE.md R3 text.
   - `docs/consumers.md` subsection: request; key handling (server-side only, rotate on staff off-boarding); field table; `joined_at` and `updated_since` semantics, plus "removals (USR-30) and expiry are invisible to `updated_since`: run a full read at least daily"; the error table incl. 404-vs-403 note; rate limits; `no-store`; the PII duty.
   - Spec dated lines.
- Rules: R1 (layers above), R2 (step 1), R3 (class 4 (a), registry, ADR), R4 (one row per call by choice; not a mutation), R5 (config/env), R6 (envelope, codes in the R6 list), R7 (invitations, tombstones and the `name` claim stay out), R8 (tests above), R9 (`Page[T]`, `limit` ≤ 100, UUID, timestamptz; `apps/{key}` exception D18), R11 (no emails in logs or errors; key hash-only already).

## Specialist input used
- identity-access-engineer:
  - Adopted: runtime path without `lock_app` with a separate throttled touch (rec 1), and `RUNTIME_SCOPES` in `models.py`.
  - Adopted: scope `org-members:read` (rec 2); check order (rec 3); per-IP 120 + per-key 60 per minute (rec 4).
  - Adopted: one audit row per call with counts (rec 5); new ADR-0025 + D34 rather than editing accepted ADRs (rec 6); R3 text (rec 7); test list (rec 8).
- database-optimizer:
  - Adopted: a new query, not a reworked `page_org_members` (rec 1); `_is_active()` reuse; `ORDER BY u.id`; two statements; `joined_at = min(created_at)`.
  - Adopted: additive `updated_since` via `HAVING`-equivalent outer filter, with no tombstones (rec 2); migration with `op.f` and delete-first downgrade (rec 4); final-list note for ADM-042 (rec 5).
  - Rejected: `array_agg(DISTINCT …)`, since the unique `(user_id, role_id, org_id)` makes it redundant. Rejected: the optional `(org_id, role_id)` index (unmeasured).
- api-platform-engineer:
  - Adopted: path `/apps/{key}/orgs/{org_id}/members` and new `organizations/app_router.py` (rec 1); runtime-key treatment (rec 2); field set (rec 3); invitations as a follow-up (rec 5); error order and `responses=` (rec 6); docs list (rec 8).
  - Rejected: scope `members:read`, which is ambiguous next to app-level membership (Q2 lists it).
  - Rejected: sort `joined_at, user_id`. `joined_at` moves when the earliest role expires or is removed, so rows shift between pages; `user_id` is immutable and unique.
- security-architect:
  - Adopted: per-key bucket built generically in `rate_limit.py`; no lock; implicit relation in an active org with a uniform 404 path; null `email`/`display_name` for disabled users; audit rows plus denied rows (extended to the 404 case for scraping evidence); caplog test; typed `updated_since` without echo; no-CORS test; consumers.md key-handling text; spec §8 PII line.
  - Rejected (default, Q3): email opt-in via `fields=email`. The ticket asks for email by default, and seat assignment is the core use. Ming may choose opt-in.
  - Rejected (default, Q3): dropping `email_verified`. Staff-granted org roles can reach unverified accounts, so it is not always true. Kept per the ticket.
  - Not adopted: the 1000/day per-key cap (no day bucket exists; per-minute bucket + audit instead). The scraping alert is a follow-up.

## Assumptions
- ADM-034 and ADM-041 are merged; ADM-042 is not. USR-29 builds `RUNTIME_SCOPES`, the throttled touch and `enforce_subject_limit`, and ADM-042 reuses them, adding its scopes to the set, the CHECK list and `SubjectScope`. If ADM-042 lands first, the rebase keeps both lists.
- Login does not write the `users` row: there are no `update(User)` or rehash writes on login (only `users/service.py:79` status, `users/repository.py:33` verify, `:38` password reset). So `users.updated_at` noise in `updated_since` is limited to verification, password reset and status changes. Those are harmless extra rows.
- `display_name` has no write path besides sign-up today. A future profile edit bumps `updated_at` through the mixin `onupdate`.
- On the 404 path, `record_failure` rolls back the request's pending work, including that call's throttled touch. This is accepted and documented in the ADR (the key row was still read).
- Role keys are returned in full (`simap:manager`), matching the `org_roles` token claim (spec §5.8).
- Hotspots with **USR-30** (PLANNING, same batch):
  - `docs/consumers.md` "Organizations", `docs/identity-architecture.md` decision log (D34 here, D35 there), `docs/spec-v1.md` §5.8/§5.7, `CLAUDE.md` R3, `app/modules/audit/actions.py`, `app/modules/rbac/repository.py`, `app/modules/organizations/errors.py`.
  - Expect a rebase. USR-30's removals are exactly what `updated_since` cannot see; the consumers.md text says so.
- Hotspot with ADM-042 (BACKLOG): `app_credentials/models.py` CHECK, `service.py`, `rate_limit.py`, `config.py`.
- Follow-ups (not in this diff, R7):
  - pending invitations for `{key}` in this read;
  - a `name` claim in access tokens (orchestrator's S ticket);
  - an alert on many distinct `org_id`s per key per hour;
  - admin-pan offering the `org-members:read` scope at issuance;
  - tombstones/removal log if Ming wants removal sync.

## Blocking questions
1. **Scope confirmation.** The spec has no app-facing member read (§5.7 lists "Org members view" as Later; §8 Q4 says "no pull endpoint and no app-credential widening for now" for entitlements). Does Ming approve this widening of class 4, ADR-0025 / D34, the CLAUDE.md R3 change and the dated spec lines? **Default: yes.**
2. **Scope name.** `org-members:read` (identity, db, security) or `members:read` (api)? **Default: `org-members:read`.**
3. **Fields.**
   - (a) `email` returned by default (ticket) or opt-in `?fields=email` (security)? **Default: by default.**
   - (b) Keep `email_verified`? **Default: keep.**
   - (c) Disabled users: `email`/`display_name` null? **Default: null (id, status, roles, joined_at only).**
4. **404 vs 403.** Unknown, unrelated and suspended orgs answer **404** `org_access_denied` here, while the cookie-class routes answer 403 with the same code. **Default: 404 as the ticket says, documented in consumers.md and ADR-0025.**
5. **"Related to `{key}`".** At least one non-expired `{key}` org role in an active org (implicit), or require an `org_entitlements` row? **Default: implicit role rule; entitlements not consulted.**
6. **`updated_since`.** Ship it as additive-only (`users.updated_at > since` OR a newer active `{key}` grant), with removals and expiry invisible and a documented periodic full read (at least daily)? Or defer the filter? **Default: ship, additive-only, documented.**
7. **Sort order.** `user_id` (db) or `joined_at, user_id` (api)? **Default: `user_id`.**
8. **Audit.** One row per successful call plus a denied row per 404 (ids/counts only), or log only? **Default: rows.**
9. **Rate limits.** **Default: 120/min per IP (before auth), 60/min per key (after auth), in config.**
10. **Runtime hot path.** USR-29 builds it generically (`RUNTIME_SCOPES`, throttled touch, `enforce_subject_limit`) for ADM-042 to reuse, with manifest/releases unchanged? **Default: yes.**
11. **Pending invitations** in this read? **Default: follow-up ticket.**
12. **Spec §8 PII line.** Add a dated note under Q6 saying consuming apps must keep member emails server-side, store only what they need, and refresh or delete them on the full read? **Default: yes.**

## Split proposal
None. The estimate is about 400 product lines plus tests and docs, at the upper bound. If Ming defers `updated_since` (Q6 = defer), the repository and service shrink by about 30 lines and three tests drop; no other section changes.
