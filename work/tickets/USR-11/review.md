## Attempt 1 — 2026-11-16
Verdict: PASS

### Checks (run by reviewer)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: ruff "All checks passed!", ruff format "228 files already formatted", mypy "Success: no issues found in 104 source files", pytest "1757 passed, 1 warning in 107.92s" (the warning is the existing forkpty DeprecationWarning in `tests/test_cli_create_account.py`). This matches impl-log.md.
- alembic check: not applicable. The diff has no models or migrations.

### Acceptance criteria
- [x] `GET /api/v1/me/permissions` (outside `/auth/*`), Bearer `aud=admin` through `current_admin`, returns `{user_id, roles, permissions}` for the caller only. `permissions` is `sorted(principal.permissions)`, the same set `current_admin` loaded for enforcement (`app/modules/admin_auth/service.py` `self_view`). `admin:*` is not expanded. No PII. Evidence: `app/modules/admin_auth/me_router.py:12-26`; `MePermissions` has `extra="forbid"` (`app/modules/admin_auth/schemas.py`); tests `test_returns_own_data_only`, `test_query_user_id_ignored`, `test_exact_keys_no_pii`, `test_super_admin_wildcard_unexpanded`, `test_permissions_and_roles_sorted`, `test_org_scoped_admin_assignment_ignored` all pass.
- [x] `Cache-Control: no-store`; no token gives 401, another `aud` gives 401, both in the R6 envelope. Evidence: `test_no_token_401`, `test_other_audience_401`, `test_list_audience_401`, `test_cache_control_no_store_on_200`, plus the parametrized `test_every_bearer_route_401_without_token` / `_with_cookie_only` over `SELF_SCOPED`.
- [x] A grant or revoke shows on the next request, and a disabled caller is treated the same as on other admin endpoints. Evidence: `test_grant_visible_on_next_request`, `test_revoke_visible_on_next_request`, `test_expired_assignment_dropped`, `test_disabled_caller_401` and `test_unverified_caller_401` (each compares against `GET /api/v1/organizations` with the same token).
- [x] The route has an explicit allow-list entry, "Bearer, self-scoped, no permission". No placeholder permission was added, and ADR-0008 records the exception. Evidence: `SELF_SCOPED` and the `(True, [])` classification in `tests/test_route_auth_classes.py`; `test_self_scoped_routes_have_no_permission` and `test_self_scoped_allow_list_is_exact`; the ADR-0008 amendment "Self-scoped admin reads (USR-11, 2026-11-16)" plus the §6 pointer. No `admin:self`/`admin:me` permission exists in `app/` or `migrations/`.
- [x] The route is in OpenAPI (the 401 response references the ADM-013 envelope) and in `docs/consumers.md` under "Calling the admin API from the admin UI". Evidence: `tests/test_openapi_contract.py` `test_me_permissions_operation` and the `checked` assertion; `test_openapi_bearer_scheme` has a self-scoped branch; `docs/consumers.md` has the new bullet and the amended token bullet.
- [x] Tests exist for 401 (missing token, wrong `aud`), own data only, a role change on the next request, `admin:*` not expanded, and the allow-list entry. All are listed above and all pass.

### Issues
None blocking.

### Rules R1–R11
No violations found.
- R1: the router is HTTP only and calls `AdminAuthService.self_view`, which calls `AuthzService.effective_roles`. No ORM code is in the router.
- R3: the exception is recorded consistently in ADR-0008, spec §5.3, CLAUDE.md R3/R9 and D23. The ADR and D23 mark it "pending owner confirmation", as the ticket requires. The owner must confirm it at PR.
- R4: the endpoint is read-only, so it writes no audit row.
- R8: there are no 404, 422 or 403 cases, because the endpoint takes no path, query or body input and nothing can return 403. The plan justifies this, and `test_query_user_id_ignored` stands in for an input case. I accept that.
- R11: the endpoint logs nothing and returns no email or token.
- Scope: every changed code, test and doc file is in the plan. `work/backlog.md`, `work/tickets/USR-11/*` and `work/tickets/USR-12/ticket.md` are workflow commits from the orchestrator, not implementer changes.

### Nits (do not block)
- 🟡 (should-fix, carried as a follow-up, from security-review.md): the ADR-0008 qualification "no path, query or body input" is not enforced by a test. Add `assert not d.path_params and not d.query_params and not d.body_params` for each `SELF_SCOPED` dependant in `test_self_scoped_routes_have_no_permission`, so a future `?user_id=` parameter cannot silently turn the route into an IDOR. The current handler has no such input, and `test_query_user_id_ignored` covers today's behaviour.
- `docs/identity-architecture.md` §4: the new row sits in the `/api/v1/auth` table, between cookie routes, even though it is a Bearer route outside `/auth`. A separate note or row group would be clearer.
- `roles` and `permissions` are read in two separate queries, so one response can be briefly inconsistent. The ADR accepts this.

### Follow-ups (out of scope)
- Add the input-free assertion to the self-scoped sweep (see the first nit).
- A per-`sub` rate limit for Bearer admin routes.
- The PR description must ask the owner to confirm the spec §5.3 / R3 / R9 / D23 wording (the ADR amendment is "pending owner confirmation").
- admin-pan (PAN-011b) should switch its UI gating to this endpoint.
