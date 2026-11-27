## Attempt 1 — 2026-11-27
Verdict: PASS

Reviewed: commit c010b6e (diff vs 86d8936), branch `claude/festive-volta-rikup7`.

### Checks (run by reviewer)
- `uv run ruff check app tests migrations` — All checks passed.
- `uv run ruff format --check app tests migrations` — 446 files already formatted.
- `uv run mypy app` — Success: no issues found in 216 source files.
- `uv run pytest -q` (full) — 2 failed, 4445 passed, 2 warnings in 879.57s. Both failures
  (`tests/test_admin_auth.py::test_disabled_user_401`, `::test_unverified_user_401`) were
  `ProgrammingError: relation "users" does not exist`. Another agent was recreating the schema of the shared test DB
  at the same time; the diff does not touch that file. Re-run of `tests/test_admin_auth.py tests/test_rbac_assignments_api.py
  tests/test_openapi_contract.py`: 109 passed in 38.11s. This matches impl-log (4447 passed).
- `uv run alembic check` (local dev DB) — No new upgrade operations detected.
- `python3 -I .github/scripts/risk_tier.py --base origin/main` — `high`.

### Acceptance criteria
- [x] OpenAPI for the POST lists exactly the real 401/403/404/409/422 outcomes; no mention of checks the code does not
  make. Evidence: `app/modules/rbac/assignments_router.py:49-74`. The 403 names `permission_denied`,
  `admin_wildcard_required` and `self_assignment_forbidden`, which matches `service.py:1760-1764` (own admin-app role, own
  designated super-admin role). The 404 names `not_found`. The 409 is built from the two constants. The 422 names
  `validation_error` and all six reasons, including `recipient_not_staff_super_admin` (`service.py:1705-1710`). The old
  "app disabled, user unverified or disabled" text is gone. The test
  `tests/test_openapi_contract.py::test_assign_role_post_documents_real_outcomes` checks the exact key set
  {201,401,403,404,409,422}, the envelope `$ref` on every 4xx, the tokens, and that neither "disabled" nor "unverified"
  appears; it passes. `test_requires_super_extension` and the other contract tests pass too. `app/core/openapi.py:39`
  keeps the custom 422 text because it is not a generic description.
- [x] An active duplicate returns 409 `conflict` with `details[0].reason = assignment_exists`, and a concurrent change
  returns 409 with `changed_concurrently`. Evidence: `service.py:1479-1484` (public `Final` constants +
  `_assignment_conflict`), raises at `service.py:1622-1624` (row vanished), `:1719` (active duplicate), `:1726-1728`
  (expired-duplicate save race), `:1742-1744` (racing insert). Tests: `test_assign_active_duplicate_409` (both
  params), `test_assign_racing_insert_409_assignment_exists`,
  `test_assign_expired_duplicate_race_409_changed_concurrently`, `test_assign_row_vanished_409_changed_concurrently`,
  `test_assignment_conflict_reason_constants`; all pass.
- [x] All other responses are unchanged (additive, R9). Messages and `code` are unchanged at every site, and `from exc`
  is kept. The DELETE 409 (`service.py:1803`) and the GET/DELETE `_USER_404` are untouched. No existing assertion was
  edited; the only change is the added `message`/`details` lines in `test_assign_active_duplicate_409`.
- [x] Tests assert each 409 reason and the route's OpenAPI responses (see above).

Scope: the 6 changed files match the plan's file list (impl-log included). `app/modules/admin_auth/deps.py` is
untouched (`git diff --stat` is empty). Overriding the 403 on the module-level kwargs is safe because
`require_permission` builds a fresh `responses` dict on each call (`deps.py:126`). The spec sentence is present at
`docs/spec-v1.md:392`. Only USR-37 constants were added. USR-36 can import `REASON_ASSIGNMENT_EXISTS` and
`REASON_CHANGED_CONCURRENTLY` from `app.modules.rbac.service`.

Other `_grant_locked` callers: `bootstrap_super_admin` (`service.py:1664`) checks the active duplicate first, and
nothing catches `ConflictError` on that path. `auth/provisioning.py:80`, `join_requests/service.py:113`,
`membership/service.py:146`, `organizations/auth_service.py:129` and `invitations/service.py:319,336` do not catch or
inspect `ConflictError`. The only `.details` readers in `app/cli.py:603,703` are on the app register and admin-origins
paths, not on grants. The only effect: if one of those routes reached a grant 409 in a race, its body would now include
`details: [{reason}]`. That change is additive and arguably better (see nit).

### Issues
None.

### Rules (CLAUDE.md)
No violations found. R1: the service raises `ConflictError`, and the router only declares docs and imports constants.
R6: same envelope and code, and `details` is a list of `{reason}`. R7: within the plan. R8: green after the re-run. R9:
the change is additive. R3: unchanged (Bearer, `admin:roles:assign`, `x-requires-super: conditional`). R4: unchanged
(`ConflictError` is not in `DENIED_ERRORS`).

### Blockers from review agents
None — appsec-engineer `security-review.md` Attempt 1: PASS, no 🔴/🟡 (two 💭 follow-ups).

### Nits (do not block)
- 🟢 The new `details` also show up on the rare race 409s of the other `_grant_locked` callers (join, join-request
  approve, invitation accept, org create). This is additive and not documented on those routes. A consumer note is only
  worth adding if a client needs it.
- 🟢 `test_assign_row_vanished_409_changed_concurrently` does not assert the message, unlike its siblings. It is
  harmless because the constant test and the expired-race test cover the same message.

### Follow-ups (out of scope)
- The DELETE `…/role-assignments/{assignment_id}` 409 "Assignment was changed concurrently" (`service.py:1803`) has
  no reason. It could reuse `REASON_CHANGED_CONCURRENTLY`.
- Add a `forbidden=` parameter to `require_permission` if another route needs custom 403 text.
- The GET/DELETE `_USER_404` text could name `not_found`.
- Ming, after the next release: check that `GET https://api.mmlabs.online/openapi.json` shows the new 403/404/409/422
  descriptions for the POST and that `info.version` is the new version.
