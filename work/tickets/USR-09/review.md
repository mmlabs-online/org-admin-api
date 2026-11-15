## Attempt 1 — 2026-11-15
Verdict: PASS

Reviewed `git diff 4c01328 HEAD` on `claude/magical-cori-2ssw8l` at c6e98aa. The clone is shallow, so `4c01328...HEAD` has no merge base. 4c01328 is an ancestor of HEAD (`git log 4c01328..HEAD` lists the 5 USR-09 commits), so the two-dot diff is the same diff.

### Checks (run by reviewer)
- make check (ruff, format, mypy, pytest): ruff "All checks passed!", format "382 files already formatted", mypy "Success: no issues found in 89 source files", pytest **"1254 passed, 1 warning in 74.02s"**. This matches impl-log.md (1254 passed). The warning is the stdlib `forkpty` DeprecationWarning from the pty test.
  - My first run had 1 failure: `tests/test_role_catalog_api.py::test_post_roles_flag_off_is_405`, `relation "apps" does not exist`. Another `pytest` process (not mine, started 09:00) was running against `org_admin_test` at the same time, and the migration tests in that run were downgrading and upgrading the schema. The rerun after that process ended was fully green. This was a test-DB conflict, not a defect in this diff.
  - The impl-log reports 381 formatted files and I got 382. This is a counting difference only (no unformatted file), so it is not a blocker.
- `uv run pytest -q tests/test_cli_create_account.py tests/test_auth_join.py`: 83 passed.
- `git diff --quiet 4c01328 HEAD -- tests/test_auth_join.py`: unchanged.
- alembic check: not applicable. There is no migration or model change in the diff.

### Acceptance criteria
- [x] **AC1.** Creates a verified user that can log in at once and, after `--join`, obtain an access token for that app.
  - `test_create_verified_user_no_join`: `email_verified_at` is set, `status=active`, `PasswordHasher.verify` passes, there are 0 assignments, and stdout has the 4 exact lines.
  - `test_email_verified_at_from_db_clock`: `email_verified_at == created_at`, both from DB `now()` (`provisioning.py:59`).
  - `test_create_with_join_then_login_and_token`: real `POST /auth/login` → 200, then `POST /auth/token` → 200 with `roles == ["stoktok:member"]` and `email_verified is True`. The assignment has `org_id`, `expires_at` and `granted_by` all NULL.
  - `test_create_no_email_tokens_or_send_log`: no new `email_tokens` or `email_send_log` rows.
- [x] **AC2.** An existing email gives exit 1 and leaves the existing user unchanged.
  - `test_existing_email_exit_1_unchanged` covers 4 cases: unverified, verified, disabled and mixed-case.
  - In each case `password_hash`, `email_verified_at`, `status` and `updated_at` are identical afterwards, and there are 0 assignments even with `--join`.
  - Detection relies only on the unique constraint (`provisioning.py:58-62`, savepoint in `UserRepository.add`). There is no pre-check and no UPDATE path.
- [x] **AC3.** A weak password is refused with the same rules as sign-up. Non-TTY stdin and a mismatched confirmation are refused.
  - `test_weak_password_refused` covers too_short, too_common, too_long and short+common. The hasher is not called and the scope is `_never`.
  - `test_password_max_length_128_accepted`. The same `PasswordPolicy(settings.password_min_length)` as `auth/deps.py:29` and the same `PASSWORD_MAX_LENGTH`. NFKC normalization is shared through `PasswordHasher`.
  - Refusal tests: `test_non_tty_refused_before_prompt_and_db`, `test_subprocess_non_tty_exit_1`, `test_password_mismatch_refused`, `test_email_retype_mismatch_refused` (different, invalid and empty retype, asked before the password prompts) and `test_getpass_warning_refused`.
- [x] **AC4.** The password never appears in stdout, stderr, log records, audit or the process list.
  - `test_secret_not_in_output_or_logs` uses PW and its NFKC variant, with and without join. It captures DEBUG on root, `sqlalchemy` and `asyncpg`, and checks that the hash is not in the output or in `app.*` records.
  - `test_db_error_no_leak` checks a `DBAPIError` and an `IntegrityError` that carry the email, the hash and a DETAIL line.
  - `test_password_option_is_usage_error`: `--password`, `--password=` and a positional value give exit 64 with the value not echoed.
  - `test_pty_end_to_end_no_echo_no_argv` checks `/proc/<pid>/cmdline` and `/environ` at the repeat prompt, and that PW is not in the whole pty transcript (so echo was off).
  - Audit stand-in: `test_audit_log_line_ids_only`.
- [x] **AC5.** `--join` with an unknown, disabled or `admin` app exits 1 and creates nothing (one transaction).
  - `test_join_refusals_create_nothing` covers 7 cases: unknown, disabled, admin, closed, approval, no default role, and an admin-permission default role. Each gives exit 1, the fixed message, 0 users and an unchanged `role_assignments` count. `admin` refuses before the scope is entered (`_never`).
  - `test_failure_after_insert_rolls_back_user`: `_grant_locked` is forced to raise, and the user inserted earlier in the same savepoint is gone.
- [x] **AC6.** An ADR records the verified-flag exception, `docs/deploy.md` is updated, and `make check` passes.
  - `docs/adr/0009-operator-vouched-verified-email.md` is present.
  - The D16 row in `docs/identity-architecture.md:243` is amended, and so is the Context of ADR-0007.
  - In `docs/deploy.md`: the new section is at lines 535-575. It says "No `-T`" (545) and that `create-account` "must never be added" to the ops workflow (573). Setup step 6 (249) and the grant-super-admin wording, step 1 and step 3 are amended.
  - make check is green (above).

### Specific points the orchestrator asked about
- **JoinService refactor is behaviour-identical.** `lock_joinable_app` (`membership/service.py:36-43`) and `join_default_role` (46-59) hold the removed code verbatim, with the same exceptions, the same messages and the same warning log on the same module logger. `JoinService.join` keeps its order: app-mismatch check → lock → account/user/verified checks → already-member short-circuit → default role → grant → audit (lines 74-104). `tests/test_auth_join.py` is unchanged and passes.
- **Refusals raise inside the transaction, so no user is orphaned on any path.**
  - Every refusal after the scope opens is an exception raised inside `async with scope()` (`cli.py:361`). `_cli_session_scope` uses `session.begin()`, which rolls back on any exception, BaseException included.
  - Output is printed only after the scope exits cleanly (`cli.py:376`).
  - Join refusals happen before the insert, and a `_grant_locked` failure is covered by the forced-failure test.
- **Lock order is app → user.** `provisioning.py:53-62` locks the app and reads the role before `INSERT INTO users`. `test_locks_app_before_user_insert` asserts the statement order: `FROM apps … FOR NO KEY UPDATE` < `INSERT INTO users` < `INSERT INTO role_assignments`.
- **Hash runs before the transaction.** `cli.py:345-352` hashes before the scope opens (line 361). `test_hash_before_session_scope` asserts `events == ["hash", "scope"]`.
- **getpass hardening.**
  - The `isatty` check comes before any prompt (`cli.py:334`).
  - `GetPassWarning` is turned into an error and mapped to `MSG_NO_TTY` (`cli.py:285-289`).
  - `EOFError` and `KeyboardInterrupt` in `getpass` map to `MSG_ABORTED` (290), and so does EOF on the email retype (279).
  - The parser has no `--password` option, and `allow_abbrev=False`.
- **Email retype.** It is normalized with the same function. An invalid or different value gives `MSG_EMAIL_MISMATCH`, and it is asked before the password prompts.
- **No `str(exc)` or DETAIL leaks.** All messages are fixed constants. The catch-all at `cli.py:390` prints `MSG_CREATE_UNEXPECTED`. All error mappings use `from None`. `main()` keeps `tracebacklimit=0` and silences the `sqlalchemy` and `asyncpg` loggers.
- **Output format (the planner's added `email: <masked>` line): accepted.** The ticket says "The email is masked, as in `grant-super-admin`", which only makes sense if an email line is printed. The line is masked (`mask_email`), so R11 holds. The runbook output block (`deploy.md`) matches the code, and the test asserts all 4 lines exactly.
- **Docs are consistent.** ADR-0009 names what it amends: D16, the Context of ADR-0007, and the "never marks an email verified" wording, now scoped to `grant-super-admin`. The `app/cli.py` docstring and `deploy.md` line 268 agree with it. Exit codes 0/1/64 match the code.

### Issues
None blocking.

### Rules R1–R11
No violations found.
- R1: the CLI does I/O only. The rules are in `auth/provisioning.py` (service layer) and the shared membership helpers. Repositories are unchanged.
- R2: no schema change.
- R3, R6, R9: no endpoint.
- R4: ADM-004 is still BACKLOG. An ids-only log line and an `# R4` marker stand in for it, and the follow-up is recorded.
- R5 and R11: the password is never in argv, env, output or logs. Argon2id comes from the shared hasher. The engine uses `hide_parameters=True`. The email is masked in output.
- R7: every changed file is in the plan's list. The ticket, backlog and consult files are workflow bookkeeping.
- R8: make check is green, and the new logic has unit and integration tests.
- R10: no hosted UI.

### Nits (do not block)
- **Ctrl-C at the email retype prompt is not caught.** `_SystemTty.read_line` (`cli.py:276-281`) catches only EOF, not `KeyboardInterrupt`. Ctrl-C there prints the bare `KeyboardInterrupt` (backstop: `tracebacklimit=0`) instead of `MSG_ABORTED`. Nothing has been read or written at that point, so nothing leaks and nothing is created, and the plan only asked for EOF on `read_line`. Wrapping `sys.stdin.readline()` in `except KeyboardInterrupt` would make the behaviour uniform.
- **One `deploy.md` line is not wrapped.** In step 1 of "Bootstrap the first super-admin", the line "then **log in once** … could have registered in your" is about 130 characters. The rest of the file wraps at about 100.
- **"Re-running it is safe" is now ambiguous** in `deploy.md` at "Bootstrap the first super-admin". After the reworded sentence, "it" could mean either command. It still means `grant-super-admin`. A re-run of `create-account` is also harmless (exit 1, nothing changed), but the sentence should say which command it means.
- **The ADR quotes the owner without diacritics.** ADR-0009 Context has "chon B"; the ticket has "chọn B".

### Follow-ups (out of scope)
- From plan.md:
  - ADM-004: add a system actor `system:cli` and record `user.create_verified` and `app.join` in the same transaction.
  - Deployment repo: add a note that `create-account` must never be an ops-workflow choice.
  - Optional: a `make create-account` target.
  - Optional: an early read-only check before the password prompts.
  - Optional: `RLIMIT_CORE=0`.
- Test infrastructure: two `pytest` runs against `org_admin_test` at the same time interfere, because the migration tests downgrade and upgrade the shared schema. Consider a per-run test database name, or a lock, so parallel sessions cannot produce false failures.

## Orchestrator note — 2026-11-15 (after merging `main`)
PR #29 conflicted with `main`, which gained ADM-002b and **ADM-004 (audit log)**. Merged `main` into the branch (commit 67f1121): textual conflicts in `docs/deploy.md` (both sections kept) and `work/backlog.md` resolved; the USR-09 ADR is renumbered **0009 → 0010** because ADM-004 took ADR-0009 (`docs/adr/0009-audit-log.md`); references updated. `make check` now fails: `mypy` — `app/modules/auth/provisioning.py:65-66` calls `_grant_locked` with the old signature (ADM-004 added an actor `AdminPrincipal | SystemActor` and changed the arity). Also, with ADM-004 landed, R4 now applies: `create-account` must write real audit entries in the same transaction (actor `SYSTEM_CLI`) instead of the log-line placeholder. → REWORK attempt 2.

## Attempt 2 — 2026-11-15
Verdict: PASS

Reviewed the rework commits `git diff 67f1121 HEAD` (dcd2644, af4e125) and, for the merge, `git diff 7d8f0ec HEAD -- app tests docs` plus `git diff b4c2504 HEAD` (main tip → HEAD), so the USR-09 delta on top of `main` is isolated from the ADM-002b/ADM-004 changes merged in.

### Checks (run by reviewer)
- make check (ruff, format, mypy, pytest): ruff "All checks passed!", format "418 files already formatted", mypy "Success: no issues found in 99 source files", pytest **"1445 passed, 1 warning in 96.25s"**, exit 0. Matches impl-log Attempt 2 (1445 passed; the warning is the stdlib forkpty DeprecationWarning). No other pytest process was running against `org_admin_test`.
- alembic check: not applicable. No model or migration change in the USR-09 delta. `audit_log.action` only has the `ck_audit_log_action_format` regex check (`^[a-z_]+(\.[a-z_]+)+$`, ≤ 64), and `user.created` matches it, so the new enum value needs no migration.

### Acceptance criteria
AC1–AC6 still hold as in Attempt 1. The full suite is green, and `tests/test_cli_create_account.py` and `tests/test_auth_join.py` both pass. The rework adds the following R4 evidence:
- [x] **`_grant_locked` gets the new actor:** `app/modules/auth/provisioning.py:79-81` calls `_grant_locked(app, role, user.id, None, None, actor=SYSTEM_CLI)`, matching the keyword-only signature at `app/modules/rbac/service.py:1080-1089`. `granted_by` stays NULL because `actor_user_id(SystemActor)` is None, which `test_create_with_join_then_login_and_token` asserts. `SYSTEM_CLI` is privileged, but the role reaching `_grant_locked` has already passed `join_default_role`, which refuses roles with admin permissions, so this widens nothing.
- [x] **`user.created` row:** written at `provisioning.py:70-77` in the same session and transaction, after the insert succeeds. The row has actor `SYSTEM_CLI`, `AuditContext.NONE`, target `(user, user.id)`, and changes `status` None→active and `verified` None→True. `test_create_verified_user_no_join` (l.170) asserts `actor_kind=system`, `actor_user_id=None`, `actor_label=system:cli`, the target, and the exact changes. It also asserts that the email, its local part, the password, the hash and `argon2` are absent from changes, ip, ua and request_id.
- [x] **`app.joined` row (with `--join`):** written at `provisioning.py:83-90` with target `(role_assignment, ra.id)`, `app_key`, and `role_key` None→key. This is the same shape JoinService writes (`membership/service.py`), except the actor is `system:cli` instead of `EndUser`. `test_create_with_join_then_login_and_token` (l.212) asserts it.
- [x] **Every refusal rolls back the audit rows:** `test_existing_email_exit_1_unchanged` (l.271, 4 cases), `test_join_refusals_create_nothing` (l.450, 7 cases) and `test_failure_after_insert_rolls_back_user` (l.479; `user.created` was flushed before the forced `_grant_locked` failure and is rolled back) each assert `_audit(...) == []`.
- [x] **ADM-004 conventions followed:** writes go only through `AuditLogger.record` (the single write path), in the caller's transaction, with no commit. The actor is a `SystemActor`, so the row gets `actor_kind=system` and `actor_label`, and `AuditContext.NONE` leaves ip, ua and request id empty, as for the other CLI commands (ADM-004 plan AC5). `changes` come from `diff.change`. The key names `status`, `verified` and `role_key` pass the `_FORBIDDEN_KEY` guard. Two rows for two distinct state changes is consistent with the catalogue. The old `# R4` placeholder comment is gone, and the ids-only log line is kept.

### Out-of-plan change: judged acceptable
- `app/modules/audit/actions.py`: adds `USER_CREATED = "user.created"`. It is needed because the catalogue had no create action for users, and the ADM-004 plan names `actions.py` as the single catalogue. It follows the past-tense `<target>.<verb>` convention. Because `GET /api/v1/audit-logs?action=` is typed `AuditAction`, the new value becomes filterable. That is additive and not a v1 break (R9). No migration is needed (see Checks).

### Merge resolution (67f1121)
- **JoinService still behaves as on `main`.** `git diff b4c2504 HEAD -- app/modules/membership/service.py` shows only the helper extraction, with the same checks, messages and order. The ADM-004 parts `_grant_locked(..., actor=SYSTEM_JOIN)` and `audit.record(APP_JOINED, actor=EndUser(user.id), ctx=ctx, ...)` are byte-identical to main. `tests/test_auth_join.py` has no diff against main and passes, including its `app.joined` audit assertion at l.79.
- **ADR numbering is consistent.** `docs/adr/` has `0009-audit-log.md` (ADM-004) and `0010-operator-vouched-verified-email.md`. Every live reference uses ADR-0010: `app/cli.py:5`, `app/modules/auth/provisioning.py:1,48`, `docs/deploy.md:272,597`, `docs/identity-architecture.md:259` (D16), `docs/adr/0007-…:7`. The ADR-0009 references in `deploy.md:641` and `identity-architecture.md:197` correctly mean the audit log. The only "ADR-0009" mentions of this ADR are in the historical ticket files (plan, consults, impl-log Attempt 1, security-review, review Attempt 1), which record what was true at the time, so they are fine.
- **Merged `docs/deploy.md` reads correctly.** Both sections are present in order: "Create an account from the CLI (bootstrap only)" (l.595) followed by main's "Audit log (ADM-004)". Setup step 6, the grant-super-admin wording and the `created_at`/`email_verified: yes` note are coherent. The post-run paragraph (l.629-632) now describes the audit rows and keeps the manual operator note, consistent with ADR-0010 risk b).

### Issues
None blocking.

### Rules R1–R11
No violations found.
- R1: the audit write is in the service (`provisioning.py`) through `AuditLogger`/repository.
- R2: no schema change.
- R4: now satisfied with real rows in the same transaction.
- R5/R11: no email, password or hash in audit rows (tested), and the forbidden-key guard applies.
- R7: one justified out-of-plan file.
- R8: green.

### Nits (do not block)
- `docs/identity-architecture.md:197-201` (S8) lists "every CLI write (`system:cli`: register-app, set-admin-origins, grant-super-admin, cleanup summary)" but does not mention `create-account` (`user.created`, `app.joined`). Add it to that list so the system overview matches the code, ADR-0010 and deploy.md.
- These carry over from Attempt 1 and are still open: Ctrl-C at the email retype, the one long line in deploy.md step 1, "Re-running it is safe" now being ambiguous between two commands, and "chon B" without diacritics in the ADR.

### Follow-ups (out of scope)
- From the impl-log: an operator identity on CLI audit rows (`--operator` or an env label).
- Security-review 🟡 (still open): a regression test that `app.modules.auth.provisioning` is imported only by `app/cli.py`.
- Carried over: a deployment-repo guard so `create-account` never becomes an ops-workflow command; a per-run test database to avoid parallel pytest interference.

### Security items (added by the orchestrator after appsec Attempt 2)
Appsec found no 🔴, so the PASS stands. Two cheap 🟡 security hardening items go into one final rework (attempt 3), because the CLI path has no Turnstile or rate limit: (1) grant with a non-privileged actor so `_guard_privileged` still protects the CLI path (audit actor stays `system:cli`), with the monkeypatch test; (2) a test that only `app/cli.py` imports `app.modules.auth.provisioning`. Plus the one-phrase S8 doc fix in `docs/identity-architecture.md`. Nothing else.

## Attempt 3 — 2026-11-15
Verdict: PASS

Scope: only the three security items listed under "Security items (added by the orchestrator after appsec Attempt 2)". Diff reviewed: `git diff af4e125 HEAD -- app tests docs` (commit a703242).

### Checks (run by reviewer)
- make check (ruff, format, mypy, pytest): ruff "All checks passed!", "418 files already formatted", mypy "Success: no issues found in 99 source files", pytest "1447 passed, 1 warning" (pty forkpty DeprecationWarning). Matches impl-log Attempt 3.
- alembic check: not applicable (no migrations changed).

### Acceptance criteria (security items)
- [x] (1) Grant with a non-privileged actor; audit actor stays `system:cli` — evidence: `app/modules/auth/provisioning.py:81` passes `actor=SYSTEM_JOIN` (`SystemActor("system:join", privileged=False)`, `app/modules/rbac/actor.py:22`) to `_grant_locked`. In `_grant_locked` (`app/modules/rbac/service.py:1080-1135`) the actor is used only by `_guard_privileged` (`require_super`, so admin-app roles and roles holding an `is_admin` permission are refused) and by `granted_by = actor_user_id(actor)`, which is `None` for every `SystemActor` — same value as before. `_grant_locked` never audits. Both audit rows (`user.created` at provisioning.py:68-74, `app.joined` at :84-91) and the log line (:93) still use `SYSTEM_CLI` / `system:cli`. Nothing records `system:join` for this path.
- [x] Monkeypatch test proves the guard — evidence: `tests/test_cli_create_account.py::test_cli_grant_still_refuses_admin_role` passes on HEAD. Mutation check by the reviewer: reverting only provisioning.py:81 to `actor=SYSTEM_CLI` makes it fail ("1 failed", the CLI printed "created account ... joined: stoktok"). Restored with `git checkout -- app/modules/auth/provisioning.py`; `git status` clean afterwards. The test also asserts exit 1, the generic message, no user, no new assignment, no audit row (whole transaction rolled back).
- [x] (2) Only `app/cli.py` imports `app.modules.auth.provisioning` — evidence: `test_only_cli_imports_provisioning` passes. Reviewer ran the same scanner logic on a synthetic tree: it flagged `import app.modules.auth.provisioning`, `import ... as p`, `from app.modules.auth import provisioning`, `from app.modules.auth.provisioning import X`, `from .provisioning import X`, `from . import provisioning`, `from ..auth import provisioning`, `from ..auth.provisioning import Y`, and it did not flag `from app.modules.auth.provisioning_other import Z`. Relative resolution is also correct for `__init__.py` (the `__init__` part is dropped by `[: -level]`).
- [x] S8 doc fix — evidence: `docs/identity-architecture.md:201` now lists "create-account: `user.created` + `app.joined`" under CLI writes (`system:cli`), which matches the two `audit.record(..., actor=SYSTEM_CLI)` calls in provisioning.py.

### Scope
Changed code/docs files: `app/modules/auth/provisioning.py`, `tests/test_cli_create_account.py`, `docs/identity-architecture.md` — exactly the three items. The other files in the commit are workflow files (`work/...`). No out-of-plan changes.

### Issues
None.

### Rules R1–R11
No violations found. R4: the audit rows are unchanged and still in the same transaction. R7: the diff stays within the three items. R8: green, and the new behaviour has a test that the mutation check shows really guards it.

### Nits (do not block)
- 💭 `docs/identity-architecture.md:201` is now longer than the ~100-column wrap of the paragraph around it; the line also reads "... `app.joined`) and `app.joined` (ADM-004)", which is accurate (the self-service join) but repeats itself a little.
- 💭 The import guard does not see dynamic imports (`importlib.import_module("app.modules.auth.provisioning")`). That is acceptable for a regression tripwire.

### Follow-ups (out of scope)
- Carried over from Attempt 2: a deployment-repo guard so `create-account` never becomes an ops-workflow command; a per-run test database to avoid parallel pytest interference.
