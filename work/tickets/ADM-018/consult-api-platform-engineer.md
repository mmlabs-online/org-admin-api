ADM-018 consult (api-platform-engineer, read-only). Based on the ticket, ADR-0005 §5, ADR-0015, consumers.md, tokens/service.py (lines 88-112), entitlements models/schemas, and tests/test_auth_token.py. I did not read ADM-006's active-entitlement code, so the "active" lookup is unverified.

FINDINGS
- Q4 is already answered by the owner (ticket notes, 2026-11-17): additive `ent` claim `{plan, limits}`, only when `org` is present, 12 h staleness accepted, no pull endpoint, R3 class 4 unchanged. spec-v1 §8 Q4 (line 148) is still unticked. The spec has no explicit `ent` mention in §5.8 line 69 (claims list) or §5.6 (line 57 says "readable by services (Q4)"; line 59 says deferred to ADM-018).
- The ADR-0005 §5 contract says "adding a claim is not breaking" and names `org` and `org_roles` as precedent. `ent` fits the same rule. Verifiers use `REQUIRED_CLAIMS = exp iat sub jti iss aud` and must not require extras.
- Claims are built in app/modules/tokens/service.py around lines 88-100: the `org` branch already sets `org` and `org_roles`. This is the single place to add `ent`.
- Entitlement model: `plan` is a string matching `^[a-z0-9][a-z0-9_-]{0,62}$`; `limits` is a JSON object, typed `dict[str, int]`.
- tests/test_auth_token.py has exact `set(claims) == {...}` assertions at lines 90-100 (no-org token) and ~339 (admin token, includes `sid`). Neither has `org`, so neither should change. Line 470 asserts `"org" not in claims and "org_roles" not in claims`.

RECOMMENDATIONS
1. Shape (AC "If token claim"): `ent: {"plan": "<str>", "limits": {"<str>": <int>}}`. Keep it an object, not flat claims such as `plan`/`limits`. That leaves room for later additive keys inside `ent` (for example `app`, `since`) and avoids collisions with registered claim names. Always emit both `plan` and `limits`; use `{}` for empty limits, never null or omitted. Do not add `org_id` or `app_key` inside it (`org` and `aud` already carry them).
2. Presence rules (state them in the ADR and consumers.md):
   - `ent` appears only when `org` is present AND the org has an active entitlement for this app. "Active" means the ADM-006 definition: enabled AND org active AND app active.
   - If there is no active entitlement, omit `ent`. A missing `ent` means "not entitled".
   - Whether `/auth/token` should still issue the token in that case is an open question (see below). Do not add a new error code in v1 by accident.
   - No `ent` on non-org tokens or `aud=admin` tokens.
3. Compatibility: purely additive, no version bump, no changelog deprecation. Document that verifiers must ignore unknown claims, and that consumers must not treat `ent` as required (same wording as the `sid` note).
4. Do not echo `ent` in the /auth/token response body. The body is frozen as `{access_token, token_type, expires_in, audience}` (test line 77, exact set). Keep one source of truth, which is the token. Echoing it would double the contract surface, and clients would be tempted to trust unsigned data.
5. Implementation: add a small lookup in the entitlements repository/service (R1: service raises `AppError`, no ORM in the router). The token service calls it only inside the `org` branch, reusing ADM-006's active query so the definitions cannot drift. Do not duplicate the "active" predicate.
6. Docs:
   - New ADR "Entitlement consumption" (next free number). It records the Q4 decision, the claim contract, the presence rule, the 12 h staleness, and that class 4 is not widened.
   - ADR-0005 §5: append "`ent` (ADR-00NN)" next to `org_roles`, or add an amendment note like the `sid` one.
   - consumers.md: add `ent` to the claims line at ~408-411. Add a short subsection after the org paragraph at ~413-418: shape, presence, "enforce limits from `ent.limits`; absence means not entitled; stale up to the TTL". Add to checklist item 11 (revocation lag): a disabled entitlement, a plan change or a limit change reach a token only at `exp`.
   - spec-v1: tick §8 Q4 with the owner's answer and date. Update §5.8 line 69 to list `ent`. Update §5.6 line 57-59 to point to the `ent` claim and remove "deferred to ADM-018".
   - identity-architecture.md: update the claims line at ~198 and the endpoint table near 171.
   - R3 in CLAUDE.md: no change (class 4 unchanged). `test_app_credential_allow_list_is_exact` needs no change. The ticket's AC lists it conditionally, so mark it N/A.
   - Ticket AC: mark the pull-endpoint, scope, rate-limit and audit items N/A. Record the "mmak_ scope" decision as "not needed, no widening".
7. Tests (R8):
   - Add to tests/test_auth_token.py:
     - org token + active entitlement: `claims["ent"] == {"plan": ..., "limits": {...}}`, and `set(claims)` equals the org set plus `ent`.
     - org token with no entitlement: `"ent" not in claims`.
     - entitlement disabled: no `ent`.
     - org suspended: still the existing 403.
     - app disabled: no `ent`.
     - a plan/limits change is reflected in the next minted token.
     - non-org token: no `ent`.
     - admin token: no `ent`.
   - Leave unchanged: the exact set at lines 90-100, the set at ~339, and the response-body set at line 77.
   - Extend the line-470 assertion to include `"ent" not in claims`.
   - Add a verifier-tolerance check, e.g. `verify_reference` accepts a token with `ent`.
   - Add a regression-style test that the response body keys are unchanged for an org token.
   - Run the existing org_roles tests in tests/test_auth_token.py (~398-455); they likely assert specific keys, not the full set, so they should stay green, but verify.

RISKS
- Token size: `limits` is unbounded in the model. Check ADM-006 for caps on key count and length. If there are none, a large `limits` could bloat the bearer token and exceed header limits at proxies (about 8 KB). Recommend a cap at write time, or a guard here, as a follow-up in R7 terms.
- Staleness: revoking an entitlement or lowering a limit takes up to 12 h to reach a token. This is an owner-accepted risk. Document it prominently, since services will use `ent` for entitlement gating. Consumers must not treat `ent` as a hard security boundary for revocation.
- Information exposure: `ent` is visible to the end user, since JWTs are readable. Plan and limits are not secret. Confirm that is acceptable and that limits hold no internal or billing data (R10: no billing).
- Extra DB read per org-token mint. This is minor, but it should be a single indexed query.
- Do not let `ent` leak into admin or no-org tokens, and do not change `REQUIRED_CLAIMS`.
- A claim name of `ent` is short and could be mistaken for a standard claim. It is not registered at IANA to my knowledge. It is fine, but note it in the ADR.

OPEN QUESTIONS
1. When an org is a member and not suspended but has no active entitlement for this `org_mode = required` app, should `/auth/token` still issue the token (without `ent`) or return 403? The owner's wording ("added only when org present") implies it still issues the token. I recommend issuing without `ent`; refusing would be a behaviour change for existing orgs.
2. Should `ent` be required in the ADR for `org_mode = required` apps (consumers must treat absence as not entitled)? I recommend yes: document absence as "no active entitlement".
3. Is there a cap on `limits` size in ADM-006? If not, add one as a follow-up.
4. The ticket says "Owner decides Q4 first ... new ADR". The answer is now recorded in the ticket, so the planner should treat the ticket as unblocked, writing the ADR and ticking Q4 as part of this ticket rather than returning BLOCKED.

Relevant files:
/home/user/org-admin-api/work/tickets/ADM-018/ticket.md
/home/user/org-admin-api/app/modules/tokens/service.py
/home/user/org-admin-api/app/modules/entitlements/ (models.py, schemas.py)
/home/user/org-admin-api/tests/test_auth_token.py
/home/user/org-admin-api/docs/adr/0005-access-token-signing.md
/home/user/org-admin-api/docs/consumers.md
/home/user/org-admin-api/docs/spec-v1.md
/home/user/org-admin-api/docs/identity-architecture.md
