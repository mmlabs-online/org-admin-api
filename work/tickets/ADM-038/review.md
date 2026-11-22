## Attempt 1 — 2026-11-22
Verdict: PASS

Reviewed commit 1f88761 against `origin/main` (batch bookkeeping under `work/` and the ADM-042/ADM-037 files ignored, as instructed).

### Checks (run by reviewer)
- `make check` — stops at `ruff check .` only on the known kit file `.github/scripts/risk_tier.py` (13 errors, out of scope). No other file is flagged. Each step was then run on its own:
- `uv run ruff check <17 changed .py files>` — All checks passed. `ruff format --check` — 17 files already formatted.
- `uv run mypy app` — Success: no issues found in 204 source files.
- `uv run pytest -q` (FULL suite) — **3893 passed, 1 warning in 678s**, exit 0. The warning is the existing pty DeprecationWarning in `test_cli_create_account.py`. This matches impl-log: 3892 + the fixed `test_list_item_shape`.
- `uv run alembic check` — No new upgrade operations detected.
- Migration round trip (upgrade, then downgrade to `a4c81e6d27f3`, then upgrade) is covered by `tests/test_bug_report_release_link_migration.py::test_upgrade_downgrade_roundtrip`, which passed in the full run.

### Acceptance criteria
- [x] 1a Reported path (production and staging): `tests/test_bug_report_release_link.py:43`, `:53`. Code: `app/modules/bug_reports/service.py:107-119` (`_link_release`), `app/modules/releases/repository.py:233-246`.
- [x] 1b A reported version links pending and failed releases too: `test_bug_report_release_link.py:64`.
- [x] 1c Inferred path: production only, latest `deployed_at` ≤ `created_at`, `status = deployed`. Tests: `:84`, `:94`, `:106`. Code: `releases/repository.py:248-262`. `func.now()` is the transaction timestamp, and it equals the `created_at` server default (`app/db/base.py:35-37`).
- [x] 1d An unknown version falls back to inferred, and matching is exact and case-sensitive: `:122`, `:133`. The reported-wins order is tested at `:74`.
- [x] 1e None path: never rejects, and never links another app's release: `:143`, `:149` (the `_submit` helper asserts 201).
- [x] 1f Environment from the version grammar (spec §5.12 and the OQ50 default, Ming's Q1): `app/modules/bug_reports/release_link.py:82-89` reuses `PRODUCTION_VERSION_RE`/`STAGING_VERSION_RE`, and `INFERRED_ENVIRONMENT = PRODUCTION` is at `:79`. Unit test: `tests/test_bug_report_release_link_rules.py:17`.
- [x] 2 Deleting a release keeps the report, sets `release` to null and keeps `release_match` and `app_version`: `test_bug_report_release_link.py:179`. Migration: FK `ON DELETE SET NULL` at `migrations/versions/20261122_b7d3f1a9c542_adm038_bug_report_release_link.py:48-55`.
- [x] 3a List filters (`release_id`, `app_version`, `release_match`) and 422 on bad input: `tests/test_bug_reports_staff_api.py` (`test_list_filter_*`, `:599`). Code: `app/modules/bug_reports/router.py:140-147`, `repository.py:257-262`.
- [x] 3b List and detail show the release summary and `release_match`: `test_bug_reports_staff_api.py:514`. There is no N+1: by inspection, the list is one outer-joined query (`bug_reports/repository.py:235-241`). The suite has no query counter, which the plan allowed for.
- [x] 3c Release detail counts: `tests/test_releases_api.py:200` and the zero-count test. These counts appear only on `StaffReleaseDetail` (`releases/schemas.py:529-534`). The machine routes keep `ReleaseDetail`. The existing tests (`test_releases_api.py:58,70,264`) still cover 401, 403 and 404 for the route.
- [x] 4 Checks green, `alembic check` clean, round trip passes (see above).

Spec §5.12/OQ50 check: rules 1, 2 and 3 are implemented as written, and the environment is chosen by grammar. One difference from the spec: under store-at-submit, deleting a release keeps the stored `release_match` instead of the fact-(2) fallback. This follows the plan's Q3 default, which Ming approved, and is documented in ADR-0028 and D37.

### Issues
None.

### Rules (CLAUDE.md)
No violations found.
- R1: release lookups and counts are in `AppReleaseRepository`, and the router calls `ReleaseService.get_detail`.
- R2: the migration has a working `downgrade()` in reverse order, and `alembic check` is clean.
- R3: no new routes.
- R4: audit is written in the same transaction and holds only ids and the enum (`service.py:179-183`, test `:159`).
- R6: no new error codes.
- R7: every changed file is in the plan.
- R9: all changes are additive.
- R11: the version string never appears in audit or logs.

### Blockers from review agents
None.

### Nits (do not block)
- `app/modules/bug_reports/repository.py:175-176` builds `ReleaseView` from positional `row[18:]` slices. A column reorder in `_select` would silently shift fields, so labelled columns would be sturdier.
- `releases/schemas.py:546-550` round-trips through `model_dump()` and re-validates. It is correct but does extra work.

### Follow-ups (out of scope)
- admin-pan: show `release_match`/`release`, the counts and the new filters, and regenerate types (`StaffReleaseDetail`).
- ADM-032/ADM-037 guide: apps should send the exact release version string as `app_version`.
- ADM-042 migration must set `down_revision = "b7d3f1a9c542"`.

### Review agents (attempt 1)
- appsec-engineer: `Verdict: PASS` — no 🔴, no 🟡; 3 💭 notes (see `security-review.md`). No blockers from review agents.
