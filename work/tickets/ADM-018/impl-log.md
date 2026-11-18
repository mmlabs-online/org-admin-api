# Implementation log — ADM-018

## Attempt 1

### Files touched
| File | Required because |
|---|---|
| `app/modules/entitlements/repository.py` | `get_active_for` reuses the single `ACTIVE` predicate (AC 4g, R1) |
| `app/modules/tokens/service.py` | `ent` claim after `org_roles` (not for `aud=admin`); `_ent_claim` out-of-contract fail-closed check |
| `tests/test_auth_token.py` | New `ent` token tests, `ent` 422 case, `"ent" not in claims` in the app-scope-only test |
| `tests/test_entitlements_repository.py` (new) | `get_active_for` legs and scoping |
| `tests/test_tokens_service.py` (new) | `test_issue_admin_never_has_ent`, `test_ent_out_of_contract_falls_back_to_null` |
| `docs/adr/0017-entitlement-consumption.md` (new) | Q4 decision record |
| `docs/adr/0005-access-token-signing.md` | `ent` amendment |
| `docs/consumers.md` | claims paragraph, "Entitlements (`ent`)" section, item 11, follow-up line |
| `docs/spec-v1.md` | Q4 ticked; §5.6 and §5.8 updated |
| `docs/identity-architecture.md` | claims, token row, D7, new D27 |

### Tests added
All tests listed in the plan. The admin-org service test was buildable (admin app `org_mode=required` in the session, org-scoped super-admin assignment, entitlement row), so no fallback was needed. The app-disabled leg is in `test_get_active_for_inactive_legs_return_none` (parametrized: enabled, org suspended, app disabled) rather than a single `..._each_active_leg` test.

### Checks
`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` on the final tree: exit 0, `2410 passed, 1 warning in 249.88s` (ruff, format check, mypy --strict, pytest, all green). One earlier run failed ruff RUF059 (unused variable in a test), fixed, then the full check re-run. No flake occurred.

### Out-of-plan files
None.

### Follow-ups
As in plan: `mmlabs-auth` `ent` accessor, DB CHECK on `limits` size, shorter TTL option, pull/push if staleness is a problem.

### Scope self-check
No route added, `CLAUDE.md` R3, ADR-0016 and `tests/test_route_auth_classes.py` untouched, no migration, `token_issued` log line unchanged. Not committed (per instruction).
