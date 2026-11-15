## Attempt 1

# Security review — ADM-004b, attempt 1 (appsec-engineer)

Commit reviewed: `c16f677 ADM-004b: audit identity events and denied privileged attempts` (`git diff origin/main...HEAD`). I only read code. I ran no tests, as instructed.

## Findings

🟡 **Downgrading the migration makes denied attempts look like successes.** `migrations/versions/20261115_af3eb84d2d5e_add_audit_log_outcome.py:45-48` drops `outcome`. A denied row keeps the attempted action, so after a downgrade a refused `role_assignment.granted`, `user.disabled` or `permission.deleted` reads exactly like one that happened. The log is append-only, so this cannot be fixed afterwards. `docs/deploy.md:596` only warns against going past `c3f1a9d27b60`.
- **Fix:** add a guard at the start of `downgrade()`: `if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM audit_log WHERE outcome <> 'success')")): raise RuntimeError("audit_log has failure/denied rows; refusing to drop outcome")`.
- Also add `af3eb84d2d5e` to the "Never downgrade production past…" line in `docs/deploy.md`.

💭 **The X-Request-ID trust check does almost nothing in production.** See `app/core/request_id.py:32-35`.
- Behind cloudflared, every request comes from a peer in `TRUSTED_PROXY_CIDRS`. Cloudflare passes client headers through, so a client can still set any id that matches `[A-Za-z0-9._-]{1,64}`, including one that collides with another request's id, for example to mix its failed-login rows into a staff request's trail.
- The check only blocks callers outside the trusted CIDR, such as a direct LAN or Docker peer. Other mmlabs containers on the cloudflared network are trusted too.
- `docs/deploy.md:599-601` states this correctly ("correlation id, not evidence"), so it is not a vulnerability.
- Optional hardening: from a trusted peer, use the `CF-Ray` header (set by Cloudflare's edge) instead of the client's `X-Request-ID`, or always generate the id and keep the inbound value only in the echoed header.

💭 **The user-agent cleanup strips C0 control characters and DEL but not C1.** See `app/modules/audit/service.py:28`. Starlette decodes headers as latin-1, so `\x80-\x9f` (for example NEL `\x85`) is stored. It cannot make the insert fail, but it can mangle how a row is shown in the admin UI. Optional fix: `r"[\x00-\x1f\x7f-\x9f]"`.

💭 **A device-session reuse detected during login is written with plain `record`, so an audit error there would turn a valid login into a 500.** See `app/modules/sessions/service.py:120-123`. It is not exploitable: every row field is validated or comes from the server (IP checked, UA cleaned, request id checked against the same regex as the DB CHECK, app key from the DB). It would only matter if a new field were added later. The ADR-0009 amendment accepts this, so no action is needed.

💭 **The `audit_write_failed` log lines carry no cause.** See `app/modules/audit/service.py:180` and `:238`. This is right for R11, but operators cannot tell a CHECK violation from a lost connection. Optional: add `type(exc).__name__`, which is safe because it holds no values.

💭 **Migration locking.** `ADD COLUMN ... DEFAULT 'success' NOT NULL` is metadata-only on PG17, and the docstring is accurate about that. But `create_check_constraint` (line 31) scans the table under ACCESS EXCLUSIVE, and the non-concurrent `create_index` (line 36) blocks writes while it builds. That is fine at today's table size. Once the table is large, use `NOT VALID` + `VALIDATE CONSTRAINT` and `CREATE INDEX CONCURRENTLY`. The CHECK name (`op.f("ck_audit_log_outcome_valid")`) matches the model's `outcome_valid` under the naming convention. Existing rows pass the CHECK. DDL does not fire the append-only row trigger.

💭 **Rows no longer prove the action happened.** Readers of `GET /audit-logs` (the admin UI) must now check `outcome`. The field is additive and not breaking under R9, but the admin UI should show it before denied rows start appearing.

## Your seven questions

1. **Can an attacker suppress their own audit row? No path found.**
   - **Bad IP:** `_valid_ip` stores NULL for an invalid IP, a scoped `%zone` address, or an IPv4-mapped address (stored as plain IPv4).
   - **Oversized or odd UA:** control characters are stripped, the value is cut to 512, and an empty result becomes NULL. NUL is removed, so Postgres cannot reject the text.
   - **Request id:** only kept if it fully matches the same regex as the DB CHECK, otherwise a UUID4 is used.
   - **`app_key`:** always comes from a DB row (`_app.key`, or the path key after `_locked_app` resolved it).
   - **`changes`:** server-built scalars or UUIDs only.
   - **Concurrency:**
     - Concurrent reuse: only the request whose UPDATE wins sees `revoke_device` return a count. Later ones return `None` and write no row but still get a 401, so each revocation gets exactly one row.
     - Concurrent login failures: each request runs its own reserve-and-commit.
   - **No per-IP or per-actor cap on audit writes**, so there is no "spam until it trips" trick (matches the security consult).
   - **Client disconnect during Argon2:** this can cancel only the row. The reservation is already committed, and `CancelledError` propagating is the documented design.

2. **Can a failure path commit a partial business write or undo a security action? No.** I traced every call site:
   - **Login failure** (`auth/service.py:177-195`): the reservation and block are committed at line 163. After that, only `get_by_email_for_share` (a FOR SHARE read) and Argon2 run, so the rollback in `record_failure` throws away nothing and keeps the counter.
   - **Denied paths:**
     - Sites: `rbac/service.py:343, 678, 738, 789, 850, 884, 919, 989, 1110, 1262` and `users/service.py:70`.
     - Each guard runs before any flush or mutation. At most, ORM attributes have been changed in memory or locks taken (`_locked_app`, FOR SHARE).
     - The rollback releases those locks, then the error is re-raised. `get_session` would have rolled back anyway, so the final state is the same as before this ticket.
     - Every target, `org_id` and `role_key` is copied into a local variable before the rollback.
     - `admin_auth` performs no flush or commit, so no dependency write is lost.
   - **CLI callers that catch `AppError`** (`cli.py:350, 450`) use `SystemActor`. `record_denied` returns early for them, so it never rolls back in a context where the error is caught.
   - **Reuse revocation** (`commit_on_reuse=True`): the row is written in a savepoint, and the outer commit still runs if it fails. Inside login, the revocation and the row are in the login transaction, as before.
   - **Last-super guard:** the `LastSuperAdminError` 409 is raised before any write. A denial changes nothing.

3. **Can a 401/403/409 become a 500 or leak account existence? No.**
   - The original exception object is re-raised, so status, body and headers are unchanged.
   - `record_failure` swallows every `Exception`, including a failed rollback.
   - **Unknown vs. known email at login:**
     - Both do Argon2 (dummy hash for the unknown email).
     - Both make one reservation commit plus one `record_failure` (rollback, insert, commit).
     - Both return the same 401 body.
     - They differ only in the row's `changes` (`unknown_account` vs. `reason`+`failures`), which only admins can read.
     - A disabled user with the right password gets the same 401.
   - 429 and Turnstile refusals write nothing (per scope).

4. **R11 (no secrets in rows): clean.**
   - No email, HMAC, password, token, cookie or hash in any row.
   - Login success stores `account_session_id` and `device_session_id`. These are database ids, not credentials: the credential is the cookie, which is stored only as a hash.
   - Password reset stores counts and booleans.
   - Denied rows store `reason` = the error code plus catalog keys.
   - `_check_keys` still rejects secret-looking keys, and the `email_send_log`/`email_tokens` exception now applies only to `email_retention.purged` with integer counts.

5. **Client-controlled data in denied rows: none that isn't checked by the server.**
   - Path ids (user, permission, role, assignment) are recorded only after they resolve to existing rows.
   - `org_id` is checked (`org_exists_key_share`) before `_guard_privileged`.
   - Permission keys in `replace_permissions` come from DB rows (`permissions_by_keys`).
   - No request body, error message or free-text field is stored.

6. **X-Request-ID behind cloudflared:** see the 💭 above. In practice it is still client-controlled, and the docs say so.

7. **Migration:** the column add is metadata-only, existing rows pass the CHECK, and the partial index predicate matches the model. The only gap is that the downgrade erases what "denied" meant (🟡 above).

## Follow-ups (out of scope)
- Run the API as a DB role with only INSERT and SELECT on `audit_log` (already tracked).
- Add an alert on `auth.login_failed` and `outcome=denied` per hour.
- Add read-time masking of IP and user agent before `admin:audit:read` is given to any non-super role (spec Q6, owner).

**Verdict: PASS with one should-fix.** No blockers: none of the failure paths lets an attacker suppress their own audit row, undo a security action, turn the intended error into a 500, or learn whether an account exists. Before merge, the migration's `downgrade()` should refuse to run while failure or denied rows exist.
