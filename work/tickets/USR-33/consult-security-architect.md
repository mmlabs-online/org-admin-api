# Consult — security-architect — USR-33 (2026-11-25)

## Findings
- A validated type for this already exists. `app/modules/usage/schemas.py:47-52` `DisplayName` = NFC (`nfc_if_str`), then strip and a 1–100 check (`_trimmed`, :28-36), then `reject_disallowed_categories`. Its 422 message is generic and does not echo the value.
- The 422 handler only returns `type`, `loc` and `msg`. It drops `input` and `ctx` (`app/core/errors.py:215-217`), so a rejected name is not echoed back.
- `DISALLOWED_CATEGORIES` (`app/core/text.py:9`) blocks Cc, Cf, Zl, Zp, Co, Cs and Cn. It does not block Mn/Me (combining marks, used for "Zalgo" text), other Zs spaces (NBSP, U+3000), or blank-looking letters such as U+3164 Hangul filler and U+2800 Braille blank. Python's `str.strip()` trims Zs at the ends but leaves them inside the name.
- The token issue path already fails closed: invalid names are dropped from the token and only the user id and app are logged (`app/modules/tokens/service.py:54-68`).
- `users.display_name` is `String(100)` with no CHECK constraint (`app/modules/users/models.py:20,31`). `usage_metrics` already has the same kind of CHECK (`app/modules/usage/models.py:51`).

## Recommendations
1. Move `DisplayName` into `app/core/text.py` and reuse it for users. Don't build a second copy (AC1). Treat an explicit `null` as "clear the name" and an empty string as 422, which keeps the rules unambiguous.
2. Add a cap on combining marks: reject more than 2 Mn/Me in a row after NFC. This stops Zalgo text overflowing the UI and stays safe for Vietnamese, which needs at most 2 after NFC. Collapse runs of whitespace into one U+0020 and reject U+3164, U+115F, U+1160, U+FFA0 and U+2800, so a name can't be "blank" while passing validation.
3. Don't use a block list for impersonation. Names are untrusted text and are already labelled UNTRUSTED (`bug_reports/schemas.py:57`). The real fix is in the UI: staff views always show the email or id next to the name. A short reserved list ("mmlabs", "admin", "support") gives false confidence and is easy to get around with homoglyphs.
4. The self-service route should be the cookie class (R3 class 2): `mm_device` cookie + `Origin` check + `X-MM-App` header, and require a verified email. Add a new `rate_limit("profile")` bucket at about 10/min per IP plus a per-account limit. A staff route would be Bearer with `admin:users:write`.
5. Audit row: actor, target, `fields=["display_name"]` and `cleared: bool`, but never the old or new value. Add a test that captures the logs (with the logger re-enabled) and asserts the submitted name does not appear (AC3).
6. Add the DB CHECK now, in a migration. Run `UPDATE users SET display_name = NULL WHERE NOT (char_length(btrim(display_name)) BETWEEN 1 AND 100)` first, then add `CHECK (display_name IS NULL OR char_length(display_name) BETWEEN 1 AND 100)`. Production data has no names yet (ticket line 18), so the risk is low. The downgrade drops the constraint.

## Risks
- Any write path that skips the schema (seed scripts, an admin bulk tool) bypasses the category rules. The CHECK only enforces length, so the issue-time guard has to stay.
- The token keeps the old name until it expires (up to the TTL). This lag is accepted and documented.

## Open questions for Ming
- Who can set the name? Default: self-service (cookie) plus staff (Bearer); sign-up does not take a name.
- Impersonation block list? Default: no; rely on the UI showing email/id.
- Combining-mark cap of 2 and whitespace collapsing? Default: yes.
- Add the CHECK now and set non-compliant rows to NULL? Default: yes.
