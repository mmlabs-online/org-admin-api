# Review — ADM-034

## Attempt 1
Verdict: PASS

Date: 2026-11-20. Commits 75e5277, a6fc042 vs `origin/main`.

### Checks (run by reviewer)
- `uv run pytest -q` — 3591 passed, 1 warning in 618s (exit 0). Matches impl-log.
- `uv run mypy app` — Success: no issues found in 197 source files.
- `ruff check` on the 34 changed `.py` files — All checks passed; `ruff format --check` — 34 files already formatted. (`make check` lint fails only on the kit file `.github/scripts/risk_tier.py`, which this ticket did not touch. This was already true before the ticket.)
- `uv run alembic check` — No new upgrade operations detected.

### Acceptance criteria
- [x] Existing manifest keys and manifest pushes behave as before. Evidence: the full suite is green, including the unchanged `tests/test_app_manifest_api.py` and `tests/test_app_credential_auth.py` (only the call sites change); `test_app_credential_scope_migration.py:48 test_existing_rows_become_manifest...`; `test_app_credentials_api.py:245/257` (no body / `{}` gives manifest). The manifest route keeps `lock=True` (`app/modules/app_credentials/deps.py:53-56`), and the registry asserts it (`tests/test_route_auth_classes.py`).
- [x] Wrong scope gives 403 `app_credential_scope`. Evidence: `app/modules/app_credentials/service.py:188` (scope check after the 401s, disabled and mismatch checks); `test_app_credential_auth.py:269`; `test_releases_write_api.py:291`. Both assert a `denied` audit row.
- [x] Every PUT outcome is tested, including the race. Evidence: `test_releases_write_api.py:116` create, `:141` replace, `:158` identical retry with no audit row, `:167/:199` enrich deployed/failed, `:217` 409 that writes nothing (also checks the log has no values), `:248` deployed no-op, `:259` status never changes, `:268` a status in the body gives 422. Also `tests/test_release_rules.py` and `tests/test_releases_write_concurrency.py:98/:104`, where two committed sessions race through ON CONFLICT DO NOTHING (`app/modules/releases/repository.py`) and `FOR UPDATE`.
- [x] 422 for each rule, 429, unknown app. Evidence: `test_releases_write_api.py:456` (one parametrised case per OQ45 rule), `:477` version (incl. `\n`/`%0A`), `:501` staging sha, `:508` nested unknown field, `:518` author fields, `:539` details never echo values, `:549` 411, `:562` 413, `:596` 415, `:612` 429 before auth. For an unknown app, Ming's Q1 default applies: `:308` gives 403 mismatch with a valid key, and `test_app_credentials_api.py:329` gives 404 on issuance. The spec §5.13 text says "404 unknown app"; this differs from the spec, but the plan flagged it and Ming approved the default.
- [x] Audit rows only for real changes, and no PII. Evidence: `app/modules/releases/app_write.py:124-131` (the audit view holds env, version, status and counts only); a no-op or conflict writes nothing (`:174-190`); `test_releases_write_api.py:625`; `tests/test_audit_coverage.py:933`. `forbid` rejects author fields.

### Issues
None blocking.

### Rules (CLAUDE.md)
No violations found.
- R1: router → service → repository holds. `read_json_body` is HTTP-side in `body.py`, and the service raises `AppError`s.
- R2: the migration is hand-written with a downgrade that deletes non-manifest keys first. A round-trip test exists, and `alembic check` is clean.
- R3: the route is in the registry with its scope, and the no-CORS, admin JWT and `mmdk_` cases are parametrised.
- R4: the release and denied audit rows are written in the same transaction.
- R5: four settings plus their keys in `.env.example`.
- R7: every changed code file is in the plan. The `work/` files of ADM-036 and ADM-040 on this branch come from the earlier batch-planning commits, not from this ticket's commits.
- R11: `repr=False` is set on the issued secret.

### Blockers from review agents
<filled by orchestrator from security-review.md, if any>

### Nits (do not block)
- `test_body_cap_413` accepts `{413, 422}` for a body longer than its declared length. Pin one code.
- The plan's separate `test_conflict_logs_warning_without_values` was folded into `test_enrich_conflict_409_writes_nothing` (`tests/test_releases_write_api.py:234-245`). Coverage is the same.
- The plan listed "a chunked body over the cap" under 413. A chunked body gets 411 by design (`body.py:26`), and that is tested at `:549`.
- On a deployed or failed record, a retry that omits `has_migrations` (incoming null against a stored value) gets 409. This matches the plan and ADR-0021 §4, but the release-workflow docs should say "always send the same full body".

### Follow-ups (out of scope)
- Ruff fails on the kit file `.github/scripts/risk_tier.py`; fix it in spaceM project-kit.
- Alembic `fileConfig` disables module loggers in tests, which makes the existing `caplog` "not in" assertions vacuous (per impl-log).
- The per-key rate bucket for the release write (Q2) is not done.
- Ops (Ming): register the `admin-pan` app row and issue `releases` keys (plan "Ming verification after merge").

### Review agents (attempt 1)
- appsec-engineer: `Verdict: PASS` — no 🔴, no 🟡; 4 💭 notes (see `security-review.md`). No blockers from review agents.
