## Attempt 1 — 2026-11-17

### Files touched
| File | Required because |
|---|---|
| `migrations/versions/20261117_c5e91d3a7b24_usr06b_org_invitations.py` | AC3, AC5, AC13 (R2): `org_invitations` table; `email_send_log` CHECK widened (NOT VALID + VALIDATE); downgrade drops invite rows |
| `app/modules/invitations/{__init__,models,schemas,repository,rules,service,router,errors}.py` | new domain module (AC1-AC12) |
| `app/api/router.py`, `app/db/models.py` | mount the router; register the model (R2) |
| `app/modules/rbac/repository.py`, `authz.py` | org-exact `org_permission_keys` / `org_permissions` (AC1, AC2, AC8) |
| `app/modules/rbac/actor.py` | `SYSTEM_ORG_INVITE` (AC6) |
| `app/modules/audit/actions.py` | `INVITATION` target, three `organization.invitation_*` actions (AC11) |
| `app/modules/auth/models.py` | `EmailSendLog` CHECK text incl. `invite` (AC13) |
| `app/modules/auth/email_throttle.py`, `repository.py`, `email_verification.py` | 70/40/10 shares, `INVITE_DAILY_LIMIT`, `daily_limit` param, `count_purpose_24h`, invites in the global cap (AC5, A6) |
| `app/modules/email/messages.py` | invitation mail copy (AC3, A9) |
| `app/core/rate_limit.py`, `app/core/config.py`, `.env.example` | scopes, TTL and caps from env (R5) |
| `docs/adr/0014-org-invitations.md` (new), `0007`, `0013`, `docs/identity-architecture.md`, `docs/consumers.md` | docs per plan |

### Tests added / changed
- `tests/test_invitations_create.py` (32 tests): happy 201, hashes only, link origin and fragment, supersede, uniform 403 for unknown/suspended/non-member org, org-exact permission cases (app scope only, other org), role rules (beyond inviter, owner, is_admin, other app, unknown, admin app, org-less app), S1 guard variants, no oracle (known/unknown/capped target, 3 per 24 h), per-inviter / per-org / rate / global caps, audit.
- `tests/test_invitations_accept.py` (20 tests): happy join + grant, `/auth/token` with `org_id`, existing member, unverified, other verified account then right account, case-insensitive match, identical 422 across five token states, bad account, 429, re-checks (inviter role revoked, admin perm gained, perm the inviter lacks, org suspended, inviter disabled), S1 guard widened and closed app (not consumed), role already held, audit rows, refusals write no audit, concurrent accepts (committed data, two real connections).
- `tests/test_invitations_rules.py`, `test_invitations_schema.py`, `test_invitations_migration.py` (new).
- `tests/test_email_throttle.py` (table for 70/40/10, invite rows, custom daily limit), `test_rbac_authz.py` (4 org-permission tests), `test_route_auth_classes.py` (COOKIE), `test_audit_coverage.py` (`IDENTITY_MULTI_ROW_AUDITED`).
- `tests/test_password_reset_api.py::test_verify_share_exhausted_reset_still_sent`: verify share at cap 5 is now 3 (was 4); docstring 50 % -> 40 %.

### Checks (actual output summary)
- make check (`env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`): ruff, format and mypy clean (115 source files); pytest `1939 passed, 1 warning in 171.04s`.
- alembic: `upgrade head`, `check` ("No new upgrade operations detected."), `downgrade -1`, `upgrade head`, `check` clean on the dev database; the test session also builds the schema from base.

### Out-of-plan files
- `tests/invitation_factories.py` (new): shared setup for the two endpoint test files.
- `tests/test_password_reset_api.py`: the 70/40/10 shares change an existing expectation (see above).
- `docs/deploy.md`: the log-reason table named the old 80 % / 50 % shares; added the invitation log line.

### Follow-ups (noticed, not done)
- USR-06c (S1: scope marker or `org_roles` claim, `build_default_role` skipping org-level permissions, `set_default_role_if_null` `is_distinct_from(org_owner_role_id)`); retention purge of `org_invitations`; list/revoke and member management; `AuthOrgCreate.name` safe-name validation; CLAUDE.md R3 class list wording (A14).
- `rules.py` imports the private `_CONTROL_CHARS` from `audit/service.py` (plan A9 says reuse); promoting it to a public name is a small separate change.
- The concurrency test commits `audit_log` rows (append-only, cleared by the session-start downgrade, like the login concurrency tests), so the invitation tests filter audit rows by org or actor.

### Scope self-check
- Could the diff be smaller? No: every file maps to the plan list; mail copy and docs are the minimum.
- Diff size: see `git diff --shortstat` of the commits (production about 700 new lines incl. migration, the rest tests and docs).

## Should-fix (after PASS review; security-review S-1, S-2, N-2, N-4, N-6, N-7, N-8)

Files touched:
- `app/modules/invitations/service.py`: `_supersede` helper; the per-target silent branch now supersedes this org's own pending invitations (audit `invitation_revoked`, reason `superseded`) before the silent 201 (S-1); the global cap / invite share raises `DailyCapReachedError(DAILY_CAP_RETRY_SECONDS)` (429 `rate_limited`, `Retry-After`) instead of a silent 201 (S-2); `hmac.compare_digest` for the email match (N-8).
- `app/modules/invitations/repository.py`: comment on `add` that the app lock prevents the partial-unique race (N-7).
- `app/core/config.py`: `invitation_max_per_inviter_per_day` default 20 -> 5 (S-2).
- `migrations/versions/20261117_c5e91d3a7b24_usr06b_org_invitations.py`: docstring lock wording only (N-6).
- `docs/adr/0014-org-invitations.md`, `docs/consumers.md`, `docs/deploy.md`: S-1/S-2 behaviour, owner-superset precondition (N-2), same-org supersede by any `org:invite` member (N-4).
- Tests: `tests/test_invitations_create.py` (new `test_target_limited_reinvite_supersedes_and_audits`, global cap test now expects 429 + Retry-After, target-capped test expects the supersede audit row), `tests/test_invitations_accept.py` (owner token gives 422 `token_invalid` after a throttled viewer re-invite).

Checks: `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`: 1941 passed (ruff, format, mypy clean). One earlier full run had a flaky `test_cli_audit_purge::test_purge_batches_share_cutoff` that passes alone and on rerun.

Out-of-plan files: none beyond the listed should-fix items. Follow-ups: N-1, N-3, N-5 unchanged.
- R-1: `service.py` create now checks the global cap (`daily_cap_refusal` -> 429) before the per-target limit (supersede + silent 201); lock order stays email advisory lock then `lock_global`. Test `test_exhausted_cap_answers_429_even_for_target_limited_address` (`email_daily_cap=5`). `make check`: 1942 passed (the flaky `test_cli_audit_purge::test_purge_batches_share_cutoff` failed on two earlier full runs, passes alone and on rerun).
