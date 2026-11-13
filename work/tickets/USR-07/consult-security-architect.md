# consult-security-architect — USR-07

**Findings**
- The `app/cli.py` pattern from USR-02f is sound: one fixed line per failure, its own `hide_parameters` engine with NullPool, `DB_ECHO` refused, `_Parser.error` drops argparse's echo of the arguments. `register-app` should reuse all of it.
- `normalize_origin` already enforces D19. It refuses `api.mmlabs.online` and its subdomains, non-https mmlabs hosts and non-default ports, and it accepts loopback. `allowed_origins` only ignores loopback in staging/production; it does not reject it.
- Nothing enforces that an origin belongs to only one app. `AppContextService` binds Origin to `X-MM-App` (D12). A shared origin still breaks app isolation (identity-architecture §194: shared `mm_account_id`). It also lets an app pre-claim the admin UI's future origin, because the admin app has no origins seeded yet.
- `AppService.update` replaces `origins` in full, without telling anyone. If the CLI re-runs with an `--origin` it should not have, it widens CORS/CSRF trust and leaves no trace. The CLI is another process, so the 60 s TTL is how API processes pick up the change (`origin_registry.py`).
- Neither `grant-super-admin` nor `register-app` can run in production today. The runbook (`docs/deploy.md:248-258`) assumes a shell on the Mac. `deploy-org-admin-api.yml` only runs `migrate` and `up`.

**Recommendations**
1. **Refuse in the CLI:** the key `admin`; any key that fails `APP_KEY_RE`; loopback origins when `APP_ENV` is staging/production; a permission that is `<key>:*`, has the wrong prefix or is is_admin (D9). The CLI never sets `status` or `default_role_id`: it cannot disable an app or change its default role. Updating a disabled app → fixed error, exit code ≠ 0.
2. **Origin uniqueness:** add a service-level check that an origin is not held by another app, `admin` included. Put it in the service, not the CLI (R1), and use it on the CLI path now. Whether the admin API also uses it is a planner decision.
3. **Updates show the change:** print `origins added:` and `origins removed:` and the new name/policy. Add `--dry-run`, which rolls back. Exit 0 with "unchanged" when nothing differs. Print only after commit.
4. **Workflow in the deployment repo,** for example `ops-org-admin-api.yml`:
   - Trigger: `workflow_dispatch` with `command` as a choice of `register-app` or `grant-super-admin`.
   - Inputs: `key`, `name`, `origins` (comma-separated), `join_policy` and `org_mode` (both choices), `permissions`, `dry_run` (default true), and `email` (grant only).
   - Gating: `if: github.ref == 'refs/heads/main'`; `environment: org-admin-api` with a required reviewer and a deployment branch rule of `main` only; the same `concurrency` group as the deploy, so it can never race a migration.
   - **Injection:** never put `${{ inputs.* }}` inside `run:`. Pass each input as an `env:` variable. Check each one with a bash regex: key `^[a-z][a-z0-9-]{1,31}$`; origin `^https://([a-z0-9-]+\.)*mmlabs\.online$`; name `^[A-Za-z0-9 ._-]{1,64}$`; permission `^[a-z0-9-]+:[a-z0-9-]+:[a-z0-9-]+$`. Then build a bash array of `--origin "$o"` and run `"${compose[@]}" run --rm --no-deps migrate /app/.venv/bin/python -m app.cli register-app "${args[@]}"`. Check that the env file exists first. Use no `set -x`.
5. **grant-super-admin through the workflow:** the email is a workflow input, and inputs are visible to anyone who can read the run. Mask it with `::add-mask::` before any other step. Use a strict regex rather than `EmailStr` parity. The deployment repo should be private. This also closes USR-02f N3.
6. **Audit (R4):** add an `# R4` marker with actor `system:cli`. Until ADM-004 lands, the workflow run, with who triggered it and the masked inputs, is the audit trail. Keep the CLI output in the step summary.

**Risks**
- Anyone with write access to the deployment repo can dispatch the workflow. Environment reviewers are the only gate.
- The CLI is one process and API processes cache origins, so origin removals take up to 60 s to apply.
- `--name` written into logs and the step summary: keep it to the charset above.

**Open questions**
- Should the origin-uniqueness rule also apply to the admin API (`PATCH /apps`)? It is a new business rule, so the spec or D19 needs updating.
- Who reviews the GitHub Environment when the owner is the only maintainer? Self-approval still adds a deliberate confirm step.
- Should `register-app` also refuse to remove origins unless `--replace-origins` is passed?
