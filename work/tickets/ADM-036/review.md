# Review — ADM-036

## Attempt 1
Verdict: PASS

Date: 2026-11-20. Reviewed commit 3592976 (branch `claude/festive-volta-rikup7`) against `origin/main` (contains ADM-034).

### Checks (run by reviewer)
- `uv run ruff check .` — 13 errors, all in `.github/scripts/risk_tier.py` (kit file, untouched by this diff; known pre-existing). No error in any changed file.
- `uv run ruff format --check .` — 1 file would be reformatted: `.github/scripts/risk_tier.py` (same kit file); 441 files formatted.
- `uv run mypy app` — `Success: no issues found in 199 source files`.
- `uv run pytest -q` — `3688 passed, 1 warning in 623.97s` (matches impl-log: 3688 passed).
- `uv run alembic check` — `No new upgrade operations detected.` (no models/migrations changed).

### Acceptance criteria
- [x] Every transition tested, incl. retries (same response, no duplicate audit) — `tests/test_releases_deployment_api.py`: `test_create_201` (deployed + failed, :121), `test_retry_of_create_200_same_body_one_audit_row` (:148, equal JSON, one audit row), `test_pending_to_deployed_200` (:163), `test_pending_to_failed_200` (:178), `test_failed_to_deployed_200` (:187), `test_failed_to_failed_new_url_200_audited` (:196), `test_failed_to_failed_same_url_noop` (:207), `test_deployed_same_url_noop` (:218), `test_deployed_new_url_redeploy_200_audited` (:229), `test_deployed_new_url_older_deployed_at_noop` (:244, Q6), `test_deployed_to_failed_409_unchanged_no_audit` (:257, code `release_already_deployed`, row and `updated_at` unchanged, zero audit, log has key_id but no URL/secret). Pure table: `decide_report` in `app/modules/releases/rules.py` covered in `tests/test_release_deployment_schemas.py` (incl. `test_window_boundaries`, `test_deployed_to_failed_conflict`). All pass.
- [x] Report-first creates a thin record ADM-034's PUT can enrich — `test_report_first_then_app_put_enriches` (:302, 201 then app PUT 200, status/deployed_at/run URL/`created_via='deployer'` unchanged, audits `release.created`, `release.updated`); `test_report_on_pending_then_identical_app_put_noop` (:329); races in `tests/test_releases_deployment_concurrency.py` (`test_report_then_app_put_concurrent`, `test_app_put_then_report_concurrent`, `test_parallel_identical_reports_one_create`). All pass.
- [x] Wrong key kind / admin JWT → 401; 422; 429 — `test_401_uniform` (:411: missing, malformed, unknown key_id, wrong secret, revoked, `mmak_` manifest, `mmak_` releases, admin JWT; one identical body, `WWW-Authenticate: Bearer realm="deployer"`, nothing written); `test_401_before_body_read` (:444); `test_rate_limit_429_before_auth` (:454, `Retry-After`, separate from `app_release` bucket); `test_invalid_body_422` (:527), `test_bad_path_422` (:552), `test_deployed_at_window_422` (:568), `test_malformed_json_422` (:505); 411/413/415 tests (:470–:495); registry/OpenAPI/no-CORS/admin-JWT/app-key tests in `tests/test_route_auth_classes.py` (`DEPLOYER_CREDENTIAL` exact, `deployerCredential` scheme). All pass.
- [x] After a rollback report the live read shows the rolled-back-to version — `test_rollback_report_moves_live` (:357, v1 → v2 → v1 with later `deployed_at`; `GET /api/v1/releases/live` returns `v1.0.0`); `test_stale_report_does_not_move_live` (:369). Pass.

Ming verification after merge (from plan, for the PR): deployment-repo step that sends the report; issue the first deployer key (ops).

### Issues
None.

Code review notes (no action needed):
- R1 layering holds: router `app/modules/releases/deployment_router.py:210-232` does HTTP only; service `app/modules/releases/deployment.py:41-132` raises `NotFoundError`/`UnprocessableError`/`ConflictError` subclasses; SQL only in `repository.py`.
- Order is rate limit (dependency, `deployment_router.py:189`) → auth (`DeployerDep`) → path check → body read → 404; unknown app 404 only after auth (`test_unknown_app_404_after_auth`).
- `deployed_at` window uses DB clock (`repository.py` `db_now`), `rules.py` `deployed_at_window_error`; schema is strict, `extra="forbid"`, `AwareDatetime`, URL fullmatch with 255 cap (`write_schemas.py` `ReleaseDeploymentReport`, `_match` uses `fullmatch`).
- Audit rows carry ids/status/field names only, never the URL or timestamp (`deployment.py:83-91`, `:124-130`; `test_audit_changes_ids_and_status_only`). No-op and 409 write nothing.
- `insert_if_absent` change (`repository.py:127`) keeps ADM-034 behaviour (status default `pending`, now overridable); ADM-034 tests pass.

### Rules (CLAUDE.md)
No violations found. R3 4(b) and R9 updated in `CLAUDE.md` to name the exact route; R5: 4 new settings in `app/core/config.py:111-115` and `.env.example:75-80` without values; R4: audited in the same transaction; R8: happy, 404, 409, 422, 401 tests present.

### Blockers from review agents
Pending: the diff touches `app/core/` and `.env.example`, so `appsec-engineer` (`security-review.md`) must run per `loop.config.md` Review triggers; its 🔴 findings, if any, override this PASS.

### Nits (do not block)
- 💭 Plan AC3 listed a "cookie only" case in `test_401_uniform`; it is not in the case list (`tests/test_releases_deployment_api.py:420-429`). The missing-header case covers the same code path.
- 💭 Planned `tests/test_release_deployment_rules.py` was merged into `tests/test_release_deployment_schemas.py`, and `test_create_deployed_201`/`test_create_failed_201` became one parametrised `test_create_201`; deviations are logged in impl-log.
- 💭 `git diff origin/main...HEAD` also shows orchestrator board edits (`work/backlog.md`, `work/batches/2026-11-20-2.md`, `work/tickets/ADM-034/ticket.md`); these are workflow state, not code.

### Follow-ups (out of scope)
- `mypy tests` has pre-existing errors (not gated); the new tests add a few of the same kind (impl-log).
- Cross-repo: deployment repo step that sends the report (ADM-037 guide).

### Review agents (attempt 1)
- appsec-engineer: `Verdict: PASS` — no 🔴, no 🟡; 3 💭 notes (see `security-review.md`). No blockers from review agents.
