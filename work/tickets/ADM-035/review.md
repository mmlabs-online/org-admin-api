# Review — ADM-035

## Attempt 1 — 2026-11-20
Verdict: REWORK

Reviewed product commit `4beedec` (diff `origin/main...HEAD`; `68affcd` / `70efdd8` are orchestrator board commits).

### Checks (run by reviewer)
- `make typecheck` — Success: no issues found in 181 source files.
- `make test` (full) — 3185 passed, 1 warning in 440.00s, exit 0. **But** see Issue 1: one new test fails about 1 run in 16
  (reproduced: 3/40 isolated runs failed, `Failed: DID NOT RAISE Exception` at `tests/test_deployer_credentials_api.py:199`).
- `uv run ruff check . --exclude .github/scripts` — All checks passed.
- `uv run ruff format --check app tests migrations` — 350 files already formatted.
- `uv run alembic check` — No new upgrade operations detected.
- Migration round-trip (dev DB `org_admin`): `alembic downgrade -1` → current `89fd8d667a25`,
  `to_regclass('deployer_credentials')` NULL; `alembic upgrade head` → `5b7d1e9c3a42 (head)`, table back with the planned
  columns; single head; `alembic check` clean afterwards.
- impl-log claims match my run (apart from the intermittent failure, which a single run need not hit).

### Acceptance criteria
- [x] Issue/list/revoke work and need `admin:*` — `test_issue_list_revoke_happy_path`, `test_routes_require_admin_wildcard`
  (3 routes, 403 with `details == {"required": "admin:*"}`), `test_routes_401_without_token`, route-class registry
  (`tests/test_route_auth_classes.py:91-93`, `test_bearer_route_permission_map`).
- [x] Audited (one row on success, none on failure) — `test_audit_coverage.py` AUDITED entries for POST (409 failure) and
  DELETE (404 failure); `test_audit_rows_hold_key_id_only`; `test_revoke_twice_is_noop`.
- [x] 3rd active key → 409 `credential_limit`; revoke-then-issue — `test_third_active_key_409`, `test_revoked_keys_do_not_count`,
  `test_revoke_then_issue`, `test_parallel_issue_at_limit_one_wins` (barrier would let both pass without the advisory lock).
- [ ] Only the hash stored; value once, `no-store`, never in logs/audit — hash/no-store/list evidence is good
  (`test_issue_returns_value_once_no_store`, `test_rejection_logs_reason_and_key_id_only`), but
  `test_value_never_logged_or_audited` is flaky and its caplog half is vacuous — see Issues 1 and 2.
- [x] Revoked key refused at once; `last_used_at` updates — `test_probe_rejections_identical[revoked]`,
  `test_revoked_between_lookup_and_touch_401`, `test_valid_key_touches_last_used`, `test_list_shows_last_used_at`.
- [x] `current_deployer` refuses `mmak_` keys and admin JWTs (single 401, `realm="deployer"`) —
  `test_probe_rejections_identical[app_key|admin_jwt|…]`, `test_wrong_prefix_no_db_read`.
- [x] `mmdk_` refused by every other class — `test_deployer_key_on_bearer_routes_401`, `test_deployer_key_on_manifest_401`,
  `test_deployer_key_on_cookie_routes_unauthenticated`, `test_app_codec_rejects_deployer_key`.
- [x] No production route uses `current_deployer` — `test_deployer_credential_allow_list_is_exact`.
- [x] Migration reversible, `alembic check` clean — `test_downgrade_drops_table` + manual round-trip above.
- [ ] `make check` green — green in my run, but not reliably (Issue 1).

Ming verification after merge (carry to the PR):
- After deploy, the migration creates `deployer_credentials`; `GET https://api.mmlabs.online/api/v1/deployer-credentials`
  with an `admin:*` token returns `{"items": [], "total": 0, …}`.
- Issue the first production key only after ADM-036 ships, store it as `MMLABS_DEPLOYER_CREDENTIAL` in
  `mmlabs-online/deployment`, following the new `docs/deploy.md` section.

### Plan deviations recorded in impl-log — accepted
- 403 code `admin_wildcard_required` (not `permission_denied`): this is what `require_permission("admin:*")` already returns
  for every plain `admin:*` route (`app/core/errors.py:69`; `tests/test_admin_guards.py:76`,
  `tests/test_app_credentials_api.py:80`). The plan was wrong, the code is consistent with the codebase.
- Raw value length 65: `5 + 16 + 1 + 43 = 65`; the plan's 64 was an arithmetic slip. Test pins 65 correctly.
- `len(AUDITED) == 30`: main was at 28 after ADM-033 merged (`git show origin/main:tests/test_audit_coverage.py:524`), +2 here.
- `SystemActor.name`, own log handler, real `admin_user` for the FK: test-mechanics only, fine.

### Issues
#### Issue 1 — 🔴 blocker — flaky test (breaks `make check` ~6% of runs)
- **Where**: `tests/test_deployer_credentials_api.py:198-199`
- **Expected**: the "wrong secret" value always differs from the real one, so `authenticate` always raises.
- **Actual**: `raw[:-1] + "A"`. The last char of `secrets.token_urlsafe(32)` (43 chars) is one of only 16 values
  (`A E I M Q U Y c g k o s w 0 4 8`, low two bits are padding), so it is already `A` with probability 1/16. Then the
  value is the valid key, `authenticate` succeeds and the test fails with `DID NOT RAISE`. Reproduced: 3 failures in 40
  isolated runs.
- **Fix instruction**: build a value that is guaranteed wrong, e.g.
  `wrong = f"mmdk_{key_id}_{'A' * 43}"` using the issued `key_id` (as `test_rejection_logs_reason_and_key_id_only` does),
  or flip the last char to one that differs (`raw[:-1] + ("B" if raw[-1] != "B" else "C")`). While there, narrow
  `pytest.raises(Exception)` to `pytest.raises(UnauthorizedError)` and drop the `# noqa: B017`.

#### Issue 2 — 🟡 should-fix — log half of `test_value_never_logged_or_audited` asserts nothing
- **Where**: `tests/test_deployer_credentials_api.py:191-203`
- **Expected**: the caplog assertion proves the raw value/secret is not logged during issue and a failed authenticate.
- **Actual**: per the impl-log (and `tests/test_deployer_credentials_auth.py:144-146`), Alembic's `fileConfig` leaves
  `app.modules.deployer_credentials.service` disabled in the test session, so `caplog` captures none of its records; the
  `raw not in caplog.text` check passes vacuously.
- **Fix instruction**: in this test, re-enable the service logger the same way `test_rejection_logs_reason_and_key_id_only`
  does (set `logger.disabled = False`, restore in `finally`), and add a positive assertion that the
  `deployer_credential_denied reason=secret` line was captured, so the negative assertion is meaningful.

### Rules (CLAUDE.md)
No violations found. R1: router HTTP-only, service raises `AppError` subclasses, repository SQLAlchemy-only. R2: migration
hand-edited, CHECK texts identical in model and migration, `downgrade()` works, model registered in `app/db/models.py`.
R3: three class-1 routes with `admin:*`; class 4 (b) dependency unused and pinned by an exact-set test. R4: audit in same
transaction, no row on failure/no-op. R5/R11: hash only, `hmac.compare_digest`, rejection logs reason + key_id only,
`no-store`. R6/R9: envelope codes 201/204/401/403/404/409/422, `Page[T]`, UUID, timestamptz. R7: every changed file is in
the plan's list.

### Blockers from review agents
None — appsec-engineer attempt 1: PASS, no 🔴 (one 🟡: `IssuedDeployerCredential.credential` should use `field(repr=False)`; follow-up unless fixed alongside).

### Nits (do not block)
- `app/modules/deployer_credentials/codec.py:15` comment says "the real value is 64 chars"; it is 65 (same stale comment
  exists in `app/modules/app_credentials/codec.py:11`, out of scope here).

### Follow-ups (out of scope)
- Fix the same "64 chars" comment in `app/modules/app_credentials/codec.py:11`.

## Attempt 2 — 2026-11-20
Verdict: PASS

Reviewed rework commit `2a28eb3` (test-only plus a comment fix in `codec.py`; no out-of-plan files).

### Checks (run by reviewer)
- `make typecheck` — Success: no issues found in 181 source files.
- `make test` (full) — 3185 passed, 1 warning in 457.38s, exit 0.
- `uv run ruff check . --exclude .github/scripts` — All checks passed (pre-existing risk_tier.py lint caveat unchanged).
- `uv run ruff format --check app tests migrations` — 350 files already formatted.
- `uv run alembic check` — No new upgrade operations detected.
- Flake check: `test_value_never_logged_or_audited` run 40 times in isolation, 0 failures (was 3/40).

### Attempt-1 issues
- Issue 1 (flaky wrong secret): fixed. Wrong value is `mmdk_<key_id>_` + 43 x `A`, cannot realistically equal the real secret;
  `pytest.raises(UnauthorizedError)`, `noqa: B017` removed.
- Issue 2 (vacuous log assertion): fixed. Service logger re-enabled and restored in `finally`; the test asserts the captured
  records are exactly `deployer_credential_denied reason=secret key_id=<id>`, then that raw/secret are absent. Meaningful now.
- Nit (comment "64 chars" -> "65") done.

All acceptance criteria stay met (evidence in attempt 1); `make check` is now reliably green.

Ming verification after merge (carry to the PR): same as attempt 1 (migration creates `deployer_credentials`; list endpoint
returns empty page with `admin:*`; first production key only after ADM-036, stored as `MMLABS_DEPLOYER_CREDENTIAL`).

### Nits / Follow-ups
- "64 chars" comment remains in `app/modules/app_credentials/codec.py:11`.
- appsec: `IssuedDeployerCredential.credential` could use `field(repr=False)`.
