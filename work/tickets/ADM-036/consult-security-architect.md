# Consult — security-architect — ADM-036 (2026-11-20)

### Findings
1. **What a leaked `mmdk_` key can do** (ADR-0021:216-217): it can mark any app's release deployed or failed, create thin records for any valid (app, env, version), including invented ones like `v999.0.0`, and move `deployed_at` forward with a new run URL (a fake rollback). All of this is integrity-only, but it reaches further than the release log. "Live now" is computed as `ORDER BY deployed_at DESC` over `status=deployed` (`app/modules/releases/repository.py:144-180`), and the §5.12 bug-report `inferred` release link reads that same value, so a forgery also mislabels bug reports. Two limits stay in place: `deployed→failed` is a 409 and the key has no write path to manifests, roles or users.
2. **The rate limit does not contain a leaked key.** The limit is per IP (`app/core/rate_limit.py:139`), so an attacker on their own IP gets a fresh 30/min bucket. The legitimate runner, meanwhile, shares one bucket across every app's deploys (ADR-0021:227).
3. **Risk of a 500 from a regex mismatch.** `DEPLOY_RUN_URL_RE` and the version regexes end in `$` (`models.py:45-51`), and the same text is used in DB CHECKs (`models.py:92-95`). If the write validator uses Python `re.match`/`re.search`, `$` also matches before a trailing `\n`. Postgres rejects that value, so the request ends in an IntegrityError and a 500. Pydantic `pattern=` (Rust engine) is safe; hand-written validators are not.
4. **The DB CHECK `deployed_iff_deployed_at`** (`models.py:76-77`) already forces `deployed_at` to be NULL on `failed`. The service must enforce "required with deployed, refused with failed" before the write, or the CHECK turns into a 500.
5. **Lock picture.** Deployer auth does an UPDATE on the credential row (`deployer_credentials/service.py:148`), and that row stays locked until commit. As a result, reports made with the same key are serialised. The ADM-034 app write takes `lock_app` (`FOR NO KEY UPDATE`, `rbac/repository.py:81-93`). Neither writer locks the other's credential row, so no lock cycle exists as long as both lock **apps row → release row** in that order.
6. **Route order** must be rate-limit → `DeployerDep` → app lookup (`deployer_credentials/deps.py:5-6`). Authenticating before the app lookup stops unauthenticated callers from finding out which app keys exist through 404 vs 401.

### Recommendations (by acceptance criterion)
- **AC1 transitions/retries:** Load the row with `SELECT … FOR UPDATE` and decide every transition in the service against the locked row. Never use a blind UPDATE.
  - A same-URL `deployed→deployed` and a retried `failed→failed` with the same URL are both no-ops: return 200 with the stored record and write no audit row.
  - **Stale-report guard:** if `deployed→deployed` arrives with a different URL and `incoming deployed_at < stored deployed_at`, answer 200 no-op and leave the row unchanged. Otherwise a late retry of an older run silently rewrites "live now", which is a lost update.
  - Audit records ids and status only, plus `created_via`. A report-first create writes exactly **one** row, `release.created`, not created + deployment_reported. Register the route in `tests/test_audit_coverage.py` with a no-op variant that asserts zero rows. A 409 writes no row.
- **AC2 report-first / concurrency:**
  - Write path: `INSERT … ON CONFLICT (app_id, environment, version) DO NOTHING RETURNING id`, then `SELECT … FOR UPDATE`.
  - If the select finds nothing because a staff delete raced in between, retry once and then return 409. It must never be a 500.
  - The report should not take `lock_app`. A plain app lookup is enough, and disabled apps are still accepted. If PLAN prefers that both writers take `lock_app`, keep the order apps → release.
  - Add a test with two sessions (report vs. ADM-034 PUT on the same key, in both orders) and assert the final row in each case. Examples: thin deployed + enrich → 200; pending PUT + report → deployed with the PUT body intact.
  - Keep the transaction short: no I/O or logging calls between the lock and the commit.
- **AC3 401/422/429:**
  - Body schema: `extra="forbid"`; `status` as a Literal; `deployed_at` as `AwareDatetime` in strict mode (ISO string only, no epoch numbers), normalised to UTC and checked against the **server** clock (≤ now+5 min, ≥ now−30 d).
  - `deploy_run_url` must full-match, with run id ≤ 20 digits and attempts ≤ 3 digits. Validate the version path parameter with the same grammar and a length cap before touching the DB.
  - Add a 422 test for a trailing `\n` (or `%0A` in the path) on both the URL and the version.
  - Error details name the field and never echo its value.
  - Add a **per-`key_id` bucket after auth** alongside the per-IP one (the R3 runtime-scope pattern). Test the 4 KiB cap with 411/413.
  - Assert that an `mmak_` key, an admin JWT and a missing header each get 401, and that a CORS preflight gets no `Access-Control-Allow-Origin` header.
- **AC4 rollback:** a test should report v2, then v1 with a new URL and later `deployed_at`, and the live read must return v1. Also test the stale-report guard: an older `deployed_at` with a new URL leaves v2 live.
- **Logs:** limit log lines to app key, environment, version, status and `key_id`. Never log the `Authorization` header or the request body.

### Risks
- Forged "live" versions mislead staff and bug-report linking until someone notices. Detection depends on `last_used_at` plus audit review; nothing alerts.
- The URL allow-list only proves the URL points to *some* run in `mmlabs-online/deployment`, not the run for this app. Proving that needs the GitHub API, which was rejected.
- OQ48 collision: org-admin-api and admin-pan both map to the `admin` app. A deploy report for admin-pan `v1.2.0` would mark org-admin-api `v1.2.0` as deployed.
- If the report's 409 rolls back the transaction, the `last_used_at` touch is rolled back with it. This is minor.

### Open questions for Ming (suggested defaults)
1. Stale `deployed→deployed` (older `deployed_at`, different URL): **default: 200 no-op, no audit.**
2. Per-key rate limit on the deployer route in addition to per-IP: **default: yes, 30/min per key.**
3. Allow the deploy report only from the runner's egress IP (config allow-list)? **Default: no for v1**, because the runner's IP may change. Revisit if a key is ever leaked.
4. OQ48: **default: admin-pan gets its own app row** before its releases are reported, and ADM-036 PLAN records that.
5. Should a staff alert or the dashboard flag a deploy report that creates a record from scratch (report-first) for production? **Default: no alert in v1.** The audit row with `created_via=deployer` is enough.

Files: `app/modules/releases/models.py`, `app/modules/releases/repository.py`, `app/modules/deployer_credentials/service.py`, `app/modules/deployer_credentials/deps.py`, `app/core/rate_limit.py`, `app/modules/rbac/repository.py`, `tests/test_audit_coverage.py`, `docs/adr/0021-release-log-and-deployer-credential.md`.
