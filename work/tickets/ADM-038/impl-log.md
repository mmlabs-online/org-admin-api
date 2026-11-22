
## Attempt 1

Plan followed with the Q1 default (spec rule: environment by version grammar, inferred = production).

### Files touched
- `migrations/versions/20261122_b7d3f1a9c542_adm038_bug_report_release_link.py` (new): R2 columns, FK SET NULL, 2 CHECKs, partial index, working downgrade.
- `app/modules/bug_reports/models.py`: columns, CHECKs, index, `BugReportReleaseMatch`.
- `app/modules/bug_reports/release_link.py` (new): pure `environment_for_version`.
- `app/modules/bug_reports/service.py`: `_link_release`, store at submit, audit + log carry release id/match.
- `app/modules/bug_reports/repository.py`, `schemas.py`, `router.py`: release summary join, filters, fields.
- `app/modules/releases/repository.py`, `service.py`, `schemas.py`, `router.py`: lookups (FOR KEY SHARE), counts, `StaffReleaseDetail`.
- Tests (new): `tests/test_bug_report_release_link_rules.py`, `tests/test_bug_report_release_link.py`, `tests/test_bug_report_release_link_migration.py`.
- Tests (modified): `tests/test_bug_reports_staff_api.py` (new tests + `test_list_item_shape` key set), `tests/test_releases_api.py`, `tests/test_bug_reports_submit.py` (audit key set).
- Docs: `docs/adr/0028-bug-report-release-link.md` (new), `docs/identity-architecture.md` (D37), `docs/adr/0020-bug-report-triage.md`.

### Checks
- `ruff check` (excluding pre-existing `.github/scripts/risk_tier.py` failures), `ruff format --check`: clean.
- `mypy app`: Success, no issues in 204 files.
- `alembic check`: No new upgrade operations detected; upgrade/downgrade/upgrade round trip OK.
- Full `pytest -q`: 3892 passed, 1 failed (`test_list_item_shape`, expected key set, fixed afterwards; file re-run: 51 passed). The 4 touched suites re-run after the fix: green.

### Out-of-plan files
None. (`test_list_item_shape` is in the already-listed `tests/test_bug_reports_staff_api.py`.)

### Follow-ups
- admin-pan: show `release_match`/`release`, counts, filters; regenerate types (`GET /releases/{id}` schema is now `StaffReleaseDetail`).
- ADM-032/ADM-037 guide: apps send the exact release version string as `app_version`.
- ADM-042 migration must use `down_revision = "b7d3f1a9c542"`.

### Scope self-check
Only plan files touched; no refactors.
