# Security review — USR-29

## Attempt 1
Verdict: PASS

Security review of USR-29, attempt 1 (commit 9c4956b against origin/main). This was a static review using git diff, grep and reading the code. I did not run pytest or `make check`, so the green-check evidence comes only from impl-log.md ("3811 passed").

I found no blockers. Every focus area holds:

- **Auth order:** checks run in a fixed order: missing, malformed, unknown, wrong secret, revoked (all one 401 with `realm="app"`), then app disabled (403), then mismatch (403, denied audit row), then scope (403, denied audit row). This is in `app/modules/app_credentials/service.py:160-197`.
- **Wrong credential kinds:** an admin JWT or an `mmdk_` key gets 401 on the new route. The parametrized registry tests in `tests/test_route_auth_classes.py:512,596` cover it once the route is added to `APP_CREDENTIAL` (`tests/test_route_auth_classes.py:65-69`).
- **Issuance for `admin`:** the allow-list at `service.py:117` refuses `org-members:read` for the `admin` app. `tests/test_app_credentials_api.py::test_org_members_scope_refused_for_admin_app` tests it.
- **Runtime-key path:**
  - It never calls `lock_app`. `deps.py:30` raises ValueError if a runtime scope is built with `lock=True`.
  - The `last_used_at` touch is throttled to once per 60 s. The query (`repository.py:88-107`) only touches a row that is not revoked.
  - Revocation and app disable take effect immediately for new requests, because `get_with_app` re-reads the key and app on every request (`populate_existing`). A request already in flight during a revoke may complete, as ADR-0022 §3 accepts.
- **Rate limits:**
  - The per-IP limit is a route-level dependency, so it runs before auth.
  - The per-key limit runs after auth (`app_router.py:57`). Its bucket key is the authenticated key id, prefixed `k:` in its own store (`rate_limit.py:166-175`), so a client cannot spoof it.
  - Limits come from settings with `ge=1`, and the keys are listed in `.env.example` without values.
- **Uniform 404:**
  - An unknown, unrelated or suspended org all go through one EXISTS query (`rbac/repository.py:644-661`) and one denied audit write. So there is no existence or timing oracle between the three cases.
  - The `org_mode` 422 depends only on the caller's app, so it leaks nothing about the org.
  - The test compares the three response bodies byte for byte (`tests/test_app_org_members.py:191-209`).
- **"Related to {key}" filter:** both queries filter on `Role.app_id == caller.app_id` and `RoleAssignment.org_id == org_id`, and skip expired assignments. Other apps' roles, app-scope roles and other orgs cannot leak (`rbac/repository.py:676-686`).
- **Field minimisation:**
  - The page query selects explicit `User` columns, never the entity, so `password_hash` and secret columns cannot reach the result (`rbac/repository.py:702-712`).
  - Disabled users get `email` and `display_name` set to null (`app_service.py:79-90`).
- **Headers and validation errors:**
  - `Cache-Control: no-store` is set on 200 responses.
  - There is no CORS: the route is not a Bearer route for `BearerRouteMatcher` and not under `/auth/`, and the parametrized no-CORS test covers it.
  - The 422 handler strips `input` and `ctx` (`app/core/errors.py:215-217`), and `test_updated_since_requires_timezone_422_no_echo` checks that the submitted value is not echoed.
- **Audit content:** audit rows hold ids and counts only, plus `status_filter` and a boolean for `updated_since`. Log lines carry `key_id` only.
- **Migration:**
  - The CHECK text is identical in the model (`app/modules/app_credentials/models.py:38-40`) and the migration (`migrations/versions/20261121_a4c81e6d27f3_usr29_org_members_read_scope.py:31`).
  - `downgrade()` deletes the `org-members:read` keys before restoring the old CHECK (migration lines 36-39).
  - `op.f("ck_app_credentials_scope_valid")` gives the same final name that ADM-034 produced through the naming convention.
  - The round-trip test checks that only the new-scope keys are deleted.

Findings:

💭 `app/modules/organizations/app_service.py:43-53` (with `app/modules/audit/service.py:185`): on the 404 path, `record_failure` rolls back the request transaction before writing the denied row, so the throttled `last_used_at` touch is lost too. A key used only to probe org ids never shows a `last_used_at`. The denied audit rows still record the probing, so this only affects the "last used" view staff see when deciding whether to revoke. Fix (optional, or as a follow-up): touch `last_used_at` again inside the failure path, or note in ADR-0025 that `last_used_at` reflects successful reads only.

💭 `app/modules/app_credentials/repository.py:91-92` and `service.py:193-196`: the docstring says "the caller re-reads if needed", but `authenticate` ignores the return value. It does this because False cannot distinguish "revoked" from "touched recently". The behaviour matches ADR-0022 §3, but the docstring is misleading. Fix: change it to "Return value is informational; runtime callers ignore it (in-flight revoke accepted, ADR-0022 §3)".

💭 `tests/test_app_org_members.py:373-405` (`test_no_email_in_logs`): the test is not vacuous. A root handler at DEBUG with loggers re-enabled captures every propagated record, and `assert records` shows the capture worked. But `records` is probably filled by httpx's request log rather than an app logger. A stronger guard would also assert that a known app log line was captured, for example by adding a wrong-scope or mismatch call that emits `app_credential_denied ...`. That would prove app-module loggers are captured.

💭 `app/modules/organizations/app_service.py:16`: importing the private helper `_org_not_supported` from `auth_service` is listed as a deviation in impl-log.md. It is not a security issue. Consider making it public in a follow-up.

Follow-ups (out of scope):
- An alert when one key reads many distinct `org_id`s (scraping signal). This is already in the plan's follow-ups.
- Rate limits are counted per process (ADR-0002), so running several uvicorn workers multiplies the effective per-key and per-IP limits. Revisit this when ADM-042 adds the other runtime scopes.
