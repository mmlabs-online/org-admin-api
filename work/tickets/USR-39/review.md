## Attempt 1 — 2026-11-26
Verdict: PASS

Reviewed: implementation commit `86fa5db` (diff vs `4bc1c61`), branch `claude/festive-volta-rikup7`.
Risk tier (`python3 -I .github/scripts/risk_tier.py --base 4bc1c61`): **high**. Ming merges; no agent merge.

### Checks (run by reviewer)
- `uv run ruff check app tests migrations`: All checks passed (exit 0).
- `uv run ruff format --check app tests migrations`: 443 files already formatted (exit 0).
- `uv run mypy app`: Success, no issues found in 216 source files (exit 0).
- `uv run pytest -q` (full): 4367 passed, 2 errors, 2 warnings in 863 s. Both errors were in
  `tests/test_admin_cors.py::test_no_allow_credentials_on_any_bearer_route[…]`, at fixture setup, with
  `UndefinedTableError: relation "apps" does not exist`. That is another agent resetting the shared test DB
  schema, not this diff. Re-run: `tests/test_admin_cors.py`, `test_auth_token_super_admin.py`,
  `test_rbac_org_of_app.py`, `test_config.py`, `test_tokens_service.py`, `test_auth_token.py`,
  `test_route_auth_classes.py`, `test_audit_coverage.py`: **767 passed**, 0 failed.
- `uv run alembic check` (local dev DB `org_admin`): No new upgrade operations detected. The diff adds no migration
  and no model change.
- My results match impl-log (ruff, format, mypy, alembic identical; impl-log reports 4369 passed, and my 4367 + 2
  interference errors add up to the same total).

### Acceptance criteria
- [x] **AC1: holder mints a token for a non-member org.** `tests/test_auth_token_super_admin.py:106-138`
  `test_holder_non_member_org_claim_snapshot` checks the signature with `verify_reference`. The key set is exactly
  `{iss,aud,sub,email,email_verified,roles,org,org_roles,ent,org_access,iat,exp,jti}`, so there is no `sid` and no
  `name`. The holder has no display name, which the impl-log records. The test also asserts `org_roles == []`,
  `org_access == "super_admin"`, `roles == ["orgapp:super-admin"]` (the holder holds `admin:super-admin` and
  `thirdapp:x`, and neither leaks), and `exp - iat == 3600` and `expires_in == 3600` with access TTL 43200.
  `ent` is covered at :141-161 (entitled, and `null` when the org belongs to the app only through an assignment).
  Code: `app/modules/tokens/service.py:149-176`, with `org_access` set only when `super_admin_org` (:171-172).
- [x] **AC2: member path wins.** `:164-175` `test_holder_member_path_wins` asserts `org_roles` non-empty, no
  `org_access`, `expires_in == 43200`, and no audit row. Code: the `else` branch at `service.py:167-168`.
- [x] **AC3: ordinary account, and unknown, suspended or not-of-the-app org, give `org_access_denied`.**
  - `:178-237` covers unknown, suspended, other-app, expired-assignment-only and disabled-entitlement-only orgs.
  - `:240-273` `test_denied_bodies_byte_identical` covers 7 scenarios, each asserting `resp.content == DENIED`
    (exact bytes) and no audit row.
  - The existing `tests/test_auth_token.py` passes.
  - Every refusal goes through one `_denied()` (`service.py:77-79`). The org lookup runs only after the holder
    check (`:156-159`), so non-holders get no timing oracle.
- [x] **AC4: each revocation source blocks the next mint.** `:289-324` `test_revocation_blocks_next_mint` is
  parametrized over: assignment revoked, assignment expired, designation cleared, `admin:super-admin` revoked,
  `admin:super-admin` expired. Each case mints 200, mutates the data, then gets 403 with the identical bytes, and
  the audit row count stays 1. The mint calls the USR-38 predicate on every request with plain reads
  (`app/modules/rbac/authz.py:76-90`, `repository.py:872-882`).
- [x] **AC5a: exactly one audit row per super-admin mint, none for normal mints.**
  - `:327-349` checks action, outcome, actor, target, `app_key`, UA, exact `changes` keys
    `{jti, account_session_id, role_id, expires_at}`, `jti` equal to the token's `jti`, and `expires_at` equal to
    `exp`.
  - `:352-369` (member and plain mints) and `:372-379` (denied mint) write no row.
  - `:382-390` finds no email and no token in the row.
  - `:490-506` (strict client): a failed audit write fails the request and leaves no row.
  - None of the keys match `_FORBIDDEN_KEY` (`app/modules/audit/service.py:28`, `hash|token|password|cookie|email|secret`).
  - Code: `service.py:199-214`, written in the request transaction (R4).
- [x] **AC5b: no other app's role in the token.** `:393-404` asserts all `roles` start with `orgapp:`, no `admin:`
  anywhere in the claims, and no `sid`. The prefix filter is `authz.py:49-52`.
- [x] **AC6: config.**
  - `app/core/config.py:165-166` adds `super_admin_org_token_ttl_seconds: int | None`, range 60..43200.
  - `:211-215` adds a property that returns `min(3600, access_token_ttl_seconds)` when the key is unset.
  - `:287-294` adds a validator: an explicit value above the access TTL raises an error that names both keys and
    does not echo the value.
  - Tests: `tests/test_config.py:193-222` covers default 900→900 and 43200→3600, explicit 1800, above-access
    rejected (and `"901"` is not in the error text), bounds 59 and 43201, and rate limit 0 rejected.
  - `.env.example:93-95` and `:127-129` list the 3 keys without values. The existing `.env.example` tests pass.
- [x] **Per-account limit counted only after the holder and org checks.** `service.py:156-163`: `limiter.hit` runs
  only after both checks pass. Tests:
  - `:407-418`: the second mint gets 429 `rate_limited` with `retry-after`, and only 1 audit row exists.
  - `:421-433`: member mints do not count.
  - `:436-445`: a non-holder never sees 429.
- [x] **`expires_in` comes from the TTL used.** `service.py:182` sets `ttl` once and uses it for `exp` (:195), the
  audit `expires_at` (:212) and `expires_in` (:226). Tests: explicit 600 (:448-458), and capped at 900 locally
  (:461-467).
- [x] **`docs/consumers.md` rule.** The claim list adds `org_access` (absent otherwise). The new section "The single
  exception" (`docs/consumers.md:629-641`) states, word for word, the plan's Approach step 7 rule, including all
  conditions, "unknown = deny" and "never infer access from `[]`". The follow-up line adds the `mmlabs-auth`
  `org_access` accessor.
- [x] **OQ89 predicate contract for USR-40.** `app/modules/rbac/repository.py:44-74`:
  `org_of_app_clause(app_id: uuid.UUID) -> ColumnElement[bool]` is module level, with the plan's name, signature and
  semantics. I compiled the SQL: both `EXISTS` subqueries correlate on `organizations.id`, and the clause includes
  `organizations.status = 'active'`, assignments that have not expired (`now()`), and entitlements with
  `enabled IS true`. Tests: `tests/test_rbac_org_of_app.py`, 8 cases, including app-scope rows being ignored.
- [x] **The USR-38 contract is used, not rebuilt.** The mint calls only `AuthzService.app_super_admin_access(user.id, app)`.
- [x] **Route auth class unchanged (R3).** `tests/test_route_auth_classes.py` is unedited and passes.
  `AuditContextDep` (`app/modules/audit/deps.py`) only reads the request.

Ming verification after merge (not mine to verify; carry these into the PR):
- In production, a simap holder mints a token for a non-member org. Check `org_access: "super_admin"`,
  `org_roles: []`, `expires_in` = 3600, and one `token.super_admin_org_issued` row in the audit view.
- Confirm that audit retention in the deploy env is long enough for incident review.

### Judgement on the impl-log deviation (extra member role in the revocation tests)
Accepted. Without another app role, a holder who loses the designated role has no role in the app left. The check
that already runs first (`service.py:138-145`) then answers `not_member`, also 403, which is today's behaviour for a
non-member and what the spec means by "same `org_access_denied` as today". The mint is blocked in both cases. Adding
a member role makes the test stricter: it reaches the new branch and asserts the byte-identical `org_access_denied`
body. Holders without an extra role are still covered by the `holder_without_admin_super_admin` and
`app_without_designation` scenarios of the byte-identical test, where the designated role keeps them app members.

### Scope
Every changed file is in the plan's Files table. `test_route_auth_classes.py`, `app/core/rate_limit.py`,
`app/core/errors.py`, `app/db/**`, migrations and `organizations/**` are untouched. No out-of-plan file. Leaving the
lag notes in `docs/consumers.md` unchanged is justified in the impl-log: they state upper bounds that still hold.

### Issues
None blocking.

#### Issue 1 — 🟡 should-fix — catalogue placement (cosmetic)
- **Where**: `app/modules/audit/actions.py:111`
- **Expected**: `TOKEN_SUPER_ADMIN_ORG_ISSUED` sits in its own spot, for example with a `# USR-39, ADR-0030 §7`
  comment as the plan's Files table says.
- **Actual**: It sits between `USAGE_CONSUMED` and `USAGE_SETTLED`, under the comment
  `# Denied-only: uses themselves are not audit entries (ADR-0022 §9)`. That reads as if this action were denied-only,
  when it is the opposite (a success row).
- **Fix instruction**: Move the line after `USAGE_REFUNDED`, with its own comment. This does not affect behaviour,
  and it can go in with the PR or a later solo change.

### Rules (CLAUDE.md)
No violations found.
- R1: branch logic is in `TokenService`, SQL in `RbacRepository`, and the router only wires.
- R3: class 2 unchanged.
- R4: the audit row is in the request transaction, and a failure aborts the request.
- R5: the new env keys are in `.env.example` without values.
- R6: only existing codes are used.
- R8: tests cover the happy path, every refusal, 429, and the config checks.
- R9: one additive claim.
- R11: no email or token in logs or audit (`:470-487`).

### Blockers from review agents
None — appsec-engineer `security-review.md` Attempt 1: PASS, no 🔴/🟡 (four 🟢 notes, two follow-ups).

### Nits (do not block)
- `service.py:199`: the guard `app.super_admin_role_id is not None` exists for mypy. If it were ever False on this
  path, a token would leave without its audit row. `app_super_admin_access` makes that unreachable today. A
  `raise`/`assert` would fail closed instead.
- `docs/adr/0030-app-super-admin-role.md:4`: the status line is now one very long line with a short wrap after it.
  Re-wrap it.
- `tests/test_auth_token_super_admin.py:182-183` (`test_holder_unknown_org_denied`) asserts the code only. The
  byte-level check for this case is in the parametrized test, so it is redundant but fine.
- No test shows that a holder refused by the org check does not use up the per-account limit. The code order
  (`service.py:158-161`) makes this clear.

### Follow-ups (out of scope)
- `mmlabs-auth`: an `org_access` accessor, and `require_org_permission` honouring the exception. simap SIM-043
  consumer changes. Both must be released before simap relies on the claim.
- Recent re-authentication (`account_sessions.authenticated_at` + max-age for super-admin mints), together with staff
  MFA (OQ3), per plan Q2.
- USR-40: reuse `org_of_app_clause(app.id)` in the picker list query (check EXPLAIN for the list form), tick the
  picker half of OQ84, and rebase `rbac/repository.py`, `authz.py`, ADR-0030 §8 and `docs/consumers.md`.
