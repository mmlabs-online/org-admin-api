USR-13 consult (software-architect). Read-only; I changed no files.

## Findings

**1. The code change is small.** Only one place enforces the 3600 limit: `app/core/config.py:86`, `access_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)`.
- `TokenService` (`app/modules/tokens/service.py:52,107,120`) uses `self.ttl` for both `exp` and `expires_in`, with no cap of its own.
- The router passes the value in from `request.app.state.settings` (`app/modules/auth/router.py:197`).
- The verifier `app/core/access_token.py` only checks `exp`/`iat` with a 30 s leeway. It has no maximum-age or `exp - iat` check, so 12 h tokens verify unchanged.

**2. Nothing else in the code assumes TTL ≤ 3600.** Each value below is independent of the token TTL:
- JWKS `Cache-Control: public, max-age=300` (`app/modules/tokens/jwks_router.py:31`, `tests/test_jwks.py:31,45`).
- Consumer JWKS cache of at most 1 h.
- Origin registry cache of 60 s (`app/modules/rbac/origin_registry.py`).
- CORS preflight `Max-Age` of 600 (`app/core/cors.py`).
- Device cookie rotation of about 24 h and idle timeout of 30 days. A 12 h token is still shorter than the 24 h rotation, so nothing interacts.

**3. Tests that encode the bound or the default.**
- `tests/test_config.py:182-185` parametrizes `[59, 3601]`. The 3601 case must move to 43201, and a test that 43200 is accepted must be added.
- `tests/test_auth_token.py:79,107` asserts the default of 900. Keep it, because the default stays 900.
- `tests/admin_auth_factories.py:66`, `tests/test_token_verification.py:38` and `tests/test_access_token.py:60` mint tokens with a hard-coded `exp = now + 900`. They don't depend on the bound and need no change.

**4. Admin is less exposed than the ticket's consequences suggest.** ADR-0008 §3 reloads the user and their effective admin permissions from the database on every admin request. So for `aud=admin`, disabling a user or revoking a role still takes effect on the next call, even with 12 h tokens. What really grows for admin:
- A leaked admin token stays usable for up to 12 h while its owner remains active and keeps the permission.
- Break-glass `set-admin-origins --clear` no longer limits existing tokens to 15 min.
- Logout (including the `EXIT` trap logout in the curl runbook) leaves the token alive for 12 h.

For consumer apps (TuDoCuaTui, ricotdin, calechip) the 12 h lag is real for `roles` and `org_roles`, because they authorize from the claims. The only way to kill one leaked consumer token is an emergency signing-key rotation, which kills every token.

**5. Key rotation.** `docs/deploy.md` step 3 waits TTL + leeway + `max-age`: 43200 + 60 + 300 = 43560 s, about 12 h 6 min. Saying "about 12.5 h" in the docs is a sensible margin. Step 1's wait is unchanged. Emergency rotation is unchanged, but it becomes the only kill switch for a leaked consumer token, so it is worth saying that in deploy.md.

**6. Docs that will be wrong in production once the value is 43200.** Many more than the three files in the ticket say "15 min" or "900 s":
- `docs/consumers.md:76,148,294,368-369`
- `docs/deploy.md:224,236,376,493,497,568,585`
- `docs/adr/0005-access-token-signing.md:19,42`. Line 19 is the algorithm rationale "for a 15-minute token"; ES256 is still fine at 12 h.
- `docs/adr/0008-admin-api-authentication.md:39,68`
- `docs/adr/0011-admin-cors.md:40` ("TTL 900 s … Do not raise it")
- `docs/adr/0007-email-verification-and-delivery.md:102`
- `docs/adr/0001-multi-app-authorization.md:42-43`
- `docs/identity-architecture.md:182` and D7 at line 260 ("~15 min")
- `.env.example:53` ("60-3600")
- `docs/spec-v1.md:104` ("~15 min") and OQ8 at line 157

**7. Spec conflict to flag.** The spec is the source of truth. Spec §5.8 says "short-lived JWT", and spec line 104 and OQ8 frame the revocation tail as about 15 min. The owner's decision changes that, so the spec text and D7 need updating in this ticket. If that is out of scope, the planner must at least flag it.

## Recommendations

- **R1. Set the bound to exactly `le=43200`, not "at least".** It matches what the owner accepted and keeps the worst case bounded. Keep `ge=60` and the default of 900. (Acceptance criterion 1.)
- **R2. Tests.**
  - `test_access_token_ttl_bounds` checks that 59 and 43201 are rejected; a separate test checks that 43200 is accepted.
  - A `/token` test builds the app with `access_token_ttl_seconds=43200` (through `app.state.settings` or the existing settings fixture) and asserts `exp - iat == 43200` and `expires_in == 43200`. Use an app audience, and optionally `admin` too.

  (Acceptance criteria 1, 2 and 4; R8.)
- **R3. Amend ADR-0005 point 6** with a dated amendment section in the style of the ADR-0007 and ADR-0008 amendments: bounds 60–43200, default 900, production 43200 (owner, 2026-11-17, TuDoCuaTui D46), the reason, and the four accepted consequences. Add the nuance from Finding 4 (admin reload means admin revocation stays immediate) and the rotation wait. (Acceptance criterion 3.)
- **R4. Use one doc wording everywhere:** "up to `ACCESS_TOKEN_TTL_SECONDS` (default 15 min; 12 h in production)". Do not hard-code 12 h, so a later config change doesn't make the docs stale again.
  - Required (acceptance criterion 3): consumers.md (lines 76, 148, 294, 368), deploy.md (rotation section plus lines 376, 493, 497, 568, 585), `.env.example`.
  - Strongly advised in the same diff, because otherwise they become false statements: ADR-0011 §6 (drop the "TTL 900 s" justification and note that `Max-Age` 600 no longer bounds anything meaningful next to the token), ADR-0008 §7 and the `--clear` amendment, ADR-0007:102, identity-architecture D7 and line 182, spec line 104 and OQ8.
  - If the planner treats the ADR, spec and identity-architecture edits beyond those as outside R7, list them as an explicit follow-up rather than leaving them silently stale.
- **R5. Consumers.md checklist item 11.** Rewrite it to say consumers whose tokens carry `roles`/`org_roles` see role changes, removals and disables up to the TTL late. Mention that apps needing faster revocation must check server-side, as the admin API does.

## Per-audience TTL: offer it to the owner, but don't block on it

Propose it as a non-blocking owner option, with the planner's default being the ticket as written (one global TTL).

- **Option A, global (accepted).** No extra code.
- **Option B, short admin TTL.** Add `ADMIN_ACCESS_TOKEN_TTL_SECONDS` (default 900, bounds 60–3600), applied when `audience == "admin"` in `/token`. It needs a few lines in the router or `TokenService`, one test, an ADR-0008 §7 amendment and an `.env.example` key.
  - It costs admin-pan nothing, because admin-pan already re-calls `/token` 60 s before `expires_in`. The curl runbook is per-session anyway.
  - It shrinks the leaked-admin-token and break-glass windows from 12 h to 15 min. Admin is the highest-privilege audience, and the request was driven by TuDoCuaTui developer ergonomics, not admin.
  - The benefit is narrower than it looks: ban and role revocation are already immediate for admin (Finding 4).
- **Option C, per-app column `apps.access_token_ttl_seconds`.** This needs a migration and admin API surface. It is overkill now (no astronautics); revisit only if a second app needs its own TTL.

The owner's "chọn C" turned down softer options (auto-refresh in the dev script, 1 h through config), but not B. B is outside the ticket's plan (R7), so the planner should either note it as a follow-up ticket proposal or ask the owner in the plan without making it a BLOCKED question.

## Risks

- **mmlabs-auth and @mmlabs/auth-client** (separate repo, not checked here) may assume a short TTL: a maximum token age check, an `iat` freshness check, a refresh timer clamped to 15 min, or a token cache keyed on age. A refresh timer of 43,200,000 ms is fine for `setTimeout`, which allows up to about 24.8 days. The repo needs checking before production is set to 43200.
- **admin-pan** may hard-code 900 instead of reading `expires_in`. That would only cause extra refreshes, which is harmless. It may also show a session expiry in the UI that becomes wrong.
- **Consumers that cache a JWKS for 1 h** are unaffected.
- **Stale docs** if only the three files named in the ticket are updated: many hard-coded "15 min" statements become false in production (Finding 6).
- **The production value is an owner step after merge.** Until then the docs describe 12 h while production still runs 900. The default-plus-production wording in R4 avoids that mismatch.

## Open questions (none blocking)

1. Does the owner want Option B (admin kept at 15 min) now, as a follow-up, or not at all?
2. Do the spec line 104 and OQ8 text and the identity-architecture D7 update belong in this ticket or a follow-up? The spec wins on scope, so the conflict must at least be recorded.
3. Has anyone checked the mmlabs-auth repo for TTL or age assumptions before the production env value changes? That could be an owner step in the deployment checklist.

## Relevant files
- /home/user/org-admin-api/work/tickets/USR-13/ticket.md
- /home/user/org-admin-api/app/core/config.py (line 86)
- /home/user/org-admin-api/app/modules/tokens/service.py
- /home/user/org-admin-api/app/modules/auth/router.py (lines 192-205)
- /home/user/org-admin-api/app/core/access_token.py
- /home/user/org-admin-api/app/modules/tokens/jwks_router.py
- /home/user/org-admin-api/tests/test_config.py (lines 182-185)
- /home/user/org-admin-api/tests/test_auth_token.py (lines 79, 107)
- /home/user/org-admin-api/docs/adr/0005-access-token-signing.md
- /home/user/org-admin-api/docs/adr/0008-admin-api-authentication.md
- /home/user/org-admin-api/docs/adr/0011-admin-cors.md
- /home/user/org-admin-api/docs/adr/0007-email-verification-and-delivery.md
- /home/user/org-admin-api/docs/adr/0001-multi-app-authorization.md
- /home/user/org-admin-api/docs/consumers.md
- /home/user/org-admin-api/docs/deploy.md
- /home/user/org-admin-api/docs/identity-architecture.md
- /home/user/org-admin-api/docs/spec-v1.md
- /home/user/org-admin-api/.env.example
