# Consult — identity-access-engineer — USR-32 (2026-11-24)

**Findings**
1. Claims are built in `app/modules/tokens/service.py:107-139`. Optional claims go into `claims` (`org`, `org_roles`, `ent` at :115-119; `sid`, admin only, at :122) and are spread into the payload at :134. The `token_issued` log line (:140-148) contains only ids, so nothing personal is logged today.
2. `users.display_name` is `String(100)`, nullable, with no CHECK constraint (`app/modules/users/models.py:31`; migration `20261111_7b847bdc5230_create_users.py:32`). **No code path writes it.** Signup and auth schemas have no such field (`app/modules/auth/schemas.py:90` is output only), `users/schemas.py:18` is read-only, and the only setter is a test (`tests/test_auth_accounts.py:73`). So no length or normalisation rule is enforced when the name is written, and in production every user's name is NULL. As specified, the claim would be absent for every token until some ticket adds a way to set the name.
3. Earlier claim additions were recorded as amendments to ADR-0005, not as new ADRs: `sid` (`docs/adr/0005-access-token-signing.md:102-110`) and `ent` (:115-121). Both say "adding a claim is not breaking; verifiers must not require it; `REQUIRED_CLAIMS` unchanged". ADR-0005 Risk 2 (:82-85) already lists the claims that stay frozen until `exp`.
4. A test already checks that marker strings never reach the logs (`tests/test_auth_token.py:881-898`). The name test can reuse it.

**Recommendations**
- **Claim name `name`**, the OIDC standard claim (OIDC Core §5.1), so standard libraries map it. Do not invent `display_name`. This is not "full OIDC" (R10); it is one registered claim name.
- **Leave the claim out when the name is unset**, not `null`. That matches OIDC practice and keeps tokens small. It differs from `ent`, where `null` carries meaning; document the difference in the amendment.
- **App tokens only** (`aud != admin`), as the ticket scope says. The admin UI already gets names from the admin API, and keeping admin tokens minimal fits the pattern that `ent` is never on `aud=admin`. Add a test that `aud=admin` has no `name`.
- Record it as an **ADR-0005 amendment** ("`name` claim, USR-32"), add a D-row (D39) and a dated spec §5.8 line. ADR-0030 is not needed.
- Add `name` to Risk 2's frozen-claims list (stale for up to TTL, 12 h).
- Document that `name` is untrusted user text: render it as text, never use it for authorization or identity, and key users on `sub`. Follow ADR-0020 §7 and ADR-0025.
- Tests: name set → claim present; NULL → key absent; with and without `org_id`; marker name never appears in the logs.

**Risks**
- With no write path, the feature does nothing for real users until one exists.
- Since nothing validates names on write, a future editor could store control or bidi characters or overlong text. That editor must normalise the name (NFC, trim, strip control characters, 1–100 characters).
- `name` is personal data inside a bearer token readable by anyone holding it, so every app holding the token can read the name. This is acceptable, since `email` is already in the token.
- Client libraries (USR-08) do not need changes as long as they do not reject unknown claims. Confirm with a follow-up in that repo.

**Open questions for Ming (defaults)**
1. Claim `name`, left out when unset, app tokens only. Default: yes.
2. No write path exists. Ship now anyway, or first open a ticket for a self-service or staff `display_name` editor with normalisation? Default: ship USR-32 and add the editor ticket as a follow-up.
3. Record it as an ADR-0005 amendment plus D39 rather than a new ADR-0030. Default: yes.
