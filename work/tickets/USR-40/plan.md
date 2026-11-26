Status: READY

# Plan — USR-40: Org picker lists every org of the app for super-admin holders (simap SIM-043)

> **Predicted tier: Medium** (no High path in the file list: `app/modules/organizations/**`, `docs/**`, `tests/**`;
> `risk_tier.py` could not be run in this planning session, tier reasoned from `.github/risk-tiers.yml` /
> `loop.config.md` High paths). It becomes **High** only if USR-39 has not shipped the "org of the app" clause and
> this ticket has to add it to `app/modules/rbac/repository.py` (see Assumptions). Ticket Gate is `plan-approval`
> (area auth, OQ84 / OQ89 need Ming), so it stops at PLAN either way. Batch 2026-11-26-1, after USR-38 and USR-39.
> Written for the **default answers** of "Blocking questions for Ming" (Q1–Q7).

## Scope summary
- Ticket Goal: "In the app's org picker (AUT-06 flow), a staff holder of the app's super-admin role sees every org of
  the app, each marked "member" or "super-admin access"."
- Ticket Scope: "`GET /api/v1/auth/orgs` (cookie class, `X-MM-App`): every item gains `access: "member" |
  "super_admin"` (additive, always present). For a holder, list every active org of the app (definition per OQ89 …);
  orgs where the holder is a member stay `member` with their roles; others are `super_admin` with `roles: []`." /
  "Non-holders: same list as today, every item `access: "member"`." / "Pagination and ordering unchanged; no N+1 (one
  paged query + count + the existing batch roles query)." / "`docs/consumers.md` picker section."
- Ticket Out of scope: "Token minting (USR-39); suspended orgs listed read-only (proposed hidden, OQ89)."
- Spec §5.16 Org list (line 417, proposal): "For a holder, `GET /api/v1/auth/orgs` (with that app's `X-MM-App`)
  returns every `active` org of the app; each item gains `access: "member" | "super_admin"` (additive, R9). A
  `super_admin` item has `roles: []`; an org where the holder is a member stays `member` with its roles. Pagination and
  ordering unchanged."
- Spec §5.16 Audit (line 421): "Org-list reads not audited (proposed)."
- Spec §8 OQ84 (line 562): "name … of the picker field (proposed `access: "member" | "super_admin"`, always present)
  … member path wins when the holder is also a member of that org (proposed)."
- Spec §8 OQ89 (line 567): "(b) orgs with at least one active role assignment of the app or an entitlement for the
  app (proposed) … Suspended orgs: hidden in the picker and refused at mint (proposed), or listed read-only?"
- USR-38 plan, Approach step 9 (shared contract): "`AuthzService.app_super_admin_access(user_id, app) -> bool` …
  **This is the holder predicate USR-39 (mint) and USR-40 (picker) must call.**" / "the "org of the app" predicate
  (OQ89) — USR-39 ships it (`RbacRepository` method), USR-40 reuses it." USR-38 Q8 (OQ82 default b): "USR-39 mint and
  USR-40 picker must re-check live via `AuthzService.app_super_admin_access`". ADR-0030 §8 "Picker (`access` field,
  per USR-40's gate)".
- `work/decisions.md`: nothing on SIM-043 / the picker.
- CLAUDE.md R1 (SQL in repository, branch in service), R3 class 2 (cookie; no new route, no new class), R4 (no
  mutation → no audit; spec line 421), R6 (errors unchanged), R7, R8, R9 (additive always-present field, `Page[T]`
  unchanged), R11 (no full emails in logs).
- ⚠️ **AC wording conflict (Q2):** AC1 says "suspended orgs are not listed", but AC2 says a non-holder's list is
  "unchanged", and today members see their suspended orgs (`tests/test_auth_orgs.py:356`
  `test_list_includes_suspended_with_status`). Spec line 417 only speaks of "every `active` org" for the holder's
  extra orgs and "an org where the holder is a member stays `member`". This plan reads AC1 as "suspended orgs are not
  listed **as `super_admin`**"; member items (holder or not) are unchanged, `status: suspended` included.

## Acceptance criteria → verification
New tests in `tests/test_auth_orgs_super_admin.py` unless another file is named.

| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1a | A holder sees every org of the app (OQ89 b): `member` items with roles, `super_admin` items with `roles: []`, member wins | `test_holder_sees_member_and_app_orgs` (holder member of org A with `calechip:org-owner`; org B with another user's org role of the app; org C with only an enabled entitlement for the app → A `member` + roles, B and C `super_admin`, `roles: []`, ordered by name); `test_holder_member_org_stays_member` (holder also member of an org other users are in → one item, `member`, not duplicated) |
| 1b | Orgs of other apps, suspended non-member orgs, and orgs with only expired / disabled-entitlement links are not listed (AC1 as reworded, Q2) | `test_holder_excludes_other_app_orgs` (org with only `stoktok` assignments / `stoktok` entitlement), `test_holder_excludes_suspended_app_org` (suspended org with app assignments of another user → absent), `test_holder_excludes_org_with_only_expired_assignments`, `test_holder_excludes_disabled_entitlement`; `test_holder_suspended_member_org_listed_as_member` (`status: suspended`, `access: member`, as today) |
| 1c | Expired member roles fall back to the app rule (member rule = mint member path: unexpired, org-exact) | `test_holder_expired_member_role_becomes_super_admin` (holder's only role in org A expired, another user active in A → `super_admin`, `roles: []`) |
| 2 | A non-holder's list is unchanged apart from `access: "member"` | `tests/test_auth_orgs.py` › `test_list_my_orgs_only_calling_app` updated (exact item dict gains `"access": "member"`); every other existing list test stays green unchanged; new `test_non_holder_sees_no_app_orgs` (designated app, ordinary member of org A, org B of the app exists → only A, `total` 1); `test_non_staff_with_designated_role_sees_member_list` (holds the designated role but no `admin:super-admin` → member-only list) |
| 3 | Losing the role (or `admin:super-admin`, OQ82) removes the extra orgs on the next call | `test_holder_loses_access_next_call` parametrized: designated assignment deleted, designated assignment expired (`expires_at` in the past), `admin:super-admin` assignment deleted, designation cleared (`apps.super_admin_role_id = NULL`), app `org_mode` irrelevant (see 5) → first call lists B, C; second call (same client, same cookie) lists only A, `total` 1 |
| 4a | Pagination `total` is correct over the mixed list | `test_holder_pagination_total_and_order` (2 member + 3 app orgs with interleaved names; `limit=2` offsets 0/2/4 → names in global `name, id` order, `total` 5 on every page, `access` per item); `test_holder_offset_past_member_orgs` |
| 4b | Query count does not grow with page size | `test_holder_query_count_constant` and `test_non_holder_query_count_constant`: `before_cursor_execute` listener (pattern `tests/test_auth_join.py:357-368`) counts statements of one call at `limit=1` and `limit=50` with ≥ 3 orgs listed → equal counts; holder path issues exactly one `FROM organizations … LIMIT` statement and one `count(*)` statement |
| 5 | 401 / 403 / 422 of the route unchanged | Existing `test_list_on_org_less_app_422`, `test_list_requires_account_id_422`, `test_list_unverified_403`, `test_list_bad_account_401`, `test_list_rate_limited_429` stay green unchanged; new `test_holder_unverified_403` (holder with unverified email → 403 `email_unverified`, no org names in body), `test_holder_bad_account_401`, `test_holder_on_admin_app_422` (`X-MM-App: admin` → `org_not_supported` as today, `admin` has `org_mode = none`); `tests/test_route_auth_classes.py` unchanged and green |
| 6 | `docs/consumers.md` updated | Reviewer reads the picker section (step 6) |
| 7 | No leak to non-holders; no audit; log without email | `test_holder_list_not_audited_and_logged` (no `audit_logs` row; `caplog` has `orgs_listed access=super_admin user_id=… app=calechip total=3`, and no `@` in the record); non-holder SQL path: `test_non_holder_query_count_constant` also asserts no statement references `org_entitlements` |
| 8 | Picker and mint agree (only if USR-39 is merged before this ticket's IMPLEMENT) | `test_every_super_admin_item_is_mintable`: for each `super_admin` item, `POST /api/v1/auth/token` with that `org_id` → 200 with `org_access: "super_admin"`; for the excluded fixtures (suspended, other app) → 403 `org_access_denied`. Otherwise recorded as a follow-up for USR-39 in impl-log |
| — | Everything | `make check` green |

## Ming verification after merge
- After deploy, with USR-38 designated and granted on simap (USR-38 "Ming verification"): sign in to simap as the
  holder, open the org picker, check the extra orgs appear with "super-admin access" and your own orgs as "member";
  have another super-admin revoke your `simap:super-admin` and reload: the extra orgs are gone. Needs simap's picker
  change (SIM-043, cross-repo).

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/modules/organizations/schemas.py` | `OrgAccess = Literal["member", "super_admin"]`; `MyOrgRead.access: OrgAccess` (no default, after `roles` :68, `Field(description=…)` "member: the caller holds roles in the org; super_admin: listed through the app's super-admin role (ADR-0030), roles is empty; tolerate unknown values") | Response model (R9 additive) | no |
| `app/modules/organizations/repository.py` | `list_for_user_app` (:104-153): new keyword `include_app_orgs: bool`; WHERE `held` (today) or `held OR org_of_app_clause(app_id)`; select an `is_member` boolean column; return `(org, is_member, roles)`; docstring | One paged query + same-WHERE count (R1: SQL only here) | no |
| `app/modules/organizations/auth_service.py` | `list_mine` (:167-189): after `_user`, `holder = await self.authz.app_super_admin_access(user.id, app)`; pass `include_app_orgs=holder`; map `access`; holder-only `logger.info` | Branch chosen server-side by the shared helper (R1) | no |
| `docs/consumers.md` | Picker paragraph after :160 (step 6); :151 `org_access_denied` row only if USR-39 did not already reword it | AC6, OQ88 | no |
| `docs/adr/0030-app-super-admin-role.md` | §8 Picker: Status line → "Accepted" with the gate answers; text of step 7 | USR-38 plan step 10 leaves §8 to this ticket's gate | no |
| `docs/spec-v1.md` | §5.16 Org list bullet (:417) → decided (dated, Q1/Q2 answers); §8 OQ84 picker half and OQ89 (:562, :567) ticked with the answers (shared with USR-39: whichever merges second only adds its half) | Spec stays the source of truth | no |
| `tests/test_auth_orgs_super_admin.py` (new) | All new tests in the table | AC1-5, 7, 8 | no |
| `tests/test_auth_orgs.py` | `test_list_my_orgs_only_calling_app` (:318-326) item dict gains `"access": "member"` | Exact-shape assertion | no |

Not changed: `app/modules/organizations/auth_router.py` (`response_model=Page[MyOrgRead]` picks up the field; docstring
:84 "Orgs where the account holds a role of the calling app" → only if the reviewer asks; optional one-line edit
"…, plus every org of the app for its super-admin holders (ADR-0030)" is allowed in this file), `app/modules/rbac/**`
(helper from USR-38, clause from USR-39), `app/core/**`, migrations, `tests/test_route_auth_classes.py` (route and
class unchanged).

## Data model / migration
None. No index (architect consult in ticket: "`ix_role_assignments_org_id` suffices for now"; api consult rec 8:
follow-up if orgs exceed ~10k). The implementer records `EXPLAIN` of the holder page query on the test DB in
impl-log (USR-38 plan "re-check EXPLAIN in USR-40").

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| GET | `/api/v1/auth/orgs` | unchanged: `account_id` (query), `limit` ≤ 100, `offset`; `mm_device` cookie, `Origin`, `X-MM-App` | `Page[MyOrgRead]`; `MyOrgRead {id, slug, name, status, roles, access}`, `access: "member" \| "super_admin"` always present. Holder: member orgs (any status, as today) + active orgs of the app where the holder has no unexpired role (`access: super_admin`, `roles: []`), one list ordered `name, id`, `total` over both. Non-holder: today's list, `access: member` | unchanged: 200; 401 `session_invalid`; 403 `email_unverified`, `origin_not_allowed` / `app_header_required`; 422 `org_not_supported`, `validation_error`; 429 | R3 class 2 (cookie). Holder = `AuthzService.app_super_admin_access(user.id, app)` (USR-38); no `admin:*` permission (cookie class) |

No new query parameter (no `?all=`, no `access` filter): the branch is chosen only by the server-side holder check.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_auth_orgs_super_admin.py` › `test_holder_sees_member_and_app_orgs`, `test_holder_member_org_stays_member` | 1a |
| › `test_holder_excludes_other_app_orgs`, `test_holder_excludes_suspended_app_org`, `test_holder_excludes_org_with_only_expired_assignments`, `test_holder_excludes_disabled_entitlement`, `test_holder_suspended_member_org_listed_as_member` | 1b, Q1, Q2 |
| › `test_holder_expired_member_role_becomes_super_admin` | 1c |
| › `test_non_holder_sees_no_app_orgs`, `test_non_staff_with_designated_role_sees_member_list` | 2, no leak |
| `tests/test_auth_orgs.py` › `test_list_my_orgs_only_calling_app` (updated) | 2 (shape) |
| › `test_holder_loses_access_next_call` (param: role deleted, role expired, `admin:super-admin` deleted, designation cleared) | 3, live re-check |
| › `test_holder_pagination_total_and_order`, `test_holder_offset_past_member_orgs` | 4a |
| › `test_holder_query_count_constant`, `test_non_holder_query_count_constant` | 4b, no N+1 |
| › `test_holder_unverified_403`, `test_holder_bad_account_401`, `test_holder_on_admin_app_422` | 5 |
| › `test_holder_list_not_audited_and_logged` | 7 |
| › `test_every_super_admin_item_is_mintable` (only if USR-39 is merged first) | 8 |

Fixtures (module-local in the new file, reusing `tests/test_auth_orgs.py` patterns `_calechip`, `_signed_in`,
`_list` — copy, do not import private helpers across test modules; use any holder factory USR-38 shipped instead of a
local one): `calechip` app with `org_mode = required`, a designated role `calechip:super-admin` (app scope, set via
`app.super_admin_role_id` + flush, not the API); holder = verified active user with an app-scope assignment of
`super_admin_role(db_session)` (`tests/rbac_factories.py:80`) and an app-scope assignment of the designated role;
entitlement rows via the existing entitlements factory or direct `OrgEntitlement(org_id, app_id, plan, enabled)`.
Test note: a request that ends in an error rolls the shared test transaction back
(`test_rbac_role_definitions_api.py:165`); put error calls last.

## Approach
1. **Schema** (`schemas.py`): `OrgAccess` literal and `MyOrgRead.access` as in the file table. No default, so the
   service must set it on every item (mypy catches a missing argument).
2. **Repository** (`repository.py`, R1). Keep the `held` subquery (:111-121) byte-for-byte: it is the member rule and
   matches the mint member path's expiry rule (`rbac/repository.py:463-483` `org_role_keys`: org-exact, roles of the
   app, `expires_at IS NULL OR > now()`, DB clock). Then:
   ```python
   is_member = Organization.id.in_(held)
   where = or_(is_member, org_of_app_clause(app_id)) if include_app_orgs else is_member
   total = select(func.count()).select_from(Organization).where(where)
   page = (select(Organization, is_member.label("is_member")).where(where)
           .order_by(Organization.name, Organization.id).limit(limit).offset(offset))
   ```
   `org_of_app_clause` comes from `app.modules.rbac.repository` (USR-39; Assumptions) and already contains
   `Organization.status == 'active'`, so suspended non-member orgs drop out while suspended member orgs stay through
   `is_member` (Q2). The boolean `is_member` column is the plan's `CASE` (member wins: an org matching both is one row,
   `is_member` true). Count and page share `where` → `total` exact (AC4a). The roles batch query (:134-152) is
   unchanged: `super_admin` rows have no unexpired assignment of the user in that org, so they come back with `[]`
   by construction. Return type `tuple[Sequence[tuple[Organization, bool, Sequence[str]]], int]`. Non-holder SQL is
   the same as today plus the `is_member` select column (no `org_entitlements` reference). Docstring names ADR-0030.
3. **Service** (`auth_service.py` `list_mine`): keep the order `org_mode` check → `_user` (401 / 403 before any holder
   work, so errors are unchanged and an unverified holder learns nothing) → `holder = await
   self.authz.app_super_admin_access(user.id, app)` (USR-38 helper: returns False without a query when the app has no
   designation, else one `EXISTS` including live `admin:super-admin`, Q4). Read live on every call, never cached (AC3).
   `access = "member" if is_member else "super_admin"`. The user is always the account's user (`_user`), never a
   parameter. If `holder`: `logger.info("orgs_listed access=super_admin user_id=%s app=%s total=%s", user.id,
   app.key, total)` — no email, no org names (R11). No audit row (spec line 421; read-only).
4. **Query count** (AC4b): holder path = account + user lookups (existing) + holder `EXISTS` + count + page + roles
   batch; constant in `limit`. Assert equality at `limit=1` vs `limit=50`, not an absolute number, so later
   changes to `_user` do not break it; additionally assert one page and one count statement.
5. **Tests** as in the table; run `make check`.
6. **`docs/consumers.md`** (after :160): "`access` is always present: `member` (the account holds roles in the org;
   `roles` lists them) or `super_admin` (staff holder of the app's designated super-admin role, ADR-0030; `roles` is
   `[]`). Treat unknown values as not openable. A `super_admin` item is opened with `/auth/token` + `org_id`; the
   token then carries `org_access: "super_admin"` (see USR-39's section). Suspended orgs appear only as `member`
   items, with `status`." Keep the claim name in sync with USR-39's gate answer.
7. **ADR-0030 §8 / spec:** §8 text: holder check = shared helper (live, incl. `admin:super-admin`); "org of the app"
   = `org_of_app_clause` (same as mint); `access` field always present, member wins; suspended orgs only as member
   items; no query parameter; not audited, one log line. Spec §5.16 Org list bullet and OQ84 (picker half) / OQ89
   updated with the dated answers.

Rules: R1 (SQL in repository; branch + mapping in service; router untouched), R3 (class 2 unchanged, registry test
untouched), R4 (no mutation), R6 (no new error), R8 (route tests incl. 401/403/422 unchanged), R9 (additive field,
`Page[T]`, ordering unchanged), R11 (no email in logs).

## Specialist input used
- **api-platform-engineer:** rec 1 `Literal`, no default, tolerate unknown values — adopted; rec 2 shared holder
  helper + shared "org of the app" clause, `holder: bool` into the repository — adopted (helper name from USR-38,
  `app_super_admin_access`; flag named `include_app_orgs`); rec 3 single paged query, same WHERE for count, `ORDER BY
  name, id` — adopted (boolean column instead of a string `CASE`, same effect); rec 4 roles batch unchanged — adopted;
  rec 5 constant query count at two limits, holder and non-holder — adopted (limits 1 and 50); rec 6 live
  `admin:super-admin` re-check — adopted (inside the USR-38 helper); rec 7 test list — adopted (holder with `org_mode =
  none` = `test_holder_on_admin_app_422` / existing 422 test); rec 8 no index now — adopted, EXPLAIN in impl-log.
- **identity-access-engineer:** rec 1 one holder helper — adopted as USR-38's `AuthzService.app_super_admin_access`
  (not re-implemented); rec 2 one clause for list and mint + consistency test — adopted (AC8, conditional on USR-39);
  rec 3 member = mint member path, expired → `super_admin` — adopted (`held` unchanged; test 1c); rec 4 suspended
  hidden for `super_admin` only, reword AC1 — adopted (Q2); rec 5 no leaks, no `?all=` — adopted; rec 6 fixed query
  count — adopted; rec 7 live, no caching — adopted; rec 8 log line, no audit — adopted; rec 9 tests — adopted.
- Risks carried into Assumptions: an org is "of the app" through any unexpired assignment, including one held by a
  disabled account (api risk 2) and disappears when its last assignment expires (identity risk 2); the existing
  member-item / mint mismatch for suspended orgs is not fixed (R7).

## Split proposal
None. Estimate: ~45 changed lines of application code, ~300 lines of tests, ~40 lines of docs; one review cycle.

## Blocking questions for Ming
1. **OQ89 — "org of the app".** *Default (b): an `active` org with at least one unexpired org-scope assignment of a
   role of the app (any user, disabled accounts included) **or** an enabled entitlement for the app (same rule as
   entitlements `get_active_for`, `entitlements/repository.py:15-17`, 94-101). The same clause gates USR-39's mint.*
   Consequence: an org whose only link expired drops out of the picker. Alternatives: (a) every active org
   platform-wide; (c) entitlement only.
2. **Suspended orgs / AC1 wording.** *Default: suspended orgs are never listed as `super_admin`; member items are
   unchanged (a member, holder or not, still sees their suspended org with `status: suspended`, as today). AC1 reads
   "…orgs of other apps and suspended orgs are not listed **as super_admin**".* Alternative: hide suspended orgs from
   holders entirely (changes the member list for holders only) or list them read-only (spec alternative).
3. **OQ84 — picker field.** *Default: `access: "member" | "super_admin"`, always present on every item (also for
   non-holders), member wins when the holder has an unexpired role in the org; `super_admin` items have `roles: []`.*
4. **OQ82 — live re-check on every list.** *Default: yes, via USR-38's `AuthzService.app_super_admin_access`
   (designated role at app scope, unexpired, plus active app-scope `admin:super-admin`, active verified account); no
   caching, so revoking either removes the extra orgs on the next call.*
5. **Audit / logging.** *Default: no audit row for list reads (spec line 421); one info log line per holder list
   (`user_id`, `app`, `total`; no email, no org names).*
6. **No query parameter.** *Default: no `?all=` / `?access=` filter; holders always get the full mixed list (simap
   filters client-side if it wants). Adding a filter later is additive.*
7. **Merge order.** *Default: USR-40 is built after USR-39 merges and reuses its clause. If USR-39 is still open when
   USR-40 is built, USR-40 adds `org_of_app_clause` itself in `app/modules/rbac/repository.py` exactly as specified in
   Assumptions (the PR then becomes High) and USR-39 rebases onto it.*

## Assumptions
- **Depends on USR-38** (merged before IMPLEMENT): `apps.super_admin_role_id` and
  `AuthzService.app_super_admin_access(user_id: uuid.UUID, app: App) -> bool` (USR-38 plan step 9). If USR-38 ships a
  different name or signature, use what shipped and record it in impl-log.
- **Reuses USR-39's "org of the app" predicate.** This plan names it
  `app.modules.rbac.repository.org_of_app_clause(app_id: uuid.UUID) -> ColumnElement[bool]`: a module-level function
  (not a method, so it composes into other repositories' statements), correlated on `Organization.id`, equal to
  `Organization.status == 'active' AND (EXISTS(role_assignments ra JOIN roles r ON r.id = ra.role_id WHERE ra.org_id =
  organizations.id AND r.app_id = :app_id AND (ra.expires_at IS NULL OR ra.expires_at > now())) OR
  EXISTS(org_entitlements e WHERE e.org_id = organizations.id AND e.app_id = :app_id AND e.enabled))`. USR-39's mint
  uses it as `select(exists().where(Organization.id == org_id, org_of_app_clause(app.id)))`. **USR-39's plan must
  match this name and signature** (the orchestrator should align both plans at the gate). If USR-39 is merged first
  with another name, shape or placement, the implementer uses whatever USR-39 shipped (adding the `active` status
  filter in this repository if USR-39's clause omits it) and records it in impl-log. App `active` status is not in
  the clause: `get_app_context` (`auth/deps.py:67-78`) and the holder helper already require it. No import cycle:
  `rbac/repository.py` imports only `organizations.models` (:27), not `organizations.repository`.
- **Hotspots:** `app/modules/organizations/repository.py` and `auth_service.py` are not edited by another open
  ticket (USR-30 is MERGED; USR-38 / USR-39 plans do not list them). `docs/consumers.md`, `docs/spec-v1.md` (§5.16,
  OQ84, OQ89) and `docs/adr/0030-app-super-admin-role.md` are also edited by USR-38 / USR-39: expect a docs-only
  rebase. `tests/test_auth_orgs.py` is only touched at :318-326.
- `admin` app (`org_mode = none`) and `org_mode = none` apps keep answering 422 `org_not_supported` before the holder
  check; the helper also returns False for them.
- The holder's own app-scope designated assignment (`org_id IS NULL`) never makes an org "of the app".
- An org listed through an assignment held only by a disabled account is acceptable (harmless; it is still an org of
  the app). Existing mismatch kept (R7): a member's suspended org is listed but its mint answers `org_access_denied`.
- `MyOrgRead` is used only by this route (grep: `auth_router.py:22,70,83`, `auth_service.py`), so the required field
  breaks no other caller.
- Tier prediction: `.github/scripts/risk_tier.py` could not be run here (no shell in the planning session); by
  `.github/risk-tiers.yml` the file list is `default: medium` + `low` (docs, tests) → **Medium**; High only under Q7's
  fallback.
