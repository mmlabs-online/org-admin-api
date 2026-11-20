# Consult — identity-access-engineer — ADM-034 (2026-11-20)

ADM-034 auth consult (identity-access-engineer). I only read files; nothing was edited.

## Findings
1. **Mismatch audit is tied to the manifest.** `authenticate` (`app/modules/app_credentials/service.py:144-181`) always records a mismatch deny as `APP_MANIFEST_SYNCED` (:169). There is no scope check yet. The order today is 401s (missing, malformed, unknown, secret, revoked), then 403 `app_disabled` (:162), then 403 mismatch (:166), then `lock_app` (:178) and `touch_last_used`.
2. **The `admin` refusal ignores scope.** It is unconditional (:115). `count_active` is per app (`repository.py:20-28`), and the partial index is on `app_id` only (`models.py:24-28`).
3. **The registry test will break.** It finds the dependency by function identity: `current_app_credential in dependant_calls(d)` (`tests/test_route_auth_classes.py:273`). A per-scope factory or closure would fail this test. `APP_CREDENTIAL` is a set (:64, :271). The 401, CORS and wrong-key-kind tests loop over it (:454-498).
4. **The issue endpoint takes no body** (`router.py:33-38`).
5. **Raw keys leak through `repr`.** `IssuedCredential.credential` (`service.py:72-74`) is printed by the dataclass `repr`. `IssuedDeployerCredential` has the same problem (`deployer_credentials/service.py:63-65`).
6. **The ADRs already settle the lock question.** ADR-0021 §1 (line 53) says auth keeps `lock_app` for both scopes, and ADR-0022 §3 (line 37) confirms it for `manifest` and `releases`. The lock-free throttled `last_used_at` is for runtime scopes only.
7. **CLAUDE.md R3 is already scope-generic** (ADM-041). ADR-0022 §10 says it absorbs the ADR-0021 §12 text, so pasting §12 verbatim would undo the runtime-scope wording.
8. **D30 is missing.** `docs/identity-architecture.md` goes from D29 (:323) to D31 (:324). ADR-0021 line 6 contains a garbled renumbering note ("renumbered to ADR-0021 / D30").

## Recommendations
- **Migration (AC1).** Add `scope text NOT NULL DEFAULT 'manifest'` with `CHECK (scope IN ('manifest','releases'))`, named so ADM-042 can widen it. Replace the active index with `(app_id, scope) WHERE revoked_at IS NULL`. `count_active(app_id, scope)` keeps running under `lock_app`. The round-trip test should show existing rows become `manifest`.
- **Issuance.** Take an optional body `{scope}` (`extra="forbid"`; missing body means `manifest`; unknown scope gives 422). Check in this order: `require_super`, then the `admin_app` refusal when `scope != releases` (an allow-list, so ADR-0022 runtime scopes stay refused), then `app_disabled`, then the per-scope limit. Add `scope` to the issued audit `changes` and to `AppCredentialRead`.
- **Fixed check order in `authenticate(raw, path_key, *, required_scope, denied_action, ctx)` (AC2):**
  1. All 401s, unchanged and identical. An `mmdk_` key or admin JWT fails `parse_credential`, so it gets the 401.
  2. 403 `app_disabled`, not audited, as today.
  3. 403 `app_credential_mismatch`, audited `denied` with `denied_action`.
  4. 403 `app_credential_scope` (new `PermissionDeniedError` subclass in `errors.py`), audited `denied` with `{"reason": "app_credential_scope"}`. Do not record the secret.
  5. `lock_app`, then `touch_last_used`.

  Putting mismatch before scope keeps `test_wrong_app_unknown_path_app_same_403` (`tests/test_app_credential_auth.py:112`) unchanged. A cross-app attempt is also the stronger incident signal. With a valid key, an unknown path app gives 403 mismatch, not 404, so app existence is never shown before auth. The AC's "404 unknown app" can therefore only be met through staff or other routes; say so in the plan.
- **Keep `lock_app` for `releases`**, as ADR-0021 §1 requires. Use the row it returns to re-check `status == active` inside `authenticate`. Today that re-check lives only in the manifest service (`manifest.py:107`, test :139), and the releases service would otherwise miss the disable race. Lock order is app row, then release row, always.
- **Race test (AC3).** Because `lock_app` serialises same-app writes, the HTTP path can never reproduce the first-insert race. Use `INSERT … ON CONFLICT` (or catch the unique violation and retry once), and test it with two sessions that bypass `authenticate`.
- **Dependency and registry.** Make the dependency a callable class, `RequireAppCredential(scope, denied_action)`, like `RequirePermission` (:181). It keeps `Security(app_credential_scheme)`, so the OpenAPI assertion at :303 still holds. Change `APP_CREDENTIAL` to `dict[(method, path), scope]` holding the manifest and release PUT routes. The exact-list test should assert both the route set and each instance's `.scope`. Existing loops can use `set(APP_CREDENTIAL)`.
- **Hide raw keys.** Use `credential: str = field(repr=False)` at `service.py:74`, and `Field(repr=False)` on the pydantic `AppCredentialIssued`. Fix the deployer class too only if Ming agrees; otherwise list it as a follow-up (R7).
- **Docs.**
  - R3 class 4(a): drop "(live; until ADM-034 adds the scope column…)", mark `releases` live with its route, state that `releases` may be issued for `admin` (manifest and runtime scopes may not), and add D30 to the D-list.
  - R9: add the natural-key releases path and the singular `/deployment` sub-document (D30).
  - Add the D30 row and the "Amended by ADR-0021" pointers in ADR-0016 and ADR-0017.

## Risks
- **Contention on the `admin` app lock.** A `releases` key for `admin` takes `lock_app("admin")` for the whole write, the same lock every staff RBAC writer uses (`rbac/service.py:797,1531`, `users/service.py:49`). It is low volume (30/min) and short, but the release write must not take any lock before the app row, or it risks deadlocks.
- **Hard-coded manifest action.** Leaving `APP_MANIFEST_SYNCED` in place would mislabel release denies in the audit log. `tests/test_audit_coverage.py` may need the new action.
- **Audit-row volume.** Wrong-scope denies write one row per request; only the per-IP limit caps this.

## Open questions for Ming (suggested defaults)
1. **Order of the two 403 checks.** Default: mismatch before scope.
2. **Audit action for denied release writes.** Default: `release.created` (the PUT is an upsert).
3. **The `admin` app collision (org-admin-api vs admin-pan).** Default: register `admin-pan` as its own active app row with no roles, and give it only a `releases` key.
4. **Fix `IssuedDeployerCredential` repr in this PR?** Default: yes. It is one line with the same risk, noted as an incidental change.
5. **Fix the garbled ADR-0021 line 6 renumbering note now?** Default: yes, a text-only fix.
