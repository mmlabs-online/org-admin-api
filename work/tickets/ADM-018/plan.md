Status: READY

# Plan — ADM-018: Service read of org entitlements (`ent` token claim)

## Spec summary
- spec §5.6 (line 57): "Which org can use which product, with plan and limits; enable/disable per org. Readable by mmlabs services (service-to-service auth, §8 Q4)."
- spec §5.6 note (line 59, ADM-006): "Active = enabled AND org active AND app active (?active=true). [...] Service-to-service read (Q4) deferred to ADM-018."
- spec §2 (line 32): "mmlabs services | read an org's status and entitlements | products enforce what admin decided".
- spec §5.8 (line 69): "Verification by apps: [...] Claims: `sub`, `email`, `email_verified`, `aud`, `roles` [...], `org` (if scoped), `org_roles` (if scoped [...]), `iat`, `exp`."
- spec §8 Q4 (line 148, open): "How do products consume admin decisions — pull (API with service keys), push (webhooks/events), or both?"
- ADR-0005 §5: "Adding a claim is not breaking; removing or renaming one is."
- Owner decisions (ticket notes, these decide scope):
  - 2026-11-17, §8 Q4: "services consume entitlements through an additive `ent` claim (`{plan, limits}`) in the access token, added only when the `org` claim is present; staleness up to the token TTL (12 h) is accepted. No pull endpoint and no app-credential widening for now (R3 class 4 unchanged). Tick spec §8 Q4 with this answer."
  - 2026-11-18: "when the token carries `org` but the org has no active entitlement for the app (none / disabled / app disabled), emit `"ent": null` (the key is always present with `org`; object when entitled). Token issuance is never gated on entitlements."
  - 2026-11-18: "a `limits` key the app needs but the entitlement lacks means the app's own default (recommended 0 / deny), never "unlimited" — document in consumers.md."

The `null` decision overrides the software-architect, identity and api-platform consults, which recommended leaving the claim out. It matches the security consult (R-1).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Q4 decision recorded in a new ADR "Entitlement consumption". R3 and ADR-0016 are not amended because class 4 is not widened. | `docs/adr/0017-entitlement-consumption.md` exists with Status Accepted (owner 2026-11-17/18) and covers every item in Approach step 5. ADR-0005 has an "ADM-018" amendment. `CLAUDE.md` R3 and ADR-0016 are byte-for-byte unchanged (`git diff --stat` shows neither). Spec §8 Q4 is ticked with the answer and date. |
| 2 | (If pull) `GET /api/v1/apps/{key}/entitlements/{org_id}` with an app credential | **N/A by owner decision (2026-11-17): no pull endpoint.** Verify that no route is added: `tests/test_route_auth_classes.py` is unchanged and green. |
| 3 | Decide whether `mmak_` keys need a scope | **Answered "no" (N/A).** With no pull endpoint, `mmak_` keys stay manifest-only (ADR-0016). This is recorded in ADR-0017. No code change. |
| 4a | `ent` claim present only together with `org` | `test_token_without_org_has_no_ent` (no `ent` key even when the org is entitled). `test_token_happy_path` exact claim set is unchanged. |
| 4b | With `org` and an active entitlement: `ent == {"plan", "limits"}` with exact DB values; `limits` is `{}` when empty | `test_token_org_entitled_has_ent`, `test_token_org_entitled_empty_limits` |
| 4c | With `org` and no active entitlement (no row / `enabled=false` / app disabled / other app only): `"ent": null`, key present, same shape for every cause | `test_token_org_not_entitled_ent_null` (parametrized: none, disabled, other-app-only). The app-disabled leg is covered in `test_get_active_for_each_active_leg`, because `/token` refuses a disabled app earlier with 403 |
| 4d | Issuance is never gated on entitlements | The 4c tests assert status 200. `test_token_suspended_org_403` and `test_token_app_member_without_org_role_403` are unchanged and green (no regression) |
| 4e | `aud=admin` never carries `ent` | `test_token_admin_audience_issued` exact set (with `sid`) is unchanged. `test_token_admin_audience_org_id_422` is unchanged. New service-level guard test `test_issue_admin_never_has_ent` |
| 4f | Staleness accepted, and a fresh mint reflects changes | `test_token_ent_reflects_update_on_next_mint` (PATCH plan/limits, then disable → next token shows the new values, then `null`) |
| 4g | The single ADM-006 active definition is reused | `test_get_active_for_each_active_leg` (enabled, org active, app active, each one flipped). Code review: `get_active_for` filters with `repository.ACTIVE`; no second predicate exists |
| 5 | Class-4 allow-list / audit / rate-limit tests; `docs/consumers.md` section | Allow-list: `test_app_credential_allow_list_is_exact` is **unchanged** and green, which shows class 4 was not widened. Audit: N/A (minting is not an admin mutation, R4). Rate limit: N/A (`/token` keeps its existing limit; `test_token_rate_limited_429` unchanged). `docs/consumers.md` has the "Entitlements (`ent`)" section with all rules in Approach step 6 |
| 6 | Body and request contract unchanged | `test_token_happy_path` body set is unchanged. New `test_token_org_body_unchanged` checks the org token response body keys. A new `ent` case in `test_token_body_validation_422` checks that a request body `ent` gives 422 |
| 7 | Token size stays bounded | `test_token_worst_case_ent_size` (32 keys × 64 chars, values 2^53-1, plan 63 chars) → `len(access_token) < 8192` |
| 8 | No `ent` contents in logs | `test_token_logs_no_entitlement_values` (plan slug and limit key are absent from `caplog.text`) |
| 9 | `make check` green | `make check` |

## Files to create / modify
| File | Change | Required because |
|---|---|---|
| `app/modules/entitlements/repository.py` | Add `EntitlementRepository.get_active_for(org_id, app_id) -> EntitlementView \| None`: `_select().where(OrgEntitlement.org_id == org_id, OrgEntitlement.app_id == app_id, ACTIVE)` with `populate_existing=True` | AC 4g. Reuse the single `ACTIVE` definition (R1: ORM stays in the repository) |
| `app/modules/tokens/service.py` | Build `EntitlementRepository` in `__init__`. Inside the `org_id` branch, after `org_roles`, set `claims["ent"]` (object or `None`) when `app.key != ADMIN_APP_KEY`. Add a private `_ent_claim(view)` that re-validates plan/limits against the ADM-006 contract and falls back to `None` with a warning (see Approach 3) | AC 4a–4f, 7 |
| `tests/test_auth_token.py` | New token tests (see Tests). Add an `ent` case to `test_token_body_validation_422`. Extend the line-470 assertion with `"ent" not in claims` | R8, AC 4–8 |
| `tests/test_entitlements_repository.py` (new) | Repository test for `get_active_for` (each ACTIVE leg, wrong app, wrong org) | AC 4g. The app-disabled and org-suspended legs cannot be reached through `/token` |
| `tests/test_tokens_service.py` (new) | Service-level `test_issue_admin_never_has_ent` and `test_ent_out_of_contract_falls_back_to_null` | AC 4e (explicit guard) and the defensive fallback |
| `docs/adr/0017-entitlement-consumption.md` (new) | The Q4 decision record | AC 1, 3 |
| `docs/adr/0005-access-token-signing.md` | Append "Amendment: `ent` claim (ADM-018, 2026-11-18)": optional claim, key always present with `org` (object or `null`), never without `org`, never on `aud=admin`, verifiers must not require it, `REQUIRED_CLAIMS` unchanged. Risk 2: add `ent` to the frozen-claims list (as a note in the amendment; the original text is not rewritten) | Claims contract, AC 1 |
| `docs/consumers.md` | Add `ent` to the claims paragraph (line ~408-411). New "Entitlements (`ent`)" subsection after the org paragraph (line ~413-418). Item 11 (revocation lag): add entitlement disable/revoke/plan or limit change. Follow-up line: `mmlabs-auth` `ent` accessor | AC 5 |
| `docs/spec-v1.md` | §8 Q4 ticked with the owner's answer (2026-11-17) and the null rule (2026-11-18). §5.6: line 57 "Readable by mmlabs services through the `ent` access-token claim (§8 Q4, ADR-0017)"; line 59 note: replace "deferred to ADM-018" with "ADM-018: `ent` claim". §5.8 line 69: add `ent` to the claims list | Owner asked to tick Q4. Spec is the source of truth |
| `docs/identity-architecture.md` | Claims paragraph (line ~198-201) and D7 row (line 282): add `ent?`. `/auth/token` with `org_id` row (line 171): "adds `org`, `org_roles`, `ent` claims". New decision-log row D27 "Entitlement consumption (owner, 2026-11-17/18): `ent` claim; no pull; class 4 unchanged; ADR-0017" | Claims list must not drift |

Not touched (on purpose): `CLAUDE.md` (R3 unchanged), `docs/adr/0016-*.md`, `tests/test_route_auth_classes.py`, `app/modules/tokens/schemas.py` (request keeps `extra="forbid"`; response unchanged), migrations (no schema change), `.env.example`.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_auth_token.py › test_token_org_entitled_has_ent` | calechip org token; entitlement row `plan="pro"`, `limits={"seats": 5, "projects": 3}`. `claims["ent"] == {"plan": "pro", "limits": {"seats": 5, "projects": 3}}`. `set(claims)` == the org claim set plus `ent` (no `sid`). Decoded with `verify_reference` (verifier tolerates the extra claim) |
| `… › test_token_org_entitled_empty_limits` | `limits={}` → `claims["ent"] == {"plan": "pro", "limits": {}}` |
| `… › test_token_org_not_entitled_ent_null` (parametrize `none`, `disabled`, `other_app_only`) | status 200, `"ent" in claims and claims["ent"] is None`. `other_app_only`: the same org is entitled (`plan="secret"`) to a second `org_mode=required` app only; the calechip token has `ent: null` and `"secret"` does not appear in the token payload (no cross-app leak) |
| `… › test_token_without_org_has_no_ent` | org is entitled, token requested without `org_id` → `"ent" not in claims` |
| `… › test_token_ent_reflects_update_on_next_mint` | mint → PATCH row values directly (plan `basic`, limits `{"seats": 1}`) → new mint shows them → set `enabled=False` → new mint `ent is None` |
| `… › test_token_worst_case_ent_size` | 32 keys of 64 chars matching `LIMIT_KEY_PATTERN`, values `2**53-1`, 63-char plan → `ent` round-trips exactly and `len(access_token) < 8192` |
| `… › test_token_org_body_unchanged` | org token response `set(body) == {"access_token", "token_type", "expires_in", "audience"}` |
| `… › test_token_logs_no_entitlement_values` | like `test_token_logs_no_secrets`, for an org token with a distinctive plan/limit key. Neither string is in `caplog.text`; exactly one `token_issued` line |
| `… › test_token_body_validation_422` (add a parametrize case) | `{"audience": "stoktok", "account_id": …, "ent": {"plan": "x"}}` → 422 `validation_error` |
| `… › test_token_without_org_app_scope_only` (extend line 470) | `"ent" not in claims` |
| `tests/test_entitlements_repository.py › test_get_active_for_each_active_leg` | active row → view with plan/limits. `enabled=False`, org `suspended`, app `disabled` → `None` each |
| `… › test_get_active_for_scoped_to_org_and_app` | a row for (org A, app X) is not returned for (org B, app X) or (org A, app Y) |
| `tests/test_tokens_service.py › test_issue_admin_never_has_ent` | Call `TokenService.issue` directly with the admin app forced to satisfy the org path (org_mode set to `required` in the session, an org-scoped admin role assignment, and an entitlement row inserted directly). Assert `"ent" not in claims` and `"sid" in claims`. If DB constraints make the admin org path impossible to build, record that in impl-log and rely on the exact-set and 422 tests (the guard stays) |
| `… › test_ent_out_of_contract_falls_back_to_null` | a row inserted directly with 33 limit keys (DB CHECK only needs an object) → org token `ent is None`, one warning log line `ent_out_of_contract` with the entitlement id and no limit keys |
| Unchanged and must stay green: `test_token_happy_path`, `test_token_admin_audience_issued`, `test_token_admin_audience_org_id_422`, `test_token_suspended_org_403`, `test_token_app_member_without_org_role_403`, `test_token_rate_limited_429`, `test_token_logs_no_secrets`, `tests/test_route_auth_classes.py › test_app_credential_allow_list_is_exact`, all `tests/test_entitlements_api.py` | no regression; class 4 not widened |

Entitlement rows in token tests are created with the ORM directly (`OrgEntitlement(org_id=…, app_id=…, plan=…, limits=…, enabled=…)`, as `tests/test_audit_coverage.py:201` does). There is no need to go through the staff API.

## Approach
1. **Repository (R1).** Add `get_active_for` to `EntitlementRepository`. It reuses `_select()` and `ACTIVE`, returns `_view(row)` or `None`, and uses `populate_existing=True` like `get_view`. Do not touch `find()` or add a second "active" predicate.
2. **Token service (R1, R3 unchanged).** In `TokenService.__init__` add `self.entitlements = EntitlementRepository(session)`. In `issue()`, inside `if data.org_id is not None:` and after `claims["org_roles"] = org_roles`:
   ```python
   if app.key != ADMIN_APP_KEY:  # invariant: `ent` never sits next to `sid`
       view = await self.entitlements.get_active_for(data.org_id, app.id)
       claims["ent"] = None if view is None else _ent_claim(view)
   ```
   The lookup is keyed only by the claim `org` and the token `aud` app, so it cannot leak another app's or org's entitlement. No new error and no new branch that refuses issuance. Every existing outcome stays identical. No audit entry (R4 covers admin mutations only). No rate-limit change.
3. **Defensive contract check (security consult R-3).** `_ent_claim(view)` validates `view.plan` against `PLAN_PATTERN` and `view.limits` against a module-level `TypeAdapter(Limits)` (both from `entitlements/schemas.py`). It returns `{"plan": view.plan, "limits": dict(view.limits)}` or, when the row breaks the API contract (for example a manual SQL write), logs `ent_out_of_contract entitlement_id=<id> aud=<key>` at WARNING and returns `None` (fail closed). The log line never contains plan or limit values (R5/R11).
4. **Logging (R5, R11).** The `token_issued` line is unchanged: no `ent`, plan or limits (consults allowed `ent=present|null` at most; choosing nothing keeps the change minimal).
5. **ADR-0017 "Entitlement consumption"** (Status: Accepted, owner 2026-11-17 and 2026-11-18). Context: spec §5.6, §8 Q4, ADM-006. Decision:
   1. Claim only. A pull endpoint is rejected for now and push stays spec §5.7 Later.
   2. R3 class 4 and ADR-0016 unchanged, so `mmak_` keys need no scope (ticket AC 3 answered "no").
   3. Shape: `ent: {"plan": str, "limits": {str: int}}`. `limits` is always an object, possibly `{}`. No `id`, `enabled`, `active`, `app_key`, `org_id` or timestamps.
   4. Presence: the key is present iff `org` is present. It is an object when the entitlement is active (ADM-006 `ACTIVE`). It is `null` for no row, a disabled row or a disabled app, with one value for every cause. It is never present without `org` and never on `aud=admin` (explicit guard).
   5. Issuance is never gated on entitlements.
   6. Staleness is up to TTL plus leeway (12 h in production). Mitigation: a fresh-token rule (consumers item 13).
   7. Out-of-contract rows fail closed to `null`.
   8. Visibility: `plan` and `limits` are readable by every org member holding the token and by the app's browser code. Staff must not put confidential terms in them.
   9. Size bound: about 3.8 KB worst case for the claim, fixed by the ADM-006 validation.
   10. A missing limit key means the app's default (recommended 0 or deny), never unlimited.

   Rejected alternatives: pull with an app credential (widens class 4); omitting the claim (cannot tell "not entitled" from "pre-deploy token"); gating `/token` (changes v1 behaviour). Consequences: rollout window; follow-ups (see Assumptions).
6. **`docs/consumers.md` "Entitlements (`ent`)" subsection.**
   - `ent` is authoritative only after full verification (signature, `iss`, `aud` = your app, `exp`, `typ`) and only together with `org`. It describes the `org` org for the `aud` app only, so scope enforcement by `org`.
   - `ent: null` on an org token means not entitled. Fail closed. An org token with no `ent` key was minted before the ADM-018 release: treat it as not entitled too, or re-mint.
   - Rollout: start enforcing at least TTL plus leeway (about 12.5 h) after the release, or only for tokens with `iat` at or after the release time. Staff must grant entitlements (calechip has none yet) before a product enforces anything.
   - Do not reject a token for `ent` being `null`. Identity and roles stay valid. `ent` grants nothing alone: access still comes from `org_roles` through the catalog.
   - `limits` are ceilings. Enforce them server-side against your own usage counts on every request. The browser may show plan or limits as a UI hint only, and never trust values sent back by the client.
   - A missing limit key means the app's own default (recommended 0 or deny), never unlimited. Ignore unknown keys. Values are integers in 0..2^53-1 (JS-safe).
   - Staleness: disable, revoke, plan or limit changes reach a token only at `exp` (up to 12 h in production). Use `iat` for the snapshot age and require a fresh token for quota-consuming or high-value actions (item 13).
   - Do not cache `ent` beyond the request (item 12).
   - `plan` and `limits` are visible to every org member.
   - Token size up to about 5 KB, so do not store the token in a cookie.

   Also add `ent` to the claims paragraph and to item 11, and add the follow-up line "`mmlabs-auth` needs an `ent` accessor (None unless `org`)" next to the `org_roles` follow-up.
7. **Spec, ADR-0005 and identity-architecture edits** as listed in Files.
8. Run `make check`. There is no migration, so `alembic check` stays clean (R2 not triggered).

Rules: R1 (repository holds the ORM; service builds the claim); R2 (no schema change); R3 (no new endpoint, class 4 unchanged); R4 (no mutation); R5/R11 (no plan or limits in logs; no secrets); R6 (no new error code); R7 (follow-ups stay out); R8 (tests above); R9 (`/auth/token` request and response unchanged; additive claim only, which ADR-0005 §5 permits); R10 (no billing: `plan` is a slug, not a price).

## Specialist input used
- **software-architect:** adopted repository method reusing `ACTIVE`, placement after `org_roles`, explicit admin guard, `limits` always an object, no extra fields inside `ent`, no gating, ADR-0017 plus ADR-0005 amendment, rollout note, no audit, `test_app_credential_allow_list_is_exact` unchanged, worst-case size test. **Rejected** "omit the claim, never send null": overridden by the owner (2026-11-18, `ent: null`).
- **identity-access-engineer:** adopted R-1, R-3 (fail closed; do not reject the token for a null `ent`; ignore unknown keys; missing key = app default, now confirmed by owner), R-4 (server-side enforcement, no caching, `ent` grants nothing alone, fresh-token mitigation), R-5, R-6, R-7 (no values logged; chose no log field at all), R-8 as a follow-up. The open question "absent vs null" was resolved by the owner in favour of `null`.
- **security-architect:** adopted R-1 (null with `org`, never without), R-2 (no logging; visibility note, placed in ADR-0017 and consumers.md rather than a separate staff doc), R-3 (out-of-contract check falls back to `null`; DB CHECK on the key count as a follow-up), R-4 tests (incl. body `ent` → 422, cross-app, admin, size), R-5 consumers rules.
- **api-platform-engineer:** adopted the object shape, no echo in the response body, docs list (spec §5.6/§5.8/§8, identity-architecture lines 171/198/282), N/A marking of the pull/scope/audit/rate-limit ACs, a body-unchanged test, a verifier-tolerance check (via `verify_reference` in `test_token_org_entitled_has_ent`). **Rejected** "omit when not entitled" (owner decided `null`).

## Assumptions
- Owner Q4 answer and 2026-11-18 notes are final, so the "planner should return BLOCKED" note from 2026-11-17 no longer applies.
- `null` is chosen over omission for all "not active" causes, including a row rejected by the out-of-contract check (fail closed).
- `TokenService` calling `EntitlementRepository` directly is allowed under R1. It already uses `SessionRepository` and `UserRepository` the same way.
- The worst-case budget 8192 bytes is the test bound (computed worst case is about 5 KB in total).
- Needs owner's decision (non-blocking; defaults chosen):
  1. Should `token_issued` log `ent=present|null`? Default: no change to the log line.
  2. Should an out-of-contract row fail closed to `null` with a warning? Default: yes. The alternative is to raise 500.
  3. Should a pre-ADM-018 org token with no `ent` key be treated as not entitled in consumers.md? Default: yes, and re-mint.
- Follow-ups (R7, not in this diff):
  - `mmlabs-auth` `ent` accessor and a server-side limit helper (separate repo).
  - DB CHECK on the `limits` key count and size in `org_entitlements`.
  - A shorter TTL for `org_mode=required` audiences if revocation lag matters.
  - A pull endpoint or push (§5.7) if 12 h staleness becomes unacceptable.

## Blocking questions
None.

## Split proposal
None. About 40 lines of app code, about 250 lines of tests and about 120 lines of docs. This is one feature (spec §5.6 consumption).

## Data model / migration
None. Reads the existing `org_entitlements` table (ADM-006) through the unique `(org_id, app_id)` index, adding one indexed query per org-token mint.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/token` (existing, R3 class 2 cookie) | unchanged `{audience, account_id, org_id?}` (`extra="forbid"`, so a body `ent` gets 422) | Body unchanged `{access_token, token_type, expires_in, audience}`. The **access token** gains `ent` iff `org` is present: `{"plan": str, "limits": {str: int}}` when active, else `null`. Never on `aud=admin` | unchanged (200, 401, 403, 422, 429); no new codes | none (cookie class) |

No new endpoint. R3 class 4 (`PUT /api/v1/apps/{key}/manifest` only) is unchanged.
