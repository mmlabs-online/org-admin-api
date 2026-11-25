# Security review — USR-33

## Attempt 1
Verdict: PASS

Static review of commit 160145e against origin/main: git diff, git show and grep only. I did not run pytest or `make check`, so R8 green-check evidence has to come from the implementer or reviewer.

**Findings**

🟡 should-fix: `app/core/text.py:55-73` (`reject_blank_lookalikes` / `UserDisplayName`). A name made only of invisible combining marks still passes. Examples: `"️"` (variation selector, Mn), `"͏"` (combining grapheme joiner, Mn), `"឴"`, `"᠋"`, or two of them in a row. None of them are removed by `strip()`. Mn/Me is not in `DISALLOWED_CATEGORIES`, the characters are not in `BLANK_LOOKALIKES`, and a run of 1–2 stays under the cap. The result is a stored name that shows as nothing in the `name` claim and the USR-29 member read. The security-architect consult (rec 2) wanted to prevent exactly that ("so a name can't be 'blank' while passing validation"). The plan's fixed list (AC 1c) does not cover it.
- Risk: low. This is UI spoofing or confusion, not an injection or authz bug.
- Fix: add one after-validator, placed after `reject_blank_lookalikes`, that requires at least one visible base character. Also reject a leading mark:
  ```python
  def require_visible_base(value: str) -> str:
      if unicodedata.category(value[0]) in {"Mn", "Me", "Mc"} or not any(
          unicodedata.category(ch)[0] in "LNPS" and ch not in BLANK_LOOKALIKES for ch in value
      ):
          raise PydanticCustomError("display_text", "contains a disallowed character")
      return value
  ```
- Tests: add `"️"`, `"͏͏"` and `"́a"` to `test_user_display_name_rejects`.
- It is a small change inside AC 1c's intent, so it can go into a rework. If you keep R7 strict, log it as a follow-up.

💭 `app/modules/users/service.py:63-66` (`set_display_name`). The same-value no-op returns before the G5 check, so a staff member without `admin:*` gets 200 on a privileged target when the value is unchanged, and no denied audit row is written. This matches `set_status` (line 99-100) and leaks nothing, because `UserRead` already shows `display_name`. No change needed. A test pinning the behaviour would be nice.

💭 `migrations/versions/20261125_d4a8e2c6f175_usr33_users_display_name_length.py:27-36`
- `btrim()` with no second argument trims only ASCII spaces. A legacy name made of NBSP or a tab would survive the data step. It still passes the CHECK, since its length is 1–100, so the migration cannot fail. No write path existed before, so this is harmless.
- `ADD CONSTRAINT … CHECK` scans the table under ACCESS EXCLUSIVE. That is fine for the size of `users`. For larger tables the pattern is `NOT VALID` + `VALIDATE CONSTRAINT`.
- `downgrade()` is correct, and the one-way data step is documented.

**Verified OK**
- **Cookie class:** `app/modules/users/auth_router.py:24-59` uses `rate_limit("profile")` (per IP, runs first), then `AppContextDep` (X-MM-App + Origin → 403) before `DeviceDep`, then `DeviceCookieDoc`. It has no `require_permission`, and it is registered in `COOKIE` in `tests/test_route_auth_classes.py`. When the device rotates, the new cookie is set on the 200 response, same as `organizations/auth_router._finish`. `Cache-Control: no-store` is set.
- **account_id binding:** `profile_service.py:44-49` resolves the account with `get_active_account(device.device_id, account_id, created_before=…)`, which requires same device, not revoked and older than the rotation cutoff. It then locks the user via `account.user_id`, never through a user id taken from the body. Disabled → 401 and unverified → 403 `email_unverified` (R11). `test_other_device_account_id_401_swap` checks that both users are unchanged and that no audit row is written. The response `account_id` echoes a value that has already been verified.
- **Rate limits:** the per-user bucket `profile_user` is keyed by `str(user.id)` and is hit only after session and verification pass (same pattern as `org_member_remove_user`). Values come from settings and `.env.example` has the keys without values (R5).
- **Staff PATCH:** still needs `admin:users:write` with `requires_super="conditional"`. The G5 check (`user_has_privileged_assignment` → `require_super`) also covers name changes, and a denial writes `record_denied`. Status runs first and keeps its lock order and the self-change and last-super-admin guards. `extra="forbid"`, `{}` → 422, `status: null` → 422, and `display_name: null` clears. The widening adds no escalation path. `app/core/cors.py` is untouched, so cookie CORS still allows only GET/POST and the staff route has no CORS change.
- **Validation:** NFC (before) → strip → `Zs` runs collapsed to U+0020 → strip + 1–100 code points (matches `char_length`) → disallowed categories (Cc/Cf/Zl/Zp/Co/Cs/Cn) → blank lookalikes → combining run ≤ 2. `""` and whitespace-only → 422. Errors are `display_text` with fixed messages, and `core/errors.py:215` strips `input`/`ctx`, so the value is never echoed.
- **updated_at:** `repository.set_display_name` writes the name and `bumped_updated_at` in one UPDATE. Both services return early on the same value, and `test_same_value_is_noop` / `test_patch_name_same_value_noop` check that.
- **Audit:** one row per change in the same transaction, `changes={"display_name": {"set": bool}}`, the self path uses an `EndUser` actor + `app_key`, and `display_name` is not in `audit.diff.USER`.
- **Logs:** only the `user_id`, `cleared` and `via` fields. `test_logs_never_contain_name` and `test_patch_name_logs_no_value` re-enable the module loggers, assert exactly one `display_name_changed` record, and check the marker is missing from `caplog.text`, including a 422 case, so the tests are not vacuous.

**Follow-ups (out of scope)**
- If the 🟡 is not fixed in this ticket, open a hardening ticket for a "visible base character" rule shared by `UserDisplayName`, and consider it for `DisplayName`/`AppName` too.
- Consumers (`docs/consumers.md`) should keep rendering `name` as text and keying on `sub`. That is documented already; no action needed.
