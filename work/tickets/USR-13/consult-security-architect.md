USR-13 security consult (read-only). The owner's decision stands. This covers what the planner should add around it.

## Findings

**F1. Spec conflict (flag it, it does not block the work).** The spec still describes the short lifetime in several places:
- `docs/spec-v1.md:66` says "short-lived JWT".
- `docs/spec-v1.md:104` says "~15 min".
- `docs/spec-v1.md:157` (OQ8) is about the ~15 min tail of admin tokens.
- `docs/identity-architecture.md:182` and its D7 row (`:260`) say "~15 min".

CLAUDE.md says the spec wins on scope, so the planner should flag this. The fix is a spec and D7 text update (by the product-manager or the owner), or explicit owner sign-off in the PR. OQ8 is effectively answered by this decision and should be recorded as such.

**F2. Admin tokens are already protected by the per-request reload (ADR-0008 §3).** Disabling a user, revoking a role or an assignment expiring takes effect on the next admin call, whatever the TTL. So the "ban latency 12 h" consequence applies to consumer apps, not to admin permissions. Three gaps remain for admin tokens, and they are new in practice at 12 h:
- (a) **Logout does not end an admin token.** It stays valid for 12 h.
- (b) **Password reset does not end an admin token.** Reset revokes sessions, but the user stays `active` with roles, so a stolen admin token outlives the "change your password" response.
- (c) **Disable then re-enable within 12 h brings a stolen token back to life**, because the reload only checks the current status.

The only ways to kill a stolen admin token are: keep the user disabled or the role revoked for at least TTL + leeway, or do an emergency key rotation.

**F3. Lagging claims in consumer tokens are frozen for up to 12 h:** `roles`, `org_roles`, `email` and `org` (suspended orgs too). Assignments with an expiry are a special case: a token minted just before the expiry keeps the role for 12 h more (`app/modules/tokens/service.py:74,107` does not cap `exp` at the earliest assignment expiry).

**F4. Key rotation timing.** The old public key must stay published for TTL + verifier leeway after the signer switch: 43200 + 60 = 43260 s. Adding the JWKS `max-age` (300) gives about 12 h 6 min, so "about 12.5 h" in `docs/deploy.md:236` is a safe, rounded-up figure. Step 1, the propagation wait for the new key, is unchanged.

Emergency rotation becomes the only global kill switch, and it is still bounded by consumer JWKS caches. A verifier that has the old `kid` cached keeps accepting old tokens for up to 1 h unless it is flushed (`docs/deploy.md:239-245` already says to flush consumers).

**F5. Stale "15 min" statements outside the ticket's listed files will become false:**
- `docs/consumers.md:148`, `:294`, `:368`
- `docs/deploy.md:376`, `:493`, `:497`, `:568`, `:585`
- ADR-0001:42, ADR-0007:102, ADR-0005:19 ("Security is equivalent for a 15-minute token")
- ADR-0008 `--clear` amendment (`:67-68`)
- ADR-0011 §6 (`:40`, "side effects still need a valid token (TTL 900 s)"). Its argument still holds, because Max-Age 600 is not tied to the TTL, but the number is wrong.
- The `app/cli.py:109` message wording.

**F6. Leaked or XSS-stolen tokens.** Today an XSS on a consumer origin or on admin-portal can already mint tokens while the victim's tab is open (credentialed `/auth/token`). At 12 h, a stolen token keeps working for 12 h after the tab closes, from any client. Admin CORS does not limit non-browser clients. ADR-0011's "host takeover = admin compromise" is now a 12 h compromise per token, not 15 min.

## Recommendations

**R-a. Hard upper bound, exactly 43200 (AC1, R5).** In `app/core/config.py:86`, use `Field(default=900, ge=60, le=43200)`. Don't use a looser cap such as 86400: a typo like `432000` (5 days) must fail startup.
- In `tests/test_config.py:182`, change the parametrize to `[59, 43201]`.
- Add a positive test that `Settings(access_token_ttl_seconds=43200)` loads.
- Keep the default at 900.
- Optionally log the effective TTL once at startup (not secret), so ops can see what production runs with.
- Add a comment for the key in `.env.example` (no value) stating the bounds.

**R-b. Issue test (AC2).** With TTL 43200: `exp - iat == 43200`, `expires_in == 43200`, and the token still verifies with the 30 s leeway in `tests/jwt_verifier.py`.

**R-c. ADR-0005 point 6 amendment (AC3).** Write it as a dated amendment, not a silent edit. It should cover:
- **New bounds** 60–43200 and the default 900. Production 43200 is set in the deploy repo by the owner (USR-13, 2026-11-17, TuDo D46). Record that the softer options were declined.
- **Accepted risks:**
  1. Global setting: every audience, `admin` included.
  2. For consumer apps, disable, role removal, org suspension, assignment expiry, email change and logout reach a token only at `exp` (≤ 12 h + leeway).
  3. A leaked token works for ≤ 12 h on its audience.
  4. Admin tokens: permissions and status are re-read per request, so revocation is immediate. Logout, password reset and disable-then-re-enable within the TTL do **not** end a stolen admin token.
  5. Rotation needs ≥ TTL + leeway (about 12.5 h) before removing the old key. Emergency rotation is the only global kill switch, bounded by consumer JWKS caches (≤ 1 h unless flushed).
- **Unchanged:** no refresh token, no denylist, no introspection.
- **Follow-ups (not in this ticket):**
  - a per-user `tokens_valid_after` cutoff checked on admin reload, which would close F2 (a)–(c) for admin at no consumer cost;
  - capping `exp` at the earliest assignment expiry (F3);
  - a per-audience TTL, if the owner ever wants admin short again.
- Also fix the "15-minute token" sentence in §1.

**R-d. `docs/consumers.md` (AC3).**
- Replace every 15 min statement with "up to 12 h (`ACCESS_TOKEN_TTL_SECONDS`)".
- **New rule: never cache an authorization decision beyond the request.** Map `roles` through the catalog per request. Don't turn a token into a longer app session, and if you do, its expiry must be ≤ the token's `exp`.
- **New rule: sensitive actions may require a fresh token.** A consumer may refuse tokens with `now - iat > N` (for example 900 s) on destructive or sensitive actions and answer 401. The SPA silently re-mints through `/auth/token` with the cookie, which re-checks status, verification, membership and roles. This is the cheapest way to get back the old latency where it matters, with no server change.
- If an app needs immediate bans, keep an app-local deny list by `sub`.
- Strengthen the storage rules (already at `:74`, `:115`): in memory only, never `localStorage`/`sessionStorage`/non-HttpOnly cookies/URLs/logs. Never log `Authorization` headers (reverse proxy, APM/Sentry breadcrumbs). Developer tokens (the TuDo dev-script use case): not in committed `.env`, shell history, CI logs or chat; treat them as a 12 h password.
- On logout, the SPA drops its in-memory token.
- Note that the verifier leeway stays ≤ 60 s.

**R-e. `docs/deploy.md` (AC3).**
- Rotation step 3: "TTL (43200 s) + leeway (60 s) + `max-age` (300 s), about 12.5 h". Say the wait is derived from the deployed TTL, so whoever changes the TTL must re-derive it. Also say: never shorten the wait when the TTL was raised less than one old TTL ago.
- Add an **incident runbook** line:
  - compromised consumer account: disable the user; tokens die at `exp` (≤ 12 h), or do an emergency rotation and flush consumer JWKS caches for an immediate global cut;
  - compromised staff account: revoke the admin role or disable the user, and **do not re-enable within 12 h** (F2c);
  - use the `jti`/`user_id` from the `token_issued` log to scope the incident.
- Update `:376`, `:493`, `:497`, `:568`, `:585`.

**R-f. Other stale numbers (R7).** Make the one-line number fixes in ADR-0008 (`--clear`), ADR-0011 §6, ADR-0001 and ADR-0007, and the `cli.py:109` wording, if the planner treats them as part of AC3 "state the new lifetime". Otherwise list them as follow-ups. Don't leave them silently wrong. The spec and identity-architecture D7 text go to the owner (F1).

## Risks

These would make 12 h unsafe enough to flag to the owner before setting production to 43200. They are checks in the consumer repos, not in this repo.
1. **Web storage.** Any consumer (admin-pan, ricotdin, TuDoCuaTui) that stores tokens in `localStorage`/`sessionStorage` or in a JS-readable cookie. Any XSS then yields 12 h tokens. For admin-pan this is a staff-API compromise for 12 h, limited only by the per-request reload.
2. **Long-lived dev tokens.** Copied into files, CI or shared tooling (the TuDo dev-script motivation).
3. **Logged headers.** Consumer backends or proxies that log `Authorization` headers.
4. **Cached authorization.** Consumer backends that cache authz by token, or mint their own longer sessions from it.
5. **Destructive actions trusting `roles` alone.** Consumers with destructive or financial actions that rely only on token `roles`, with no freshness check or local deny list.
6. **The admin gaps in F2 (logout, reset, re-enable).** They are the main real-world gap, because staff accounts are the high-value target.
7. **Rotation done on the old schedule.** An operator who rotates by the old 25-minute schedule after the switch to 12 h would break every live token. Mitigation: R-e wording, and tie the runbook number to the env value.

## Open questions

1. Who updates `docs/spec-v1.md` §5.8 (and OQ8) and the identity-architecture D7 row: is it part of this PR, or a separate `/idea` or product-manager edit? (F1)
2. Should the stale numbers in ADR-0001/0007/0008/0011 and `cli.py` be fixed in this ticket or listed as follow-ups? (R-f, R7)
3. Should the `tokens_valid_after` follow-up for admin (F2) be opened as a ticket now, given that staff tokens become 12 h?
4. Can the owner confirm that admin-pan, ricotdin and TuDoCuaTui keep tokens in memory only before production goes to 43200? (Risk 1)

Relevant files:
- /home/user/org-admin-api/app/core/config.py
- /home/user/org-admin-api/tests/test_config.py
- /home/user/org-admin-api/app/modules/tokens/service.py
- /home/user/org-admin-api/docs/adr/0005-access-token-signing.md
- /home/user/org-admin-api/docs/adr/0008-admin-api-authentication.md
- /home/user/org-admin-api/docs/adr/0011-admin-cors.md
- /home/user/org-admin-api/docs/consumers.md
- /home/user/org-admin-api/docs/deploy.md
- /home/user/org-admin-api/docs/spec-v1.md
- /home/user/org-admin-api/docs/identity-architecture.md
- /home/user/org-admin-api/app/cli.py
