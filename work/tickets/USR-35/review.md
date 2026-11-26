## Attempt 1 — 2026-11-26
Verdict: PASS

Reviewed: implementation commit `7d03544` (diff vs `0333df6`), branch `claude/festive-volta-rikup7`.
Tier: `python3 -I .github/scripts/risk_tier.py --base origin/main` → **medium** (matches the plan's prediction).
Appsec: no review trigger applies (ticket Areas `api, db`; no changed path matches the `appsec-engineer` globs in
`.claude/loop.config.md:66`).

### Checks (run by reviewer)
- `uv run ruff check app tests migrations` — exit 0, "All checks passed!" (run on the final commit, after the
  implementer's import-sort fix).
- `uv run ruff format --check app tests migrations` — exit 0, "446 files already formatted".
- `uv run mypy app` — exit 0, "Success: no issues found in 216 source files".
- `uv run pytest -q` (full, local Postgres `org_admin_test`) — exit 0, **4442 passed, 2 warnings** in 907 s (the
  2 warnings are the pre-existing `pty.forkpty` DeprecationWarning). Matches impl-log's 4442.
- `uv run alembic check` (local dev DB) — "No new upgrade operations detected." (no model change).
- impl-log vs reviewer run: consistent. Impl-log reports `risk_tier` "low" for a pre-commit working-tree run; the
  committed branch is medium — not a mismatch of results, just a different input.

### Acceptance criteria
- [x] `app` alone returns users with an active assignment of any role of the app, app scope and org scope —
  `app/modules/users/repository.py:180-191` (correlated `EXISTS` over `role_assignments JOIN roles`,
  `Role.app_id == app_id`, `active_assignment_clause()`, `.correlate(User)`); test
  `tests/test_users_filters.py:70` `test_app_filter_matches_app_and_org_scope` (U1 app scope + U2 org scope, U3 other
  app and U4 none excluded, `total == 2`) — passed.
- [x] `role_id` alone, app and org scope — `repository.py:169-179`; test `:81`
  `test_role_filter_matches_app_and_org_scope` — passed.
- [x] Both together — service validates the pair and then keeps only the role `EXISTS`
  (`app/modules/users/service.py:97-105`); test `:93` `test_app_and_role_filter_together` (same ids and total as
  `role_id` alone) — passed.
- [x] Expired-only users excluded; one active row is enough; future expiry included — shared predicate
  `active_assignment_clause()` (`repository.py:15-22`, USR-34) used in both `EXISTS`; test `:106`
  `test_expired_only_excluded[app|role_id]` — passed.
- [x] `total` counts distinct users — filters are `EXISTS` conditions in the shared `conditions` list feeding both the
  count (`repository.py:192`) and the page select, no JOIN; test `:140` `test_total_is_distinct` (one user with four
  matching rows counted once; totals 3 / 2 include the fixture's own holders on a test-created app, deterministic) —
  passed.
- [x] Combines with `email` (`:159`), `status` (`:175`), pagination incl. offset past the end and `created_at desc`
  order (`:189` `test_filters_paginate`) — passed.
- [x] `include=access` not narrowed (Q4/Q11 default) — router passes only the page's user ids to
  `access_summaries` (`app/modules/users/router.py:96`); test `:213`
  `test_filters_with_include_access_not_narrowed` (access groups for both `flt-a` and `flt-b`) — passed.
- [x] 422 `unknown_app` — `service.py:92-95`; test `:239` asserts `code == "unprocessable"`,
  `details == [{"field": "app", "reason": "unknown_app"}]` and that the submitted key is not echoed — passed.
- [x] 422 `unknown_role` (alone and with a known app) — `service.py:98-100`; test `:245` — passed.
- [x] 422 `role_not_in_app` — `service.py:101-104`; test `:253`, exact details — passed.
- [x] `unknown_app` checked first (Q1/Q8) — app lookup precedes the role lookup (`service.py:92` before `:97`); test
  `:259` (unknown app + unknown role, unknown app + known role → only `unknown_app`, one detail) — passed.
- [x] Details shape: one `{field, reason}`, fixed message (Q5/Q12) — `_filter_unprocessable` `service.py:34-35`,
  same shape as `rbac/service.py` `_assignment_unprocessable`; `UnprocessableError` docstring contract
  (`app/core/errors.py:45-50`) respected.
- [x] Malformed and empty values → 422 `validation_error` (Q2/Q9) — `Query(pattern=APP_KEY_RE)` (`router.py:50`),
  `uuid.UUID | None` (`router.py:59`); test `:271` `test_malformed_filters_422` (8 cases incl. `app=` and
  `role_id=`, `loc == ["query", name]`) — passed. FastAPI did not treat `role_id=` as absent.
- [x] Disabled app and its roles accepted (Q3/Q10) — `RbacRepository.get_app_by_key` has no status filter
  (`app/modules/rbac/repository.py:108-109`), `role_with_app` neither (`:575-582`); test `:125`
  `test_disabled_app_accepted` (all three param combinations, 200 with the holder) — passed.
- [x] `app=admin` accepted like any app (Q6/Q13) — by construction: `admin` matches `APP_KEY_RE`, the `admin` app is
  seeded (`migrations/versions/20261111_b638a7c84c31_create_rbac.py:193`) and `get_app_by_key` excludes nothing. No
  dedicated test (not in the plan's test table) — see nits.
- [x] 401 without token — test `:292` — passed. 403 without `admin:users:read`, 200 with it alone — test `:298` —
  passed. Route stays R3 class 1 (`require_permission("admin:users:read", …)`, `router.py:30-41`), no new route.
- [x] OpenAPI documents both params and the 422 reasons — `router.py:32-39` (422 description), `:47-67` (param
  descriptions); test `tests/test_openapi_contract.py:540` `test_users_list_filter_params` (pattern == `APP_KEY_RE`,
  `format == "uuid"`, not required, reasons in descriptions, 422 references the envelope) — passed; existing
  `test_every_error_response_references_envelope` and USR-34 contract tests green.
- [x] Existing behaviour unchanged — `tests/test_users_admin.py`, `tests/test_users_access.py` untouched and green in
  the full run.
- [x] No High path touched — changed code files are `app/modules/users/{repository,router,service}.py` only;
  `app/modules/rbac/**`, `app/core/**`, `app/db/**`, migrations untouched (`APP_KEY_RE`, `UnprocessableError`,
  `RbacRepository` imported/called only). Tier medium confirmed by `risk_tier.py`.

Ming verification after merge (carried to the PR, not verified here): admin-pan PAN-034 filters by app and role on
production; a mistyped app key shows the 422 message, not an empty table; optional p95 of `GET /api/v1/users?app=…`
under 200 ms.

### Scope
Every changed code/test file is in the plan's file list. Other files in `origin/main...HEAD` (`work/backlog.md`,
`work/batches/2026-11-26-2.md`, `work/tickets/USR-34/ticket.md`, `work/tickets/USR-35/*`) are workflow state. No
out-of-plan changes. One planned file was **not** changed: `docs/spec-v1.md` (Issue 1).

### Issues
#### Issue 1 — 🟡 should-fix — spec not updated with the owner's answer (planned step skipped)
- **Where**: `docs/spec-v1.md:365` and `:552` (OQ76).
- **Expected**: the plan's Files table (`plan.md:102`) and Approach step 5 make the spec edit conditional on Ming
  answering Q4 in the batch round. He did: `work/batches/2026-11-26-2.md:33` (item 11 = USR-35 Q4) and `:59`
  ("câu 1–34 theo mặc định", 2026-11-26), also recorded in `work/tickets/USR-35/ticket.md` Notes. The condition is
  met, so :365 "Proposed (not answered …)" and :552 "Not answered (PLAN default)" should read as answered (owner,
  2026-11-26, batch 2026-11-26-2), plus the one line under :364 recording Q1/Q2 (precedence, empty strings).
- **Actual**: spec untouched; impl-log Deviations misreads the condition as "left to the orchestrator".
- **Why not a blocker**: the code implements exactly the spec's stated proposal, so the spec and the code do not
  disagree on behaviour; only the "answered" annotation is stale. It is docs-only (Low tier) and not an acceptance
  criterion.
- **Fix instruction**: preferably add it to this PR before it opens, as a docs-only commit (`USR-35: record owner
  answers in spec §5.15`): replace the two "not answered" phrases with "answered (owner, 2026-11-26, batch
  2026-11-26-2): `access` is not narrowed by `app` / `role_id`", and add under :364 "Both unknown → `unknown_app`
  only (one detail); empty `app=` / `role_id=` → 422 `validation_error`; `app=admin` accepted (owner, 2026-11-26)."
  If the orchestrator prefers not to touch the PR, carry it as the first follow-up (USR-36/USR-37 edit §5.15 in the
  same batch and could fold it in) — it must not be dropped.

### Judgement on impl-log deviations
1. `docs/spec-v1.md` not edited — 🟡 should-fix (Issue 1), not REWORK: behaviour matches the spec, no AC depends on
   it.
2. EXPLAIN not run — 🟢 acceptable as a follow-up. Not an acceptance criterion; the plan made an index a follow-up
   only if a seq scan appeared. The indexes the plan relies on exist (`ix_role_assignments_role_id`,
   `app/modules/rbac/models.py:229`; `uq_roles_app_id_key` leading `app_id`, `:176`), and the query shape is the
   plan's (correlated `EXISTS`, explicit `.correlate(User)`, only one `EXISTS` when both filters are given).
3. Full suite ran before a final import-sort lint fix — resolved: I ran the full suite and all lint/type checks on the
   final commit `7d03544`; all green (4442 passed).
4. `test_total_is_distinct` totals include fixture holders — fine: the filter targets a test-created app/role, so
   the totals are deterministic and the duplicate check (`count(str(u.id)) == 1`) is the real assertion.

### Rules (CLAUDE.md)
No violations found. R1: SQL only in `UserRepository.list`; existence checks and `UnprocessableError` in the service;
router passes HTTP params only. R3: class 1, unchanged `admin:users:read`, no new route (auth registry unaffected).
R4: read-only, no audit. R5: no config. R6: 422 `validation_error` / `unprocessable` through the standard envelope;
fixed messages, no submitted value echoed. R7: inside the plan (spec step aside, Issue 1). R8: happy path,
combinations, 422s, 401/403 tested; 404/409 not applicable (list, read-only). R9: additive optional query params on
`/api/v1/users`, `Page[T]` unchanged, no breaking change. R11: nothing logged.

### Blockers from review agents
None (no review trigger applies).

### Nits (do not block)
- 💭 No test pins `app=admin` (plan Q6 default). A one-liner (`app=admin` → 200, includes a seeded super-admin
  holder) would lock the decision against a future "exclude admin" change.
- 💭 `tests/test_users_filters.py:292-295` — the 401 test asserts only the status; asserting the envelope
  `error.code` would match the 403 test style.
- 💭 `tests/test_users_filters.py:140` — the plan asked also for `len(items) == 1` on a single-holder case; the
  current per-id count assertion covers the same risk.
- 💭 `# fmt: skip` blocks in `tests/test_openapi_contract.py:557-559` and `tests/test_users_filters.py:64-67` are
  harmless but unusual for this repo.

### Follow-ups (out of scope)
- Spec §5.15 :365 / OQ76 :552 answered-annotation (Issue 1), if not folded into this PR.
- Record `EXPLAIN (ANALYZE, BUFFERS)` of the count query for `app=` and `role_id=` on a seeded DB (~1 000 users,
  ~3 000 assignments); add an index only if `role_assignments` is seq-scanned.
- admin-pan PAN-034: surface `details[].reason` (`unknown_app`, `unknown_role`, `role_not_in_app`) as field errors.
