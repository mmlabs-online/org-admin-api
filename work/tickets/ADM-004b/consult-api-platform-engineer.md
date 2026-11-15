# consult-api-platform-engineer — ADM-004b

**Findings**
- `AuditLogRead` (app/modules/audit/schemas.py) has no `outcome`. The filters in router.py are typed enums (`ActorKind`, `AuditAction`, `AuditTarget`), so a new enum member automatically becomes a valid query value and appears in the OpenAPI enum. That is additive.
- `ActorKind.ANONYMOUS` already exists and the CHECK allows it. `actor_consistent` lets anonymous rows have no user id and no label, so no constraint change is needed for unknown-email failures.
- `test_audit_coverage.py` covers only `BEARER` mutating routes, and pins `len(AUDITED) == 16`. `test_denied_guard_writes_no_row` currently asserts **0 rows** for `admin_wildcard_required` and `last_super_admin`. ADM-004b must invert this test, not just add to the suite.
- `device_session.reuse_revoked` is not tied to a route. It fires from `DeviceSessionService` (`commit_on_reuse`) on any cookie route, and on login via `commit_on_reuse=False`. A route map cannot cover it.
- The CLI already has `cleanup` (EmailRetentionService, `--dry-run`, writes the `email_retention.purged` summary). The ops workflow `ops-org-admin-api` exposes it behind the `org-admin-api-ops` approval. The audit trigger blocks DELETE and TRUNCATE for every role (ADR-0009). deploy.md says "a purge comes with ADM-004b".

**Recommendations**
1. **Contract (R9, AC2).** Add `outcome: AuditOutcome` (StrEnum `success|failure|denied`) to `AuditLogRead`, always present and never null (server default `success`). Add the filter `outcome: Annotated[AuditOutcome | None, Query()]` and pass it through `AuditLogFilter`. Both changes are additive. Existing clients ignore the new field, and the default makes old rows read `success`. Don't make the response field nullable. Index: the planner and DB consult decide; the low-cardinality filter can ride on `ix_audit_log_action`, so don't add a standalone index by default.
2. **Action naming.** Keep the catalogue convention (past tense, `<domain>.<verb>`, matches `action_format`): `auth.login_succeeded`, `auth.login_failed`, `auth.logout`, `auth.logout_all`, `device_session.reuse_revoked`, `auth.password_reset_completed`, `auth.email_verified`.
   - `logout` and `logout_all` are not past tense. Prefer `auth.logged_out` and `auth.logged_out_all`, or document the exception. Decide before shipping, because these names become frozen filter values.
   - Denied attempts reuse the **attempted** action plus `outcome=denied` (for example `user.enabled`/`user.disabled`, `role_assignment.granted`/`revoked`, `app.updated`), not new `*.denied` actions. Auditors can then filter on one axis. Put the error code in `changes` as `{"reason": {"from": null, "to": "admin_wildcard_required"}}`, or add a small allowlisted `reason` key in diff.py. Either way, never put request bodies there.
   - Add `AuditTarget.DEVICE_SESSION` for the reuse and logout rows (target = device session id, never the cookie).
3. **Coverage test (R8, AC1).** Add an `IDENTITY_AUDITED` map over `PUBLIC | COOKIE`, plus an explicit `IDENTITY_NOT_AUDITED` set. Assert that their union equals all non-GET public/cookie routes, so a new identity route fails until it is classified.
   - **Audit:** login (success, and credential-checked failure), logout, verify-email, password/reset.
   - **Do not audit:** signup, verify-email/resend, password/forgot (unauthenticated write amplification, and they carry or reveal email existence); token (high volume, and the JWT `jti` is enough); `GET accounts`.
   - `apps/{key}/join` is already audited (`app.joined`). Move it into the map.
   - Test 429, the Turnstile-required refusal and the Turnstile-failed refusal: each writes 0 rows.
   - Test `reuse_revoked` separately, through one cookie route and through login.
4. **Denied rows in the API.** They appear in the normal list:
   - actor is the staff user, `outcome=denied`, target = the attempted target, `changes` = reason only;
   - the HTTP response is unchanged (same status, code and envelope);
   - test it with `make_strict_client`.
   - Failed logins appear as `actor_kind=anonymous` (unknown email) or `actor_kind=user` (known account, wrong password), with `outcome=failure` and no `changes`.
5. **Retention purge (R3, R5).** Make it a separate CLI command, `audit-purge --before-days N` or `--older-than` defaulting to a settings value, with `--dry-run`. Add it to the ops workflow allowlist. Don't extend `cleanup`:
   - its trust level is different: destroying audit evidence needs its own approval line in the workflow run log;
   - it needs its own failure messages (no DSN);
   - it writes its own `audit_log.purged` summary row, with a row count and cutoff date and no PII. Insert that row **after** the delete batches, in a committed transaction.
   - Never expose the purge over HTTP, and add no route (the classification test will catch one).
   - Prefer a trigger bypass via a transaction-local `SET LOCAL org_admin.audit_purge = 'on'`, checked by the trigger for DELETE only, with TRUNCATE still blocked. Monthly partitions are a bigger migration.
   - A retention period of 0 or a missing value must refuse to run.

**Risks**
- If the purge cutoff is computed from client-supplied input without a floor, one mistyped workflow input wipes the log. Enforce a minimum, for example 90 days.
- A rollback inside `record_failure` on paths where a row is added before an existing commit (the login reservation) could discard the failure-counter increment. Add the row before that commit; do not call `record_failure` there.
- Failed-login rows grow without bound until the owner answers spec Q6.
- Changing the `logout` names after release would break saved auditor filters.

**Open questions**
1. Spec Q6: what is the retention period, and should IPs be truncated? This blocks the purge default and the floor.
2. Naming: `auth.logout` or `auth.logged_out`?
3. Should denied rows carry the reason in `changes`, or in a dedicated `reason` column? A column would be another additive migration, and would need its own filter.
4. Should a known-account failed login record the `actor_user_id`? That reveals account existence to auditors only, which is acceptable, but it needs confirming.
