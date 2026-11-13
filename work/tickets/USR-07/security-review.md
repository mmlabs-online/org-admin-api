# Security review — USR-07

## Attempt 1

**Verdict: no 🔴 blockers.** The CLI path is sound. Two 🟡 items should be fixed, either in this diff (a docs or code change) or in the deployment PR. Everything else is a 💭 note or a follow-up.

**What I checked:**
- `git diff fb7978f HEAD` (app, Makefile, docs, tests).
- `/home/user/deployment/.github/workflows/ops-org-admin-api.yml` (branch `org-admin-api-ops`, commit a6f27e1).
- `apps/org-admin-api/compose.yml` and `deploy-org-admin-api.yml` in the deployment clone.
- `uv run pytest tests/test_cli_register_app.py -q`: **41 passed**.

### Findings

🟡 **S1. Adding a permission can set `default_role_id`, but the docs say it never changes.**
- Where: `app/modules/rbac/service.py:307` (docstring) and `docs/deploy.md:356`, against `service.py:409` → `RoleCatalogService._build_for_app` → `repo.set_default_role_if_null` (`repository.py:100`).
- What happens: the docstring and the runbook both say "`status` and the default role never change". But when `--permission` adds a key to an existing app whose `default_role_id` is NULL, the rebuild sets `default_role_id = <key>:member`.
- Risk: an operator may have cleared the default role on purpose, so that joiners get no role. A later CLI run that only adds a permission would silently turn auto-granting back on (privilege widening on join once USR-03b lands). The output never shows this change, so it leaves no trace.
- Fix, either one:
  - (a) In `register`, for an existing app, remember `old_default = app.default_role_id`. After the rebuild, if `old_default is None`, restore it, or refuse. Add a test `test_permission_add_keeps_null_default_role`.
  - (b) Keep the behaviour and make it visible: add a `~ default_role: (none) -> <key>:member` line to `RegisterResult.changes`, and correct the docstring and `deploy.md:356`.
- I prefer (b): it matches D9 build semantics and the runbook's "check the +/- lines" discipline.

🟡 **S2. Sharing the concurrency group with the deploy has side effects nobody has written down.**
- Where: `deployment/.github/workflows/ops-org-admin-api.yml:65-67`, with `deploy-org-admin-api.yml:29-31`.
- Sharing the `deploy-org-admin-api` group does stop races with migrations. But GitHub keeps only one pending run per group, so a newer queued run cancels the older pending one:
  - If an ops run is dispatched while a deploy is running and another deploy (a merged release PR) is pending, the pending deploy is cancelled.
  - The reverse also happens: a new deploy cancels a queued ops run.
- An ops run waiting for `org-admin-api-ops` environment approval holds the group, so it blocks every org-admin-api deploy until someone approves or rejects it.
- Neither is a confidentiality issue, but a silently cancelled deploy can leave a security fix undeployed.
- Fix: add to the runbook (`docs/deploy.md`, "Register an app" / "Bootstrap" steps) and to the workflow header:
  - approve or reject ops runs promptly;
  - don't dispatch while a deploy is queued;
  - if a deploy run shows *cancelled*, re-run it.

💭 **N1. The workflow's input handling is clean.**
- Inputs reach the shell only through `env:`. `${{ inputs.* }}` appears only in `run-name` (the `command` choice, which is safe) and in `env:` blocks.
- `set -f` is on. Strict regexes are anchored on the whole string (bash `=~` has no multiline mode, so newlines are rejected).
- `fail` messages never echo the input. Arguments travel in a NUL-delimited file into a bash array, which goes to `docker compose run` as separate argv entries. There is no word-splitting or injection path into the CLI.
- `admin` and the wildcard are refused at the workflow (`:120`, `:131`) and again in the CLI and service.
- `APP_ENV: production` is fixed in `compose.yml`, so the loopback refusal (`service.py:313`) is live in production.
- The image comes from `release.env` (pinned digest); the workflow does not use `latest`.

💭 **N2. Email masking does its job.**
- The `mask` step reads the email from `GITHUB_EVENT_PATH` before any step prints env values (`:78-93`). It masks the raw and lowercased forms.
- The CLI never prints the full email (`mask_email`), so the step summary (`:167-176`) is safe even if `::add-mask::` were not applied to job summaries.
- Remaining exposure, already documented in the workflow header (`:15-16`): the raw email stays in the run's event payload, readable by anyone who can read the deployment repo.
- Minor: if the job is cancelled mid-`run`, `rm -f "$ARGS_FILE"` (`:165`) does not run. The email then stays in `$RUNNER_TEMP` on the Mac until the runner cleans it. Acceptable, given the host already holds the DB credentials.

💭 **N3. The `if: main` + environment gate does not stop anyone with push access.** (Not new in this PR.)
- Where: `ops-org-admin-api.yml:71-73`.
- The job uses no GitHub secrets; its power comes from the self-hosted runner, which can read `/Users/mingming/.config/org-admin-api/org-admin-api.env` and run docker.
- Anyone who can push a branch to `mmlabs-online/deployment` can write their own workflow that targets `[self-hosted, macbook-runner]` without the `if` or the environment, and get the same access.
- This is true of every existing deploy workflow. The real boundary is write access to the deployment repo. See Follow-ups.

💭 **N4. CLI output and error handling follow the existing discipline.**
- Every service refusal maps by `details[0].reason` to a fixed message (`cli.py` `_REASONS`); `str(exc)` is never printed.
- Pydantic errors are reduced to a per-field fixed message, so `input_value` is never echoed.
- A catch-all prints a fixed line. The CLI has its own engine with `hide_parameters=True` and `NullPool`; `DB_ECHO` is refused.
- The success output echoes name, origins and permissions only after commit. These are already validated: the name rejects Cc/Cf/Zl/Zp (no ANSI or terminal-escape injection), and origins and permissions are ASCII-regex values.
- Dry-run rolls back by raising inside `session.begin()`. The `after_commit` origin-registry listener does not fire, and the flag is popped on rollback.

💭 **N5. Origin uniqueness is enforced only on the CLI path, and is not race-proof.**
- Where: `service.py` (`app_keys_holding_origins` check before the write; `repository.py:40`).
- It is a check-then-write under a per-app lock with no global constraint. Two concurrent registrations of *different* apps could both claim one origin, and the admin API (`POST/PATCH /apps`) does not apply the rule at all.
- This is accepted in the plan (the shared concurrency group serializes production runs; the admin API is flag-gated) and is already in Follow-ups.

💭 **N6. The member rebuild drops permissions added to the member role by hand.**
- Adding any permission rebuilds `<key>:member` to *all* non-admin permissions of the app. This drops hand-added grants and can also widen the role by re-adding non-admin permissions that were removed from it by hand.
- This is documented (D9, runbook), dry-run shows it, and a plain re-run leaves the role alone (tested). No change needed. Operators should read the `+ member` / `- member` lines in dry-run.

💭 **N7. Makefile `register-app` passes `$(ARGS)` unquoted to the shell** (`Makefile:25-27`).
- It is a local developer convenience, and the operator controls the input, so there is no trust boundary. Production uses the workflow.

### Follow-ups (out of scope)
- **Deployment repo, beyond this PR:**
  - restrict the self-hosted `macbook-runner` to `main`-ref workflows, through a runner group restricted to selected workflows (if the plan allows it) or branch protection/rulesets on workflow files;
  - keep write access to `mmlabs-online/deployment` limited to the owner, since that is the real gate for every ops and deploy job (N3).
- **Admin API origin uniqueness:**
  - apply the one-origin-one-app rule to `POST/PATCH /api/v1/apps` and record it as the D19 amendment (already listed);
  - consider a DB-level guarantee, such as an exclusion constraint or a normalized `app_origins(origin UNIQUE)` table, so the rule is race-proof on both paths (N5).
- **After ADM-004:** replace the R4 marker in `AppService.register` with an audit entry (actor `system:cli`) covering origin changes, permission adds, member diff and any `default_role_id` change (S1).
- **Optional:** move the ops job to job-level concurrency, or a separate group with an explicit "deploy running?" check, so an unapproved ops run cannot block deploys (S2).
