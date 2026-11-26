Status: READY

# Plan — USR-39: Org token for a non-member org via the app's super-admin role (simap SIM-043)

> **High** (`app/modules/tokens/**`, `app/modules/auth/**`, `app/modules/rbac/**`, `app/core/config.py`,
> `.env.example`): stops at PLAN for Ming's approval (Gate `plan-approval`), never merged by an agent.
> Batch 2026-11-26-1. **Builds on `work/tickets/USR-38/plan.md` (READY, not yet approved or implemented)** and imports
> its shared contract (USR-38 plan, Approach step 9); IMPLEMENT starts only after USR-38 is merged (`Depends on`).
> Written for the **default answers** of "Blocking questions for Ming" (Q1–Q12). Q1, Q2, Q4 and Q7 change code; the
> others confirm behaviour or docs. No migration (Q2 default).

## Scope summary
- Ticket Goal: "A staff holder of an app's designated super-admin role can mint an access token for any org of that
  app without being a member, with an explicit opt-in claim, a short lifetime and an audit row per mint."
- Ticket Scope: "`POST /api/v1/auth/token` with `org_id`: when the account has no role in that org, succeed only if
  the app has a designated super-admin role, the account holds it, the account still holds active
  `admin:super-admin` (mint-time check, OQ82), and the org is active and an org of the app (OQ89); otherwise the same
  `org_access_denied` as today (no existence oracle)." / "Claims (proposed, OQ84): usual claims, `roles` = holder's
  app-scope roles (includes `<app>:super-admin`), `org` = X, `org_roles: []`, `ent` as for any org token, opt-in
  `org_access: "super_admin"`. Member path wins when the holder has org roles in X (normal token, no `org_access`)." /
  "TTL (OQ85): new env key `SUPER_ADMIN_ORG_TOKEN_TTL_SECONDS` (default 3600, ≤ `ACCESS_TOKEN_TTL_SECONDS`) in
  `app/core/config.py` and `.env.example`; `expires_in` reflects it." / "Audit (OQ86): one row per super-admin mint
  (`token.super_admin_org_issued`: actor, app, org, `jti`; no email) in the mint transaction; normal mints stay
  log-only." / "Security extras (OQ87, e.g. per-account mint rate limit, recent re-auth) as decided at PLAN." /
  "`docs/consumers.md`: claim contract, opt-in rule, the single documented exception to "authorize org data from
  `org_roles` only" (OQ88)."
- Ticket Out of scope: "Org picker (USR-40); token deny-list; staff MFA (OQ3); `mmlabs-auth` accessor and simap
  changes (their repos)."
- Spec §5.16 Token (line 418): "`POST /api/v1/auth/token` with `org_id` succeeds when the account has no role in that
  org but holds the app's super-admin role … unknown or suspended org → `org_access_denied` as today … `org_roles: []`
  (never a fabricated org role), `ent` as for any org token, **plus a new explicit claim** (e.g. `org_access:
  "super_admin"`) … If the holder is also a member with org roles, the member path wins (normal token, no new claim)."
- Spec §5.16 TTL (line 419): "`SUPER_ADMIN_ORG_TOKEN_TTL_SECONDS`, default 3600, never above
  `ACCESS_TOKEN_TTL_SECONDS`; listed in `.env.example`". Revocation (line 420): "Mint reads the DB, so removing the role
  (or the designation, OQ80) blocks new tokens at once; live tokens last until `exp` … No deny-list". Audit (line 421):
  "One audit row per token minted through this path (e.g. `token.super_admin_org_issued`: actor = the account, app,
  org, `jti`, time), in the mint transaction; normal tokens stay log-only." Consumers (line 422): "the single exception
  to "authorize org data from `org_roles` only" — a token with `org` and the opt-in claim grants the app's super-admin
  powers in that org, and only then."
- Spec §8 OQ84–OQ89 (lines 562–567) open; OQ89 (line 567): "(a) every `active` org platform-wide, (b) orgs with at
  least one active role assignment of the app or an entitlement for the app (proposed), (c) orgs with an active
  entitlement for the app only? Suspended orgs: hidden in the picker and refused at mint (proposed), or listed
  read-only?"
- Spec §5.16 line 405 (fact): "One TTL setting `ACCESS_TOKEN_TTL_SECONDS` (60..43200; 12 h in production)." Code:
  `app/core/config.py:158` default **900** → the conflict with the 3600 default (Q1).
- USR-38 plan step 9 (imported, not rebuilt): `is_designated_super_admin_role(app, role_id)`,
  `RbacRepository.is_active_staff_super_admin(user_id)`, `RbacRepository.app_super_admin_access(user_id, role_id)`,
  `AuthzService.app_super_admin_access(user_id, app) -> bool` ("**This is the holder predicate USR-39 (mint) and
  USR-40 (picker) must call.**"); "**Not in USR-38:** the "org of the app" predicate (OQ89) — USR-39 ships it
  (`RbacRepository` method), USR-40 reuses it." ADR-0030 §7 "Org-token exception … names and values per USR-39's gate"
  and D42 (pointer to ADR-0030 §7–§8) are written by USR-38.
- `work/decisions.md`: nothing on SIM-043 / super-admin tokens. ADR / D numbering: **no new ADR, no new D number** —
  USR-39 fills ADR-0030 §7 (reserved for it by USR-38) and D42 already points there.
- CLAUDE.md: R1 (rules in `TokenService`, SQL in `RbacRepository`), R3 class 2 (cookie route, unchanged class), R4
  (audit in the same transaction), R5 (config from env, `.env.example` keys without values), R6 (existing
  `org_access_denied` / `rate_limited`), R8, R9 (one additive claim), R10, R11 (no email / token in logs or audit).

## Acceptance criteria → verification
All tests in `tests/test_auth_token_super_admin.py` (new) unless another file is named. "Holder" fixture = active,
verified account with an app-scope assignment of the designated `simap:super-admin` (USR-38 rules: org app
`simap`-like test app `orgapp`, `org_mode = required`, designated role with app-scope permissions only) **and** an
app-scope `admin:super-admin`; token client uses `make_client(access_token_ttl_seconds=43200)` unless stated.

| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Holder mints a token for a non-member org: claims as specified, `org_roles: []`, `org_access: "super_admin"`, `exp - iat` = the new TTL | `test_holder_non_member_org_claim_snapshot` — verified with `verify_reference`; exact key set `{iss, aud, sub, email, email_verified, roles, org, org_roles, ent, org_access, name?, iat, exp, jti}` (no `sid`); `org_roles == []`, `org_access == "super_admin"`, `roles` = app-scope roles incl. `orgapp:super-admin`, `exp - iat == 3600`, response `expires_in == 3600`; `test_holder_token_ent_entitled` / `test_holder_token_ent_null_when_org_of_app_by_assignment_only` (`ent` as any org token) |
| 2 | Holder who is a member of that org gets a normal member token | `test_holder_member_path_wins` — `org_roles` non-empty, no `org_access` key, `exp - iat == 43200`, no audit row |
| 3a | Ordinary account still gets `org_access_denied` | `test_non_holder_denied_bodies_identical` (below) + existing `tests/test_auth_token.py` org tests stay green |
| 3b | Unknown, suspended or not-of-the-app org → `org_access_denied` for a holder too | `test_holder_unknown_org_denied`, `test_holder_suspended_org_denied`, `test_holder_org_of_other_app_denied` (org has only another app's assignments / entitlement), `test_holder_org_with_only_expired_assignments_denied`, `test_holder_org_with_disabled_entitlement_only_denied` (Q4 default) |
| 3c | One answer, byte-identical (no existence oracle) | `test_denied_bodies_byte_identical` — parametrized scenarios: non-holder × real org, non-holder × unknown org, holder × unknown org, holder × suspended org, holder × other-app org, app without designation, holder without `admin:super-admin`; all `resp.status_code == 403` and `resp.content` equal to one reference body `{"error":{"code":"org_access_denied","message":"No access to this organization","details":{}}}` |
| 4 | Removing the role, the designation, or `admin:super-admin` blocks the next mint at once | `test_revocation_blocks_next_mint` parametrized over sources: `designated_assignment_revoked` (row deleted), `designated_assignment_expired` (`expires_at` in the past, DB clock), `designation_cleared` (`apps.super_admin_role_id = NULL`), `admin_super_admin_revoked`, `admin_super_admin_expired`. Each: first mint 200 → mutate in the same test DB → second mint 403 with the reference body, and audit row count stays 1 |
| 5a | Exactly one audit row per super-admin mint, none for normal mints | `test_super_admin_mint_writes_one_audit_row` (action `token.super_admin_org_issued`, outcome `success`, `actor_user_id` = holder, `target_type = organization`, `target_id` = org, `app_key`, `org_id`, `changes` keys exactly `{jti, account_session_id, role_id, expires_at}`, `changes.jti` = token `jti`, ip / request id from `AuditContext`); `test_normal_and_member_mints_write_no_audit_row`; `test_denied_mint_writes_no_audit_row`; `test_audit_row_has_no_email` (row JSON contains neither the email nor the token); `tests/test_audit_coverage.py` › `test_no_secret_or_email_in_any_row` stays green |
| 5b | The token never contains another app's role | `test_holder_token_has_no_other_app_role` (holder holds `admin:super-admin` + a role of a third app: `roles` all start with `orgapp:`, no `admin:` key anywhere in the payload) |
| 6a | New config key validated (≤ normal TTL) | `tests/test_config.py` › `test_super_admin_org_ttl_default_follows_access_ttl` (unset: 900 → 900, 43200 → 3600), `test_super_admin_org_ttl_explicit` (1800 with access 43200 → 1800), `test_super_admin_org_ttl_above_access_ttl_rejected` (`ValidationError`, message names both keys, no value echoed), `test_super_admin_org_ttl_bounds` (59, 43201), `test_super_admin_org_mint_rate_limit_bounds` (0 → error) |
| 6b | Listed in `.env.example`; `docs/consumers.md` updated | `tests/test_config.py` › existing `test_env_example_keys_only`, `test_env_example_copy_gives_local_defaults` stay green with the three new keys; reviewer reads `docs/consumers.md` claim list and the exception paragraph |
| 7 | Per-account mint limit (OQ87, Q3) | `test_super_admin_mint_rate_limited_429` (`make_client(super_admin_org_token_user_rate_limit=1)`: first super-admin mint 200, second 429 `rate_limited` with `retry-after`, no second audit row); `test_member_mints_do_not_count_toward_super_admin_limit` (limit 1: two member mints 200 then super-admin mint 200); `test_non_holder_never_sees_super_admin_429` (limit 1, two denied non-holder calls → both 403, never 429) |
| 8 | TTL wiring | `test_holder_token_uses_explicit_ttl` (`super_admin_org_token_ttl_seconds=600` → `exp - iat == 600`, `expires_in == 600`); `test_holder_token_ttl_capped_locally` (default config, access 900 → 900) |
| 9 | Route auth class unchanged (R3) | `tests/test_route_auth_classes.py` green without edits |
| — | Everything | `make check` green |

## Ming verification after merge
- After USR-38 is deployed and `simap:super-admin` is designated and granted to you (USR-38 "Ming verification"):
  sign in on simap, request a token for an org you are not a member of; check `org_access: "super_admin"`,
  `org_roles: []`, `expires_in` = 3600 in production (access TTL 43200), and one `token.super_admin_org_issued` row in
  the admin audit view. simap and `mmlabs-auth` must ship their consumer changes before simap relies on the claim
  (cross-repo follow-ups, Q9).
- Confirm audit retention (`app/modules/audit/purge.py` job settings in the deploy env) is long enough for incident
  review of mint rows (security consult Q6; no code change).

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/modules/tokens/service.py` | `TokenService.__init__` gains keyword-only `super_admin_ttl_seconds: int`, `limiter: RateLimiter`, `super_admin_limit: RateLimit`; `self.audit = AuditLogger(session)`. `issue(app, device, data, *, ctx: AuditContext)`. New branch only inside `if not org_roles:` (:133-134); per-mint `ttl` local used for `exp` (:159) and `expires_in` (:172); audit after signing (:149-162); `super_admin_org` in the log line (:163-171) | Ticket scope (mint branch, TTL, audit, OQ87 limit) | **High** |
| `app/modules/auth/router.py` | `token` (:181-206): add `ctx: AuditContextDep`; pass `super_admin_ttl_seconds=settings.super_admin_org_token_ttl`, `limiter=request.app.state.rate_limiter`, `super_admin_limit=RateLimit(settings.super_admin_org_token_user_rate_limit, settings.super_admin_org_token_user_rate_window_seconds)`, `ctx=ctx`; docstring names the super-admin path | TTL and audit wiring (architect note: request session commits on success) | **High** |
| `app/core/config.py` | After :158: `super_admin_org_token_ttl_seconds: int \| None = Field(default=None, ge=60, le=43200)`, property `super_admin_org_token_ttl` (= value, or `min(3600, access_token_ttl_seconds)` when unset), `model_validator` `_super_admin_org_ttl_within_access_ttl`; near :123-127: `super_admin_org_token_user_rate_limit: int = Field(default=30, ge=1)`, `super_admin_org_token_user_rate_window_seconds: int = Field(default=3600, ge=1)` | OQ85 TTL, OQ87 limit (R5) | **High** |
| `.env.example` | After :123: `SUPER_ADMIN_ORG_TOKEN_TTL_SECONDS=` with comment; after :92: `SUPER_ADMIN_ORG_TOKEN_USER_RATE_LIMIT=`, `SUPER_ADMIN_ORG_TOKEN_USER_RATE_WINDOW_SECONDS=` with comment | R5 | **High** |
| `app/modules/rbac/repository.py` | Module-level `org_of_app_clause(app_id: uuid.UUID) -> ColumnElement[bool]` (OQ89 predicate, contract below) and `RbacRepository.is_org_of_app(org_id: uuid.UUID, app_id: uuid.UUID) -> bool`; import `OrgEntitlement` (no cycle: `entitlements/models.py` imports only `app.db.base`) | The one OQ89 predicate, reused by USR-40 | **High** |
| `app/modules/rbac/authz.py` | `AuthzService.is_org_of_app(org_id, app_id) -> bool` (one-line pass-through, like `org_role_keys` :45) | R1: token service calls RBAC through `AuthzService` | **High** |
| `app/modules/audit/actions.py` | `TOKEN_SUPER_ADMIN_ORG_ISSUED = "token.super_admin_org_issued"` (after :90, comment "USR-39, ADR-0030 §7") | Audit catalogue (matches `action_format` CHECK, `audit/models.py:33-35`; no migration) | no |
| `app/modules/tokens/schemas.py` | `TokenResponse.expires_in` field description: "seconds until this token's `exp`; shorter for super-admin org tokens" (no shape change) | OpenAPI accuracy (api rec 1) | **High** |
| `docs/consumers.md` | Claim list (:617-620) adds `org_access`; org paragraph (:622-627) adds the single exception (exact rule in Approach step 7); lag notes (:172, :331-332, :647) mention the super-admin TTL; follow-up (:654-656) adds the `mmlabs-auth` accessor; error tables (:151, :197) unchanged text but "(also for super-admin holders)" | OQ88 | no |
| `docs/adr/0030-app-super-admin-role.md` (created by USR-38) | §7 "Org-token exception" → Accepted with the gate answers: branch order, claim, TTL rule, audit row, mint limit, OQ89 predicate, no re-auth (follow-up), consumer rule pointer. No other section touched | ADR-0030 reserved §7 for USR-39 | no |
| `docs/identity-architecture.md` | API table row `POST /api/v1/auth/token` with `org_id` (:176): add the super-admin path (`org_access`, TTL, audit, ADR-0030 §7) | Keep API reference true | no |
| `docs/spec-v1.md` | §5.16 Token / TTL / Revocation / Audit / Consumers bullets (:418-422) → decided (dated) with Ming's answers; §8 OQ84 (token half), OQ85–OQ88 ticked; OQ89 ticked by whichever of USR-39 / USR-40 merges first | Spec stays the source of truth | no |
| `tests/test_auth_token_super_admin.py` (new) | All API tests in the tables | AC1–5, 7, 8 | no |
| `tests/test_rbac_org_of_app.py` (new) | Predicate tests (DB, no HTTP) | Pure / repository logic first | no |
| `tests/test_config.py` | 5 config tests | AC6a | no |
| `tests/test_tokens_service.py` | `_service` (:21-28) passes the new kwargs; `.issue(` calls (:44, :73) pass `ctx=AuditContext(...)` | Constructor change keeps `make check` green | no |
| `tests/test_audit_coverage.py` | Comment at :955 only: "token issuance not audited, except the super-admin org path (USR-39, tests/test_auth_token_super_admin.py)"; `IDENTITY_NOT_AUDITED` keeps `/token` (normal mint writes no row) | Classification stays true | no |

No change to `app/core/rate_limit.py` (the per-account bucket uses `RateLimiter.hit` directly, as
`users/profile_service.py:53`), `app/core/errors.py` (existing `OrgAccessDeniedError`, `RateLimitedError`),
`app/db/**`, migrations, `tests/test_route_auth_classes.py`, `organizations/**` (USR-40 owns the picker).

## Data model / migration
None. The audit action is a new string within the existing `action_format` CHECK; no new column (Q2 default: no
`account_sessions.authenticated_at`, re-auth deferred). No new index: `org_of_app_clause` uses
`ix_role_assignments_org_id` (`rbac/models.py:208`) and `uq_org_entitlements_org_id_app_id`
(`entitlements/models.py:29`) for a single `org_id`; USR-40 re-checks EXPLAIN for the list form.

**OQ89 predicate (shared contract with USR-40 — exact name, signature, semantics):**
```python
# app/modules/rbac/repository.py, module level
def org_of_app_clause(app_id: uuid.UUID) -> ColumnElement[bool]:
    """OQ89 (b), ADR-0030 §7: `Organization` (correlated on `Organization.id`) is an org of the app.
    Organization.status == 'active' AND (
      EXISTS an org-scope role assignment in this org of a role of `app_id`, unexpired (DB clock)
      OR EXISTS an `org_entitlements` row for (this org, `app_id`) with `enabled IS TRUE`)."""
```
- Assignment branch: `select(1).select_from(RoleAssignment).join(Role, Role.id == RoleAssignment.role_id)
  .where(RoleAssignment.org_id == Organization.id, Role.app_id == app_id, or_(RoleAssignment.expires_at.is_(None),
  RoleAssignment.expires_at > func.now()))`, `.exists()`. The assignee's user status is not part of it (an org with
  only disabled members still belongs to the app).
- Entitlement branch: `OrgEntitlement.org_id == Organization.id, OrgEntitlement.app_id == app_id,
  OrgEntitlement.enabled.is_(True)` — same `enabled` rule as `entitlements/repository.py:15-17` `ACTIVE`; the `App.status`
  part of `ACTIVE` is not repeated because callers gate on the app first (`AuthzService.app_super_admin_access`
  requires `app.status == active`).
- `RbacRepository.is_org_of_app(org_id, app_id) -> bool` =
  `await session.scalar(select(exists().where(Organization.id == org_id, org_of_app_clause(app_id))))`.
  USR-40 uses `org_of_app_clause(app.id)` in its list query. Plain reads, no lock.

## API contract
No new route, status code, error code or response field. R3 class 2 (cookie + `Origin` + `X-MM-App`) unchanged.

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/token` | unchanged `TokenRequest {audience, account_id, org_id?}` | unchanged `TokenResponse {access_token, token_type, expires_in, audience}`; `expires_in` = this token's lifetime (super-admin TTL on the new path) | 200; 401 `session_invalid`; 403 `not_member`, `email_unverified`, `audience_mismatch`, `org_access_denied` (also every super-admin refusal, byte-identical); 422 `org_not_supported`; 429 `rate_limited` (per IP as today; per account on the super-admin path only) | cookie class; no `require_permission` |

**Super-admin org token claims (additive, R9):** `iss`, `aud` = app, `sub`, `email`, `email_verified: true`, `roles`
= the holder's app-scope roles of the app (from `effective_roles(user, app)`, prefix-filtered; includes the designated
role key), `org` = org id, `org_roles: []`, `ent` (as `get_active_for`, `null` when not entitled), `name` (as today),
`org_access: "super_admin"`, `iat`, `exp = iat + super_admin_org_token_ttl`, `jti`; header `typ = at+jwt`. Never
`sid` (app ≠ `admin`; `admin` cannot designate, USR-38 CHECK). `org_access` is **absent** (never `null`) on every
other token.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_rbac_org_of_app.py` › `test_org_of_app_by_unexpired_assignment`, `…_by_enabled_entitlement`, `…_false_for_expired_assignment_only`, `…_false_for_disabled_entitlement_only`, `…_false_for_other_app_only`, `…_false_for_suspended_org` (assignment + entitlement present), `…_false_for_unknown_org`, `…_ignores_app_scope_assignments` (app-scope `org_id NULL` rows never count) | OQ89 predicate (repository, DB) |
| `tests/test_auth_token_super_admin.py` › `test_holder_non_member_org_claim_snapshot`, `test_holder_token_ent_entitled`, `test_holder_token_ent_null_when_org_of_app_by_assignment_only` | AC1 |
| › `test_holder_member_path_wins` | AC2 |
| › `test_holder_unknown_org_denied`, `test_holder_suspended_org_denied`, `test_holder_org_of_other_app_denied`, `test_holder_org_with_only_expired_assignments_denied`, `test_holder_org_with_disabled_entitlement_only_denied` | AC3b |
| › `test_denied_bodies_byte_identical` (param, 7 scenarios) | AC3a, AC3c |
| › `test_non_app_scope_or_expired_holder_is_non_holder` (designated role held only expired → 403 reference body) | identity Q "expired holder = non-holder" |
| › `test_revocation_blocks_next_mint` (param, 5 sources) | AC4 |
| › `test_super_admin_mint_writes_one_audit_row`, `test_normal_and_member_mints_write_no_audit_row`, `test_denied_mint_writes_no_audit_row`, `test_audit_row_has_no_email` | AC5a |
| › `test_holder_token_has_no_other_app_role` | AC5b |
| › `test_super_admin_mint_rate_limited_429`, `test_member_mints_do_not_count_toward_super_admin_limit`, `test_non_holder_never_sees_super_admin_429` | AC7 |
| › `test_holder_token_uses_explicit_ttl`, `test_holder_token_ttl_capped_locally` | AC8 |
| › `test_super_admin_mint_logs_no_secrets` (caplog: no email, no token, `super_admin_org=True` in `token_issued`) | R11 |
| › `test_audit_failure_blocks_token` (monkeypatch `AuditLogger.record` to raise → 500, no token in body; strict client) | R4 atomicity (security finding 5) |
| `tests/test_config.py` › `test_super_admin_org_ttl_default_follows_access_ttl`, `test_super_admin_org_ttl_explicit`, `test_super_admin_org_ttl_above_access_ttl_rejected`, `test_super_admin_org_ttl_bounds` (param 59, 43201), `test_super_admin_org_mint_rate_limit_bounds` | AC6a |

Test notes: build holder fixtures with `tests/rbac_factories.py` (`assign`, `make_org`, `make_role`,
`super_admin_role`) and the USR-38 designation helper (set `app.super_admin_role_id` directly in the DB for speed;
the USR-38 API is tested there). Revocation tests mutate rows between two calls on the same client. A 403/429 rolls
the request back but not the shared test transaction setup (pattern `tests/test_auth_token.py:716-727`).

## Approach
1. **Config (R5):** in `config.py` add the three fields (Files table). Module constant
   `_SUPER_ADMIN_ORG_TOKEN_TTL_DEFAULT = 3600`. Property `super_admin_org_token_ttl -> int`: explicit value, else
   `min(_SUPER_ADMIN_ORG_TOKEN_TTL_DEFAULT, self.access_token_ttl_seconds)`. Validator: if the value is set and
   `> access_token_ttl_seconds` → `ValueError("SUPER_ADMIN_ORG_TOKEN_TTL_SECONDS must not exceed
   ACCESS_TOKEN_TTL_SECONDS")` (no value echoed; `hide_input_in_errors` already on). Empty `.env.example` entries are
   ignored (`env_ignore_empty=True`, :34) so the copy-gives-defaults test stays green. Q1.
2. **OQ89 predicate** in `rbac/repository.py` + `AuthzService.is_org_of_app` (Data model section).
3. **Mint branch** in `TokenService.issue` — only where `org_roles` is empty (:133). Order (identity rec 1; no
   query on the org for non-holders, so unknown vs real org cannot be told apart by timing):
   ```text
   if not org_roles:
       if not await self.authz.app_super_admin_access(user.id, app):   # USR-38 contract: designation, unexpired
           raise _denied()                                              # app-scope holding, live admin:super-admin,
       if not await self.authz.is_org_of_app(data.org_id, app.id):     # app active, org_mode required, not admin
           raise _denied()
       retry_after = self.limiter.hit("super_admin_org_token", str(user.id), self.super_admin_limit)
       if retry_after is not None: raise RateLimitedError(retry_after)
       super_admin_org = True   # roles: keep the app-scope `roles` from :118 (no second query)
   else:
       roles = await self.authz.effective_roles(user.id, app.key, data.org_id)   # unchanged member path
   ```
   `_denied()` returns `OrgAccessDeniedError("No access to this organization", details={})` — the one existing
   instance text, so every refusal is byte-identical (api rec 2). The limiter is hit only after both checks pass, so
   only would-be-successful super-admin mints count and a 429 is reachable only by a live holder (api rec 8; Q3).
   Claims: `org`, `org_roles = []`, `ent` exactly as today (:138-140), and `org_access = "super_admin"` only when
   `super_admin_org`. Holder checks are plain reads in the mint transaction, no cache, no lock (USR-38 step 9).
4. **TTL per mint:** `ttl = self.super_admin_ttl if super_admin_org else self.ttl`; use it for `exp` and
   `IssuedToken.expires_in`.
5. **Audit (R4, OQ86):** after `sign` and inside `issue`, when `super_admin_org`: `await self.audit.record(
   AuditAction.TOKEN_SUPER_ADMIN_ORG_ISSUED, actor=EndUser(user.id), ctx=ctx, target=(AuditTarget.ORGANIZATION,
   data.org_id), app_key=app.key, org_id=data.org_id, changes={"jti": jti, "account_session_id": str(account.id),
   "role_id": str(app.super_admin_role_id), "expires_at": <ISO-8601 UTC of exp>})`. Key names checked against
   `_FORBIDDEN_KEY` (`audit/service.py:28`: `hash|token|password|cookie|email|secret`): none match; **never** use
   `token_ttl`, `access_token_jti`, `email` (→ `ValueError` → 500, security finding 6). `role_id` instead of the
   security consult's `role_key` (no extra query; the key is resolvable from the id). `record` flushes in the request
   session; `get_session` commits after the handler (`app/db/session.py:27-32`), so a failed audit fails the request
   and no token leaves (security finding 5). Normal mints: no row (log only, unchanged).
6. **Router:** add `ctx: AuditContextDep` to `token` (pure request read, no DB, no auth-class change) and the new
   constructor arguments (Files table). Log line `token_issued … super_admin_org=%s` (no email, no token).
   Former-holder warning (security rec 5) not added: detecting "former" needs extra queries on the non-holder path
   (timing) — see Specialist input.
7. **`docs/consumers.md` exact rule (OQ88, Q9 default):** "Grant super-admin powers in an org only if **all** hold:
   the token is fully verified (signature, `iss`, `aud` = your app, `exp`, `typ`); `org` is present; `org_access ==
   "super_admin"` (exact string; a missing key, any other value or type means no super-admin access); and `roles`
   contains your app's designated super-admin role key (configured in your app, e.g. `simap:super-admin`). Then treat
   the caller as holding every `scope = org` permission of your app (from the role catalog) in the `org` org only,
   plus the app-scope permissions of its `roles` as usual. Never infer access from an empty `org_roles` (`[]` grants
   nothing). This is the only exception to "authorize org data only from `org_roles`". Log `sub` and `jti` for staff
   actions and show staff as staff in your UI. These tokens last up to `SUPER_ADMIN_ORG_TOKEN_TTL_SECONDS` (1 h in
   production), carry no `sid` and survive logout until `exp`; read `expires_in`, do not assume 12 h." Line 626
   ("If `org` is present and `org_roles` is absent, reject") stays true (`org_roles` is present as `[]`). Follow-up
   line: `mmlabs-auth` gains an `org_access` accessor and `require_org_permission` honours the exception.
8. **ADR-0030 §7, identity-architecture row, spec §5.16 / §8** edits (Files table), with the gate answers dated.
9. `make check`; impl-log records the USR-38 / USR-40 rebase points.

Rules: R1 (branch logic in `TokenService`, SQL only in `RbacRepository`, router wiring only), R3 (class 2 unchanged;
registry untouched), R4 (step 5), R5 (step 1, `.env.example` keys without values), R6 (existing codes only), R8
(tests above incl. 401/403/422/429 already covered by `tests/test_auth_token.py`), R9 (additive claim, no field
change), R10 (no impersonation, no OIDC), R11 (no email, token or cookie in logs, errors or audit).

## Specialist input used
- **identity-access-engineer:** rec 1 branch order — adopted (designation / holding / `admin:super-admin` collapse into
  USR-38's single `app_super_admin_access` call, org check last); rec 2 OQ89 (b) single repository method — adopted
  as `org_of_app_clause` + `is_org_of_app` with "active entitlement" = `enabled IS TRUE` (Q4); rec 3 member path first —
  adopted; rec 4 claims — adopted; rec 5 TTL field + validator — adopted with `None` default and `min()` (Q1);
  rec 6 audit — adopted; rec 7 per-account limit — adopted (30/h, Q3); re-auth max-age — **rejected for USR-39**:
  verified in code that `upsert_account` (`sessions/repository.py:188-197`) only sets `last_used_at` on conflict, so
  `account_sessions.created_at` is the first sign-in on the device, not the latest (security finding 3 is right) →
  Q2; rec 8 tests — adopted.
- **security-architect:** rec 1 — adopted; rec 2 audit — adopted with `role_id` instead of `role_key`, `AuditContextDep`
  on the token route — adopted; rec 3 per-account limit — adopted, env names aligned with the TTL key
  (`SUPER_ADMIN_ORG_TOKEN_USER_RATE_LIMIT` / `…_WINDOW_SECONDS`, pattern of `PROFILE_USER_RATE_LIMIT`) instead of
  `SUPER_ADMIN_ORG_MINT_USER_RATE_LIMIT`; rec 4 TTL — adopted; rec 5 no refused-mint audit — adopted, former-holder
  `logger.warning` — rejected (extra queries on the non-holder path = timing and cost; Q8); rec 6 consumer rule
  (all three conditions, unknown = deny) — adopted (Q9); rec 7 tests — adopted. Q3 shorter device session — not in
  v1 (Q10); Q6 purge exemption — no (Ming verification).
- **api-platform-engineer:** recs 1–7 — adopted; rec 8 "reuse `rate_limit("token")` with a keyed bucket" — adopted in
  spirit via `RateLimiter.hit` with its own scope string (no `app/core/rate_limit.py` change); OQ84 holder creates
  orgs / accepts invitations — normal rules (Q11); `expires_in` docs note — adopted.
- **software-architect (ticket note):** no new plumbing, no migration for the action string — confirmed
  (`audit/models.py:33-35`).

## Split proposal
None. Estimate: ~120 changed lines of application code, ~350 lines of tests, ~80 lines of docs; one review cycle.

## Blocking questions for Ming
1. **OQ85 — TTL default vs `ACCESS_TOKEN_TTL_SECONDS` default (conflict).** The ticket says default 3600 and
   "≤ `ACCESS_TOKEN_TTL_SECONDS`", but the access TTL defaults to **900** (`config.py:158`; 43200 in production), so a
   plain `default=3600` + validator fails startup locally and in tests. *Default: `SUPER_ADMIN_ORG_TOKEN_TTL_SECONDS`
   unset → effective `min(3600, ACCESS_TOKEN_TTL_SECONDS)` (3600 in production, 900 locally); set explicitly →
   60..43200 and startup fails if above `ACCESS_TOKEN_TTL_SECONDS`.* Alternative: default 3600 and the validator
   clamps silently (hides misconfiguration — not proposed).
2. **OQ87 — recent re-authentication (consultant conflict).** Identity proposed `account_sessions.created_at`;
   verified in code it is not the latest sign-in (`upsert_account` keeps it, `sessions/repository.py:193-196`), so a
   real check needs a new `authenticated_at` column (migration, `sessions/**`). *Default: no re-auth in USR-39;
   follow-up ticket (USR) "authenticated_at + max-age for super-admin mints", together with staff MFA (OQ3).*
3. **OQ87 — per-account mint limit.** *Default: yes, 30 per 3600 s per account (all three consultants; 20 was the
   alternative discussed), env `SUPER_ADMIN_ORG_TOKEN_USER_RATE_LIMIT` / `SUPER_ADMIN_ORG_TOKEN_USER_RATE_WINDOW_SECONDS`;
   counts only mints that passed the holder and org checks; 429 `rate_limited`; member mints never count.*
4. **OQ89 — "org of the app".** *Default (b): `active` org AND (an unexpired org-scope assignment of any role of the
   app OR an entitlement for the app with `enabled = true`); a disabled entitlement alone does not count; one
   predicate `org_of_app_clause(app_id)` shared with USR-40.* Consequence: an org whose members all left and that has
   no enabled entitlement is not reachable. Alternatives: count any entitlement row; (a) every active org; (c)
   entitlement only.
5. **OQ89 — suspended orgs.** *Default: refused at mint with the same `org_access_denied` (and hidden in the USR-40
   picker).*
6. **OQ84 — claim.** *Default: `org_access: "super_admin"`, absent otherwise (never `null`); `roles` = the holder's
   app-scope roles incl. the designated key; `org_roles: []`; `ent` as any org token; member path wins (normal token,
   normal TTL, no audit).*
7. **OQ86 — audit content.** *Default: one row `token.super_admin_org_issued` (actor = account, target = org,
   `app_key`, `org_id`, `changes {jti, account_session_id, role_id, expires_at}`, IP / UA / request id); no email, no
   name; refused mints and normal mints not audited.*
8. **Refused mints by former holders.** *Default: no audit and no extra warning log (it would add queries to every
   non-holder refusal); the revocation itself is already audited by USR-38 (`role_assignment.revoked`,
   `app.updated`).* Alternative: `logger.warning` when the holder passes but the org check fails (cheap) — say if
   wanted.
9. **OQ88 — consumer rule.** *Default: the exact rule in Approach step 7 (all three conditions; unknown `org_access`
   = deny; grant every `scope = org` permission of the app in `org`, since under USR-38 Q1's default the designated
   role carries no org permissions to "map through the catalog" as the spec text says); simap (SIM-043) and
   `mmlabs-auth` take the follow-ups in their repos and release before simap relies on it.* If USR-38 Q1 is answered
   the spec way (role holds org permissions), the rule becomes "map the designated role's permissions through the
   catalog".
10. **OQ87 — device-session length / separate staff account.** *Default: no change in v1; ADR-0030 §7 recommends a
    separate staff account per person and names the residual risk (XSS on the app origin, stolen `mm_device` cookie up
    to 365 days).*
11. **OQ84 — other actions through this access.** *Default: none — creating orgs, accepting invitations, joining
    follow the normal rules; the super-admin token grants nothing beyond the claim.*
12. **ADR-0030 handling.** *Default: no new ADR or D number; USR-39 fills ADR-0030 §7 (written by USR-38 as "accepted
    in principle") and D42 keeps pointing there.*

## Assumptions
- **Depends on USR-38 (READY, not yet approved or implemented).** This plan imports, never re-implements:
  `app.modules.rbac.authz.is_designated_super_admin_role`, `RbacRepository.is_active_staff_super_admin`,
  `RbacRepository.app_super_admin_access`, and calls only `AuthzService.app_super_admin_access(user_id, app)` from the
  token path. If Ming changes USR-38's contract at its gate (names, Q1, Q3), this plan is updated before IMPLEMENT.
  `apps.super_admin_role_id` and ADR-0030 exist only after USR-38 merges.
- **USR-40 (parallel) reuses `org_of_app_clause(app_id: uuid.UUID) -> ColumnElement[bool]`** exactly as defined in
  "Data model" (module-level in `app/modules/rbac/repository.py`, correlated on `Organization.id`, includes
  `Organization.status == 'active'`). Whichever ticket merges second rebases on `rbac/repository.py`, `authz.py`,
  `docs/spec-v1.md` (OQ89 tick), `docs/adr/0030-…` (§7 vs §8) and `docs/consumers.md` (picker `access` field vs token
  claim).
- **Hotspots:** `app/modules/rbac/repository.py` and `authz.py` are also edited by USR-38 / USR-40 (this batch) and
  planned by USR-25 / USR-36 / USR-37 (BACKLOG); `tokens/service.py` and `auth/router.py` have no other open ticket.
  No ticket in `IN_PROGRESS` / `REVIEW` / `DONE` touches these files (board checked; ADM-007 is deploy-only).
- The per-account bucket is in-process like every limiter (ADR-0002); with one uvicorn process that is exact.
- `AppContextDep` already refuses disabled apps (`tests/test_auth_token.py:649`); `app_super_admin_access` re-checks
  app status anyway.
- Holder disabled / unverified: refused earlier by the existing session and `email_unverified` checks (:113-117), and
  again by `is_active_staff_super_admin`.
- No `sid` on consumer tokens: logout does not end a super-admin token before `exp` (architect note); documented, no
  deny-list (spec out of scope).
