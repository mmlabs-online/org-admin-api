## Attempt 1

## Security review: USR-13, attempt 1 (branch ticket/USR-13, diff origin/main...HEAD)

I only read code and ran git. I did not run make check or pytest, as you asked.

**Verdict: PASS. No blockers.** There are two 🟡 doc should-fixes and a few 💭 notes.

### Findings

🟡 **S1: the staff incident runbook can let the attacker back in.** `docs/deploy.md` ~L258–261 ("Compromised staff account").
- **Risk:** the runbook says to disable the user or revoke the role, then not re-enable within TTL + 60 s. It never says to cut the attacker's other credentials before access is restored. If the attacker has the password (phishing, reuse), re-enabling after 12 h lets them log in and mint new admin tokens. The same applies to step 1 of "Compromised consumer account".
- **Fix:** add one bullet: "Before re-enabling or re-granting, reset the password (forgot/reset flow, which ends every account session) and confirm the email inbox is not compromised. If the user holds several admin roles, revoke all of them (any remaining `admin:*` permission keeps the stolen token working)."

🟡 **S2: OQ8 is closed slightly beyond what the ticket records the owner was shown.** `docs/spec-v1.md:371` (OQ8 ticked "answered").
- **Risk:** per the ticket notes, the owner was told three things: the TTL is global including admin, the code bound is 3600, and ban latency becomes 12 h. The specific admin gap was not on that list: logout, password reset, staff session revoke and `--clear` do not end an admin token. ADR-0005 amendment item 4 states it correctly, but OQ8 is closed on the owner's behalf. The plan told the implementer to do this (plan.md L57), so it is not an R7 breach, but the owner should see it.
- **Fix:** keep the edit, and have the PR body spell out ADR-0005 risk 4 (the admin gap and the "keep disabled or role revoked for TTL + leeway" mitigation) so the owner approves it when merging. Or append "(admin gap: ADR-0005 amendment risk 4)" to the OQ8 answer line.

💭 **N1: wording.** `docs/spec-v1.md:353` still says "short-lived JWT" for a 12 h token. Suggest "access JWT" or "per-app JWT".

💭 **N2: wording.** `docs/consumers.md:197`, `:219` and `:300` have doubled parentheses: "(up to `ACCESS_TOKEN_TTL_SECONDS` (default 15 min; 12 h in production))". Use a semicolon or a single parenthesis.

💭 **N3: re-enable timing in ADR-0005.** ADR-0005 amendment item 4 says "within the TTL", but deploy.md correctly says "TTL + 60 s". Align the ADR to "TTL + leeway", counted from the disable/revoke time (a token may have been minted just before it).

### Verified OK
- **Bound and default** (`app/core/config.py:87`): `Field(default=900, ge=60, le=43200)`. The upper bound is exactly 43200 and the default is still 900. The comment says that going above 43200 needs a new decision.
- **Startup failure:** `get_settings()` runs at import (`app/db/session.py:12`) and in `create_app`, so 43201 raises `ValidationError` before uvicorn serves anything.
  - `model_config` has `hide_input_in_errors=True` (`config.py:26`), so the value is not echoed.
  - `tests/test_config.py` covers this: 43201 from env is rejected and `"43201" not in str(exc)`. 59 and 43201 are rejected; 60, 3601 and 43200 are accepted.
- **Verifier and issuer unchanged:** the diff touches no code in `app/core/access_token.py`, the token service or admin_auth.
  - Required claims are still `exp, iat, sub, jti, iss, aud`, leeway is still 30 s, and alg/aud/iss pinning is unchanged.
  - The only app-code change besides config is the `MSG_ADMIN_CLOSED` text in `app/cli.py`, which is more accurate now.
- **Tests on admin revocation with long-lived tokens** (`tests/test_admin_auth.py`): both mint a token with iat = now − 6 h and exp = now + 6 h.
  - `test_long_lived_token_role_revoked_403`: 200 before the role is deleted, 403 after.
  - `test_long_lived_token_disabled_401`: 200 before disable, 401 after.
  - This matches `AdminAuthService.principal` (`app/modules/admin_auth/service.py:32–38`), which reloads status, email verification and effective permissions on every request.
- **Issued TTL test** (`tests/test_auth_token.py`): `exp − iat == 43200` and `expires_in == 43200`, verified with the reference verifier through the JWKS.
- **ADR-0005 amendment** states the risks accurately:
  - The TTL is global, `admin` included.
  - Claims are frozen until `exp` on consumer apps.
  - A leaked token or an XSS on an app origin now yields 12 h of access.
  - The admin gap is described correctly against the code: the reload does not check account sessions, `iat` against password change, or admin origins, so logout, reset, staff session revoke, disable-then-re-enable and `--clear` do not end an admin token.
  - Emergency rotation is described as the only global kill switch, bounded by consumer JWKS caches of at most 1 h.
  - Unchanged: no refresh token, no denylist, no introspection.
- **deploy.md rotation math:** 43200 + 60 + 300 = 43560 s ≈ 12 h 06 min, rounded up to 12.5 h. The wait is counted from when the last old-key signer stopped, and it uses the largest TTL any signer ran with. With the default 900 s it is about 25 min.
  - The `token_issued` log really carries `user_id` and `jti` (`app/modules/tokens/service.py:112`).
  - The TTL change is an owner step after merge, gated on checking the consumers.
- **consumers.md guidance** is accurate:
  - Tokens are kept in memory only and `Authorization` is never logged.
  - Logout drops the token, and apps re-mint after a join or role change.
  - Developer tokens are treated as a 12 h password.
  - Authorization decisions are not cached beyond the request, and an app session must not outlive `exp`.
  - Sensitive actions can require a fresh token (`now − iat > N` → 401, then a silent re-mint through `/auth/token`, which re-checks status, verification, membership and roles). This fits the existing "on 401 call `/token` once" guidance.
  - Item 14 (no stricter max-age for ordinary requests) is about interoperability and creates no risk.
- **Spec edits** at §5.8, §5.10 and OQ8 are each tagged "(owner, 2026-11-17)". All the other stale "15 min" statements are inside the plan's row 3a scope (ADR-0001/0007/0008/0011, identity-architecture, `.env.example`, cli).
- **No secrets:** none in the diff (I scanned for private keys, JWTs and password/secret assignments). `.env.example` has no values.

### Follow-ups (out of scope; already partly listed in the ADR-0005 amendment)
1. A per-audience TTL so `aud=admin` goes back to 900 s; this removes most of the admin-gap risk at little cost. Also a `tokens_valid_after` (or account-session `sid`) check in the admin per-request reload, so logout, reset and staff session revoke end admin tokens.
2. Cap `exp` at the earliest assignment `expires_at` when minting.
3. Before setting 43200 in `mmlabs-online/deployment`, check `mmlabs-auth`, admin-pan, ricotdin and TuDoCuaTui. None of them should have a max-age below the TTL, cache authorization past `exp`, or store tokens outside memory.
4. A helper in `mmlabs-auth` for the "fresh token for sensitive actions" check (consumers.md item 13), so apps don't each write their own.

Files reviewed: `/home/user/org-admin-api/app/core/config.py`, `/home/user/org-admin-api/app/cli.py`, `/home/user/org-admin-api/app/modules/admin_auth/service.py`, `/home/user/org-admin-api/app/modules/admin_auth/deps.py`, `/home/user/org-admin-api/docs/adr/0005-access-token-signing.md`, `/home/user/org-admin-api/docs/deploy.md`, `/home/user/org-admin-api/docs/consumers.md`, `/home/user/org-admin-api/docs/spec-v1.md`, `/home/user/org-admin-api/tests/test_config.py`, `/home/user/org-admin-api/tests/test_admin_auth.py`, `/home/user/org-admin-api/tests/test_auth_token.py`, `/home/user/org-admin-api/tests/test_access_token.py`.

**Verdict: PASS (0 🔴, 2 🟡 doc fixes, 3 💭).**
