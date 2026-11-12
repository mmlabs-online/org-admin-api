# Security review — USR-02f

## Attempt 1

**Verdict: PASS. No blockers, no should-fix items.** `make check` is green: ruff, format, mypy (53 files) and pytest (531 passed).

**Scope reviewed:** `git diff origin/main...HEAD` covering `app/cli.py`, `app/core/text.py`, `app/modules/rbac/service.py`, `app/modules/users/repository.py`, `Makefile`, `docs/deploy.md` and the tests.

### Threat model checks (all pass)
| Check | Evidence | Result |
|---|---|---|
| Not reachable over HTTP | Nothing imports `app.cli` or calls `bootstrap_super_admin` except the CLI and its tests; there is an AST guard in `tests/test_cli_grant_super_admin.py` (`test_cli_not_imported_by_app`) | OK |
| No user creation, no password, no "mark verified" (D16) | `app/modules/rbac/service.py` `bootstrap_super_admin` only reads the user and calls `_grant_locked` | OK |
| Email not echoed on usage errors | `app/cli.py:50` `_Parser.error` drops argparse's message. Subparsers inherit `_Parser`, so their errors are covered too. `-h` exits 0 with static help only | OK |
| Generic failure lines, no `str(exc)` anywhere | `app/cli.py:104` (a settings error can carry the DSN), `app/cli.py:138` (catch-all) | OK |
| DB parameters and echo | `app/cli.py:78` uses its own engine with `hide_parameters=True, echo=False, NullPool`. `DB_ECHO` is refused. I confirmed that importing `app.modules.rbac.service` does not load `app.db.session` (the API engine with `echo=DB_ECHO`) | OK |
| Logging | `app/cli.py:146` gives the `sqlalchemy` and `asyncpg` loggers a NullHandler with no propagation, so Python's last-resort stderr handler is not used for them | OK (see N1) |
| Commit before output | The success lines print only after `async with scope()` exits, i.e. after the commit. A failing commit prints `MSG_UNEXPECTED`, never "granted" | OK |
| Email normalization matches sign-up | `app/cli.py:66` vs `app/modules/auth/service.py:31` (`strip().lower()` after `EmailStr`); the lookup is CITEXT | OK |
| Lock order and races | `lock_app("admin")` (`FOR NO KEY UPDATE`), then the user row `FOR SHARE` (blocks a concurrent disable or delete), then `_grant_locked`. A `ConflictError` from a concurrent change ends in a generic exit 1. A test checks the lock order | OK |
| Disabled account | `status != active` gives exit 3 and nothing is written. `UserStatus` only has active and disabled, so no third state slips through | OK |
| Super-admin cannot expire | `granted_by` and `expires_at` are NULL. An expired row is replaced in place through `_grant_locked`, which keeps its guard on super-admin expiry | OK |
| Pre-registration hijack | Runbook in `docs/deploy.md:115-146`: sign up yourself, check `created_at`, revoke with `psql` if it does not match, reset the password after USR-04. D16 (a verified email is needed for any token) means an attacker who pre-registers the owner's email cannot use the grant without the inbox | Adequate |
| No-leak tests | Every exit path asserts there is no full email (case-insensitive), no `Traceback` and no `postgresql`. A subprocess test with an unreachable DSN checks that the password and host are absent | OK |

### Findings
- 🟢 **N1: `app/cli.py:146`.** Only the `sqlalchemy` and `asyncpg` loggers are silenced. Other WARNING+ records still reach Python's last-resort stderr handler, and that handler prints `exc_info` tracebacks. The ones that matter are the `asyncio` logger ("Task exception was never retrieved" or "Future exception…") and the `py.warnings` path. In theory an orphaned asyncpg task's error, such as a constraint DETAIL that contains the email, could print there. I could not trigger it: `NullPool`, `engine.dispose()` and the unreachable-DB subprocess test all stay clean. Cheap hardening: add `"asyncio"` to the tuple, or put `logging.getLogger().addHandler(logging.NullHandler())` on the root logger in `main()`.
- 🟢 **N2: `app/cli.py:145`.** `sys.tracebacklimit = 0` hides frames but still prints `ExcType: message` for a `BaseException` that escapes. A `CancelledError` or `KeyboardInterrupt` has no message, so nothing leaks today. I note it only so nobody treats it as a full backstop.
- 🟢 **N3: `Makefile:23`.** `--email '$(email)'` breaks on a single quote, and `EmailStr` accepts addresses like `o'brien@x.com`. This target is for local development only and the operator runs it in their own shell, so it is not a privilege boundary. It fails as a usage error or a shell error, and neither leaks anything. Optional: document it or use `--email "$$EMAIL"` via an env var.
- 🟢 **N4: `docs/deploy.md:115`.** "Log in once (after USR-03)": USR-03 is still BACKLOG, so for now the `created_at` check plus D16 is the only proof of ownership. That is acceptable because the grant has no effect until ADM-002, which depends on USR-03, and needs a verified email (USR-04). No change needed.

### Follow-ups (out of scope)
- **FU-A:** When ADM-004 lands, the audit entry for the CLI grant (actor `system:cli`) at the `# R4` marker in `bootstrap_super_admin`. This is already plan FU2.
- **FU-B:** Reading the email from stdin (`--email -`) to keep it out of argv, `ps`, `docker inspect` and shell history. This is already plan FU4 and pairs with N3.
- **FU-C:** A matching operator CLI `revoke-super-admin`, so the runbook's revoke step does not need a hand-written `psql DELETE` against production. The current wording is correct but easy to get wrong.
- **FU-D:** Any future disable or delete path must take `lock_app("admin")` before the user row (already plan FU5).
