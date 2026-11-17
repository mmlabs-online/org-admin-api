Status: READY

# Plan — USR-13: Allow 12-hour access tokens

## Spec summary
Ticket goal (owner, 2026-11-17, from TuDoCuaTui D46): "Access tokens can live 12 hours
(`ACCESS_TOKEN_TTL_SECONDS=43200`), so developers and apps do not have to re-mint a token every 15
minutes." Owner answer: "chọn C". The owner was told that the bound is 3600 in code, that the TTL is
global for every app including admin, and that ban latency becomes 12 h. The softer options
(dev-script auto-refresh; 1 h through config only) were declined. **Do not re-ask any of this.**

Relied-on text that this ticket supersedes (the owner's decision is the authority; spec edits are
tagged "(owner, 2026-11-17)"):
- ADR-0005 §6: "**Lifetime:** `ACCESS_TOKEN_TTL_SECONDS` = 900 (bounds 60–3600). No refresh token, no
  revocation list, no `jti` denylist: a disabled user, a removed role or a logout keeps a live token
  for at most the TTL (accepted under D7)."
- Spec §5.8: "an app asks for its own short-lived JWT for a chosen account".
- Spec §5.10: "**Known limit (no refresh tokens, D7):** access tokens already issued stay valid until
  they expire (~15 min). … Whether that is acceptable for admin tokens: §8 OQ8."
- Spec §8 OQ8: "Is the ~15 min tail of already-issued access tokens after a staff revoke acceptable,
  including `aud=admin` tokens?" The owner's decision answers this: the tail is accepted at 12 h.

**Spec conflict (flagged, resolved in this diff).** Spec §5.8/§5.10/OQ8 and identity-architecture
D7 say "~15 min". CLAUDE.md says the spec wins on scope. The owner decided this change explicitly
on 2026-11-17, so this ticket updates the spec text to match and tags each edit
"(owner, 2026-11-17)". The owner confirms these edits when merging the PR.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | `ACCESS_TOKEN_TTL_SECONDS=43200` is accepted at startup; values above the new bound are rejected | `tests/test_config.py`: `test_access_token_ttl_bounds` (rejects 59, 43201), `test_access_token_ttl_accepted` (accepts 60, 3601, 43200), `test_access_token_ttl_from_env` (env `43200` loads; env `43201` raises `ValidationError` naming the field without echoing `43201`). `app/core/config.py` shows `Field(default=900, ge=60, le=43200)` |
| 2 | An issued token has `exp - iat = 43200` when configured that way, and `expires_in` matches | `tests/test_auth_token.py::test_token_ttl_43200`: `make_client(access_token_ttl_seconds=43200)`, `/token` returns `expires_in == 43200`; `verify_reference` passes; `claims["exp"] - claims["iat"] == 43200`. The existing `test_token_happy_path` still asserts 900 (default unchanged) |
| 2a | The verifier needs no change: no age cap, leeway unchanged | `tests/test_access_token.py::test_long_lived_token_accepted` (iat = now-43000, exp = now+200, accepted); the existing `test_expired_rejected` (exp = now-31 is `expired`) stays green |
| 2b | Admin revocation stays immediate with long tokens (ADR-0008 §3) | `tests/test_admin_auth.py::test_long_lived_token_role_revoked_403` and `::test_long_lived_token_disabled_401`: a token with iat = now-6 h and exp = now+6 h gets 200, then 403 after the role is deleted / 401 after the user is disabled |
| 3 | ADR-0005, `docs/consumers.md` and `docs/deploy.md` state the new lifetime and its consequences | Read the dated "Amendment — 12-hour access tokens (USR-13, 2026-11-17)" section in ADR-0005; consumers.md and deploy.md as listed under Files. `rg -n "15 min\|15-minute\|about 15 minutes\|900 s\|60-3600\|60–3600" docs .env.example app` returns no hit that describes the access-token lifetime (hits for login backoff "cap 15 min", invitation/email TTLs, the historical ADR-0005 §6 line and the `Retry-After` 3600 are expected) |
| 3a | Stale lifetime statements elsewhere fixed (orchestrator scope, follows from AC3) | ADR-0001, ADR-0007, ADR-0008, ADR-0011, identity-architecture (line 182 and D7), spec §5.8/§5.10/OQ8, `.env.example`, `app/cli.py` `MSG_ADMIN_CLOSED`. Same `rg` check as row 3 |
| 4 | Checks green | `make check` (ruff, format, mypy --strict, pytest) passes; `alembic check` is unaffected (no schema change) |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/core/config.py` | Line 86: `le=3600` → `le=43200`. Add a one-line comment: "ADR-0005 §6 (USR-13): production 43200; above needs a new decision" | AC1 |
| `.env.example` | Line 53 comment: "(default 900, 60-43200; production 43200, ADR-0005 §6)". The key stays without a value | AC1, R5 |
| `app/cli.py` | `MSG_ADMIN_CLOSED`: "note: no new admin tokens can be obtained once this applies; issued tokens last up to ACCESS_TOKEN_TTL_SECONDS (revoke the role or disable the user to cut them)". Keep lines ≤ ruff limit. `tests/test_cli_set_admin_origins.py` imports the constant, so no test edit is needed | Wording no longer implies a short tail (security consult F5) |
| `tests/test_config.py` | Change the parametrize to `[59, 43201]`; add the accept test and the env-load test | AC1, R8 |
| `tests/test_auth_token.py` | Add `test_token_ttl_43200` | AC2 |
| `tests/test_access_token.py` | Add `test_long_lived_token_accepted` | AC2a |
| `tests/test_admin_auth.py` | Add two long-lived-token revocation regression tests | AC2b |
| `docs/adr/0005-access-token-signing.md` | Line 19: replace "Security is equivalent for a 15-minute token" with "Security is equivalent at the allowed lifetimes (up to 12 h, §6 amendment)". At the end of §6 add "*(Amended by USR-13, 2026-11-17: bounds 60–43200, see below.)*". Leave the original §6 text as history. Append the dated amendment section (content under Approach step 3) | AC3 |
| `docs/consumers.md` | Lines 76, 148, 294, 368–369; storage and freshness guidance (Approach step 4) | AC3 |
| `docs/deploy.md` | Line 224 and rotation step 3; a new "Revoking live tokens (incident)" subsection; a TTL-change note; lines 376, 493, 497, 568, 585 (Approach step 5) | AC3 |
| `docs/adr/0001-multi-app-authorization.md` | Lines 42–43: "≤ access-token TTL (`ACCESS_TOKEN_TTL_SECONDS`: default 15 min, 12 h in production since USR-13). There are no refresh tokens (D7): immediate cut-off is only a server-side check (admin, ADR-0008 §3) or an emergency key rotation." This also removes the stale refresh-token clause | Stale statement (AC3a) |
| `docs/adr/0007-email-verification-and-delivery.md` | Line 102: "stay valid ≤ `ACCESS_TOKEN_TTL_SECONDS` (default 15 min; 12 h in production, USR-13)" | AC3a |
| `docs/adr/0008-admin-api-authentication.md` | §7: append "Production runs 12 h (USR-13, ADR-0005 §6 amendment). The reload covers disable, role revoke and assignment expiry; it does not cover logout, password reset, staff session revoke or `--clear`." `--clear` amendment line 67–68: "issued tokens last up to their TTL (12 h in production since USR-13; revoke the role or disable the user to cut them sooner, and do not re-enable within the TTL)" | AC3a; admin nuance |
| `docs/adr/0011-admin-cors.md` | §6 line 40: replace "(TTL 900 s)" with "(`ACCESS_TOKEN_TTL_SECONDS`, 12 h in production since USR-13; the 600 s bound is independent of the token TTL)". Keep "Do not raise it." | AC3a |
| `docs/identity-architecture.md` | Line 182 "~15 min" → "lifetime `ACCESS_TOKEN_TTL_SECONDS` (default 15 min, 12 h in production; ADR-0005 §6 as amended by USR-13)". D7 row (line 260) "(~15 min)" → "(TTL default 15 min, 12 h in production; owner, 2026-11-17, USR-13)" | AC3a; D7 text |
| `docs/spec-v1.md` | §5.8 line 66: "short-lived JWT" → "short-lived JWT (lifetime `ACCESS_TOKEN_TTL_SECONDS`, up to 12 h; 12 h in production (owner, 2026-11-17))". §5.10 line 104: "(~15 min)" → "(up to `ACCESS_TOKEN_TTL_SECONDS`: 12 h in production (owner, 2026-11-17); admin role revoke and disable still apply on the next admin request, ADR-0008 §3)". OQ8: tick it, keep the question, append "— **answered (owner, 2026-11-17):** accepted with 12 h tokens for every audience, `admin` included (USR-13, ADR-0005 §6 amendment)" | Spec conflict resolution (owner decision) |

The implementer must not touch any other file without recording why in `impl-log.md`. In
particular: no change to `app/modules/tokens/service.py`, `app/core/access_token.py`,
`app/modules/auth/router.py`, `tests/admin_auth_factories.py`, `tests/test_token_verification.py`
or the existing 900 assertions in `tests/test_auth_token.py`.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_config.py` › `test_access_token_ttl_bounds` (parametrize `[59, 43201]`) | Out-of-range values fail startup (AC1). The 3601 case moves to 43201 |
| `tests/test_config.py` › `test_access_token_ttl_accepted` (parametrize `[60, 3601, 43200]`) | Edges and the old bound + 1 load; `settings.access_token_ttl_seconds == ttl` (AC1) |
| `tests/test_config.py` › `test_access_token_ttl_from_env` | `monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "43200")`; `Settings(_env_file=None)` loads 43200. Then `"43201"` raises `ValidationError` matching `access_token_ttl_seconds`, and `"43201" not in str(exc)` (`hide_input_in_errors`, R5). This is the "accepted at startup" path (AC1) |
| `tests/test_auth_token.py` › `test_token_ttl_43200` | `async with make_client(access_token_ttl_seconds=43200) as c:` with the `stoktok`/`viewer` fixtures and `_member`; `/token` → 200, `expires_in == 43200`, claims verified via `verify_reference` (JWKS from the same client), `exp - iat == 43200` (AC2) |
| `tests/test_access_token.py` › `test_long_lived_token_accepted` | `mint(SUB, iat=now - 43000, exp=now + 200)` verifies: no maximum-age check (AC2a) |
| `tests/test_admin_auth.py` › `test_long_lived_token_role_revoked_403` | `make_staff(db, "admin:organizations:read")`; `mint(staff, iat=now - 21600, exp=now + 21600)`; GET ORGS 200; delete the assignments; GET ORGS 403 (AC2b; mirrors `test_role_revoked_after_issue_403`) |
| `tests/test_admin_auth.py` › `test_long_lived_token_disabled_401` | The same long token; 200; `staff.status = "disabled"`, flush; `_assert_401` (AC2b; mirrors `test_disabled_after_issue_401`) |

Use the plain `client` fixture for the admin tests, not `admin_client`: `admin_client` overrides
`current_admin`, so the reload would not run.

## Approach
1. **Config (R5).** `access_token_ttl_seconds: int = Field(default=900, ge=60, le=43200)`. The bound
   is exactly 43200: it is what the owner accepted, and a typo such as `432000` must fail startup.
   The default stays 900 (scope bullet 2; the owner did not ask to change it). There is no
   environment-specific logic. Pydantic `le` already fails `get_settings()` at startup without
   echoing the value.
2. **No issue-path or verifier change.** `TokenService.issue` derives `iat`, `exp` and `expires_in`
   from one `now` and `self.ttl`. The verifier has no age cap. The tests lock both in.
3. **ADR-0005 amendment** ("## Amendment — 12-hour access tokens (USR-13, 2026-11-17)"), in the
   style of the ADR-0008 amendments:
   - **Decision:** bounds 60–43200, default 900. Production `ACCESS_TOKEN_TTL_SECONDS=43200` is set by
     the owner in the deployment repo after merge (owner, 2026-11-17, TuDoCuaTui D46; softer options
     declined). Above 43200 needs a new decision.
   - **Why:** developers and apps no longer re-mint every 15 min.
   - **Unchanged:** no refresh token, no revocation list, no `jti` denylist, no introspection; the
     verifier leeway stays 30 s (consumers ≤ 60 s).
   - **Accepted risks:**
     1. The setting is global: every audience, `admin` included.
     2. For consumer apps, disable, role removal, org suspension, assignment expiry, email change,
        logout and password reset reach a live token only at `exp` (≤ TTL + leeway). The claims
        `roles`, `org_roles`, `email` and `org` are frozen for that long. A token minted just before
        an assignment expires keeps that role until its `exp`.
     3. A leaked token works for up to 12 h on its audience, from any client. An XSS on an app origin
        now yields 12 h tokens.
     4. **Admin nuance:** the per-request reload (ADR-0008 §3) keeps disable, role revoke and
        assignment expiry **immediate** for `aud=admin`. But logout, password reset, staff session
        revoke, disable-then-re-enable within the TTL, and `set-admin-origins --clear` do **not** end
        a stolen admin token. Cutting one means keeping the user disabled or the role revoked for at
        least TTL + leeway, or an emergency key rotation.
     5. Key rotation must keep the old public key for TTL + leeway + JWKS `max-age` (≈ 12 h 06 min;
        wait 12.5 h) after the last old-key signer stopped. Emergency rotation is the only global kill
        switch, bounded by consumer JWKS caches (≤ 1 h unless flushed).
   - **Follow-ups (not in this ticket):** the list under Assumptions → Follow-ups.
4. **`docs/consumers.md`.** Use one wording everywhere: "up to `ACCESS_TOKEN_TTL_SECONDS` (default
   15 min; 12 h in production)". Do not hard-code 12 h alone.
   - Line 76: "may lag up to the token TTL (`ACCESS_TOKEN_TTL_SECONDS`, …)".
   - Line 148: replace "(about 15 minutes)" with the standard wording.
   - Line 294: replace "at most 15 min" with the standard wording.
   - Checklist item 11, rewritten: logout, a disabled user, a removed role or org role, a suspended
     org and an expired assignment reach a token only at its `exp` (up to the TTL). Apps that need
     faster revocation check server-side, as the admin API does, or keep an app-local deny list by
     `sub`.
   - New checklist items:
     - 12: Never cache an authorization decision beyond the request. Map `roles` through the catalog
       per request. Do not turn a token into a longer app session; any app session must end at or
       before the token's `exp`.
     - 13: Sensitive or destructive actions may require a fresh token. Refuse `now - iat > N` (for
       example 900 s) with 401; the SPA silently re-mints through `/auth/token`, which re-checks
       status, verification, membership and roles.
     - 14: Do not add your own maximum-age check below the TTL for ordinary requests; tokens up to
       the TTL are valid.
   - In "Getting a token (SPA)", next to the existing storage bullet (line 115):
     - never log `Authorization` headers (reverse proxy, APM, Sentry breadcrumbs);
     - on logout, drop the in-memory token;
     - re-mint after a join or role change instead of waiting for `exp`;
     - treat developer tokens (copied for scripts) as a 12 h password: never in committed `.env`
       files, shell history, CI logs or chat.
5. **`docs/deploy.md`.**
   - Line 224: "Tokens live `ACCESS_TOKEN_TTL_SECONDS` (default 900 s; production 43200 s, USR-13)".
   - Rotation step 3: "Wait TTL + verifier leeway (≤ 60 s) + JWKS `max-age` (300 s), counted from
     when the **last** process still signing with the old key stopped (step-2 redeploy finished),
     not from when the deploy was triggered. Production (43200 s): 43560 s ≈ 12 h 06 min, so wait
     **12.5 h**. With the default 900 s: about 25 min. Derive the wait from the deployed TTL. If the
     TTL changed during the wait window, use the largest TTL any signer ran with." Step 1 is
     unchanged.
   - After "Emergency rotation", add "**Revoking live tokens (incident):**":
     - **Compromised consumer account:** disable the user. This stops new tokens; live tokens die at
       `exp` (≤ TTL). For an immediate global cut, do an emergency rotation and flush consumer JWKS
       caches.
     - **Compromised staff account:** revoke the admin role or disable the user. This takes effect on
       the next admin request. Do **not** re-enable or re-grant within TTL + 60 s, because the stolen
       token would work again. Logout, password reset, staff session revoke and `--clear` do not end
       an issued admin token.
     - **Scope:** use `user_id`/`jti` from the `token_issued` log lines.
   - Add an owner post-merge note (next to the env-key section or rotation section, wherever env
     keys are listed): set `ACCESS_TOKEN_TTL_SECONDS=43200` in `mmlabs-online/deployment`. It
     applies to tokens issued after the restart. Before setting it, confirm the follow-up checks
     (mmlabs-auth, admin-pan).
   - Lines 376, 493, 497, 568, 585: replace "15 minutes"/"(15 min)" with the standard wording. At
     493 and 568, add "(12 h in production)". At 497, keep "disabling a user or revoking a role
     applies on the next request".
6. **Other stale statements:** the edits listed under Files (ADR-0001/0007/0008/0011,
   identity-architecture, spec, `.env.example`, `cli.py`). Each is a one- or two-line wording fix.
   No decision text other than the lifetime changes.
7. Run `make check`.

Rules:
- R1, R2, R4, R6, R9: no code paths, schema, endpoints or mutations change, so none are affected.
- R3: auth classes are unchanged.
- R5: the value comes from env only and the bound is enforced in `config.py`; no secrets are
  involved.
- R7: the edits beyond the three named docs were requested by the orchestrator and are limited to
  lifetime wording.
- R8: the tests above.
- R10: no refresh tokens are added.
- R11: signing keys, `kid` and rotation are unchanged; only the documented wait changes.

Estimated diff: about 5 lines of code, 70 lines of tests and 120 lines of docs, well under 400.

## Specialist input used
- **Bound exactly `le=43200`, `ge=60`, default 900** (all three consults): adopted.
- **Test set** (software-architect R2, identity-access R-2 to R-5, security R-b): adopted. That is
  bounds, accept values, the env path with no value echo, issue `exp - iat`/`expires_in`, the
  verifier with no age cap, and the admin reload with long tokens. An admin-audience `/token` test
  at 43200 (software-architect, "optionally") is rejected: it needs the admin origin setup, and the
  path is the same `TokenService`.
- **Dated ADR-0005 §6 amendment with accepted risks and the admin nuance** (all three consults):
  adopted. Also the line-19 fix (security R-c, identity R-6).
- **Rotation math ≈ 12 h 06 min, wait 12.5 h, counted from the last old-key signer**
  (identity-access 7, security F4, software-architect 5): adopted, together with the "derive from
  deployed TTL / largest TTL in window" rule (security R-e).
- **Incident runbook lines** (security R-e): adopted.
- **Consumers wording "up to `ACCESS_TOKEN_TTL_SECONDS` (default 15 min; 12 h in production)"**
  (software-architect R4), storage, no-caching and freshness guidance (security R-d), and re-mint
  after a role change (identity-access risks): adopted.
- **Fix stale statements in ADR-0001/0007/0008/0011, identity-architecture, spec and `cli.py`**
  (software-architect R4, security R-f, identity-access R-7): adopted per the orchestrator. Spec
  edits are tagged "(owner, 2026-11-17)".
- **`.env.example` comment** (software-architect, security R-a): adopted. Identity-access R-8 said no
  change was needed, but the comment "60-3600" is wrong after this change.
- **Log the effective TTL at startup** (security R-a, optional): rejected. Not needed for the ACs
  (R7); the value is visible in the deploy env.
- **Per-audience/admin TTL (Option B/C)**, **`tokens_valid_after`/`sid` cutoff**, **capping `exp` at
  the earliest assignment expiry**, **checking consumer repos**: recorded as follow-ups only, per the
  orchestrator and R7.

## Assumptions
- The owner's 2026-11-17 decision in the ticket is the authority for amending the spec, ADR-0005 and
  D7. The owner sees the spec edits in the PR.
- The default stays 900. Production 43200 is an owner step in the deployment repo after merge. Until
  then production runs 900, and the "default 15 min; 12 h in production" wording stays accurate
  once the step is done.
- The historical text of ADR-0005 §6 stays in place, pointing to the amendment, as other ADRs do.
- **Follow-ups (owner options, not in this diff):**
  1. A per-audience admin TTL, e.g. `ADMIN_ACCESS_TOKEN_TTL_SECONDS` (default 900, 60–3600) applied
     when `audience == "admin"`. It would cost admin-pan nothing (it re-mints 60 s before
     `expires_in`) and would shrink the stolen-admin-token and `--clear` windows back to 15 min.
  2. A `tokens_valid_after` per user, or a `sid` claim (additive under the ADR-0005 §5 contract)
     checked in the ADR-0008 §3 admin reload. Either would let logout, password reset, staff
     session revoke and disable-then-re-enable end admin tokens, at no cost to consumers.
  3. Cap `exp` at the earliest expiry of the assignments that produced the token's roles (security
     F3).
  4. Before setting production to 43200, verify that `mmlabs-auth` / `@mmlabs/auth-client` have no
     age cap, `iat` freshness check, clamped refresh timer or age-keyed cache. Verify that admin-pan
     reads `expires_in` rather than hard-coding 900 and keeps tokens in memory only. Do the same for
     ricotdin and TuDoCuaTui (no `localStorage`, no logged `Authorization` headers).

## Blocking questions
None.

## Split proposal
None.

## Data model / migration
None.

## API contract
None. `/api/v1/auth/token` keeps its shape. `expires_in` already reflects the configured TTL, so
this is not a breaking change in v1.
