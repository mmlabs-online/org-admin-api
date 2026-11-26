# Consult — security-architect — USR-32 (2026-11-24)

**Findings**
- F1 (High, the main one): the write-side validation the brief assumes is not there. `users.display_name` is a bare `String(100)` with no CHECK constraint (`app/modules/users/models.py:31`, `migrations/versions/20261111_7b847bdc5230_create_users.py:32`). No schema anywhere writes it. Grep turns up only read models (`app/modules/users/schemas.py:18`, `app/modules/auth/schemas.py:90`). `app/core/text.py:9,17` (`DISALLOWED_CATEGORIES`, `reject_disallowed_categories`) is never applied to it. Whatever is stored, from a seed, import, SQL or a future endpoint, would be signed as-is.
- F2: the token already carries PII. `email` is at `app/modules/tokens/service.py:131`. `name` adds more PII, but the token's sensitivity class stays the same. The issuance log (`service.py:140-148`) logs only ids, so it is safe as long as the claim is never added there.
- F3: staleness. Disabled users get no token (`service.py:93`), but live tokens last up to the TTL (12 h; ADR-0005 lines 42-44, 82-87). A name change or a disable reaches apps only at `exp`, the same as `email`. Note that ADR-0025 nulls `display_name` for disabled accounts on the member read, so the token is less fresh than that read.
- F4: even with the category filter, confusables (Cyrillic "а" vs Latin "a") and "Support Team"-style names stay possible. Filtering cannot stop that; apps have to treat the name as an unverified label.
- F5: size impact is small. 100 chars × up to 4 UTF-8 bytes, base64 encoded, adds at most about 540 B.

**Recommendations**
- R1: on issuance, NFC-normalise the value and run `reject_disallowed_categories` on it (defence in depth). If it fails, omit the claim (fail closed, like `_ent_claim` at `service.py:38-47`) and log only `user_id`. Add a CHECK `char_length(display_name) BETWEEN 1 AND 100` (`usage/models.py:51` is the precedent). Tests: bidi U+202E, zero-width U+200B, 101-char name, Cc chars all lead to no claim.
- R2: leave the claim out when the name is unset; don't send `null`. That fits "optional" and "ignore unknown claims".
- R3: no `name` in `aud=admin` tokens. The admin UI already reloads the user on each request (ADR-0008), so the claim is not needed there.
- R4: in `docs/consumers.md` near item 8 (line 521) and in spec §5.8, state that `name` is untrusted, user-controlled display text. Render it as text only, never as HTML or markdown or in an identity or authorization decision, and use `sub` as the identity key. It may be stale for up to the TTL, and apps must not log decoded claims.

**Risks**
- Without R1, the first display-name write path (or a manual DB edit) puts unfiltered text into a signed token. Apps treat a signed token as trusted, which makes impersonation through bidi characters in their UIs possible.

**Open questions for Ming (defaults)**
1. Should a display-name write path and its validation come first, as a separate ticket? Default: yes. USR-32 still ships R1, so it does not depend on that ticket.
2. Claim name `name`, left out when unset? Default: yes.
3. Should admin tokens get it? Default: no.
