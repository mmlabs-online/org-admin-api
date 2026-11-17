USR-14 consult (identity-access-engineer, read-only)

**Bottom line:** use option (a) only. Add a `sid` claim (the account session id) to `aud=admin` tokens, and have the per-request admin reload check that the session is still active and belongs to `sub`. Every trigger in the AC already revokes account-session rows, so `sid` covers all of them without new columns or clock comparisons. A `tokens_valid_after` cutoff adds nothing that `sid` doesn't already cover. It also kills too much (logging out one device ends every token of the user) and brings back second-granularity and clock-skew bugs.

## Findings

**F1. Where admin tokens come from.** Every admin token is minted by `TokenService.issue` (`/home/user/org-admin-api/app/modules/tokens/service.py`). That path needs an active account session, and `account.id` is already in scope: it is logged as `account_session_id` but not put into the token. There is no dev-token script (`scripts/` only has `init-test-db.sql`). The curl runbook (`docs/deploy.md` §6) also goes through `/auth/login` and `/auth/token`. So every real admin token can carry `sid`. The only cookie-less minting is `tests/admin_auth_factories.py` (`mint`, `mint_raw`), which has no session today.

**F2. Every trigger already revokes `account_sessions` rows** (`/home/user/org-admin-api/app/modules/sessions/repository.py`):

| Trigger | Code path | Effect |
|---|---|---|
| Logout of one account | `auth/service.py:247` → `revoke_account` | that row |
| Logout all / device | `auth/service.py:236` → `revoke_device` | revokes the accounts first |
| Cookie reuse after the grace period (R11) | `sessions/service.py:103` → `revoke_device` | same; bonus coverage |
| Password reset | `auth/password_reset.py:110` → `revoke_all_accounts_of_user` | all rows of the user |
| Staff revoke one / all (USR-10) | `staff_service.py` → `revoke_account_of_user` / `revoke_all_accounts_of_user_ids` | that row / all rows |
| Disable | `users/service.py:81` → `revoke_all_accounts_of_user` | all rows; re-enable does not un-revoke them, so a `sid` check closes disable→re-enable |
| Role revoke / assignment expiry | already covered by the reload | — |

**F3. What the admin reload checks today.** `AdminAuthService.principal` (`/home/user/org-admin-api/app/modules/admin_auth/service.py`) checks only that the user exists, is active and is verified, plus their permissions. `current_admin` (`admin_auth/deps.py`) passes it only `sub` and `jti`.

**F4. `tokens_valid_after` compared with `iat`. What it would cover:**
- Password reset, revoke-all, disable→re-enable: yes, if every one of those writes sets the cutoff.
- Logout of one session and staff revoke of one session: only by over-killing. The user's tokens on every other device die too.
- The cookie-reuse device revoke: no, unless that path is also changed.
- Every new revoking path in the future must remember to bump the column. That is easy to forget; `sid` follows the existing source of truth automatically.

**F5. Clock and leeway problems with a cutoff:**
- `iat` is `int(time.time())` from the app clock, truncated to whole seconds. The cutoff would be the DB `now()`, with microseconds.
- With `iat < cutoff`, a token minted earlier in the same second as the cutoff survives (the truncation brings its `iat` down to the second, below the cutoff).
- With `iat <= floor(cutoff)`, a token minted in the same second just after the cutoff is wrongly rejected. Tests get flaky.
- Skew between the app host and the DB moves the boundary further. The 30 s verifier leeway does not apply to this check, so the plan would have to define the rule explicitly.
- `sid` has none of these problems: it is a yes/no lookup against DB state.

**F6. A race that `sid` handles.** `issue` reads the account as active without a lock. A revoke can commit between that read and signing. The token still carries the now-revoked `sid`, so it dies on its first admin request. A cutoff only catches this case if the app clock agrees with the DB clock.

**F7. Logout in the curl runbook.** The runbook's EXIT trap calls `logout {"all":true}`. With `sid`, the runbook token really ends when the subshell exits. That is good, but `docs/deploy.md` lines ~520 and ~264 currently say the opposite and must be rewritten.

**F8. Gaps neither option closes:**
- `set-admin-origins --clear` (ADR-0005 amendment risk 4). It is origin config, not user state.
- A fresh login by an attacker who knows the password.
- Consumer-app tokens, which have no introspection. This is the AC's intended scope.

## Recommendations

**Rec 1 (AC1).** Choose `sid` only. Record it as an amendment to both ADR-0005 (rewrite risk 4) and ADR-0008 §3. Reject `tokens_valid_after` in writing, with the reasons from F4 and F5.

**Rec 2 (AC3, AC4).** Emit `sid` only when `app.key == ADMIN_APP_KEY`:
- Consumer tokens stay byte-for-byte identical, which matches AC3 to the letter. It also follows the "minimal claims" rule: consumers have no way to use `sid`.
- Format: string UUID, the same value as the `account_id` that apps already send.
- Do not add `sid` to the required claims of the shared `verify_access_token` (`REQUIRED_CLAIMS`). Enforce it inside the admin path, so mmlabs-auth and consumer verifiers are not affected.

**Rec 3 (AC2).** In `current_admin`, parse `claims["sid"]` as a UUID. If it is missing or malformed → 401 `invalid_token`, with `_deny("sid", jti, user_id)`. Pass it to `principal(user_id, sid, jti)`, which should:
- load the account session by id and require `revoked_at IS NULL` and `user_id == sub`;
- treat a missing row as revoked, so a future cleanup job stays safe;
- fold this into the user lookup if you want one round trip (it is a primary-key lookup);
- not check device idle/max expiry. Idle is 30 d and max age 365 d, both far above the 12 h TTL, and `revoke_device` already revokes the account rows.

Use the same 401 body for every cause, as today. The log reason should say `session`, not reveal details to the client. Optionally keep `sid` on `AdminPrincipal` for logs; do not put it in audit `changes` unless the plan wants it.

**Rec 4 (rollout).** After deploy, reject admin tokens that have no `sid`. Otherwise tokens issued before the deploy stay unrevocable for up to 12 h. The impact is one forced re-mint: the admin portal re-mints silently on 401 (`consumers.md` ~394), and curl users rerun the runbook. Put this in the release note and `deploy.md`.

**Rec 5 (AC5 tests).**
- Update `tests/admin_auth_factories.mint` to create a real device session and account session (or take a `sid`), and add a negative `mint_raw` case without `sid`.
- One test per trigger: logout one, logout all, password reset, staff revoke one, staff revoke all, disable→re-enable. Each issues a token, fires the trigger, then expects 401 on an admin GET.
- Bonus test: cookie reuse after the grace period, then 401.
- Isolation: revoking session A leaves a token bound to session B (another device) working.
- After a trigger, logging in again gives a new token that works.
- A `sid` belonging to another user's live session (forged or swapped) → 401.
- A consumer token has no `sid` and its other claims are unchanged (extend `tests/test_auth_token.py`).
- Register every new 401 path in the existing route-auth tests where relevant.

**Rec 6 (AC3 docs).**
- `consumers.md`: admin tokens carry `sid`; consumer tokens are unchanged and still valid until `exp`.
- `deploy.md`: rewrite the incident runbook (~255–269) and the curl notes (~520, ~643, ~660). For admin tokens, logout, reset, staff revoke and disable now end them on the next request; `--clear` still does not.
- `identity-architecture.md` line ~224 and the API description in `users/router.py` ~69: remove the "including admin tokens" / "stays valid until exp" wording for admin.

**Rec 7 (AC4).** No migration is needed, since there is no new column. State that in the plan so the reviewer does not expect one.

## Risks

1. **mmlabs-auth or admin-portal strict claim models.** If any verifier parses claims into a schema that forbids unknown fields (for example Pydantic `extra="forbid"`), a new claim breaks it. Rec 2 limits this to admin-audience verifiers, which in practice means the admin portal, if it decodes the token. This needs checking; I could not see those repos.
2. **Forced re-mint at deploy** (Rec 4). Cheap, but any long-running admin script holding a token sees 401s once.
3. **One more query per admin request.** It is negligible (indexed primary key) but adds a little latency; combining it with the user lookup avoids a second round trip.
4. **Gaps that remain:** `set-admin-origins --clear`, and a token used from an attacker's own device while the victim's session stays active (the `sid` stays live). The kill switches for those are still revoking the session, disabling the user, or an emergency key rotation. Write this down in the ADR.
5. **Test churn.** Every admin test that uses `mint(user)` needs a session fixture. Hide this inside the factory to keep the diff small (R7).

## Open questions

1. Should `sid` go to consumer audiences too, for future introspection and uniformity? I recommend admin only now, per AC3 and minimal claims. Owner to confirm.
2. Is it acceptable that admin tokens without `sid` are rejected right after deploy (forced re-mint), or does the owner want a transition window of up to TTL? I recommend rejecting.
3. Does mmlabs-auth, or the admin portal's own token handling, validate the claim set strictly? This decides whether there is any client change.
4. Should `set-admin-origins --clear` get its own admin cutoff, for example a global `admin_tokens_valid_after` setting? That is out of scope here; I suggest a follow-up ticket.
