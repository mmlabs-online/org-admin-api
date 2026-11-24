# Review — ADM-037

## Attempt 1 — 2026-11-24
Verdict: PASS

Reviewed commit 803bf4f (branch `claude/festive-volta-rikup7`) against `origin/main`.

### Checks (run by reviewer)
- `uv run ruff check app tests`: exit 0, "All checks passed!"
- `uv run mypy app`: exit 0, "Success: no issues found in 209 source files"
- pytest not run: docs-only diff, no code change (orchestrator instruction). No Postgres/Docker daemon in this environment anyway (`pg_isready`: no response; `docker ps`: no socket).
- `python3 -I .github/scripts/risk_tier.py --base origin/main`: `low`
- `git diff --name-only origin/main...HEAD | grep -vE '^(docs|work)/'`: empty. Only `docs/release-log.md` (new), `docs/deploy.md` and `work/**` changed.
- `create_app().openapi()` dump of the two routes: compared below.

### Acceptance criteria
- [x] 1. Paths, fields and codes match OpenAPI/code. Evidence:
  - Paths and methods: both `PUT /api/v1/apps/{key}/releases/{environment}/{version}` and `…/deployment` are in OpenAPI, and the guide uses them at docs/release-log.md:14-15,34,80. `version` maxLength 32 (OpenAPI param, app_router.py:70) matches release-log.md:37.
  - Version grammar: `models.py:45-46` (`v(0|[1-9][0-9]*)…`, `sha-[0-9a-f]{7}`) and the staging SHA rule (`write_schemas.py:188-191`) match release-log.md:37-38.
  - App-write body (write_schemas.py:86-140; OpenAPI `required: [git_sha, images]`, `additionalProperties: false`, images 1-10, changes ≤200, title 1-200, risk enum low/medium/high, name maxLength 255) matches the table at release-log.md:43-55. Unique `name`/`pr_url` (write_schemas.py:108-117), digest/sha/pr_url/deployment_pr patterns (:37-42) and control-character stripping (:70-76) all match.
  - Deploy-report body (write_schemas.py:143-170; OpenAPI `required: [status, deploy_run_url]`), the run URL with optional `/attempts/<n>` (:43-46), and the deployed_at window of 5 min / 30 days (rules.py:60-70) match release-log.md:85-89.
  - Codes. App write: OpenAPI has 200/401/403/409/411/413/415/422/429, and the code returns 201 on create (app_router.py:84). Deploy report: 200/401/404/409/411/413/415/422/429 plus 201 (deployment_router.py:161). The error table at release-log.md:106-120 matches. The error codes `app_credential_mismatch`, `app_disabled` and `app_credential_scope` (app_credentials/errors.py:7,13,19) and `release_not_pending` / `release_already_deployed` (releases/errors.py) match. 404 only on the report (deployment.py:54, `app_id_by_key` includes disabled/admin, repository.py:135-137) matches release-log.md:82,112.
  - Body caps 256 KiB / 4 KiB (config.py:109,114) and 30/min/IP before auth (config.py:107-108,112-113; rate_limit.py:127-130) match release-log.md:40,83,102. The 411 → 415 order and Content-Length requirement (body.py:186-198) match release-log.md:39-40.
  - Decision rules (rules.py:45-57, 95-117) match release-log.md:97-101.
- [x] 2. Two copy-paste steps parse and use placeholders only. I parsed both YAML blocks with `yaml.safe_load` (each is a list with one step, `continue-on-error: True`, and the deploy step has `if: always()`). I also extracted both `run:` scripts and ran them with `bash -eo pipefail` against a dead port using dummy env values. Both printed `::warning::…` and exited 0. The heredoc terminator `PY` is at column 0 after YAML de-indent. A `deployed_at` in the `date -u +%Y-%m-%dT%H:%M:%SZ` format validates in `ReleaseDeploymentReport.model_validate_json` (checked).
- [x] 3. Warning-only rule: stated up front at release-log.md:6-8. Both snippets end in `|| echo "::warning::…"` with `continue-on-error: true` (release-log.md:130,154,168,187). Every row of the error table says warn.
- [~] 4. Manual curl run against `make dev`: the implementer did not do it, and it cannot be done here (no Postgres, no Docker daemon). Substitute evidence: the snippets ran end to end (see 2), and the contract was checked line by line against code and OpenAPI (see 1). The behaviour is already covered by merged tests (`tests/test_releases_write_api.py`, `tests/test_releases_deployment_api.py`). This moves to the plan's "Ming verification after merge" (first real run in an app repo and in `deploy-<app>.yml`), which supersedes a local curl run. I do not count it as a blocker for a docs-only ticket.
- [x] 5. Owner runbook. docs/deploy.md:545-546 has "2 active per (app, scope)" (service.py:35,129) and "`admin` may only get `releases`" (service.py:122-124). docs/deploy.md:578-582 has the optional `{"scope": "releases"}` body with default `manifest` (schemas.py:29-34, router.py:45-47), the `MMLABS_RELEASE_CREDENTIAL` Environment secret and rotation. docs/deploy.md:600 links the guide from the deployer section.
- [x] 6. No secrets: `grep -nE "mm[ad]k_[A-Za-z0-9]{6,}" docs/release-log.md docs/deploy.md` gives no match. Only `<key_id>`/`<secret>`/`<app-key>` placeholders and `${{ secrets.… }}` appear.

Ming verification after merge (carry to PR): first real run of the app release step in one app repo and of the `deploy-<app>.yml` step in `mmlabs-online/deployment`, with both records visible in the admin release list. Optionally, the local curl run of criterion 4.

### Issues
#### Issue 1 — 🟡 should-fix — secret hygiene in the copy-paste snippet
- **Where**: docs/release-log.md:139-141 (and :155)
- **Expected**: The temp file that holds `Authorization: Bearer <credential>` is always removed, including on the early exit.
- **Actual**: If the body build fails, the step runs `exit 0` before `rm -f -- "$hdr" "$body"`, so a 0600 file containing the release credential stays in `$TMPDIR`. On a self-hosted runner it persists.
- **Fix instruction**: In both snippets, right after `hdr=$(mktemp); body=$(mktemp)`, add `trap 'rm -f -- "$hdr" "$body"' EXIT` and drop the trailing `rm` lines (or keep them; they are harmless).

### Rules (CLAUDE.md)
No violations found. R7: the diff stays inside the plan's file list (docs/consumers.md was left untouched, which the plan allows). R5/R11: no secrets in the docs, and the credential goes through a header file, never argv.

### Blockers from review agents
None

### Nits (do not block)
- release-log.md:151,184: `--data @file` strips CR/LF. `--data-binary @"$body"` is the exact-bytes form. This is harmless today because `json.dump` and `printf` emit no raw newlines.
- release-log.md:96: `--retry 3` does not retry "connection refused". Consider adding `--retry-connrefused`.
- release-log.md:100-101: the deploy report also returns no-op 200 for a different `deploy_run_url` with an older `deployed_at` (the stale guard, rules.py:113-114). Its 409 can also mean "concurrent change, retry" (deployment.py:98, deployment_router.py:131). Both are worth one line.
- docs/deploy.md:564: the pre-existing comment in the issue snippet says "deployment repo's GitHub Environment secret". For a `releases` key the target is the app repo. The bullet at :578-582 says so, but the comment could note it.

### Follow-ups (out of scope)
- OpenAPI omits the `201` response on both release routes (app_router.py:47-59, deployment_router.py:127-137), although the code returns 201 on create. Add `201` to `responses=` (code ticket).
- A reusable GitHub Action in `mmlabs-online/deployment` and a PR-title-to-`changes` helper script (from the plan).

### Orchestrator note (attempt 1)
- Should-fix applied as a docs-only follow-up commit: `trap … EXIT` cleans up the temp credential file in both snippets (no other change). Verdict stands: PASS. No review agents triggered (docs only; no Review-trigger path or area).
- Criterion 4 (manual curl against a local server) moved to Ming's after-merge verification.
