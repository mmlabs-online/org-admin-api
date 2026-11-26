# Security review — ADM-045

## Attempt 1
Verdict: PASS

Scope: static review of `git diff origin/main...eb2b111` (app/cli.py, app/core/config.py, .env.example, app/modules/usage/retention.py, app/modules/audit/actions.py, docs/deploy.md, Makefile, tests). As instructed, I ran no tests and no `make check`. R8 still requires a green `make check` from the orchestrator before DONE.

I found no blockers or should-fix items. Each focus point:

- **Dry run is the default, and no destructive path is easy to hit by mistake.** app/cli.py:830-831 only deletes with an explicit `--apply` (`store_true`). `allow_abbrev=False` stops `--app` or `--a` from counting as `--apply`. `_Parser.error` (app/cli.py:199-202) turns any unknown argument into exit 64 without echoing it. The dry run (retention.py:88-91) runs `SET TRANSACTION READ ONLY` as its first statement, then only counts. The autobegin session from `_cli_cleanup_scope` (app/cli.py:255-268) makes that a true first statement. The CLI has no retention override, and the Makefile target passes ARGS through but defaults to a dry run.
- **Config validation.** app/core/config.py:149 sets `Field(default=90, ge=90)`, so 0 or a negative value from the environment fails `Settings` validation. An empty `USAGE_EVENT_RETENTION_DAYS=` (the .env.example value) is ignored through `env_ignore_empty=True` (config.py:34) and falls back to 90. config.py:281-285 refuses a retention below `USAGE_ADJUST_WINDOW_SECONDS`, and the message names the keys, not their values (R5). Settle and refund use a strict `<` window (metering.py:44), so equality is safe. The repository also enforces the floor (retention.py:37-38, and `type(days) is not int` rejects bool). It is tested in test_config.py:424 and test_usage_events_retention.py:167.
- **SQL safety.** The cutoff is computed on the DB clock with a bound parameter, `make_interval(days => :n)` (retention.py:39-41). Everything else is SQLAlchemy Core with no string building. `hide_parameters=True` is set on the engine.
- **Batching and locks.** Each batch is a MATERIALIZED CTE: `ORDER BY created_at, id LIMIT 1000 FOR UPDATE SKIP LOCKED`, then `DELETE ... IN` (retention.py:55-69). Each batch is its own transaction with `SET LOCAL lock_timeout = '2s'`, and the snapshot used to compute the cutoff is committed before the first batch. Consume only inserts new rows. Settle and refund lock only rows inside the adjust window, which is no longer than the retention, so the purge never competes with the hot path. The one overlap is a replay of a purged key arriving at the same moment as its delete. It waits at most one short batch and then counts as a new consume, which matches ADR-0022 and is tested by test_purged_key_consumes_again. Nothing else references `usage_events` by foreign key, so the delete cannot cascade or be blocked.
- **Counters untouched.** The service never touches `usage_counters` (test_counters_untouched).
- **No sensitive output.** The CLI prints one line with the count, the retention and the cutoff (app/cli.py:840-843). Any other failure prints a fixed line and never `str(exc)` (app/cli.py:854-857). Audit `changes` hold only `cutoff`, `retention_days` and `deleted` (retention.py:100-104). No idempotency keys, user ids or emails appear anywhere. The audit row is written in the same transaction as its batch, and no row is written for zero deletions (R4).

💭 docs/deploy.md:1011-1013: the section says to add the choice to the `ops-org-admin-api` workflow and run a dry run first. It does not say in so many words that the CLI is never run against production outside that workflow or the documented Mac fallback, and the plan's line "Agents never run it against production" (plan.md:29) is not in the doc. The wording matches the existing `cleanup` section, so this is optional. If wanted, add one sentence: "Production runs go only through the `ops-org-admin-api` workflow (or the fallback below); never point a local checkout's `DATABASE_URL` at production."

💭 app/modules/usage/retention.py:109-112: one short batch ends the run, so a single row held by SKIP LOCKED can end a large purge early. This is documented ("run again") and harmless. It only matters if the job is scheduled later.

💭 app/core/config.py:149: there is no upper bound on the retention. A very large value just means nothing gets purged. That is safe in the security sense, but an upper bound (for example `le=3650`) would catch typos.

Follow-ups (out of scope):
- Owner: add the `usage-events-purge` choice to `ops-org-admin-api` in `mmlabs-online/deployment`. Scheduling is an open owner decision.

## Attempt 2
Not re-run: the rework commit 8bfc001 changes only `tests/test_cli_usage_events_purge.py` (audit query scoped to its own transaction) and `impl-log.md`; the attempt-1 PASS applies (orchestrator note).
