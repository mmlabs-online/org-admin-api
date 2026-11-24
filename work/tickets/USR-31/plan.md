Status: BLOCKED

> BLOCKED only for owner choices at the plan gate (Areas include auth; `app/modules/app_credentials/deps.py` is a High path). Every question below has a default, and the plan is written on the defaults. If Ming answers "all defaults", change this line to `READY` and leave the rest unchanged. A non-default answer changes only the sections that question names.

# Plan — USR-31: Pending org invitations in the app-facing member read (requested by simap)

## Scope summary
Ticket `work/tickets/USR-31/ticket.md`, "Scope":
> Server-to-server read with the existing `org-members:read` app credential (runtime key, ADR-0025); PLAN decides between a new route (e.g. `GET /api/v1/apps/{key}/orgs/{org_id}/invitations`, `Page[T]`) and an opt-in field on the USR-29 member read, and whether a separate scope is needed.
> Item fields: invitation id, role key(s) of `{key}`, `created_at`, `expires_at`. Never the invitee email, token or token hash (as USR-23). Only pending, unexpired invitations of `{key}` in that org.
> Same guards as USR-29: one 404 `org_access_denied` for unknown / unrelated / suspended orgs, 422 `org_not_supported` for `org_mode = none`, per-IP and per-key rate limits, `Cache-Control: no-store`, no CORS, audit row per call (ids and counts only).
> Docs: `docs/consumers.md` "Organizations" section; ADR-0025 amendment line; registry entry in `tests/test_route_auth_classes.py` if a route is added.

Scope sources it rests on:
- Spec §5.8 (docs/spec-v1.md:88): "**Org members for apps (2026-11-21, USR-29, ADR-0025):** an app backend with an `org-members:read` app credential may read the members of an organization that holds roles of that app (`GET /api/v1/apps/{key}/orgs/{org_id}/members`); read-only, server-to-server."
- ADR-0025 §2 (route allow-list), §5 (relation rule), §6 (fields, `Page[T]`, `no-store`), §8 (audit: "counts and filter flags only, plus a denied row for each 404 … Logs carry `key_id` and `org_id` only"), §9 (blast radius).
- ADR-0014: invitations store only `SHA-256(token)` and the email HMAC (models.py:29-31), one role per invitation (`role_id`, composite FK with `app_id`, models.py:35-40), and at most 50 pending per org (ADR-0014 §9, line 42).
- USR-29 plan, Q11 (approved 2026-11-21): "Pending invitations in this read? **Default: follow-up ticket.**" This ticket is that follow-up.
- CLAUDE.md R3 class 4 (a): an app credential is "accepted only on the routes its ADR lists for it (registry: `tests/test_route_auth_classes.py`)".

**Spec conflict (flagged, needs Ming, Q1).** Spec §8 Q4 (docs/spec-v1.md:351) says: "No pull endpoint and no app-credential widening for now (R3 class 4 unchanged)." This ticket adds one more route to the `org-members:read` scope. That is a widening of class 4. USR-29 added the §5.8 line but left Q4 without a dated note. Under CLAUDE.md the spec wins, so the plan adds a dated line under §5.8 and a dated note under §8 Q4, both pending Ming's confirmation. It does not rewrite either section.

## Acceptance criteria → verification
| # | Criterion (ticket) | How the reviewer verifies it |
|---|---|---|
| 1 | With simap's `org-members:read` key, simap gets the pending, unexpired invitations of simap in an org. Accepted, revoked and expired invitations and other apps' invitations are not listed. | `tests/test_app_org_invitations.py` › `test_lists_pending_invitations_of_key_only` (one pending invitation of each app role; an accepted, a revoked and an expired one; another app's pending invitation in the **same** org; a pending `{key}` invitation in **another** org. Only the two `{key}` pending ids come back and `total == 2`). › `test_expiry_uses_db_clock` (`expires_at` set by SQL to `now() - 1 s` and `now() + 5 min`. Only the second is listed). › `test_paging_stable_by_created_at_then_id`, › `test_limit_over_100_is_422` |
| 2 | No invitee email, token or hash in the response, logs or audit rows (tested). | › `test_item_has_exactly_four_fields` (key set equals `{id, role, created_at, expires_at}`). › `test_no_secret_in_response_logs_or_audit`: sentinels are the invitee email, `email_hmac.hex()`, the raw token, `token_hash.hex()`, the inviter's email and the inviter's `user_id`. None may appear in `resp.text`, the captured log text or `str(row.changes)` for any audit row written by the call. The log capture must be non-vacuous (see Tests). |
| 3 | Another app's key → 403 `app_credential_mismatch`. Wrong scope → 403 `app_credential_scope`. Admin JWT or deployer key → 401. | › `test_other_app_credential_403_mismatch_audited` (the denied row has action `organization.invitations_read`), › `test_manifest_releases_usage_keys_403_scope`. Parametrized registry tests in `tests/test_route_auth_classes.py` pick the route up from `APP_CREDENTIAL`: `test_admin_jwt_on_app_credential_route_401`, `test_deployer_key_on_app_credential_route_401`, `test_app_credential_route_401_without_credential`, `test_app_credential_route_no_cors`, `test_app_credential_allow_list_is_exact` (also checks `.lock is False`). |
| 4 | Unknown, unrelated and suspended orgs give the same 404 body. `org_mode = none` → 422. | › `test_unknown_unrelated_suspended_org_same_404` (the suspended org **has** pending `{key}` invitations. The bodies are compared byte for byte.) › `test_org_mode_none_422` |
| 5 | Contract documented in `docs/consumers.md` "Organizations". | The reviewer reads the new "Pending invitations" subsection: route, query, fields, errors, shared limits, "pending is not a seat guarantee", snapshot note. |
| 6 (plan) | One audit row per call, counts only. A denied row on 404. `no-store`. Shared rate-limit buckets. | › `test_success_writes_one_audit_row_counts_only`, › `test_404_writes_one_denied_row`, › `test_cache_control_no_store`, › `test_per_ip_bucket_shared_with_members_read`, › `test_per_key_bucket_shared_with_members_read`. `tests/test_audit_coverage.py` › `test_app_credential_routes_are_classified` + new › `test_org_invitations_read_route_is_audited` |
| 7 (plan) | The USR-29 member read is unchanged after the shared guard refactor. | The full existing `tests/test_app_org_members.py` passes unchanged. `make check` is green. |

## Ming verification after merge
- From simap's backend, call `GET /api/v1/apps/simap/orgs/{org_id}/invitations` once in staging/production with the existing `MMLABS_ORG_MEMBERS_CREDENTIAL`. No new key and no deploy step are needed: there is no migration and no config change.

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/modules/app_credentials/deps.py` | new `OrgInvitationsCredentialDep` after `OrgMembersCredentialDep` (lines 84-93): `RequireAppCredential(AppCredentialScope.ORG_MEMBERS_READ, AuditAction.ORGANIZATION_INVITATIONS_READ, lock=False)` | the denied audit action is bound to the dependency (deps.py:34,63-64). Reusing `OrgMembersCredentialDep` would log denials as `organization.members_read` | **High** |
| `app/modules/audit/actions.py` | `ORGANIZATION_INVITATIONS_READ = "organization.invitations_read"` after line 41 | new audit action (R4 style, ADR-0025 §8) | no |
| `app/modules/invitations/repository.py` | new row type `AppInvitationRow = tuple[uuid.UUID, str, datetime, datetime]` and method `page_pending_for_app(org_id, app_id, *, limit, offset) -> tuple[list[AppInvitationRow], int]` after `page_for_org` (143-176) | the app-filtered pending query. `page_for_org` has no `app_id` filter and returns `invited_by`/`accepted_by` | no |
| `app/modules/organizations/schemas.py` | `AppOrgInvitationRead` after `AppOrgMemberRead` (line 71-78): `id: uuid.UUID`, `role: str`, `created_at: datetime`, `expires_at: datetime` | dedicated response item. A field added to the staff `OrgInvitationRead` can then never leak to apps | no |
| `app/modules/organizations/app_service.py` | move the guard block (lines 39-53) into a module function `_require_visible_org(rbac, audit, caller, org_id, ctx, action)`. `AppOrgMemberService.list` calls it with `ORGANIZATION_MEMBERS_READ`. New `AppOrgInvitationService.list(caller, org_id, *, limit, offset, ctx)` calls it with `ORGANIZATION_INVITATIONS_READ`, then pages and audits | one guard path, so the 404 body and the denied row stay identical (R1) | no |
| `app/modules/organizations/app_router.py` | new `GET /{key}/orgs/{org_id}/invitations` route (see API contract). Docstring updated | route (R3 class 4 (a)) | no |
| `tests/test_app_org_invitations.py` | new API tests (see Tests) | R8 | no |
| `tests/test_route_auth_classes.py` | `ORG_INVITATIONS = f"{V1}/apps/{{key}}/orgs/{{org_id}}/invitations"` (after line 66). `("GET", ORG_INVITATIONS): frozenset({"org-members:read"})` in `APP_CREDENTIAL` (line 74). `assert with_dep[("GET", ORG_INVITATIONS)].lock is False` (after line 322) | R3 registry | no |
| `tests/test_audit_coverage.py` | `APP_CREDENTIAL_AUDITED` entry `{"organization.invitations_read"}` (after line 990). New `test_org_invitations_read_route_is_audited` modelled on lines 1050-1065 | audit registry (line 1000 asserts equality with `APP_CREDENTIAL_ROUTES`) | no |
| `docs/consumers.md` | new subsection "#### Pending invitations (USR-31)" at the end of the USR-29 subsection (after line 250, before line 252) | AC5 | no |
| `docs/adr/0025-org-members-read-scope.md` | new section "## Amendment (USR-31, 2026-11-24)" after "Decision" (after line 50): §2 route added, §6 invitation fields, §8 audit action, shared §4 buckets, residual risk | ticket: ADR-0025 amendment line | no |
| `docs/identity-architecture.md` | decision-log row **D38** after D37 (line 334) | decision log | no |
| `docs/spec-v1.md` | dated line under §5.8 after line 88. Dated note appended to §8 Q4 (line 351) | spec conflict (Q1) | no |
| `CLAUDE.md` | R3 class 4 (a): the `org-members:read` entry becomes "`GET /api/v1/apps/{key}/orgs/{org_id}/members` and `GET /api/v1/apps/{key}/orgs/{org_id}/invitations`, a runtime key (USR-29, USR-31, ADR-0025)" | R3 must list every route of the scope | no (owner-owned rules; Ming approves at the gate) |

Tier: **High**, because of `app/modules/app_credentials/deps.py`. No agent merges it. If the dependency were defined in `app_router.py` instead, the tier would drop to Medium. That would break the deps.py convention ("One dependency per route", deps.py:3), so this plan does not do it.

No other file. No migration, no `app/core/` or config change, no `.env.example` change, and no router mounting: `organizations.app_router` is already included.

## Data model / migration
None. Alembic head stays `c91e4b7a3d58`. The query uses the existing `ix_org_invitations_org_id_created_at (org_id, created_at)` (models.py:55). Per org, the rows are capped at 50 pending (ADR-0014 §9), so a new `(org_id, app_id, created_at)` index is not worth adding (database-optimizer, rec 1).

Repository query `InvitationRepository.page_pending_for_app`:
- Filters: `OrgInvitation.org_id == org_id`, `OrgInvitation.app_id == app_id`, `accepted_at IS NULL`, `revoked_at IS NULL`, `expires_at > func.now()`. These are explicit predicates, the same shape as `count_pending_for_org` (:44-53). It does **not** use `status_expr()`.
- Count: `select(func.count()).select_from(OrgInvitation).where(*filters)`.
- Rows: `select(OrgInvitation.id, Role.key, OrgInvitation.created_at, OrgInvitation.expires_at)` with `.join(Role, (Role.id == OrgInvitation.role_id) & (Role.app_id == OrgInvitation.app_id))`, `.where(*filters)`, `.order_by(OrgInvitation.created_at.asc(), OrgInvitation.id.asc())` (Q3), `.limit/.offset`.
- It selects explicit columns only and never the entity, so `email_hmac`, `token_hash`, `invited_by` and `accepted_by` are never loaded. `func.now()` is the transaction start time, so the count and the rows agree.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/apps/{key}/orgs/{org_id}/invitations` | `Authorization: Bearer mmak_…` (scope `org-members:read`). Query: `limit` (≤ 100), `offset` (`PageDep`). No other query parameters. | 200 `Page[AppOrgInvitationRead]` with `Cache-Control: no-store`. Item: `id` (UUID), `role` (the full role key of `{key}`, e.g. `simap:member`), `created_at`, `expires_at` (timestamptz). Order: `created_at` ascending, then `id`. | 200. 401 `unauthorized` (missing/unknown/revoked key, deployer key, admin JWT). 403 `app_credential_mismatch` / `app_credential_scope` / `app_disabled`. 404 `org_access_denied`. 422 `org_not_supported` / `validation_error`. 429 `rate_limited` | R3 class 4 (a), app credential `org-members:read`, runtime key (no `lock_app`). No CORS. |

Route definition (app_router.py):
- `dependencies=[rate_limit("app_org_members")]`: the per-IP bucket is shared with the member read (Q5).
- `responses=_RESPONSES`, `response_model=Page[AppOrgInvitationRead]`, a summary and a description that say "pending = not accepted, not revoked, not expired at read time; not a seat guarantee".
- In the body: `enforce_subject_limit(request, "app_org_members_key", caller.key_id)`, which is the shared per-key bucket. Then call the service and set `Cache-Control: no-store`.

R9: plural noun, `Page[T]`, under the existing `{key}` exception. No new R9 exception.

## Tests to write
All in `tests/test_app_org_invitations.py` unless noted. Copy the helpers `_setup`, `_auth`, `_audit` from `tests/test_app_org_members.py:23-68`. `_setup` also assigns one user the `{key}:member` org role, so the org is related. Create invitations with `tests/invitation_factories.py::make_invitation` (:100-129). For timestamps that must be relative to "now", set `created_at`/`expires_at` afterwards with `update(OrgInvitation).values(created_at=func.now() - timedelta(days=8), expires_at=func.now() - timedelta(seconds=1))`. That is the DB clock, never `datetime.now()`. Commit before calling the API.

| Test (file › name) | Covers |
|---|---|
| › `test_lists_pending_invitations_of_key_only` | AC1. Pending manager and member invitations of `{key}` are listed with `role` equal to the full keys. Excluded: accepted (`accepted_by=user`), revoked (`revoked_at` set), expired (DB-side past `expires_at`), another app's pending invitation in the same org (the other app's role, the org related to both), and a `{key}` invitation in another org. `total == 2` |
| › `test_expiry_uses_db_clock` | AC1 boundary. `expires_at = now() - 1 s` is excluded and `now() + 5 min` is listed, both set by SQL |
| › `test_paging_stable_by_created_at_then_id` | 4 invitations, two with an identical DB-set `created_at`. `limit=2&offset=0` and `offset=2` together return all ids once, in `(created_at, id)` order. `total == 4` on both pages |
| › `test_limit_over_100_is_422` | pagination bound (R9) |
| › `test_empty_list_for_related_org_without_invitations` | 200, `items == []`, `total == 0` |
| › `test_item_has_exactly_four_fields` | AC2. `set(item) == {"id", "role", "created_at", "expires_at"}`. No `invited_by`, `status`, `role_id`, `app`, `email*`, `token*` |
| › `test_no_secret_in_response_logs_or_audit` | AC2. The invitation is created with `invited_by=manager_user`. Its `email_hmac` is overwritten with `hmac_email(<known sentinel email>)` from the app's HMAC helper, or a fixed 32-byte value if that helper is not importable without settings. Calls: one 200 and one 404. Sentinels: the invitee email, `email_hmac.hex()`, the raw token, `token_hash.hex()`, `manager_user.email`, `str(manager_user.id)`. Asserts: no sentinel in `resp.text`, in the joined log messages, or in `str(row.changes)` of any `organization.invitations_read` row. **Non-vacuous:** (1) re-enable loggers disabled by Alembic `fileConfig` and set root to DEBUG, as in `test_app_org_members.py:381-403`. (2) Assert `records` is non-empty **and** that at least one record contains the `org_id` or the `key_id` of the call (the implementer names the logger that supplies it in impl-log). (3) The implementer records in impl-log that the test fails when a temporary `logger.info(email_hmac.hex())` is added to the service. |
| › `test_unknown_unrelated_suspended_org_same_404` | AC4. The suspended org holds a `{key}` member and a pending `{key}` invitation. Bodies are byte-identical and contain `org_access_denied` |
| › `test_org_with_only_expired_key_roles_is_404` | relation rule (ADR-0025 §5) with a pending invitation present |
| › `test_org_mode_none_422` | AC4 |
| › `test_other_app_credential_403_mismatch_audited` | AC3. The denied row has action `organization.invitations_read` and changes `{"reason": "app_credential_mismatch"}` |
| › `test_manifest_releases_usage_keys_403_scope` | AC3. `manifest`, `releases` and `usage:read` keys of `{key}` → 403 `app_credential_scope`, one denied row each |
| › `test_disabled_app_403` | `app_disabled` |
| › `test_success_writes_one_audit_row_counts_only` | exactly one row: actor `app:{key}/{key_id}`, target `("organization", org_id)`, `org_id`, `changes == {"returned", "total", "limit", "offset"}` with values. No `organization.members_read` row is written |
| › `test_404_writes_one_denied_row` | one `organization.invitations_read` denied row with `{"reason": "org_access_denied"}` |
| › `test_cache_control_no_store` | header |
| › `test_per_ip_bucket_shared_with_members_read` | `make_client(app_org_members_rate_limit=1)`: a members call then an invitations call from the same IP → the second is 429 with `Retry-After` |
| › `test_per_key_bucket_shared_with_members_read` | `make_client(app_org_members_key_rate_limit=1)`: members (404, unknown org) then invitations with the same key → 429. A second key → not 429 |
| `tests/test_route_auth_classes.py` (registry entry) | AC3 via the parametrized tests listed in the AC table. `lock is False` |
| `tests/test_audit_coverage.py` › `test_org_invitations_read_route_is_audited` | the route writes exactly `["organization.invitations_read"]` |
| `tests/test_app_org_members.py` (unchanged) | AC7, the shared-guard refactor is behaviour-neutral |

## Approach
1. **Audit action.** Add `ORGANIZATION_INVITATIONS_READ` to `AuditAction`.
2. **Dependency (High).** Add `OrgInvitationsCredentialDep` in deps.py. The constructor already refuses `lock=True` for runtime scopes (deps.py:40-42), so `lock=False` is mandatory.
3. **Repository (R1).** Add `page_pending_for_app` as in "Data model". Do not touch `page_for_org`.
4. **Schema.** Add `AppOrgInvitationRead` with the four fields. Do not subclass `OrgInvitationRead`.
5. **Service (R1, R4-style audit, R11).**
   - Extract `_require_visible_org` from `AppOrgMemberService.list` (app_service.py:39-53) unchanged, with the action as a parameter. Order: load app → 422 `_org_not_supported()` unless `org_mode == required` → `rbac.org_related_to_app(org_id, caller.app_id)` → on False, `audit.record_failure(action, DENIED, …, changes={"reason": "org_access_denied"})` and raise `OrgNotVisibleToAppError`.
   - `AppOrgInvitationService.list` runs the guard before any invitation query, so a suspended org with invitations still gives 404. It then calls `InvitationRepository(session).page_pending_for_app(org_id, caller.app_id, …)`.
   - It then writes `audit.record(ORGANIZATION_INVITATIONS_READ, …, changes={"returned", "total", "limit", "offset"})`. No ids and no role keys.
   - Finally it maps the rows to `AppOrgInvitationRead`. No new log lines.
6. **Router (R1, R3, R9).** Add the route as in "API contract". The router never touches the ORM.
7. **Tests** as listed. Then run `make check`. There is no migration, so `alembic check` is not needed, but running it is harmless.
8. **Docs.**
   - `docs/consumers.md` subsection: route, the same key, query, field table, "only pending (not accepted, not revoked, `expires_at` after now on the server clock)", ordering, the same error table (referencing the member-read table), shared limits ("members and invitations share one per-IP and one per-key budget"), "pending is not a seat guarantee (it can be revoked, expire, or fail on accept if the inviter lost the right to invite)", and "a snapshot: an item can expire right after the read".
   - ADR-0025 amendment.
   - D38: "**App-facing pending invitations (owner, 2026-11-24, USR-31):** `org-members:read` also grants `GET /api/v1/apps/{key}/orgs/{org_id}/invitations` (`Page[T]`, pending of `{key}` only: id, role, created_at, expires_at; never email, HMAC, token hash or inviter); same guards, shared rate-limit buckets, audit `organization.invitations_read` with counts only. See ADR-0025 amendment."
   - Spec §5.8 line: "**Pending invitations for apps (2026-11-24, USR-31, ADR-0025 amendment):** the same credential may read the org's pending invitations of that app (`GET …/orgs/{org_id}/invitations`): id, role, created and expiry times only; read-only, server-to-server."
   - Spec §8 Q4 note: "_2026-11-24 (USR-31): the owner approved two app-credential reads outside entitlements, `org-members:read` (USR-29) and its invitation read (USR-31); see ADR-0025._"
   - CLAUDE.md R3 line.

Rules: R1 (layers kept), R3 (class 4 (a), registry), R4 (audit in the same transaction, success and denied), R5 (no config change), R6 (existing codes only), R8 (happy path, 404, 422, 401/403, bounds), R9 (plural, `Page[T]`), R11 (no email, HMAC, token or hash anywhere; tested).

## Specialist input used
- **api-platform-engineer.** Adopted: new route rather than an opt-in field (rec 1; member rows are per user, invitees have no user id), reuse the scope (rec 2), minimal fields and a dedicated schema (rec 3), `(created_at, id)` order and only `limit`/`offset` (rec 4, ascending per its default), DB-clock pending (rec 5), identical errors (rec 6), registry and CLAUDE.md R3 (rec 7), docs (rec 8). Rejected: separate `app_org_invitations` buckets (rec 8). Three consults prefer shared buckets, and shared buckets need no `app/core/` change (Q5). Rejected: the field name `role_key` in favour of `role` (Q4).
- **database-optimizer.** Adopted: no migration or index (rec 1), the explicit-column `page_pending_for_app` with the composite Role join, no App join and `func.now()` (rec 2, 3), the shared guard path (rec 4), and the no-hash and DB-side expiry tests (rec 5). Changed: order ascending, not descending like USR-23 (Q3).
- **identity-access-engineer.** Adopted: reuse the scope plus a new route (rec 1), R3 text (rec 2), the ADR-0025 amendment plus D38 with no ADR-0029 (rec 3), its own dependency and the `organization.invitations_read` action (rec 4), shared buckets (rec 5), the relation query (rec 6), and excluding the email HMAC (finding 4). The Q4 spec conflict is flagged (Q1).
- **security-architect.** Adopted: the explicit-column query with `app_id = caller.app_id` (rec 1), `invited_by` and `email_hmac` omitted (rec 2), the guard before the query (rec 3), audit counts only with no invitation ids or role keys (rec 4), shared buckets (rec 5), and the accepted residual risks documented in the ADR amendment (timing correlation, 404 when the org has no app roles left).

## Assumptions
- Each invitation has exactly one role (`role_id`), so the ticket's "role key(s)" becomes a single `role` string, the full key (e.g. `simap:member`), as in `org_roles` (ADR-0025 §6).
- The field name is `role`, matching the cookie create body `{account_id, email, role}` (docs/consumers.md:177). Q4 covers the alternative `role_key`.
- An org whose only `{key}` members have left, but which still has pending `{key}` invitations, answers 404 (relation rule ADR-0025 §5). This is accepted and documented.
- Offset paging on a live list can skip or repeat an item between calls. This is accepted at ≤ 50 pending per org. Ascending order keeps earlier pages stable when new invitations are added.
- `make_invitation` lets the test set `email_hmac` after creation via `update(...)`. No factory change is needed. If a factory tweak turns out to be simpler, `tests/invitation_factories.py` is the only extra file, and the reason goes in impl-log.
- Hotspots: **USR-32** (PLANNING, not yet open) also edits `docs/spec-v1.md` §5.8, the `docs/identity-architecture.md` decision log (it takes D39) and `docs/consumers.md`. Expect a docs-only rebase. No IN_PROGRESS/REVIEW/DONE ticket edits these files.
- Follow-ups (not in this diff, R7): a `role` filter if simap asks; admin-pan display of the new route in the key-issuance help text.

## Blocking questions
1. **Spec conflict / widening.** Spec §8 Q4 says "No pull endpoint and no app-credential widening for now". USR-29 already widened class 4 under ADR-0025. Does Ming approve adding this route to `org-members:read`, with a dated §5.8 line, a dated note under §8 Q4, the ADR-0025 amendment, D38 and the CLAUDE.md R3 change? **Default: yes.**
2. **Shape and scope.** Use a new route `GET …/orgs/{org_id}/invitations` (`Page[T]`) rather than an opt-in field on the member read, and reuse `org-members:read` rather than a new scope (a new scope would need a migration of the scope CHECK and a second key for simap)? **Default: new route, reuse the scope, no migration.**
3. **Sort order.** Should it be `created_at` ascending, then `id` (oldest first; pages stay stable as invitations are added), or descending, then `id` (newest first, like the USR-23 staff read)? **Default: ascending, then `id`.**
4. **Fields.** Use only `id`, `role`, `created_at`, `expires_at`, and omit `invited_by` (it reveals who invited whom, and a removed inviter's id would leak)? Name the role field `role` (as the cookie create body) or `role_key` (as the staff read)? **Default: those four fields, `invited_by` omitted, name `role`.**
5. **Rate limits.** Should the invitation read share the member read's per-IP (120/min) and per-key (60/min) buckets, with no config change, or get its own? Sharing means a client polling both routes uses its budget twice as fast. **Default: shared.**
6. **"Pending is not a seat guarantee".** Document in `docs/consumers.md` and the ADR-0025 amendment that a pending invitation can still be revoked, expire, or be refused at accept (the inviter lost the right to invite, ADR-0014 §7), and that the list is a snapshot? **Default: yes.**
7. **Decision record.** Use an ADR-0025 amendment plus D38, leaving ADR-0029 unused, rather than a new ADR-0029? **Default: amendment plus D38.**

## Split proposal
None. The estimate is about 70 product lines (deps 9, action 1, repository ~30, schema 6, service ~35 including the extraction, router ~30), about 300 test lines, and about 60 doc lines. That fits one cycle.
