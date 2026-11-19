## Attempt 1 — 2026-11-19
Verdict: PASS

Reviewed product commit b2bba43 (`git diff origin/main...HEAD`). The other files in the range (`work/backlog.md`, `work/batches/2026-11-19-1.md`, `work/tickets/USR-23/ticket.md`, `work/tickets/USR-24/ticket.md`) are orchestrator bookkeeping from 735b9e5 and 8ef9da5, not product changes.

### Checks (run by reviewer)
Environment: local Postgres, default TEST_DATABASE_URL.
- `make typecheck` — exit 0, mypy: no issues.
- `uv run ruff check . --exclude .github/scripts` — exit 0, All checks passed.
- `uv run ruff format --check app tests migrations` — exit 0, all files already formatted.
- `uv run alembic check` — exit 0, No new upgrade operations detected.
- `make test`, run 1 — exit 2: `1 failed, 2738 passed in 345.50s`. The failure was `tests/test_app_credential_auth.py::test_no_secret_in_logs` (`assert 401 == 200` on the valid-credential manifest PUT). The diff does not touch `app/modules/app_credentials/**` or `app/core/**`, and the 401 comes from credential auth, which runs before any manifest or RBAC code. On re-runs the test file passed 3 times out of 3 and the single test passed 25 times out of 25.
- `make test`, run 2 — exit 0: `2739 passed, 1 warning in 362.94s`. This matches impl-log (2739 passed). I count the run 1 failure as an intermittent flake that existed before this ticket (see Follow-ups).

### Acceptance criteria
- [x] Every permission replace bumps a version visible on `RoleDefinitionRead` and `RoleRead`. Evidence: `service.py:208` (`touch_role` only on a non-empty diff, inside the shared `_set_role_permissions`, which is used by the PUT, the manifest at `manifest.py:219` and the default-role builder); `repository.py:201-209` (uses `bumped_updated_at`, then refresh); `schemas.py:227` adds `RoleRead.updated_at`. Tests: `test_put_changing_set_bumps_updated_at`, `test_put_noop_does_not_bump`, `test_default_role_rebuild_bumps_member_version`, `test_touch_role_strictly_increasing`, `test_app_manifest_api.py › test_manifest_change_bumps_role_version`, and `ROLE_READ_KEYS` in `test_rbac_roles_api.py:19`. All pass.
- [x] The PUT accepts an optional precondition, and a mismatch gives 412 with nothing written and no audit row. Evidence: `service.py:1283-1288` (check right after the 404, before any write or `record_denied`), `router.py:421-428` (`If-Match` param, ETag set). Tests: `test_put_stale_if_match_412` (envelope, `details: null`, current ETag, `_held` unchanged, audit count unchanged, `updated_at` unchanged), `test_two_clients_second_gets_412`, `test_put_weak_or_malformed_if_match_412[weak|garbage]`, `test_put_if_match_list_containing_current_200`, `test_put_if_match_star_200`, `test_put_noop_with_stale_if_match_412`, and `test_openapi_contract.py › test_role_put_412_and_if_match_declared`. 412 was chosen over 409, as Ming approved (plan Q1 default).
- [x] Without a precondition the PUT behaves as today (R9). Evidence: `test_put_without_if_match_unchanged`. The existing PUT tests in `test_rbac_roles_api.py` and `test_rbac_authz.py` are unedited apart from the additive `ROLE_READ_KEYS` change, and they pass.
- [x] Compare and write are atomic. Evidence: the compare reads `role` after `_locked_app` (`FOR NO KEY UPDATE`). `get_role` loads with `populate_existing=True` (`repository.py:194-199`), so the value comes from after the lock. Every writer of `role_permissions` takes the same lock: the PUT, the manifest (`manifest.py:107`) and the default-role builder (`service.py:288`). Tests: `test_precondition_read_after_app_lock` (lock statement comes before the roles select; no INSERT, UPDATE or DELETE on 412) and `test_get_role_populates_existing`.
- [x] Tests cover a matching version (succeeds and bumps), a stale version (412, permissions unchanged, no audit row) and no precondition. Evidence: `test_put_matching_if_match_200` (exactly one audit row, new tag differs from the old one) plus the tests above. The suite is green on run 2.

Order rule (plan Q3 default, 404 → 412 → 422 → 403 → 409): `test_put_unknown_role_404_beats_412`, `test_put_stale_and_unknown_permission_gives_412`, `test_put_stale_and_409_rule_gives_412`, `test_put_stale_and_needs_super_gives_412_no_denied_audit`. Rename bump (Q4): `service.py:1214`, `test_rename_bumps_updated_at` (a no-op PATCH does not bump).

Ming verification after merge (from plan, optional): in admin-pan (PAN-030), open the same role in two tabs, save in tab 1, then save in tab 2 and check that it takes the 412 "re-fetch" path.

### Issues
None.

### Rules (CLAUDE.md)
No violations found.
- R1: the SQL bump is in the repository, the compare is in the service and raises `PreconditionFailedError` (an `AppError`), and the router only parses `If-Match` and sets `ETag`.
- R3: auth classes and permissions are unchanged.
- R4: no audit row on 412, and the audit row on success is unchanged.
- R6: the 412 uses the existing envelope.
- R7: every changed product file is in the plan's file list, and impl-log lists no out-of-plan files.
- R8: the new behaviour has tests, including 401/403 with `If-Match`.
- R9: changes are additive (optional header, response header, new `updated_at` field).
- R2: no schema change, and `alembic check` is clean.

### Blockers from review agents
None — appsec-engineer attempt 1: PASS, no 🔴 (security-review.md).

### Nits (do not block)
- `tests/test_rbac_roles_if_match.py:251`: `test_put_if_match_401_unchanged` accepts `401` or `403`. `make_client()` with no permissions does not override auth, so the response is a real 401, and the test could assert `== 401` as the plan says.
- `tests/test_rbac_roles_if_match.py:241`: `_audit_total` is defined between tests. It would read better next to `_audit_count` at the top.

### Follow-ups (out of scope)
- `If-Match` on PATCH and DELETE roles. `ETag` header on POST and PATCH roles. `If-Match` on permission PATCH and app PATCH (from the plan).
- `tests/test_app_credential_auth.py::test_no_secret_in_logs` is intermittently flaky in a full run (401 on the valid credential, 1 failure in 2 full runs, 0 failures in 28 isolated runs). Code paths not touched by USR-24.
- Since before this ticket, test and production sessions differ: `app/db/session.py:21` uses `autoflush=False`, but the test session in `tests/conftest.py:105` keeps the default `autoflush=True`. Tests could hide autoflush-dependent bugs (relevant now that `get_role` uses `populate_existing`, which overwrites unflushed attribute changes; no current caller has any).
- Since before this ticket, on main: `ruff check .` and `ruff format --check .` fail only on the kit-managed `.github/scripts/risk_tier.py`. Fix in the spaceM project-kit.
