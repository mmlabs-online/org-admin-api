# Security review — USR-39 (Attempt 1, appsec-engineer, 2026-11-26)

Saved by the orchestrator (the appsec agent is read-only at REVIEW).

**Verdict: PASS** — no 🔴, no 🟡; four 🟢 notes, two follow-ups.

Scope: diff `4bc1c61..86fa5db`. Tests run: `tests/test_auth_token_super_admin.py`, `tests/test_rbac_org_of_app.py`, `tests/test_config.py`, `tests/test_tokens_service.py` → 177 passed (local `org_admin_test`). Migration tests and the full suite left to the reviewer.

## Verified
- **No existence or holder oracle** (`app/modules/tokens/service.py:149-168`): the super-admin branch runs only after the org-exact lookup returns no roles; the holder check (`app_super_admin_access`) runs before `is_org_of_app`, so a non-holder never touches the org. Every refusal uses `_denied()` (:77-79), same 403 body as the member refusal; `test_denied_bodies_byte_identical` checks 9 cases byte for byte (non-holder, no designation, lost `admin:super-admin`, unknown / suspended / other-app org).
- **429 only for live holders:** `limiter.hit` after both checks (:160-164); `test_non_holder_never_sees_super_admin_429`, `test_member_mints_do_not_count_toward_super_admin_limit`.
- **Live re-check every mint:** `AuthzService.app_super_admin_access` (`rbac/authz.py:77-90`: not `admin`, app active, `org_mode=required`, designation set); `RbacRepository.app_super_admin_access` (`rbac/repository.py:872-882`: unexpired app-scope assignment of the designated role + `is_active_staff_super_admin`). Plain reads, no cache; `test_revocation_blocks_next_mint` covers 5 sources.
- **Org of the app** (`repository.py:44-74`): active AND (unexpired org-scope assignment of one of the app's roles, DB clock, OR enabled entitlement). App-scope assignments cannot qualify (correlated on `RoleAssignment.org_id == Organization.id`). `tests/test_rbac_org_of_app.py` covers each case.
- **Token contents:** `roles` = app-scope set filtered to `<app>:` (no other app, no `admin:*`); `org_roles: []`; `org_access: "super_admin"`; `ent` per org; no `sid` (admin app excluded). `test_holder_token_has_no_other_app_role`.
- **TTL:** field 60–43200 (`app/core/config.py:166`); validator refuses > `ACCESS_TOKEN_TTL_SECONDS` (:287-294); unset → `min(3600, access TTL)` (:211-215); `exp` and `expires_in` share one `ttl`.
- **Audit (R4, R5, R11):** one row per mint in the request session (`service.py:199-214`), committed before the response; `test_audit_failure_blocks_token`. Keys `jti`, `account_session_id`, `role_id`, `expires_at` — none match `_FORBIDDEN_KEY`. Denied mints write no row. Log adds only boolean `super_admin_org`; `test_super_admin_mint_logs_no_secrets`.
- **Rate-limit keying:** scope `super_admin_org_token` keyed on `user.id`; per-IP `token` limit still applies first; defaults 30 / 3600 s; keys in `.env.example` without values.
- **Consumer contract** (`docs/consumers.md:629-641`) fails closed: exact `org_access == "super_admin"` + designated role key in `roles` on a verified token; empty `org_roles` grants nothing; token outlives logout until `exp`; read `expires_in`.

## Findings
- 🟢 Holder-only org-existence signal (`tokens/service.py:156-164`): a live holder can tell an org of the app from others — by design.
- 🟢 App-designation timing signal (`rbac/authz.py:83-89`): app-level only, acceptable.
- 🟢 Audit skip guard could fail loudly (`tokens/service.py:199`): the narrowing conditions would silently skip the audit row if the invariant broke; hardening option: assert / explicit error.
- 🟢 Enum placement (`audit/actions.py:111`): `TOKEN_SUPER_ADMIN_ORG_ISSUED` sits under the "Denied-only" usage comment — cosmetic.

## Follow-ups (out of scope)
1. Before designating a super-admin role for any consumer app, check its backend does not treat "`org` present" as proof of membership (runbook / staff-UI checklist item).
2. `mmlabs-auth`: add the `org_access` accessor and the super-admin exception in `require_org_permission` (`docs/consumers.md:670-671`); until then library consumers get nothing (safe default).
