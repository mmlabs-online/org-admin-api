# Review — ADM-018

## Attempt 1
Verdict: PASS

Date: 2026-11-18. Diff: `git diff origin/main...HEAD` (commits 59784d5, f7adce4, d57db40). Working tree clean.

### Checks (run by reviewer)
- `service postgresql start`; `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: exit 0.
  - `ruff check .`: All checks passed!
  - `ruff format --check .`: 321 files already formatted
  - `mypy app` (strict, per Makefile line 48): `Success: no issues found in 147 source files`
  - pytest: `2410 passed, 1 warning in 243.94s` (warning is the pre-existing forkpty DeprecationWarning in `test_cli_create_account.py`). No BUG-001 flake; no rerun needed.
  - Matches impl-log (2410 passed).
- `alembic upgrade head` then `alembic check`: `No new upgrade operations detected.` (no migration in this diff, R2 not triggered).
- Targeted rerun of the ADM-018 related tests (`tests/test_tokens_service.py`, `tests/test_entitlements_repository.py`, `tests/test_auth_token.py`, `tests/test_route_auth_classes.py`, `-k "ent or allow_list or body_validation or happy_path or admin_audience or without_org"`): 95 passed.

### Acceptance criteria
- [x] AC1 Q4 decision in new ADR — evidence: `docs/adr/0017-entitlement-consumption.md` (Accepted, owner 2026-11-17/18, decisions 1–10 match plan Approach 5); ADR-0005 amendment appended; spec §8 Q4 ticked with both owner answers; spec §5.6/§5.8 and identity-architecture (row 171, claims para, D7, new D27) updated. `git diff --stat origin/main...HEAD -- CLAUDE.md docs/adr/0016* tests/test_route_auth_classes.py migrations` is empty, so R3/ADR-0016 are unchanged as required (class 4 not widened).
- [x] AC2 pull endpoint — N/A by owner decision (2026-11-17). No route added; `tests/test_route_auth_classes.py` unchanged and green.
- [x] AC3 `mmak_` scope — answered "no" in ADR-0017 decision 2; no code change.
- [x] AC4a `ent` only with `org` — `test_token_without_org_has_no_ent`, extended `test_token_without_org_app_scope_only` (`"ent" not in claims`); `test_token_happy_path` unchanged and green.
- [x] AC4b object with exact DB values; `{}` limits — `test_token_org_entitled_has_ent` (verified with `verify_reference`, exact claim set), `test_token_org_entitled_empty_limits`.
- [x] AC4c `null` for every not-active cause — `test_token_org_not_entitled_ent_null[none|disabled|other_app_only]` (status 200, key present, `None`, no cross-app `secret` leak); app-disabled and org-suspended legs in `tests/test_entitlements_repository.py::test_get_active_for_inactive_legs_return_none`.
- [x] AC4d issuance never gated — 4c tests assert 200; no new error branch in `app/modules/tokens/service.py:117-119`; `test_token_suspended_org_403`, `test_token_app_member_without_org_role_403` unchanged and green.
- [x] AC4e never on `aud=admin` — guard at `app/modules/tokens/service.py:117`; `tests/test_tokens_service.py::test_issue_admin_never_has_ent` (admin org path built with an entitlement row; asserts `sid` present, `ent` absent); `test_token_admin_audience_issued` unchanged.
- [x] AC4f fresh mint reflects changes — `test_token_ent_reflects_update_on_next_mint` (pro→basic→null).
- [x] AC4g single ACTIVE definition — `app/modules/entitlements/repository.py:93-100` filters with the existing `ACTIVE`; no second predicate. `test_get_active_for_each_active_leg`, `test_get_active_for_inactive_legs_return_none`, `test_get_active_for_scoped_to_org_and_app`.
- [x] AC5 allow-list / audit / rate limit / consumers.md — `test_app_credential_allow_list_is_exact` unchanged and green; audit and rate limit N/A (no admin mutation; `/token` limit unchanged, `test_token_rate_limited_429` green); `docs/consumers.md` "Entitlements (`ent`)" section covers every rule of plan Approach 6, including the owner's missing-key = app default (deny) rule; item 11 extended.
- [x] AC6 contract unchanged — `test_token_org_body_unchanged`; new 422 case for body `ent` in `test_token_body_validation_422`.
- [x] AC7 size bounded — `test_token_worst_case_ent_size` (32×64-char keys, 2^53-1 values, 63-char plan; `len(token) < 8192`).
- [x] AC8 no `ent` values in logs — `test_token_logs_no_entitlement_values`; `ent_out_of_contract` warning logs only entitlement id and aud (`service.py:45`), checked by `test_ent_out_of_contract_falls_back_to_null` (`"zzkey" not in caplog.text`).
- [x] AC9 `make check` green — above.

### Issues
None.

### Rules R1–R11
No violations found.
- R1: ORM stays in `EntitlementRepository.get_active_for`; `TokenService` composes the claim (same pattern as its existing `SessionRepository`/`UserRepository` use).
- R2: no schema change; `alembic check` clean.
- R3: no new endpoint; class 4 untouched.
- R5/R11: no secrets; plan/limits never logged; `token_issued` line unchanged.
- R6: no new error codes. R7: every changed file is in the plan's file list (plus workflow files `work/backlog.md`, ticket, consult notes, plan, impl-log). R8: tests present for every new behaviour. R9: additive claim only (ADR-0005 §5). R10: `plan` is a slug, no billing.
- Code check: `_ent_claim` uses `re.fullmatch(PLAN_PATTERN, …)` (anchored, rejects trailing newline) and `TypeAdapter(Limits).validate_python(..., strict=True)` (strict rejects bools/floats, enforces max 32 keys and 0..2^53-1), failing closed to `None`.

### Nits (do not block)
- `test_token_org_body_unchanged` (`tests/test_auth_token.py`) does not assert `resp.status_code == 200` before checking body keys; a non-200 body would fail anyway, but the assertion message would be less clear.
- `test_token_org_not_entitled_ent_null`: the `none` and `other_app_only` branches duplicate the same delete statement; could be one `if case != "disabled"` block.
- `docs/consumers.md` item 11 and the claims paragraph now have lines past the file's usual wrap width.
- Plan named the inactive-leg test `test_get_active_for_each_active_leg`; implementation split it into `..._each_active_leg` (active case) and `..._inactive_legs_return_none` (flips). Coverage is equivalent.
- Makefile runs `mypy app` only, so new test files are not type-checked (pre-existing, not introduced here).

### Follow-ups (out of scope)
- As in plan: `mmlabs-auth` `ent` accessor and server-side limit helper; DB CHECK on `limits` key count/size in `org_entitlements`; shorter TTL option for `org_mode=required` audiences; pull or push if 12 h staleness is unacceptable.
- Owner non-blocking defaults from plan Assumptions (no `ent` field in `token_issued`; fail-closed on out-of-contract rows; pre-ADM-018 org tokens treated as not entitled) were applied; owner may revisit.
