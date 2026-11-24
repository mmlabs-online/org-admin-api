# Security review — USR-32

## Attempt 1
Verdict: PASS

Scope: commit 2283210 (USR-32) against origin/main. This was a static review using only git and grep. As instructed, I did not run pytest or `make check`. The `make check` gate (R8) is still the reviewer's to confirm.

Every focus point is met, with no blockers:
- **App tokens only:** `app/modules/tokens/service.py:144-145`. `name` sits in the `elif` branch of `if app.key == ADMIN_APP_KEY`, so it can never be on `aud=admin` and never sits next to `sid`. `test_token_admin_has_no_name_claim` checks the exact admin claim set with a name present.
- **Issuance guard:** `service.py:54-68`.
  - It normalises to NFC first. A blank or whitespace-only name returns `None` without a log line.
  - It then checks `len > 100` and `reject_disallowed_categories` (Cc, Cf, Zl, Zp, Co, Cs, Cn). That covers bidi overrides, zero-width characters, the BOM, tag characters, controls, private use and unassigned code points.
  - On failure the claim is omitted and the token is still issued. Both `ValueError` and `PydanticCustomError` are caught.
  - The warning logs only `user_id` and `aud`, never the value or the email.
- **Nothing else changed:** signing, `kid`, `REQUIRED_CLAIMS`, verification, the other claims, the TTL and the `token_issued` log line are all untouched. The claim is spread through the existing `**claims` before `iat`, `exp` and `jti`, so it cannot override them.
- **Log test is non-vacuous:** `tests/test_auth_token.py` `test_token_logs_no_name` re-enables the loggers. It asserts the token does carry `name == "zznamemarker"`, then that the marker is not in `caplog.text`. Its second case checks for exactly one `name_out_of_contract` record that contains `user_id` and not the marker. `tests/test_tokens_name_claim.py` `test_name_rejected_logs_user_id_only` does the same at unit level.
- **Docs:**
  - `docs/consumers.md` item 15 says: untrusted text, render as text only, not for identity or authorization (key on `sub`), may be absent, stale up to the TTL, never log decoded claims. Item 11 adds the lag.
  - The ADR-0005 amendment, `identity-architecture.md` §4 and D39, and the spec line all match.

Findings:

💭 `app/modules/tokens/service.py:54-68`: the category guard allows unlimited combining marks (Mn/Me, "Zalgo" stacking within 100 code points) and unusual space characters (Zs, e.g. U+3000, U+00A0) inside a name. Leading and trailing whitespace is also kept, which is intentional (the plan leaves trimming to the write path). This is not exploitable, because the name is documented as untrusted render-only text. Confusables also stay possible, as consult-security-architect F4 already says. The fix belongs in the USR-33 write path, not here: NFC, then trim, plus an optional cap on consecutive combining marks.

💭 `tests/test_auth_token.py:987`: the plan's AC1c lists a 101-character case for the API-level parametrised test, but only U+202E, U+200B and U+0007 are used. The length limit is covered at unit level (`tests/test_tokens_name_claim.py` `test_name_exactly_100_ok_101_rejected`), so the gap is small. Optional fix: add `"a" * 101` to the `bad` list.

💭 `tests/test_tokens_name_claim.py:21`: `test_name_empty_or_whitespace_returns_none` checks that `name_out_of_contract` is not in `caplog.text` but does not re-enable the `app.modules.tokens.service` logger first. If logging config has disabled that logger, the no-warning check passes vacuously. Fix: add the same `monkeypatch.setattr(logging.getLogger("app.modules.tokens.service"), "disabled", False)` used in `test_name_rejected_logs_user_id_only`.

Follow-ups (out of scope):
- USR-33, the display-name write path: validate on write with NFC, trim, the `app.core.text` category check and 1-100 characters. Consider a DB CHECK (`char_length BETWEEN 1 AND 100`) so the issuance guard really is only defence in depth.
