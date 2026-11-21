## Attempt 1 — 2026-11-21
Verdict: PASS

### Checks (run by reviewer)
- `uv run pytest -q` (FULL suite, after the implementer's last test fixes) — `3775 passed, 1 warning in 508.98s` (warning: pre-existing forkpty DeprecationWarning in `test_cli_create_account.py`). exit 0.
- `uv run mypy app` — `Success: no issues found in 199 source files`.
- `uv run ruff check .` — 13 errors, all in `.github/scripts/risk_tier.py` (kit file, untouched, known pre-existing). `ruff check` on the 17 changed `.py` files — `All checks passed!`.
- `uv run ruff format --check .` — 1 file would be reformatted: `.github/scripts/risk_tier.py` (same known kit file). Changed files: `17 files already formatted`.
- `uv run alembic heads` — `911eb99ddf8b (head)` (single head, on ADM-034's `58a494f0449e`).
- `uv run alembic check` — `No new upgrade operations detected.`
- Migration round-trip on the test DB (`TEST_DATABASE_URL`): upgrade head → downgrade `58a494f0449e` → upgrade head — clean, `current = 911eb99ddf8b (head)`. Also covered in-suite by `tests/test_usage_overrides_migration.py::test_downgrade_removes_table` (passed).
- impl-log claims match my run (impl-log reported 3772+3 before fixes; full suite now 3775 passed).

### Acceptance criteria
- [x] 1a Set — `test_usage_overrides_api.py::test_put_creates_201_etag` (201, ETag, `created_by`, no `reason`, 1 row).
- [x] 1b Replace — `::test_put_replaces_200_no_duplicate` (200, one row, `created_at`/`created_by` kept, `updated_at` bumped).
- [x] 1c Remove — `::test_delete_204_then_404`, `::test_delete_expired_override_allowed`.
- [x] 1d Expired → default (DB clock) — `test_usage_overrides_db.py::test_effective_limit_{default_without_override,active_override,expired_override_falls_back,permanent_override,other_user_unaffected,unknown_metric_none}` (rows with `now() - interval`, no clock mock); `::test_get_expired_override_is_inactive`; expiry in the `ON` clause, `app/modules/usage/repository.py:222-236`.
- [x] 1e Below default incl. 0 — `::test_put_zero_and_below_default_allowed`, `::test_effective_limit_zero_override`.
- [x] 2a Reason required — `::test_put_422_cases`, `::test_delete_without_reason_422` (no body / `{}` / blank), `test_usage_schemas.py::test_override_set_422_cases`, `::test_override_remove_requires_reason`.
- [x] 2b Audit rows — `::test_put_set_audit_row_with_reason`, `::test_put_replace_audit_row`, `::test_delete_audit_row`, `::test_noop_put_writes_nothing`; `tests/test_audit_coverage.py` PUT/DELETE entries (count 34).
- [x] 2c 401/403/404 — `::test_routes_401_without_token`, `::test_reads_403_without_read`, `::test_writes_403_with_read_only`, `::test_404_matrix`; `tests/test_route_auth_classes.py` 4 entries.
- [x] 2d Reason never returned/logged — `::test_reason_not_in_response_or_logs` (caplog DEBUG sentinel).
- [x] 2e Past/naive `expires_at` 422 — `::test_put_expires_at_past_or_naive_422`; DB clock via `db_now()`.
- [x] 3 Migration reversible, no drift — see Checks.
- Ming verification after merge: none (plan).

### Scope
Every product/test file is in the plan's file list. `work/backlog.md`, `work/batches/2026-11-20-2.md`, `work/tickets/ADM-036/ticket.md` come from the orchestrator commit `eb91fe9` (bookkeeping), not the implementer. High paths touched (`migrations/**`, `app/db/models.py`) are named in the plan, and the plan gate is approved (ticket.md Notes). Plan deviations (down_revision → `58a494f0449e`, AUDITED 34, DELETE diff has 3 fields) are justified in impl-log.

### Issues
#### Issue 1 — 🟡 should-fix — audit accuracy (R4); same finding as security-review.md
- **Where**: `app/modules/usage/service.py:265-273`
- **Expected**: the `.updated` diff for `expires_at` shows a change only when the instant changes, and stores it in one canonical form (UTC, as the DB returns it).
- **Actual**: `before` is the DB value (`…+00:00`) and `after` is `diff.to_json(data.expires_at)`, which keeps the client's offset. If you PUT a new `limit` together with the same `expires_at` written as `+07:00`, the audit entry records a false `expires_at` from/to change.
- **Fix instruction**: normalise once at the top of `set()` with `expires_at = None if data.expires_at is None else data.expires_at.astimezone(UTC)`, and use it in the no-op compare, `apply`, `insert` and the diff. Add an API test that sets a UTC `expires_at`, then PUTs the same instant as `+07:00` with a new limit, and asserts that `changes == {"limit_value": …, "reason": …}`.
  This does not block, so it can go into a small follow-up or into the next rework, if there is one.

### Rules (CLAUDE.md)
No violations found. R1: routers call the service only, the service raises `NotFoundError`/`UnprocessableError`, and the repository holds the SQLAlchemy code. R2: migration with downgrade, model registered at `app/db/models.py:16`, CHECK text identical. R3: Bearer with declared `admin:usage:read`/`write`. R4: audit in the same transaction, and a no-op writes nothing. R5/R11: log lines contain ids only. R6/R9: 201/200/204/404/422, `Page[T]`, UUIDs, timestamptz.

### Blockers from review agents
None (security-review.md: PASS, one 🟡 = Issue 1 above).

### Nits (do not block)
- `app/modules/usage/service.py:285`: an unchanged PUT logs `usage_override_updated` even though nothing was written. Log `unchanged` instead, or skip the line.
- `app/modules/usage/service.py:261-264`: in a three-way race (insert → DO NOTHING → the other row is deleted before the re-lock), the assert gives a 500. This is very unlikely with staff callers (security-review 💭).

### Follow-ups (out of scope)
- Plan follow-ups stand: admin UI screen, purge of expired rows, index on `created_by`, the seed description of `admin:usage:write`.

### Review agents (attempt 1)
- appsec-engineer: `Verdict: PASS` — no 🔴; 1 🟡 (non-UTC `expires_at` gives a false audit diff on replace, service.py), 3 💭 (see `security-review.md`). No blockers from review agents.
