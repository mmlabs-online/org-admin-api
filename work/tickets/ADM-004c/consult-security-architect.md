# consult-security-architect — ADM-004c

## Findings

1. **Anyone can set the GUC, so it is a safety latch and not access control.** `audit.allow_purge` is a custom dotted placeholder GUC. Any role can set it with `SET`, `SET LOCAL` or `set_config(...)`, and that includes the API's own DB role (today the same role as the migration owner, which can also `DISABLE TRIGGER`). The GUC only stops accidental deletes from app code. The real guard is the **hard-coded 90-day floor inside the trigger**: whoever sets the flag still cannot erase the last 90 days without DDL. That DDL is visible and is fixed by the planned split runtime role.
2. **The floor must not come from env or settings.** A floor in settings or env is controlled by the same deploy env the API reads, so a leaked `.env` or a bad deploy could set it to 0. A floor in the migration changes only through a reviewed migration (R2) that runs as a separate deploy step.
3. **The current trigger function raises for every operation and shares one function between the row and statement triggers.** The replacement must branch on `TG_OP`, and `RETURN OLD` only on the allowed DELETE.
4. **Email cleanup today writes one summary row after all batches have committed** (`app/modules/auth/retention.py:66-89`). If it fails mid-run, deletions are left with no row. This confirms the ticket's per-batch item.
5. **A new summary row can never be deleted by the purge that writes it.** Its `occurred_at` is `now()`, which is not older than any cutoff of 90 days or more. Old `audit_log.purged` rows can still be purged by later runs.

## Recommendations

- **Trigger** (`CREATE OR REPLACE FUNCTION`, triggers unchanged):
  `IF TG_OP = 'DELETE' AND current_setting('audit.allow_purge', true) = 'on' AND OLD.occurred_at < now() - interval '90 days' THEN RETURN OLD; END IF; RAISE EXCEPTION 'audit_log is append-only';`
  - Match exactly `'on'`. A NULL or empty setting must fall through to the raise.
  - Downgrade runs `CREATE OR REPLACE` with the original text from `c3f1a9d27b60`, byte for byte. Add a test that compares `prosrc` after up/down.
- **CLI `audit-purge`:**
  - `--older-than-days` is required and is an `int`, with 90 ≤ N ≤ 3650. Refuse non-integers and refuse anything else with exit 64 and a fixed message, never echoing argv.
  - Keep `MIN_DAYS = 90` as a constant, and add a test that asserts it matches the `interval '90 days'` in the migration.
  - Compute the cutoff **once, in SQL** (`now() - make_interval(days => :n)`) and reuse it for every batch, so the cutoff does not creep and the client clock does not matter.
  - Each batch transaction does: `SET LOCAL lock_timeout`, then `SET LOCAL audit.allow_purge = 'on'` (never a plain `SET`), then `DELETE … WHERE id IN (SELECT id … WHERE occurred_at < :cutoff ORDER BY occurred_at, id LIMIT n FOR UPDATE SKIP LOCKED)`, then insert the summary row, then commit.
  - Exclude `action = 'audit_log.purged'` from the delete (tiny volume, and it keeps the purge trail).
  - `--dry-run` only counts, in a `READ ONLY` transaction, and never sets the GUC.
  - DB errors print one fixed line (never `str(exc)`), as the existing `cleanup` command does.
- **Summary row:**
  - `AuditAction.AUDIT_LOG_PURGED = "audit_log.purged"`, actor `system:cli`, `AuditContext.NONE`.
  - `changes = {"cutoff": <ISO timestamptz>, "older_than_days": N, "deleted": int}`, written only when the batch deleted more than 0 rows.
  - No ids, actions or contents of the deleted rows. These keys already pass `_check_keys`.
- **Email cleanup:** write one `email_retention.purged` row in each batch transaction that deletes more than 0 rows (count for that table only), and drop the final aggregate row.
- **Grep test** (`tests/test_audit_purge_guard.py`, a static scan of `app/**/*.py` and `migrations/**/*.py`):
  - Case-insensitive `allow_purge` may appear only in the CLI-only module (e.g. `app/modules/audit/purge.py`) and in the new migration.
  - In `app/`, forbid `DISABLE TRIGGER`, `session_replication_role` and `ALTER TABLE audit_log`.
  - An AST check that only `app/cli.py` imports the purge module, and that no router or `main.py` does.
  - A route-table check that no path contains `purge`.
  - This is a tripwire (string concatenation defeats it), and that is acceptable.
- **Ops workflow:**
  - A separate `audit-purge` choice in `ops-org-admin-api`, in the same `org-admin-api-ops` environment (owner approval), and **never** in a scheduled or cron workflow.
  - Input `older_than_days` is a `choice` (`365`, `180`, `90`), not free text.
  - `dry_run` defaults to `true`.
  - Pass inputs as `env:` variables, never as `${{ inputs.* }}` inside `run:`, to avoid script injection.
  - deploy.md runbook: run the dry run first, keep the run summary as the operator trail, and note that purged rows still exist in backups.
- **Downgrade guard for `af3eb84d2d5e`:**
  - Use the appsec snippet (`SELECT EXISTS (… outcome <> 'success')` → `RuntimeError`).
  - Raise when `context.is_offline_mode()` (the check cannot run there).
  - Add tests for both cases: refuses with a denied row, and passes on a clean table.
  - Add the revision to the "Never downgrade production past" line in deploy.md.
- **Scope of the carried 💭 items:**
  - **In this ticket:** stripping C1 characters (`[\x00-\x1f\x7f-\x9f]`) in `_clean_user_agent`, and adding `type(exc).__name__` to `audit_write_failed`. Both are one-line changes in `audit/service.py`, each with a test and no behaviour risk.
  - **To ADM-002d:** `CF-Ray` / always generating the request id. It changes the middleware's trust semantics, the echoed header, docs and the ADR-0009 amendment, and it is an auth-hardening topic.
  - **Out of both:** the large-table DDL pattern goes into the deploy.md note only, and the admin UI showing `outcome` stays an admin-UI follow-up.

## Risks

- Until the runtime role is split, the table owner can still bypass everything with DDL. The floor limits damage only from the GUC path.
- Backups keep purged rows, which matters if Decree 13 requires actual erasure. Backup retention becomes part of the policy.
- Repeated purges at 90 days erase evidence older than 90 days by design, so the floor is a minimum and not the default. Spec Q6 sets the default at 365.
- The per-row trigger adds work to each delete. It is negligible at batch sizes of about 1000.
- A cleanup that stops partway leaves committed batches, each with its own row. That is correct and safe to re-run.

## Open questions

1. Should the CLI minimum be 90 (the DB floor) or 365 (the policy default, with 90 behind an explicit `--below-policy` flag)? I recommend 90 plus the workflow `choice` input to keep the ticket small.
2. Should `audit_log.purged` rows be kept forever (excluded from the purge, as I recommend) or purged like other rows?
3. Spec Q6 and Decree 13/2023: owner confirmation is still pending. Is backup retention in scope for the purge policy?
