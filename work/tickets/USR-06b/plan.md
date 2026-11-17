Status: READY

# Plan — USR-06b: Org invitations (create, accept by verified email)

> The owner answered B1–B4 with "defaults" on 2026-11-17 (ticket Notes). This plan uses those defaults. Every choice the consults left open is recorded under `## Assumptions` (A1–A14). Nothing here needs a spec change. S1 is handled by a data-only, fail-closed guard (A1), not by a new token claim.

## Spec summary
- spec §5.1 note (2026-11-17): "End-user create/list and org-scoped tokens: ADR-0013. Invitations: USR-06b."
- spec §5.8: "**Organizations:** optional per app (`org_mode`); one `organizations` table owned by this Identity module (D10, D15, USR-06)." and "**Email flows:** … one-time, expiring links, sent through Gmail SMTP behind an `EmailSender` interface; resend at most 1/min and 5/day per email (D3)." and "Unverified users … must verify their email before joining any app or getting an app token."
- identity-architecture.md D10: "Org-scoped apps: join gives an app-scope role (e.g. with `calechip:org:create`); org roles from creating an org (owner) **or invitation**; token `roles` = app-scope + selected-org roles."
- identity-architecture.md §4 (line 157): "`POST /api/v1/auth/orgs/{id}/invitations` `{email, role}` · `POST /api/v1/auth/invitations/{token}/accept` | cookie | invite / accept into an org | USR-06b". B3 changes accept to a body token, so §4 gets updated (doc follows the owner's decision).
- ADR-0013 line 14: "A later `<app>:org:invite` (USR-06b)" is checked in-org.
- CLAUDE.md R3 (cookie class), R4, R11 ("reset and verification tokens stored only as hashes … never log … full emails"; "**Verified email** is required to join an app or get any app token").
- Ticket AC4, AC6 (invitation part), AC7 (invitation part); owner defaults B1–B4.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | (AC4, B2) `POST /api/v1/auth/orgs/{org_id}/invitations` (cookie class) creates an invitation only if the caller holds `<app>:org:invite` **in that org**, checked org-exact (never the app+org union). Unknown, suspended and not-a-member orgs all get one 403 `org_access_denied` (`details={}`). A member without the permission gets 403 `org_invite_forbidden`. | `tests/test_invitations_create.py::test_create_happy_201`, `::test_create_unknown_org_403_same_as_non_member`, `::test_create_suspended_org_403`, `::test_create_member_without_invite_perm_403`, `::test_invite_perm_at_app_scope_only_does_not_count`, `::test_invite_perm_in_org_a_does_not_allow_org_b` |
| 2 | (AC4/AC7, B2) Invitable role: a role key of the calling app (unknown or other app's key → 422 `unknown_role`). Refused with 403 `invite_role_forbidden` if the role holds any `is_admin` permission, or if any of its permissions is not `permits(inviter_org_exact_permissions, key)`. The admin app is refused before anything else (403 `origin_not_allowed` via `MailLinkAppContextDep`). | `::test_role_beyond_inviter_perms_403`, `::test_member_inviter_cannot_invite_owner_role`, `::test_subset_is_org_exact_app_scope_perms_do_not_count`, `::test_is_admin_role_403`, `::test_other_app_role_key_422`, `::test_unknown_role_422`, `::test_admin_app_context_403` |
| 3 | (AC4, B4) Storage and token: only `SHA-256(token)` and `email_hmac` are stored, no email column. The token is `secrets.token_urlsafe(32)`. The link is `{validated Origin}{INVITE_PATH}#token=…`. `expires_at = now() + INVITATION_TTL_HOURS` (default 168, i.e. 7 days) on the DB clock. Re-inviting the same `(org, app, email)` revokes the previous pending invitation (audited `organization.invitation_revoked`, reason `superseded`). | `::test_row_stores_hashes_only` (no column holds the email; `token_hash == sha256(link token)`), `::test_link_uses_request_origin_and_fragment` (fake dispatcher captures the body), `::test_reinvite_supersedes_previous`; `tests/test_invitations_schema.py` |
| 4 | (AC7 no oracle, B4) The create response is the same `201 {id, org_id, role, expires_at}` whether the email has an account, is already a member, or hit the per-target or Gmail cap. The create path never reads `users` by email. | `::test_response_same_for_known_and_unknown_email`, `::test_response_same_when_target_capped` (no mail queued, no new row, same body keys and status), `::test_create_does_not_query_users_by_email` (grep-level review plus no `get_by_email*` call in `app/modules/invitations/`) |
| 5 | (B4) Limits: per IP `invitation_create` and `invitation_accept` → 429. Per inviter ≤ `INVITATION_MAX_PER_INVITER_PER_DAY` (20), per org ≤ `INVITATION_MAX_PENDING_PER_ORG` (50) pending → 403 `invitation_limit_reached`. Per target email: 1/min and 3/24 h across all orgs (send log purpose `invite`) → silent. The Gmail daily cap gets a new `invite` share of 10 %, and verify keeps ≥ 50 % and reset keeps ≥ 20 % (A6). | `::test_per_inviter_cap_403`, `::test_per_org_pending_cap_403`, `::test_create_rate_limited_429`; `tests/test_email_throttle.py::test_cap_refusal` (extended table incl. invite rows), `::test_invite_share_cannot_take_reset_floor`; `tests/test_invitations_create.py::test_global_cap_counts_invites` |
| 6 | (AC4, B1, B3) `POST /api/v1/auth/invitations/accept {account_id, token}` (cookie class). The account must be active and verified; unverified → 403 `email_unverified`, checked before any token lookup, invitation not consumed. A token that is unknown, used, expired, revoked/superseded, or belongs to another app gets one 422 `token_invalid` (`details={}`, same message). A different verified account gets 403 `invitation_email_mismatch` and the invitation is **not** consumed. On success the invitee holds the invited role in the org (and joins the app with the default role if they had no app-scope role). `/auth/token` with `org_id` then works. Response 200 `{org_id, roles}`. | `tests/test_invitations_accept.py::test_accept_happy_joins_and_grants`, `::test_accept_then_token_with_org_id`, `::test_accept_existing_member_no_second_join`, `::test_accept_unverified_403_not_consumed`, `::test_accept_other_verified_account_403_not_consumed_then_right_account_ok`, `::test_replayed_expired_superseded_unknown_other_app_identical_422` (parametrized, compares full bodies) |
| 7 | (AC4 single-use, AC7 concurrency) Two concurrent accepts of one token → exactly one 200, the other 422 `token_invalid`; exactly one org grant row. | `::test_concurrent_accepts_one_succeeds` (two sessions / two clients, `asyncio.gather`, pattern of `tests/test_login_concurrency.py`) |
| 8 | (AC4, security F3/F9) Accept re-checks the current state under the app lock: org `active`; role still in the app and admin-free; inviter still exists, active, and still holds org-exact `<app>:org:invite` plus a superset of the role's permissions. If any fails → 422 `token_invalid`, not consumed. | `::test_accept_after_inviter_role_revoked_422`, `::test_accept_after_role_gains_admin_perm_422`, `::test_accept_after_org_suspended_422`, `::test_accept_after_inviter_disabled_422` |
| 9 | (A1, S1 guard) Invite and accept work only if: app `org_mode = required`, `join_policy = open`, `default_role_id` and `org_owner_role_id` set, and the default role and owner role share no permission except `<app>:org:create`. Otherwise create → 403 `org_invite_unavailable`, and accept → 403 `org_invite_unavailable` with the invitation not consumed. `org_mode = none` → 422 `org_not_supported` on create. | `::test_create_unavailable_when_default_overlaps_owner`, `::test_create_unavailable_when_join_not_open`, `::test_create_unavailable_owner_role_unset`, `::test_create_org_less_app_422`; `test_invitations_accept.py::test_accept_unavailable_when_default_widened_not_consumed`, `::test_accept_closed_app_refused_not_consumed`; `tests/test_invitations_rules.py::test_roles_overlap_*` (pure function) |
| 10 | (A3) Accept when the invitee already holds the role in that org: the invitation is consumed, response 200, audit `invitation_accepted` with `granted: false`, and no `role_assignment.granted` row. | `::test_accept_role_already_held_consumes_and_audits` |
| 11 | (AC6) Same transaction as the change: create writes `organization.invitation_created` (actor `EndUser(inviter)`, target `invitation`, `app_key`, `org_id`, `changes={role_key, expires_at}`), plus `organization.invitation_revoked` per superseded row. Accept writes `organization.invitation_accepted` (`{role_key, invited_by, granted}`), `role_assignment.granted` (`role_key`, `org_id`) when granted, and `app.joined` when B1 joined. No email, HMAC or token in `changes`. Refusals write nothing. | `::test_create_audit_row`, `::test_accept_audit_rows`, `::test_refusals_write_no_audit`, `::test_audit_changes_have_no_email_or_token` (assert no `@`, no token, no hmac hex in any `changes`); `tests/test_audit_coverage.py` with both routes in `IDENTITY_MULTI_ROW_AUDITED` |
| 12 | (R3) Both routes are cookie class: in `COOKIE`; 400 without `X-MM-App`; 401 without cookie; unregistered Origin refused; no `x-permission`; `Cache-Control: no-store`. | `tests/test_route_auth_classes.py` (routes added to `COOKIE`; `_fill` already substitutes `{org_id}`) |
| 13 | (R2) Migration: new `org_invitations` table, and `email_send_log` CHECK widened to `('verify','reset','invite')`. `downgrade()` works. `alembic check` is clean. The CHECK texts in models and migration match. | `tests/test_invitations_schema.py::test_send_log_purpose_check_text`, `::test_invitations_constraints` (via `pg_get_constraintdef`), `tests/test_invitations_migration.py::test_upgrade_downgrade_roundtrip_drops_invite_send_rows`; CI `alembic check` |
| 14 | (R8) `make check` green | `make check` |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `migrations/versions/20261117_<rev>_usr06b_org_invitations.py` | new revision, `down_revision = "b4d82f6a1c93"` (verify with `alembic heads`) | AC3, AC5, AC13 (R2) |
| `app/modules/invitations/__init__.py` (new) | package | new domain module (reference shape) |
| `app/modules/invitations/models.py` (new) | `OrgInvitation` model (see Data model) | AC3 |
| `app/modules/invitations/schemas.py` (new) | `InvitationCreate {account_id, email: EmailStr, role: str}`, `InvitationCreated {id, org_id, role, expires_at}`, `InvitationAccept {account_id, token: str (1..256)}`, `InvitationAccepted {org_id, roles}`; all request models `extra="forbid"` | AC1–AC6 |
| `app/modules/invitations/repository.py` (new) | `count_created_by_24h`, `count_pending_for_org`, `supersede_pending` (UPDATE … RETURNING id), `add` (expires on DB clock), `get_pending_for_update(token_hash, app_id)`, `consume(id, user_id) -> bool` (conditional UPDATE), `db_now` | AC3–AC8 (R1: SQL only here) |
| `app/modules/invitations/rules.py` (new) | pure functions: `roles_overlap(app_key, default_keys, owner_keys) -> bool`, `is_subset(role_keys, granted) -> bool` (via `permits`), `safe_org_name(name) -> str \| None` | AC2, AC9, A9 (unit-testable without DB) |
| `app/modules/invitations/service.py` (new) | `InvitationService.create(...)`, `.accept(...)`, `_guard(...)` (S1 guard), `_user(...)` (same checks as `OrgSelfService._user`) | AC1–AC11 (R1) |
| `app/modules/invitations/router.py` (new) | two cookie routes; schedules mail with `BackgroundTasks` after commit (pattern of `auth/router.py` forgot) | AC1, AC6, AC12 (R3) |
| `app/modules/invitations/errors.py` (new) | `OrgInviteForbiddenError` (403 `org_invite_forbidden`), `OrgInviteUnavailableError` (403 `org_invite_unavailable`), `InviteRoleForbiddenError` (403 `invite_role_forbidden`), `InvitationLimitReachedError` (403 `invitation_limit_reached`), `InvitationEmailMismatchError` (403 `invitation_email_mismatch`), `UnknownRoleError` (422 `unknown_role`, `details=[{"field":"role","reason":"unknown_role"}]`) | R6 |
| `app/api/router.py` | include `invitations.router` | AC1, AC6 |
| `app/db/models.py` | import and export `OrgInvitation` | R2 |
| `app/modules/rbac/repository.py` | `org_permission_keys(user_id, app_key, org_id)`: org-exact (`RoleAssignment.org_id == :org`), org `active`, unexpired on DB clock, `Permission.app_id == App.id`. Mirrors `org_role_keys`. | AC1, AC2, AC8 (security F2, identity F1) |
| `app/modules/rbac/authz.py` | `AuthzService.org_permissions(...)` passthrough with the app-prefix filter | AC1, AC2 |
| `app/modules/rbac/actor.py` | `SYSTEM_ORG_INVITE = SystemActor("system:org_invite", privileged=False)` | AC6 (grant via `_grant_locked`, like `SYSTEM_ORG_CREATE`) |
| `app/modules/audit/actions.py` | `AuditTarget.INVITATION = "invitation"`; `ORGANIZATION_INVITATION_CREATED/ACCEPTED/REVOKED = "organization.invitation_created/accepted/revoked"` | AC11 (R4) |
| `app/modules/auth/models.py` | `EmailSendLog` CHECK text → `purpose IN ('verify', 'reset', 'invite')` | AC5, AC13 |
| `app/modules/auth/email_throttle.py` | `VERIFY_CAP_PERCENT = 70`, `RESET_CAP_PERCENT = 40`, new `INVITE_CAP_PERCENT = 10`, `INVITE_DAILY_LIMIT = 3`; `cap_refusal(..., invite_24h: int)` with reset used = `total − verify − invite`; `resend_retry_after(..., daily_limit=DAILY_LIMIT)` param; update the docstring invariants | AC5, A6 |
| `app/modules/auth/repository.py` | `EmailSendLogRepository.count_purpose_24h(purpose)` | AC5 (invites counted in the Gmail cap) |
| `app/modules/auth/email_verification.py` | `daily_cap_refusal` adds the invite count (`sends.count_purpose_24h("invite")`) to total and passes `invite_24h` | AC5 |
| `app/modules/email/messages.py` | `INVITE_SUBJECT` (fixed, no org name) and `invitation_message(link, app_name, org_name \| None)` (vi then en, plain text, no inviter email) | AC3, A9 |
| `app/core/rate_limit.py` | scopes `invitation_create`, `invitation_accept` | AC5 (R11) |
| `app/core/config.py`, `.env.example` | `INVITATION_CREATE_RATE_LIMIT` (20), `INVITATION_CREATE_RATE_WINDOW_SECONDS` (3600), `INVITATION_ACCEPT_RATE_LIMIT` (20), `INVITATION_ACCEPT_RATE_WINDOW_SECONDS` (600), `INVITATION_TTL_HOURS` (168), `INVITATION_MAX_PER_INVITER_PER_DAY` (20), `INVITATION_MAX_PENDING_PER_ORG` (50), all `ge=1`; keys listed without values | R5 |
| `tests/test_invitations_create.py` (new) | create endpoint tests | AC1–AC5, AC9, AC11 |
| `tests/test_invitations_accept.py` (new) | accept endpoint tests incl. concurrency | AC6–AC11 |
| `tests/test_invitations_rules.py` (new) | pure-function unit tests | AC2, AC9, A9 |
| `tests/test_invitations_schema.py` (new) | constraints, CHECK texts, partial unique, FK cascades | AC3, AC13 |
| `tests/test_invitations_migration.py` (new) | upgrade/downgrade roundtrip (pattern `tests/test_orgs_migration.py`) | AC13 (R2) |
| `tests/test_email_throttle.py` | update the `cap_refusal` table for 70/40/10 and `invite_24h`; add invite rows | AC5 |
| `tests/test_route_auth_classes.py` | add both routes to `COOKIE` | AC12 (R3) |
| `tests/test_audit_coverage.py` | add both routes to `IDENTITY_MULTI_ROW_AUDITED` | AC11 (R4, 06a S2 pattern) |
| `tests/test_rbac_authz.py` | `org_permission_keys` org-exact, suspended org empty, expired ignored, other-app perms excluded | AC1 |
| `docs/adr/0014-org-invitations.md` (new) | decisions: org-exact checks, subset rule, email-HMAC binding, body token, re-checks at accept, S1 guard + its residual risk, B1 implicit join, cap shares | design record |
| `docs/adr/0007-email-verification-and-delivery.md` | amendment: invite purpose; shares 70/40/10 keep the verify ≥ 50 % and reset ≥ 20 % floors | A6 |
| `docs/adr/0013-org-scoped-identity.md` | replace the `<app>:org:invite` forward reference with a pointer to ADR-0014 | docs follow code |
| `docs/identity-architecture.md` | §4 line 157: accept → `POST /api/v1/auth/invitations/accept {account_id, token}` (B3), create body `{account_id, email, role}`; D10 add "(ADR-0014)" | B3 |
| `docs/consumers.md` | Organizations section: invitations flow, `/accept-invitation` page contract, error table, the S1 guard config requirement (default role ∩ owner role ⊆ `{<app>:org:create}`), invitations use `credentials: "include"`, owner role is invitable and member removal is staff-only | consumers |

The implementer does not edit `ticket.md`, `docs/spec-v1.md`, or `CLAUDE.md`.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| test_invitations_rules › test_roles_overlap_none / _only_org_create_allowed / _shared_perm_overlaps / _empty_sets | A1 guard logic |
| test_invitations_rules › test_is_subset_exact / _wildcard_granted / _missing_key / _empty_role | AC2 |
| test_invitations_rules › test_safe_org_name_plain_ok / _rejects_url / _rejects_at / _rejects_domain_like / _rejects_bidi_and_controls / _rejects_over_100 | A9 |
| test_invitations_create › happy 201 (body keys exactly `{id, org_id, role, expires_at}`, mail queued to the given address, link `origin + /accept-invitation#token=`), stores hashes only, re-invite supersedes (old token → 422 at accept), unknown/suspended/non-member org identical 403 `org_access_denied`, member without `org:invite` 403, invite permission held only at app scope (incl. via the default role) does not count, permission in org A does not allow org B, role beyond inviter 403, member cannot invite owner, subset uses org-exact permissions only, is_admin role 403, unknown/other-app role key 422, admin app 403, org-less app 422, S1 guard variants 403 `org_invite_unavailable`, unverified 403, bad account 401, per-inviter cap 403, per-org pending cap 403, per-target cap silent same 201 (no new row, no mail, no audit), global cap counts invites, same response for known and unknown email, rate limit 429, audit row, refusals write no audit, no email/token/hmac in audit | AC1–AC5, AC9, AC11 |
| test_invitations_accept › happy (joins + grants, 200 `{org_id, roles}` with the invited role), then `/auth/token` with `org_id` works and has the `org` claim, existing app member is not re-joined (no `app.joined`), unverified 403 not consumed, other verified account 403 mismatch not consumed and the right account then succeeds, identical 422 for replayed/expired/superseded/unknown/other-app tokens, concurrent accepts one success, re-checks (inviter role revoked, inviter disabled, role gains admin perm, org suspended) → 422 not consumed, S1 guard widened after invite → 403 not consumed, `join_policy` changed to closed after invite → 403 `org_invite_unavailable` not consumed (the guard runs before join), role already held → consumed with `granted: false`, audit rows for each case, refusal no audit, bad account 401, rate limit 429 | AC6–AC11 |
| test_invitations_schema › token_hash unique and length 32, email_hmac length 32, `expires_at > created_at`, accepted-and-revoked CHECK, partial unique pending `(org_id, app_id, email_hmac)`, org delete cascades invitations, role delete cascades invitations, inviter delete sets NULL, `email_send_log` CHECK text via `pg_get_constraintdef` | AC3, AC13 |
| test_invitations_migration › test_upgrade_downgrade_roundtrip_drops_invite_send_rows | AC13 (R2) |
| test_email_throttle › test_cap_refusal (updated table: verify 70 % = 210/300, reset 40 % = 120/300, invite 10 % = 30/300; each share floors at 1; total cap binds all) / test_invite_share_cannot_take_reset_floor / test_resend_retry_after_custom_daily_limit | AC5, A6 |
| test_rbac_authz › test_org_permission_keys_org_exact / _suspended_org_empty / _expired_ignored / _other_app_excluded | AC1 |
| test_route_auth_classes, test_audit_coverage (existing sweeps with the 2 new routes) | AC11, AC12 |

## Approach
1. **Migration and models** (see `## Data model / migration`). Model `__table_args__` names and the partial-index `postgresql_where` text must match the migration exactly. Alembic does not compare CHECKs, so a schema test covers them.
2. **Org-exact permission read.** `RbacRepository.org_permission_keys` is a copy of `org_role_keys` joined through `RolePermission` to `Permission` (`Permission.app_id == App.id`). `AuthzService.org_permissions` applies the prefix filter. Rule for every decision in this ticket: never call `effective_permissions`/`effective_roles` with an `org_id` (security F2, identity F1).
3. **Pure rules** (`invitations/rules.py`). Wildcards (`<app>:*`) are always `is_admin` (CHECK `ck_permissions_wildcard_is_admin`), so an invitable role, the default role and the owner role never hold one. Overlap is plain set intersection minus `{f"{app}:org:create"}`. Subset is `all(permits(granted, k) for k in role_keys)`, so a staff-granted wildcard on the inviter still counts.
4. **S1 guard `_guard(app)`**. All of these must hold: `org_mode == required`, `join_policy == open`, `default_role_id` and `org_owner_role_id` set and both roles found, default role has no admin permission, and `roles_overlap(...)` is false. Otherwise 403 `org_invite_unavailable` and a `logger.warning("org_invite_unavailable app_id=%s reason=%s")`. This fails closed when staff or the USR-05 manifest re-widens `<app>:member`.
5. **Create** (`InvitationService.create(app, device, org_id, data, link_origin, ctx) -> tuple[InvitationCreated, OutgoingEmail | None]`), in order:
   1. `lock_app(app.key)` (ADR-0003 §10). App not active → 403 `origin_not_allowed`. `org_mode != required` → 422 `org_not_supported` (reuse `_org_not_supported` shape).
   2. `_user` (account on this device → 401; active → 401; verified → 403 `email_unverified`).
   3. `org_role_keys(user, app, org_id)` empty → 403 `org_access_denied`, `details={}`.
   4. `perms = org_permissions(user, app, org_id)`. If `not permits(perms, f"{app}:org:invite")` → 403 `org_invite_forbidden`.
   5. `_guard(app)`.
   6. `role = get_role_by_key(app.id, data.role)`. None → 422 `unknown_role`. `role_has_admin_permission` or not `is_subset(role_permission_keys(role), perms)` → 403 `invite_role_forbidden`.
   7. Per-inviter 24 h count ≥ limit, or per-org pending count ≥ limit → 403 `invitation_limit_reached`.
   8. `email_hmac = email_hasher.hmac(normalize_email(str(data.email)))`. Then `sends.lock(email_hmac, "invite")` (serializes every invite for that address across orgs, which also covers the partial-unique race), then `window` + `resend_retry_after(..., daily_limit=INVITE_DAILY_LIMIT)`, then `daily_cap_refusal(..., "invite", cap)`. If any of these limits → **silent**: return `InvitationCreated(id=uuid4(), org_id, role=role.key, expires_at=db_now()+ttl)` and `None` for the mail. Nothing is written or audited, and the log line has the reason and no email.
   9. `supersede_pending(org_id, app.id, email_hmac)` → one `invitation_revoked` audit row per id (`changes={"reason": "superseded"}`).
   10. Insert the row (`token_hash = email_token_hash(token)`, `invited_by = user.id`), `sends.insert(email_hmac, "invite")`, audit `invitation_created`.
   11. Build the `OutgoingEmail(to=str(data.email).strip(), purpose="invite", user_id=user.id)` (inviter id, security F7) with `invitation_message(f"{link_origin}{INVITE_PATH}#token={token}", app.name, safe_org_name(org.name))`. `INVITE_PATH = "/accept-invitation"`.
   12. The router queues it with `background_tasks.add_task(dispatcher.enqueue, email)`, so it runs only after commit.
   
   No `users` lookup by email anywhere in create (security F8).
6. **Accept** (`InvitationService.accept(app, device, data, ctx) -> InvitationAccepted`). Lock order: app → user → invitation row → org (KEY SHARE inside `_grant_locked`). That is the same order as join and org create, so no new deadlocks.
   1. `lock_app(app.key)`, app active.
   2. `_user` (401 / 403 `email_unverified`, before any token lookup).
   3. `row = get_pending_for_update(sha256(token), app.id)` (`accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now()`, `FOR UPDATE`). None → 422 `token_invalid` (reuse `auth.errors.TokenInvalidError`, message "Token is invalid or expired", `details={}`).
   4. `row.email_hmac != hmac(normalize_email(user.email))` → 403 `invitation_email_mismatch` (no details), not consumed.
   5. `_guard(app)` → 403 `org_invite_unavailable`, not consumed.
   6. Re-checks, all → 422 `token_invalid`, not consumed:
      - role still in the app and admin-free;
      - `invited_by` not NULL, inviter user exists and is active;
      - `org_permissions(inviter, app, row.org_id)` permits `org:invite` and is a superset of the role's permissions (this also covers org `active`: the read is empty for a suspended org).
   7. B1: if `effective_roles(user, app)` (app scope) is empty → `join_default_role(rbac, app)`, whose errors propagate with no consume (the guard already guarantees open + default set).
   8. `consume(row.id, user.id)` (conditional UPDATE … `RETURNING id`). False → 422 `token_invalid`.
   9. If joining: `_grant_locked(app, default_role, user.id, None, None, actor=SYSTEM_JOIN)` + `app.joined` audit (same shape as `JoinService`).
   10. Org role: `find_assignment(user, role, org)` active (DB clock) → `granted=False`. Otherwise `_grant_locked(app, role, user.id, row.org_id, None, actor=SYSTEM_ORG_INVITE)` + `role_assignment.granted` audit (`role_key`, `org_id` only, as in 06a).
   11. Audit `invitation_accepted` (`role_key`, `invited_by`, `granted`).
   12. Return `{org_id, roles: org_role_keys(user, app, org_id)}`.
   
   Any error after step 8 rolls back the request transaction, so the token stays pending.
7. **Routers (R1, R3).** Copy the `organizations/auth_router.py` shape:
   - create: `dependencies=[rate_limit("invitation_create")]`, `app: AppContextDep`, `link_origin: Annotated[str, Depends(get_link_origin)]` (refuses `admin`), `_cookie: DeviceCookieDoc`, body, `device: DeviceDep`, `ctx: AuditContextDep`, `background_tasks`, dispatcher, email hasher;
   - accept: `rate_limit("invitation_accept")`, `AppContextDep`, cookie doc, body, device, ctx.
   
   Both: `_finish` (rotated cookie echo, `Cache-Control: no-store`), documented responses (create 201/400/401/403/422/429; accept 200/400/401/403/422/429), no `require_permission`/`x-permission`. The token appears only in the body. Never log it; logs carry UUIDs and the app key only.
8. **Email cap (A6).** `daily_cap_refusal` adds `count_purpose_24h("invite")` to the token-based total (invite sends are not in `email_tokens`, DB F2). Every invite send-log row is a real send, because rows are inserted only when mail is queued. `cap_refusal` gives each purpose its own share. Hold `lock_global` as today. Existing verify/reset callers keep their behaviour apart from the new shares.
9. **Docs** as listed. ADR-0014 records the residual S1 risk and the follow-ups.
10. Rules:
    - R1: routers HTTP only; the service raises `AppError` subclasses; SQL only in repositories.
    - R2: migration with downgrade; `alembic check`.
    - R3: cookie class.
    - R4: same-transaction audit rows.
    - R5: limits, TTL and rates from env.
    - R6: only 201/200/401/403/422 plus the shared 400/429.
    - R7: no member management, no revoke/list endpoint, no `org_roles` claim, no `AuthOrgCreate` change.
    - R8: tests above.
    - R9: `/api/v1/auth/...`, UUIDs, timestamptz, additive only.
    - R11: hashes only, verified email required, no emails/tokens in logs/audit/errors, Origin + `X-MM-App`.

## Specialist input used
- **Adopted, all four consults:** org-exact permission read `org_permission_keys` for both the invite check and the subset rule, never the union (security F2/R2, identity F1/R-2, DB subset SQL, API). `email_hmac` only, `token_hash` SHA-256, body token, `#token=` link from `get_link_origin`. Atomic conditional consume, wrong account does not consume. One uniform invalid-token error. Re-checks at accept. B1 implicit join, refusing (not consuming) for closed/unavailable apps. No `users` lookup on create. Uniform 201. Purpose CHECK migration with `NOT VALID` + `VALIDATE` and a downgrade that deletes invite rows (DB). Audit without email/hmac/token. COOKIE sweep and `IDENTITY_MULTI_ROW_AUDITED` registration. New non-privileged system actor `SYSTEM_ORG_INVITE`.
- **Adopted, security:** R1 data-only S1 guard (403 `org_invite_unavailable`, checked at create and accept). F3, org `active` checked at accept (via the org-exact inviter read). F7, `OutgoingEmail.user_id` = inviter id. R4 step 8, already-held role consumes + audits. R7 fixed subject and safe org-name rendering. R5 FKs.
- **Adopted, DB:** composite FK `(role_id, app_id)` → `roles(id, app_id)` CASCADE. Org FK CASCADE. Timestamps instead of a status enum. Partial unique pending index plus supersede-first. Constraint-text test. Advisory lock per email for create.
- **Adopted, API:** endpoint shapes and schema names, `INVITE_PATH = "/accept-invitation"`, response 200 for accept, `Cache-Control: no-store`, the consumers.md page contract (fragment cleared with `history.replaceState`, `Referrer-Policy: no-referrer`).
- **Adopted, identity:** partial unique includes `app_id`. Unverified checked before token lookup. Role addressed by key within the calling app only.
- **Rejected:**
  - Additive `org_roles` claim inside 06b (identity R-1). It needs a spec §5.8 claims line, and the security guard covers 06b without a spec change. It moves to follow-up USR-06c before USR-05 (A1).
  - Folding the email mismatch into 422 (API). A wrong-account 403 is better UX and leaks only "this live token is not for you" to someone who already holds the link. Ticket AC7 requires uniformity only for replayed/expired/revoked tokens (A4).
  - Not re-checking the inviter at accept (API Q3). That gives no revocation path while v1 has no revoke endpoint (A5).
  - `record_denied` for disallowed roles (security R2). `AuditLogger.record_denied` writes only for `AdminPrincipal`, and end-user refusals write nothing (06a precedent).
  - Counting the `org_invitations` table in the global cap (DB). Send-log `invite` rows are exact sends and need no retention coupling.
  - Retention purge of `org_invitations` (DB). Moved to a follow-up to keep the slice small.
  - Masked `email_hint` (DB Q2). Not needed without a list endpoint.
  - Tightening `AuthOrgCreate.name` (security R7). It changes a 06a schema; safe rendering in the email is enough here (A9).
  - 404 for unknown org (R8 "404" test). That would be an org-existence oracle; the 06a precedent is a uniform 403.

## Assumptions
- **A1. S1 handling.** The data-only, fail-closed guard (security R1) ships in 06b. No new token claim, no spec change.
  - Residual risk, recorded in ADR-0014 and consumers.md: staff-granted app-scope roles carrying org-level permissions still reach every org token.
  - Once USR-05 re-widens `<app>:member`, invitations for that app become unavailable (403) until USR-06c.
  - Follow-up **USR-06c**, before USR-05: a permission scope marker or `org_roles` claim (needs a spec §5.8 line, owner decision), `build_default_role` skipping org-level permissions for `org_mode = required`, and the `set_default_role_if_null` `is_distinct_from(org_owner_role_id)` fix from the 06a re-check.
  - Judgement: this is sufficient for 06b. With the guard, the invitee's app-scope default role holds no permission of the owner role (the definition of "org-level" today) except `org:create`. A narrower invited role is therefore not silently widened in the org token.
- **A2. Owner role invitable.** Yes, per B2 (subset rule only; the owner may invite co-owners). There is no member removal in v1 (staff only). Documented in consumers.md and ADR-0014.
- **A3. Role already held at accept.** Consume, return 200, and write `invitation_accepted` with `granted: false`. A live secret is not left pending.
- **A4. Wrong verified account.** 403 `invitation_email_mismatch`, not consumed. Replayed, expired, revoked/superseded, unknown and other-app tokens get one 422 `token_invalid`.
- **A5. Inviter re-checked at accept.** The inviter must still be active and still pass the org-exact invite + subset checks (implicit revocation). Deleting the inviter (`invited_by` SET NULL) kills the invitation.
- **A6. Email cap shares.** verify 70 %, reset 40 %, invite 10 %, independent per purpose. This keeps the documented floors: verify ≥ 100−40−10 = 50 %, reset ≥ 100−70−10 = 20 %. It lowers today's verify ceiling (80 → 70) and reset ceiling (50 → 40). ADR-0007 is amended.
- **A7. Per-target limits.** 1/min (existing `MIN_INTERVAL`) and 3/24 h (`INVITE_DAILY_LIMIT`), keyed by `email_hmac` across all orgs. These are constants like the existing verify/reset limits, because they are safety invariants. Per-target and global-cap refusals on create are silent: same 201 with a throwaway id, nothing written, no audit. A small timing difference remains and is accepted.
- **A8. Limit values.**
  - Per-inviter and per-org caps answer 403 `invitation_limit_reached`. They are about the caller, not the target, so they are no oracle.
  - Defaults: create 20/h per IP; accept 20/10 min per IP; TTL 168 h; 20 invites per inviter per 24 h; 50 pending per org.
- **A9. Email content.**
  - Fixed subject "Lời mời tham gia tổ chức / You're invited to an organization".
  - The app name is staff data. The org name is included only if `safe_org_name` accepts it: no C0/C1/bidi/format characters (reuse the `_CONTROL_CHARS` set from audit), no `://`, `www.`, `@`, no domain-like token (`\w\.[a-z]{2,}` case-insensitive), ≤ 100 chars. Otherwise the sentence omits the name.
  - Never the inviter's email.
  - `AuthOrgCreate.name` is not changed (R7; follow-up).
- **A10. Org-exact definitions.** "Member of the org" = `org_role_keys` non-empty (06a definition). An invitee may already be a member with another role; they still get the new role.
- **A11. Join on accept.** B1 joins with `SYSTEM_JOIN` and the normal `app.joined` audit, so a joined-by-invite user is indistinguishable from a normal join.
- **A12. Accept for `admin` / org-less apps.** No invitation can exist for them (create refuses), so accept answers 422 `token_invalid` through the `app_id` filter. No special branch.
- **A13. Email normalization.** Same as login/send log (`strip().lower()`). Gmail dots and plus tags are not folded; consumers.md says the invitee must sign in with exactly the invited address. Rotating `IDENTITY_HMAC_KEY` voids pending invitations (runbook note in ADR-0014).
- **A14. CLAUDE.md R3 class list.** R3 names "accounts, token, join, logout, orgs, resend verification". `/auth/invitations/accept` falls under the general definition ("identity endpoints under `/api/v1/auth/*` that act for a signed-in account"). Follow-up: the owner may add "invitations" to the list. The implementer does not edit CLAUDE.md. A9 of USR-06a (`mm_device` naming) still stands.

## Blocking questions
None. The owner gave defaults for B1–B4 on 2026-11-17, and every remaining choice is a non-blocking assumption above.

## Split proposal
None. This is one spec feature (invitations: create + accept), already split out of USR-06. Estimate: about 500–600 lines of production code (new module about 350, migration about 90, throttle/config/docs about 100) plus tests. That is above the ~400-line rough guide. Create is not useful or testable end to end without accept, though, so a further split would leave a dead endpoint. Cut line, if the review loop shows it is too big: docs (consumers.md, ADR-0014) may land in a docs-only follow-up commit on the same branch. Code and tests stay together.

Follow-ups (not in this diff): USR-06c (A1); retention purge of `org_invitations`; list/revoke pending invitations and member management (needs `/idea` + spec line); `AuthOrgCreate.name` safe-name validation; CLAUDE.md R3 list wording (A14); `mmlabs-auth` docs on the guard.

## Data model / migration
One revision `usr06b_org_invitations`, `down_revision = "b4d82f6a1c93"`. Docstring: "expand-only except the `email_send_log` CHECK swap. Downgrade is lossy for invitations and `invite` send-log rows."

**Step 1. Table `org_invitations`** (model `OrgInvitation(UUIDPkMixin, Base)` in `app/modules/invitations/models.py`):

| Column | Type | Null | Default / FK |
|---|---|---|---|
| `id` | uuid | no | PK (mixin) |
| `org_id` | uuid | no | FK `fk_org_invitations_org_id_organizations` → `organizations.id` ON DELETE CASCADE |
| `app_id` | uuid | no | part of the composite FK |
| `role_id` | uuid | no | composite FK `fk_org_invitations_role_same_app` `(role_id, app_id)` → `roles(id, app_id)` ON DELETE CASCADE |
| `email_hmac` | bytea | no | — |
| `token_hash` | bytea | no | UNIQUE `uq_org_invitations_token_hash` |
| `invited_by` | uuid | yes | FK → `users.id` ON DELETE SET NULL |
| `expires_at` | timestamptz | no | set to `now() + ttl` by the repository |
| `accepted_at` | timestamptz | yes | — |
| `accepted_by` | uuid | yes | FK → `users.id` ON DELETE SET NULL |
| `revoked_at` | timestamptz | yes | — |
| `created_at` | timestamptz | no | `now()` |

- CHECKs: `ck_org_invitations_email_hmac_length` `octet_length(email_hmac) = 32`; `ck_org_invitations_token_hash_length` `octet_length(token_hash) = 32`; `ck_org_invitations_expiry_after_creation` `expires_at > created_at`; `ck_org_invitations_not_accepted_and_revoked` `accepted_at IS NULL OR revoked_at IS NULL`.
- Indexes:
  - `uq_org_invitations_pending` UNIQUE `(org_id, app_id, email_hmac) WHERE accepted_at IS NULL AND revoked_at IS NULL` (race backstop; `IntegrityError` → `ConflictError` 409 `conflict`, practically unreachable under the advisory lock);
  - `ix_org_invitations_org_id_created_at` `(org_id, created_at)` (per-org pending count, org FK cascade);
  - `ix_org_invitations_invited_by_created_at` `(invited_by, created_at)` (per-inviter count, SET NULL scan);
  - `ix_org_invitations_role_id_app_id` `(role_id, app_id)` (role/app delete cascade).
  - No index on `accepted_by` (user deletes are rare; noted).

**Step 2. `email_send_log` purpose CHECK.** Drop `ck_email_send_log_purpose_valid`. Re-add it as `purpose IN ('verify', 'reset', 'invite')` with `NOT VALID`, then `VALIDATE CONSTRAINT`. `email_tokens` is unchanged (invitations do not use it).

**Downgrade** (reverse order):
1. Drop the `org_invitations` indexes and table.
2. `DELETE FROM email_send_log WHERE purpose = 'invite'`.
3. Drop the widened CHECK and recreate `purpose IN ('verify', 'reset')`.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/orgs/{org_id}/invitations` | headers `X-MM-App`, `Origin`; cookie `__Host-mm_device`; body `InvitationCreate {account_id: uuid, email: EmailStr, role: str}` (`extra=forbid`) | 201 `InvitationCreated {id: uuid, org_id: uuid, role: str, expires_at: datetime}`. Identical for known/unknown/member/capped targets; never echoes the email or token. | 400 `app_header_required`; 401 `session_invalid`; 403 `origin_not_allowed` (incl. `admin`), `email_unverified`, `org_access_denied`, `org_invite_forbidden`, `org_invite_unavailable`, `invite_role_forbidden`, `invitation_limit_reached`; 422 `validation_error`, `org_not_supported`, `unknown_role`; 429 | cookie class (R3-2); org-exact `<app>:org:invite` checked in the service |
| POST | `/api/v1/auth/invitations/accept` | same headers/cookie; body `InvitationAccept {account_id: uuid, token: str}` (`extra=forbid`) | 200 `InvitationAccepted {org_id: uuid, roles: [str]}` (caller's org-exact role keys) | 400 `app_header_required`; 401 `session_invalid`; 403 `origin_not_allowed`, `email_unverified`, `invitation_email_mismatch`, `org_invite_unavailable`, plus join errors from `join_default_role` (unreachable while the guard holds); 422 `validation_error`, `token_invalid`; 429 | cookie class (R3-2); bound to the invited email |
