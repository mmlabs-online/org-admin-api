## Attempt 1

## Security review: ADM-018 (`ent` claim on org tokens)

I read the full `git diff origin/main...HEAD` along with `app/modules/tokens/{service,schemas}.py`, `app/modules/entitlements/{repository,schemas,models}.py`, `app/core/access_token.py`, the ADR-0017 and ADR-0005 amendments, `docs/consumers.md` and the ticket folder. As instructed, I did not run `make check` or pytest.

### Focus checks
- **Scoped to exactly (org claim, aud app). OK.** `get_active_for(data.org_id, app.id)` runs only after `org_role_keys` passes (`app/modules/tokens/service.py:111-119`). `app` is the calling app, and `audience == app.key` is enforced at line 79. `uq_org_entitlements_org_id_app_id` makes `one_or_none()` safe. Tests check cross-app (`other_app_only` → null) and cross-org/app at the repository level (`tests/test_entitlements_repository.py`).
- **Uses the single ACTIVE definition. OK.** `app/modules/entitlements/repository.py:93-100` reuses `_select()` + `ACTIVE` (enabled AND org active AND app active). All three inactive cases are tested.
- **Admin tokens never carry `ent`. OK.** There is an explicit guard at `service.py:117`, on top of admin `org_mode=none`. `tests/test_tokens_service.py` forces admin to `org_mode=required` and asserts that `ent` is absent while `sid` is present.
- **Client cannot inject `ent`. OK.** `TokenRequest` keeps `extra="forbid"`, and there is a test showing a body with `ent` gets 422. Claims are built on the server and `**claims` is spread before `iat/exp/jti`, so `ent` cannot override registered claims.
- **Out-of-contract rows fail closed. OK.** `_ent_claim` (`service.py:51-60`) re-checks plan with `re.fullmatch(PLAN_PATTERN)` and limits with a strict `TypeAdapter(Limits)` (StrictInt rejects bool and float; max 32 keys; key pattern; 0..2^53-1). On failure it returns `None`. The DB CHECKs (`plan_format`, `jsonb_typeof(limits)='object'`) mean `dict(ent.limits)` in `_view` cannot raise on a non-object, so a bad row cannot turn into a 500 that blocks token issuance.
- **No plan or limits in logs. OK.** The warning logs only `entitlement_id` and `aud`, and `token_issued` (`service.py:140-148`) is unchanged. Two tests assert that values do not reach the log.
- **Token size bound. OK.** The worst case is about 3.8 KB for the claim, enforced at issuance by the strict validator. There is a worst-case size test.
- **REQUIRED_CLAIMS unchanged. OK.** It is still `["exp","iat","sub","jti","iss","aud"]` at `app/core/access_token.py:13`.
- **Consumer docs. OK.** `docs/consumers.md:420-445` covers: authoritative only after full verification and only together with `org`; null or a missing key means not entitled, fail closed; rollout and enforcement timing (TTL plus leeway, or `iat` ≥ release); limits enforced server-side, with browser values as hints only; a missing limit key means the app default (deny), never unlimited; staleness up to `exp` with the fresh-token rule; no caching; visibility to org members.

### Findings
No 🔴 blockers.

🟡 should-fix: none.

💭 notes (optional, all small doc tweaks):
1. `docs/consumers.md:403` (item 13): the re-mint "re-checks status, verification, membership and roles". It should also say "and entitlements (`ent`)", because the `ent` section (line ~437) points to item 13 as the staleness mitigation. Fix: append "and the `ent` entitlement snapshot" to that sentence.
2. `docs/consumers.md:442`: "`plan` and `limits` are visible to every org member." It would help to add "and to your browser code; staff must not put confidential terms in them". ADR-0017 §8 says this, but staff working in the admin UI read neither document. Follow-up: put a hint next to the plan and limits fields in the admin-pan entitlement form.
3. `app/modules/tokens/service.py:58`: an out-of-contract row logs a warning on every mint for that org/app. At a 12 h TTL the volume is negligible, and the line contains no secrets or values. No change needed.
4. `service.py:60` emits `dict(view.limits)`, not the validator output. Under `strict=True` these are identical, so this is fine. Keep `strict=True` if the code is refactored later, so that coercion like `"5"` → 5 or `True` → 1 never starts silently re-shaping values.

### Follow-ups (out of scope; already listed in ADR-0017 Consequences)
- A DB CHECK on the size and number of keys in `limits`, as defense in depth for writes that bypass the API.
- An `ent` accessor in `mmlabs-auth` that returns None unless `org` is present, so consumers stop hand-parsing the claim.
- A shorter TTL for `org_mode=required` audiences, or a pull/push channel, if the 12 h downgrade window becomes a cost risk.

**Verdict: PASS (security). No blockers; four optional notes, mostly doc wording.**
