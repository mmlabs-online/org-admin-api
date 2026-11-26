## Attempt 1
Verdict: PASS

Reviewed commit 160145e (branch claude/festive-volta-rikup7) against origin/main, 2026-11-25. Batch bookkeeping under `work/` ignored.

### Checks (run by reviewer)
- `uv run pytest -q` (full suite, local Postgres): `4161 passed, 2 warnings in 810.11s`. This matches the impl-log (4161 passed).
- `uv run mypy app`: `Success: no issues found in 211 source files`.
- `uv run ruff check <changed .py files>`: `All checks passed!`. `ruff check .`: the 13 known errors, all in `.github/scripts/risk_tier.py`. `ruff format --check .`: only `.github/scripts/risk_tier.py` is flagged. `git diff origin/main...HEAD -- .github/scripts/risk_tier.py` is empty, so that file is unchanged and out of scope.
- `uv run alembic check` (test DB): `No new upgrade operations detected.`
- Migration round trip on `org_admin_test`:
  - `upgrade head` → `d4a8e2c6f175 (head)`
  - `downgrade -1` → `c91e4b7a3d58`; `pg_constraint` lists only `ck_users_status_valid`
  - `upgrade head` → `d4a8e2c6f175`; `ck_users_display_name_length` is back
  - Conclusion: the downgrade at migrations/versions/20261125_d4a8e2c6f175_usr33_users_display_name_length.py:39 resolves the short name through the naming convention correctly.

### Acceptance criteria
- [x] 1a Self-service set, clear and no-op: tests/test_auth_profile.py:84, :100, :111 pass.
- [x] 1b Staff set, clear and both fields at once; G5 enforced: tests/test_users_admin.py:482, :491, :504, :564 pass.
- [x] 1c 422 for invalid values, with no echo: tests/test_auth_profile.py:159 and :172, plus `test_patch_invalid_display_name_422_no_echo` (tests/test_users_admin.py:535) and the `test_user_display_name_rejects` unit tests.
- [x] 1d Normalisation: `UserDisplayName` (app/core/text.py:83-93) applies NFC, then strip, collapse of Zs runs, trim to 1-100, the category check, the blank-lookalike check and the combining-mark cap. Tested by tests/test_auth_profile.py:135 and the `test_text.py` normalise cases.
- [x] 1e DB CHECK in app/modules/users/models.py:22-25 and in the migration. Covered by `test_display_name_check_rejects_empty`, the round trip and `alembic check` above.
- [x] 2a The token `name` claim follows a change: tests/test_auth_profile.py:310 sets a name, gets `name` in the new token, then clears it and the claim is absent.
- [x] 2b The USR-29 member read shows the change at once: tests/test_auth_profile.py:329 uses an org-members:read key with `updated_since` set before the change and gets back the user with the new `display_name`.
- [x] 2c `updated_at` changes only on a real change:
  - The same-value short-circuit is at app/modules/users/profile_service.py:56 and app/modules/users/service.py:63.
  - The strict bump is at app/modules/users/repository.py:38-45.
  - Tests: `test_same_value_is_noop` and `test_set_name_bumps_updated_at`.
- [x] 3a Audit rows never contain the name:
  - Both paths write `changes={"display_name": {"set": bool}}` (profile_service.py:64, service.py:83).
  - The denied row (service.py:70-77) has no `changes`.
  - `audit.diff.USER` is unchanged; `test_user_diff_tuple_excludes_display_name` covers it.
  - Tests: tests/test_auth_profile.py:355 and tests/test_users_admin.py:590.
- [x] 3b The log tests are non-vacuous:
  - tests/test_auth_profile.py:371-390 and tests/test_users_admin.py:605 re-enable the module loggers and capture `app` at DEBUG.
  - They assert exactly one `display_name_changed` record from the right module, containing the user id, so the test would fail if logging were silenced.
  - They then assert the marker is absent from `caplog.text`. The self-service test covers a 422 request too.
- [x] 4a Self-service 401/403/429: tests/test_auth_profile.py:200-305 cover no cookie, logged out, disabled, account swap from another device, missing app header, wrong origin, unverified email, admin app allowed, IP limit and per-user limit.
- [x] 4b Staff 403/404/G5:
  - Tests: :547 (`admin:users:write` required), :558 (unknown user → 404), :564 (privileged target → 403 `admin_wildcard_required` with a denied audit row; `admin:*` → 200).
  - G5 also covers name changes: service.py:65-77.
- [x] 4c Route-class registry: `("POST", "/api/v1/auth/profile")` added to `COOKIE` (tests/test_route_auth_classes.py:58). `IDENTITY_AUDITED` and the PATCH entry in `AUDITED` were updated in tests/test_audit_coverage.py.
- [x] 4d Docs: the diffs of the spec (§5.8 line and Admin bullet), identity-architecture (§3, §4, D40), the ADR-0005 amendment, consumers.md and CLAUDE.md R3 (2) all match the plan's Q10 and D40 text.

Specific checks requested:
- **Moving the shared `DisplayName` did not change usage behaviour.**
  - The chain at app/core/text.py:76-81 is byte-identical to the removed copy in usage/schemas.py: NFC, then `trimmed(100)`, then the category check.
  - `Reason` now uses the moved `trimmed(500)` and is otherwise unchanged.
  - `test_display_name_usage_unchanged` and the full usage suite pass.
- **The staff PATCH keeps the USR-01b status contract.**
  - `UserUpdate` (app/modules/users/schemas.py:25-41) uses `extra="forbid"`, rejects `{}` and rejects an explicit `status: null`.
  - The existing `test_patch_rejects_invalid_bodies` cases (`{}`, `{"status": None}`, `banned`, extra fields) are unchanged and pass.
  - A status-only body still calls `set_status` unchanged (service.py:50-53).

### Issues
None.

### Rules (CLAUDE.md)
No violations found. Rule by rule:
- R1: the router does HTTP only; services raise `AppError` subclasses.
- R2: hand-written migration with a working downgrade.
- R3: cookie class, registry updated.
- R4: the audit row is written in the same transaction.
- R5: settings plus `.env.example` keys.
- R6/R11: no echo of the value, no value in logs.

### Blockers from review agents
None. security-review.md has `Verdict: PASS`.

### Nits (do not block)
- 💭 app/modules/users/service.py:63: the same-value short-circuit runs before the G5 check. A `users:write` actor without `admin:*` therefore gets 200 (no-op, no denied row) when sending a privileged target's current name. Nothing changes, so this is harmless; the order could be swapped for consistency with `set_status`.
- 💭 The migration uses the short name `display_name_length` with the naming convention, while older migrations use `op.f("ck_…")`. The round trip proves it works.

### Follow-ups (out of scope)
- As listed in impl-log: one shared helper for "resolve account on device"; the USR-32 test nits; staff UIs showing email/id next to the name.

### Review agents (attempt 1)
- appsec-engineer: `Verdict: PASS` — no 🔴; 1 🟡 (a name made only of invisible combining marks still passes — needs a "visible base character" rule), 2 💭 (see `security-review.md`). No blockers from review agents.
