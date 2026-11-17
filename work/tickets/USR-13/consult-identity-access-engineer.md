## USR-13 consult (identity-access-engineer, read-only)

### Findings
1. **Config.** `app/core/config.py:86` reads `access_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)`. Pydantic's `le` already makes startup fail: `get_settings()` raises `ValidationError` naming the field and does not echo the value (`hide_input_in_errors=True`). No model validator is needed. The existing test `tests/test_config.py:182-185` is parametrized `[59, 3601]`, so with the new bound 3601 becomes a valid value and that test breaks.
2. **Issue path.** `TokenService.issue` (`app/modules/tokens/service.py:95-120`) sets `iat=now`, `exp=now+ttl` and `expires_in=self.ttl` from one `int(clock())`. That makes `exp - iat == expires_in == ttl` exact, so no code change is needed. The router passes `settings.access_token_ttl_seconds` (`app/modules/auth/router.py:197`). `tests/test_auth_token.py:79,107` assert 900 under the default settings, which stays correct if the default stays 900. `make_client(**settings_overrides)` (`tests/conftest.py:171-184`) already accepts `access_token_ttl_seconds=43200`.
3. **Verifier.** `app/core/access_token.py` uses a 30 s leeway and checks `exp`, a future `iat` and required claims. It does not check maximum token age (`exp - iat`), and neither does the consumers checklist (`docs/consumers.md:351-369`). Raising the TTL therefore needs no change on the verifier side. Consumers who added their own age cap would start rejecting tokens, but I saw no such rule in the docs. The ticket's "token age rule" can only mean the revocation-lag text (consumers.md item 11).
4. **Admin (ADR-0008 §3).** `AdminAuthService.principal` reloads the user on every request (exists, `active`, `email_verified_at`) and the effective `admin` permissions. So for admin, a disabled user, a revoked role or an expired assignment still takes effect on the next request, whatever the TTL. **The reload does not check account or device sessions** (`app/modules/admin_auth/service.py:37`). These therefore do NOT cut a live admin token, which now lives up to 12 h:
   - logout (including the curl runbook's `{"all": true}` in the EXIT trap, `docs/deploy.md:490-493`)
   - password reset (USR-04b)
   - staff session revoke (spec §5.10)
   - `set-admin-origins --clear` (`docs/deploy.md:566-569`, ADR-0008 ADM-002c amendment)
   Today a stolen admin token stays usable for up to 12 h while its owner is still active staff. The only ways to cut it are disabling or de-roling that staff member (which also locks them out) or an emergency key rotation (every token on every app fails, about one 401 per client).
5. **Other apps.** Bans, role removal, logout and reset can lag up to 12 h + leeway (consumers.md lines 76, 148, 294, 368-369). This is what the owner accepted.
6. **Device session and cookie.** `mm_device` Max-Age equals the idle time (30 d, `app/modules/auth/cookies.py:35`), with an absolute limit of 365 d and rotation about every 24 h with 60 s grace. All of this is independent of the TTL, so nothing changes. One edge case: a token minted just before idle or absolute expiry outlives the device session by up to 12 h. The same is true today at 15 min. Document it, no code change.
7. **Rotation math (`docs/deploy.md:222-237`).** Step 3 today says TTL 900 + leeway 60 + max-age 300 ≈ 25 min. With 43200 that is 43560 s ≈ **12 h 06 min**, not 12.5 h. The ticket's "about 12.5 h" only holds if a margin is stated.
   - The real clock starts when the **last** process still signing with the old key has stopped after the step-2 redeploy, not when the deploy is triggered.
   - `max-age` is not strictly needed for removing the old key. Keeping it as margin is fine.
   - Step 1's wait (300 s plus the consumer JWKS cache of up to 1 h) does not depend on the TTL and is unchanged.
   - Emergency rotation is unchanged, and it is now the only "kill all live tokens" switch.
8. **Docs that say 15 min / 900 but are not in the ticket's scope.** The spec wins on scope, so these must be flagged:
   - spec-v1 §5.8 line 66 ("short-lived"), §5.10 line 104 ("~15 min") and OQ8 line 157 (which explicitly asks whether the ~15 min admin tail is acceptable; the answer is now 12 h)
   - `docs/identity-architecture.md:182,260` (D7 "~15 min")
   - ADR-0001:42-43 ("~15 min"; it also mentions refresh tokens, already stale)
   - ADR-0005:19 ("Security is equivalent for a 15-minute token")
   - ADR-0007:102 ("≤ 15 min")
   - ADR-0008 §7 and the `--clear` amendment
   - ADR-0011 §6 line 40 ("side effects still need a valid token (TTL 900 s). Do not raise it.")
   - deploy.md:376, 492-498, 568
   - consumers.md:76, 148, 294, 368-369

### Recommendations
- **R-1 (AC1).** Set `le=43200` exactly, not "at least". 43200 is what the owner accepted, and anything above it should need a new decision. Keep `ge=60` and `default=900` (scope bullet 2). Do not add environment-specific logic. Add a comment pointing to ADR-0005 §6.
- **R-2 (AC1, R8).** Tests:
  - change `test_access_token_ttl_bounds` to `[59, 43201]`
  - add an accept case `[60, 3601, 43200]` asserting the stored value
  - add a case loading from env (`monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "43200")`, `Settings(_env_file=None)`), so "accepted at startup" is shown through the env path
  - assert the error message does not contain the input value
- **R-3 (AC2).** In `tests/test_auth_token.py`, add a test using `make_client(access_token_ttl_seconds=43200)`:
  - assert `body["expires_in"] == 43200`
  - check `claims["exp"] - claims["iat"] == 43200` after `verify_reference`
  - optionally use a fixed `clock` on `TokenService` to check `iat == now`
  - keep the existing 900 default assertions unchanged
- **R-4 (AC2, verifier).** In `tests/test_access_token.py`, add:
  - a token with `iat=now-43000, exp=now+200` → accepted (no age cap)
  - `exp=now-31` → `expired` (leeway unchanged at 30 s)
  This locks in that the long TTL needs no verifier change.
- **R-5 (ADR-0008 §3 interaction).** In `tests/test_admin_auth.py`, add a regression test:
  - a long-lived admin token (`mint(user, iat=now-6h, exp=now+6h)`) is accepted
  - after disabling the user, or revoking the admin role, the same token gets 401 or 403 on the next request
  This shows the admin bans and role changes are still enforced server-side with 12 h tokens. If equivalent tests already exist, parametrize them with the long-lived token.
- **R-6 (AC3).** Amend ADR-0005 §6: lifetime 900 by default with bounds 60–43200; the reason (owner, 2026-11-17, TuDoCuaTui D46); the four accepted consequences, plus the admin nuance from Finding 4. Also correct line 19. In deploy.md step 3, write the formula and the number: "TTL + verifier leeway (≤ 60 s) + JWKS max-age (300 s), counted from when the last old-key instance stopped. With 43200 s that is ≈ 12 h 06 min; wait 12.5 h." Replace every hard-coded "15 min" with "up to `ACCESS_TOKEN_TTL_SECONDS` (production: 12 h)", so the docs do not go stale at the next change.
- **R-7 (spec rule).** The planner should flag the spec conflict (Finding 8). Either it proposes the spec §5.8/§5.10/OQ8 text edits within this ticket's doc changes, or the owner or product-manager updates the spec. Also update ADR-0008 §7, the `--clear` text and ADR-0011 §6. ADR-0011's "Do not raise it" is about the preflight Max-Age, but its reasoning relied on a 900 s token, so add one sentence there.
- **R-8 (R5).** `.env.example` already lists the key without a value, so no change. Add a deploy.md note for the owner's post-merge step (`ACCESS_TOKEN_TTL_SECONDS=43200` in the deployment repo) and say that it only affects tokens issued after the restart.

### Risks
- **Admin tokens survive logout, password reset, session revoke and `--clear` for up to 12 h** (Finding 4). The owner was told that ban latency becomes 12 h and that admin is included. Explicitly, they were not told that for admin, bans are still immediate but logout and reset are not. The curl runbook's "logout ends it" is now a 12 h gap if the header file leaks.
- A leaked app token is valid for 12 h on its audience. Cutting it means disabling the user (only effective at admin, or at apps that recheck) or an emergency key rotation for everyone.
- Rotation: removing the old key before about 12 h after the last old-key signer stopped breaks live tokens. Operators used to "25 min" are the risk, so the doc must be precise.
- Consumers that copied an older checklist with their own max-age cap would reject tokens. This is low risk; mention it in consumers.md.
- `roles` in a token lags up to 12 h, so UIs that render from the claim show stale roles. The admin UI already uses `/me/permissions`; other apps should be told to re-mint the token after a join or role change.

### Open questions
1. Should the owner be told about the admin-specific gap (logout, reset, session revoke and `--clear` don't cut an admin token for 12 h) before PLAN closes? One cheap fix exists and would be a follow-up, not this ticket (R7): add a `sid` claim (additive under the ADR-0005 §5 contract) and check that the account session is active in the ADR-0008 §3 reload. Another option is a separate, shorter admin TTL, which the owner declined.
2. Spec §5.8/§5.10 and OQ8 say "~15 min": does the planner update the spec text in this ticket, or does it go to the owner or product-manager? The spec wins on conflicts.
3. Should the upper bound be exactly 43200 (my recommendation) or something higher for headroom? The ticket says "at least 43200".
4. Stay with the global setting, or should staging keep 900? This is an owner choice in the deployment repo and needs no code.

Files: /home/user/org-admin-api/app/core/config.py, /home/user/org-admin-api/app/modules/tokens/service.py, /home/user/org-admin-api/app/core/access_token.py, /home/user/org-admin-api/app/modules/admin_auth/service.py, /home/user/org-admin-api/tests/test_config.py, /home/user/org-admin-api/tests/test_auth_token.py, /home/user/org-admin-api/tests/test_access_token.py, /home/user/org-admin-api/tests/test_admin_auth.py, /home/user/org-admin-api/docs/adr/0005-access-token-signing.md, /home/user/org-admin-api/docs/adr/0008-admin-api-authentication.md, /home/user/org-admin-api/docs/adr/0011-admin-cors.md, /home/user/org-admin-api/docs/deploy.md, /home/user/org-admin-api/docs/consumers.md, /home/user/org-admin-api/docs/spec-v1.md, /home/user/org-admin-api/docs/identity-architecture.md
