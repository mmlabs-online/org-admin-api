## Attempt 1 — 2026-11-26
Verdict: PASS

Reviewed: implementation commit 25066d1 (diff vs 875130f) on `claude/festive-volta-rikup7`. Real tier:
`python3 -I .github/scripts/risk_tier.py --base origin/main` → **medium** (no High path touched; `app/modules/rbac/**`,
`app/core/**`, migrations unchanged; USR-38's `AuthzService.app_super_admin_access` and USR-39's `org_of_app_clause`
are imported, not edited).

### Checks (run by reviewer)
- `uv run ruff check app tests migrations` — exit 0, "All checks passed!"
- `uv run ruff format --check app tests migrations` — exit 0, "444 files already formatted"
- `uv run mypy app` — exit 0, "Success: no issues found in 216 source files"
- `uv run pytest -q` (full suite, Postgres `org_admin_test`) — exit 0, "4393 passed, 2 warnings in 865.82s"
  (matches impl-log; new file 24 tests incl. the 4 parametrized live-check cases)
- `uv run alembic check` (local dev DB `org_admin`, config default) — "No new upgrade operations detected."
- `risk_tier.py --base origin/main` — `medium`

### Acceptance criteria
- [x] `access` always present — `app/modules/organizations/schemas.py:63` `OrgAccess = Literal["member", "super_admin"]`,
  `:72` `access: OrgAccess = Field(...)` with no default (mypy would reject a missing argument); set on every item at
  `app/modules/organizations/auth_service.py:194`. Shape test `tests/test_auth_orgs.py:325` (`"access": "member"`).
- [x] Holder list = member orgs (unchanged, suspended member orgs included) + active orgs of the app as `super_admin`
  with `roles: []`, member wins — `repository.py:131-132` (`is_member = Organization.id.in_(held)`;
  `where = or_(is_member, org_of_app_clause(app_id))` for holders); `held` subquery unchanged byte-for-byte;
  `roles` batch unchanged so `super_admin` rows get `[]` by construction. Tests:
  `test_holder_sees_member_and_app_orgs` (assignment + entitlement orgs), `test_holder_member_org_stays_member` (one row,
  member), `test_holder_excludes_other_app_orgs`, `test_holder_excludes_suspended_app_org`,
  `test_holder_excludes_org_with_only_expired_assignments`, `test_holder_excludes_disabled_entitlement`,
  `test_holder_suspended_member_org_listed_as_member` — all pass. AC1 read as "suspended orgs not listed as
  `super_admin`" per Ming-approved Q2 default.
- [x] Member rule identical to the mint member path — picker `held` (`repository.py:120-130`): org-exact, roles of the
  app, unexpired on the DB clock; mint `RbacRepository.org_role_keys` (`app/modules/rbac/repository.py:497-517`) applies
  the same rule plus `Organization.status == active`. For active orgs identical; the suspended-member-org difference is
  the pre-existing, plan-documented mismatch (Assumptions; Q2), not introduced here. Expired member role falls back to
  the app rule: `test_holder_expired_member_role_becomes_super_admin`. "Org of the app" is the very same
  `org_of_app_clause` the mint uses (`rbac/repository.py:44-74`, via `is_org_of_app` at `tokens/service.py:159`).
- [x] Non-holder output unchanged except `access: "member"` — non-holder WHERE is exactly the old
  `Organization.id.in_(held)`; only the `is_member` select column is added. Tests: `test_non_holder_sees_no_app_orgs`,
  `test_non_staff_with_designated_role_sees_member_list`, every existing `tests/test_auth_orgs.py` list test green;
  `test_non_holder_query_count_constant` asserts no `org_entitlements` reference.
- [x] Correct `total` — count and page share one `where` (`repository.py:133-141`). Tests:
  `test_holder_pagination_total_and_order` (`total` 5 on offsets 0/2/4, global `name, id` order across interleaved
  member/super_admin items), `test_holder_offset_past_member_orgs`.
- [x] Constant query count — `test_holder_query_count_constant` / `test_non_holder_query_count_constant`: equal statement
  counts at `limit=1` vs `limit=50`; holder path exactly one page and one `count(*)` statement on `organizations`.
  Holder check adds a fixed 1–2 statements (`rbac/repository.py:872-882`), 0 when the app has no designation
  (`rbac/authz.py:83-89`).
- [x] Live re-check — `auth_service.py:179` calls `self.authz.app_super_admin_access(user.id, app)` on every call, no
  cache; `test_holder_loses_access_next_call[deleted|expired|staff_deleted|cleared]` (designated assignment deleted,
  expired, `admin:super-admin` deleted, designation cleared) → second call only the member org, `total` 1.
- [x] Picker/mint consistency — `test_every_super_admin_item_is_mintable` (every `super_admin` item, by assignment and by
  entitlement, mints 200 with `org_access: "super_admin"`); `test_excluded_orgs_are_not_mintable` (suspended app org
  absent from list and 403 `org_access_denied` at mint).
- [x] No audit, log without email — no `audit_logs` write in the diff; `auth_service.py:183-186` logs
  `orgs_listed access=super_admin user_id=%s app=%s total=%s` (UUID, app key, count; no email, no org names, R11).
  `test_holder_list_not_audited_and_logged` asserts the exact line, no `@`, unchanged audit count.
- [x] 401/403/422 unchanged — order kept: `org_mode` check → `_user` (401/403) → holder check
  (`auth_service.py:176-179`), so an unverified holder learns nothing. Existing tests green; new
  `test_holder_unverified_403` (no org name in body), `test_holder_bad_account_401`, `test_holder_on_admin_app_422`.
  `tests/test_route_auth_classes.py` untouched and green (R3 class 2, no new route).
- [x] Docs — `docs/consumers.md:173-177` picker paragraph; ADR-0030 §8 accepted (`docs/adr/0030-app-super-admin-role.md:87-93`);
  spec §5.16 Org list bullet decided (`docs/spec-v1.md:417`), OQ84 ticked with the picker half (`:562`); OQ89 was
  already ticked by USR-39 (`:567`).

### Issues
None blocking or should-fix.

### Deviations (impl-log) — judged
- Extra test `test_excluded_orgs_are_not_mintable`: within the plan's AC8 intent (excluded fixtures → 403); accepted.
- Warm-up request in the query-count tests: the first request in a test loads the CORS origins (one-off statement);
  the assertion still compares two measured calls at different limits, which is what AC4b asks. Accepted.
- `test_holder_on_admin_app_422` sets the seeded `admin` app's origins inside the rolled-back test transaction to get
  past the Origin check and reach the 422; test-only, no production effect. Accepted.
- `EXPLAIN` not recorded: a plan instruction, not an acceptance criterion; the clause is USR-39's and already in
  production for the mint; index review is a recorded follow-up. Accepted, carried to Follow-ups.

### Rules (CLAUDE.md)
No violations found. R1: SQL only in `repository.py`, branch and mapping in the service, router untouched. R2: no
schema change. R3: route and class unchanged. R4: read-only, no audit needed (spec §5.16). R6: no new error. R7: every
changed file is in the plan's file list (`impl-log` "Out-of-plan files: None" confirmed against the diff). R8: green
checks, route tests incl. 401/403/422. R9: additive always-present field, `Page[T]`, ordering unchanged. R11: no email
in logs.

### Blockers from review agents
None — appsec-engineer `security-review.md` Attempt 1: PASS, no 🔴/🟡 (two 💭 follow-ups).

### Nits (do not block)
- `tests/test_auth_orgs_super_admin.py:426-429` builds a `foreign` (other-app) org but never mints against it (only
  one error call per test is possible because an error rolls back the shared transaction); the list assertion covers
  it, and other-app mint refusal is covered by USR-39 tests. Drop the fixture or move it to its own test.
- `app/modules/organizations/auth_router.py:84` docstring (OpenAPI description) still reads "Orgs where the account
  holds a role of the calling app"; the plan allowed an optional one-line addition ("…, plus every org of the app for
  its super-admin holders (ADR-0030)").

### Follow-ups (out of scope)
- EXPLAIN / index review of the holder page query (`org_of_app_clause` EXISTS per org) when orgs per app exceed ~10k
  (api consult rec 8).
- Pre-existing mismatch: a member's suspended org is listed (`access: member`, `status: suspended`) but its mint
  answers `org_access_denied` (plan Assumptions; R7 kept it).
