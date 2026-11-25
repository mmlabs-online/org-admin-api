Status: READY

# Plan — USR-33: Let users and staff set a display name

> High path (migration, `app/core/`, `.env.example`): stops at PLAN for Ming's approval.
> The plan is written for the default answers in "Blocking questions" (Q1–Q10). If Ming accepts every default, it is
> READY as written. Q1, Q3 and Q6 change the file list; the other questions only change behaviour or limits.

## Scope summary
- Ticket USR-33 Goal: "Give `users.display_name` a real write path so the USR-32 `name` claim and the USR-29 member
  read's `display_name` carry a value; today nothing can set it and every name is NULL in production."
- Ticket Scope: "PLAN decides: self-service (cookie class, the signed-in account sets its own name) and/or staff
  (Bearer, `admin:users:write`), and whether sign-up accepts an optional name." / "Validation on write: NFC, trim,
  reject disallowed Unicode categories (`app/core/text.py`), length 1–100; clearing the name sets NULL. Optionally a
  DB CHECK on length (deferred from USR-32, Q9)." / "Audit the change (ids only, never the name value in logs; R11)."
  / "Documented lag: tokens pick the new name up on the next issue (≤ TTL); the USR-29 member read shows it at once."
- Ticket Out of scope: "Other profile fields (avatar, locale); name uniqueness."
- Scope reference: "Batch 2026-11-24-1, Q8 default (Ming, 2026-11-24): ship USR-32 as specified and open this
  follow-up for a display-name write path with validation." The USR-32 plan's Q1 default named the follow-up:
  self-service (cookie class) and/or staff `PATCH /api/v1/users/{id}` (`admin:users:write`, audited), "default: both,
  self-service first".
- Spec §5.8 (line 83): claims include "optionally `name` (display name; app tokens only, absent when unset)"; line 88:
  "**Display name claim (2026-11-24, USR-32):** … stale up to TTL; ADR-0005 amendment, D39." Line 92: "**Admin:**
  staff can list, look up, disable/enable users, …" (no name editing yet).
- `docs/identity-architecture.md` D39: "the display-name write path is a separate ticket (USR-33)". ADR-0005
  amendment (line 136): "**Scope:** no write path exists yet; every name is NULL until one ships (USR-33)."
- ⚠️ **Spec gap (ticket line 23):** no spec section says who may set the display name. This plan adds a dated §5.8
  line (Q10) for Ming's confirmation at the plan gate. `work/decisions.md` has nothing on display names.
- CLAUDE.md R3 (2): cookie class = "identity endpoints under `/api/v1/auth/*` that act for a signed-in account …:
  `mm_device` cookie + `Origin` check + `X-MM-App` (R11)". R11: "Verified email is required to join an app or get
  any app token." R4: audit in the same transaction. R7: stay inside the plan.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1a | The signed-in account sets and clears its own display name (cookie class) | `tests/test_auth_profile.py` › `test_set_name_returns_account_read`, `test_null_clears_name`, `test_same_value_is_noop` |
| 1b | Staff set and clear a display name (`admin:users:write`; `admin:*` for privileged targets) | `tests/test_users_admin.py` › `test_patch_sets_display_name`, `test_patch_null_clears_display_name`, `test_patch_status_and_name_together`, `test_patch_name_privileged_target_needs_wildcard` |
| 1c | Invalid values → 422 without echoing the value: control / bidi / zero-width chars, > 100 chars, blank after trim, `""`, blank-looking letters, > 2 combining marks in a row | `tests/test_text.py` › `test_user_display_name_*` (unit); `tests/test_auth_profile.py` › `test_invalid_name_422_no_echo` and `tests/test_users_admin.py` › `test_patch_invalid_display_name_422_no_echo` (parametrized; body has `code == "validation_error"`, the marker is absent from `resp.text`, the DB value is unchanged) |
| 1d | Normalisation: NFC, trim, internal whitespace runs collapsed to one U+0020 | `tests/test_text.py` › `test_user_display_name_normalises` (decomposed Vietnamese → NFC; `"  Ming  　 Le "` → `"Ming Le"`); `tests/test_auth_profile.py` › `test_name_stored_normalised` |
| 1e | DB CHECK: `display_name` is NULL or 1–100 characters | `tests/test_users_model.py` › `test_display_name_check_rejects_empty`; `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` on the test DB; `uv run alembic check` clean |
| 2a | The next app token carries the new `name`; a cleared name drops the claim | `tests/test_auth_profile.py` › `test_next_token_carries_new_name` (set via the route → `POST /auth/token` → `name` == new value; clear → `name` absent) |
| 2b | The USR-29 member read returns the new name at once and the user shows up under `updated_since` | `tests/test_auth_profile.py` › `test_member_read_shows_new_name` (read with `updated_since` = a timestamp taken before the change; the user is in `items` with the new `display_name`) |
| 2c | `updated_at` moves only on a real change | `test_same_value_is_noop` (no new `updated_at`, no audit row); `test_set_name_bumps_updated_at` |
| 3a | Each change writes one audit row `user.display_name_changed` with `changes == {"display_name": {"set": bool}}` and never the value | `tests/test_auth_profile.py` › `test_audit_row_has_no_value`; `tests/test_users_admin.py` › `test_patch_name_audit_row_has_no_value`; `tests/test_audit_coverage.py` (identity one-row harness + Bearer `AUDITED` set); `test_user_diff_tuple_excludes_display_name` |
| 3b | No name appears in logs (non-vacuous) | `tests/test_auth_profile.py` › `test_logs_never_contain_name`: re-enables the `app.modules.users.*` loggers, sets `zznamemarker…`, asserts exactly one `display_name_changed` record with the `user_id`, and the marker is not in `caplog.text`; also a 422 case and the staff route (`tests/test_users_admin.py` › `test_patch_name_logs_no_value`) |
| 4a | 401/403 for the self-service route | `tests/test_auth_profile.py` › `test_no_cookie_401`, `test_wrong_origin_403`, `test_missing_app_header_403`, `test_unverified_email_403`, `test_disabled_user_401`, `test_logged_out_account_401`, `test_other_device_account_id_401_swap` (account_id of another device/user → 401 `session_invalid`, neither user changed), `test_ip_rate_limit_429`, `test_per_user_rate_limit_429` |
| 4b | 401/403/404 for the staff route | `tests/test_users_admin.py` › `test_patch_name_requires_users_write` (403), existing 401 tests, `test_patch_missing_user_returns_404` (name body), `test_patch_name_privileged_target_needs_wildcard` (403 `admin_wildcard_required` + denied audit row) |
| 4c | Route-class registry updated | `tests/test_route_auth_classes.py` (`COOKIE` gains `("POST", "/api/v1/auth/profile")`; all existing class tests pass) |
| 4d | Docs updated | Reviewer reads the diffs of `docs/spec-v1.md`, `docs/identity-architecture.md` (§3, §4, D40), ADR-0005 amendment, `docs/consumers.md`, `CLAUDE.md` R3 |
| — | Everything | `make check` green; `uv run alembic check` clean |

## Ming verification after merge
- Before deploying the migration: on production run `SELECT count(*) FROM users WHERE display_name IS NOT NULL AND
  char_length(btrim(display_name)) = 0;` (expected 0; the migration sets such rows to NULL anyway) and then run the
  migration step per `docs/deploy.md`.
- In a real app (and admin-portal): set a name, then check it in the account switcher, in a new access token, and
  in the staff user view (shown next to the email).

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/core/text.py` | Add `trimmed(max_length)` (moved from `usage/schemas.py:27-36`), `DisplayName` (moved, unchanged chain), new `collapse_spaces`, `reject_blank_lookalikes`, `cap_combining_marks` validators and the `UserDisplayName` type | One shared validated type (security rec 1, api rec 3) | **High** |
| `app/core/config.py` | 4 settings: `profile_rate_limit` (20), `profile_rate_window_seconds` (60), `profile_user_rate_limit` (10), `profile_user_rate_window_seconds` (3600), all `ge=1` | Per-IP + per-account limits (Q8) | **High** |
| `app/core/rate_limit.py` | Add `"profile"` to `Scope` and `_LIMITS` | Per-IP bucket for the new cookie route | **High** |
| `.env.example` | 4 keys, no values | R5 | **High** |
| `migrations/versions/20261125_<rev>_usr33_users_display_name_length.py` | Data step + CHECK `ck_users_display_name_length`; `down_revision = "c91e4b7a3d58"` | R2, Q6 | **High** |
| `app/modules/users/models.py` | Add `CheckConstraint("display_name IS NULL OR char_length(display_name) BETWEEN 1 AND 100", name="display_name_length")` to `__table_args__` (line 20) | Model matches the migration (`alembic check`) | no |
| `app/modules/users/schemas.py` | `UserUpdate` (replaces `UserStatusUpdate` on the PATCH), `DisplayNameSet` (self-service body) | Request bodies | no |
| `app/modules/users/repository.py` | `set_display_name(user, value)` — one `UPDATE … SET display_name, updated_at = bumped_updated_at(User.updated_at)`, then refresh | Strict `updated_at` bump (db rec 3) | no |
| `app/modules/users/service.py` | `UserService.update(user_id, body, actor, ctx)` dispatching to `set_status` (unchanged) and new `set_display_name` (G5 check, audit, log) | Staff write path (R1 business rules) | no |
| `app/modules/users/profile_service.py` (new) | `DisplayNameService.set(app, device, account_id, value, ctx)`: resolve account on this device, lock user, active + verified checks, per-user limit, write, audit, log | Self-service business rules (R1) | no |
| `app/modules/users/auth_router.py` (new) | `POST /auth/profile` (cookie class), `_finish` pattern from `organizations/auth_router.py:32-36` | Self-service route | no |
| `app/modules/users/router.py` | PATCH `/{user_id}` takes `UserUpdate`, calls `UserService.update`; handler renamed `update_user`; docstring mentions the name | Staff route (Q3) | no |
| `app/api/router.py` | `include_router(users_auth_router)` | Register the new router | no |
| `app/modules/audit/actions.py` | `USER_DISPLAY_NAME_CHANGED = "user.display_name_changed"` | R4 | no |
| `app/modules/usage/schemas.py` | Import `DisplayName` and `trimmed` from `app.core.text`; delete the local copies (lines 27-36, 47-52). No behaviour change | Single source of the type | no |
| `tests/test_text.py` | Unit tests for the new validators and `UserDisplayName` | Pure logic first | no |
| `tests/test_auth_profile.py` (new) | Self-service API tests (see Tests) | AC1–4 | no |
| `tests/test_users_admin.py` | Staff name tests; existing invalid-body cases stay | AC1b, 1c, 3, 4b | no |
| `tests/test_users_model.py` | CHECK test | AC1e | no |
| `tests/test_auth_token.py` | Drop `""` from `test_token_name_claim_absent_when_empty_or_blank` parametrize (line 938; the CHECK now forbids it); keep `"  "` | The CHECK makes that fixture impossible | no |
| `tests/test_route_auth_classes.py` | `COOKIE` += `("POST", f"{V1}/auth/profile")` (line 49-62) | AC4c | no |
| `tests/test_audit_coverage.py` | `AUDITED[("PATCH", USER)]` action set += `"user.display_name_changed"` (line 500); `IDENTITY_AUDITED` += `("POST", f"{AUTH}/profile"): ("user.display_name_changed", _id_profile, 200)` with a new `_id_profile` scenario | Audit registry (R4) | no |
| `docs/spec-v1.md` | §5.8: new dated line after line 88; line 92 Admin bullet adds "set or clear a user's display name" | Spec gap (Q10) | no |
| `docs/identity-architecture.md` | §3 data model (line 91): note the CHECK; §4 table: row for `POST /api/v1/auth/profile`, note on staff `PATCH /api/v1/users/{id}`; §8: D40 | Decision log | no |
| `docs/adr/0005-access-token-signing.md` | Amendment `Scope` bullet (line 136): the write path exists (USR-33, D40); lag unchanged | ADR accuracy | no |
| `docs/consumers.md` | New subsection "Setting the display name" after "Listing the accounts on this browser" (after line 479) | Consumer docs | no |
| `CLAUDE.md` | R3 (2) list: add "display name — D40" after "org member removal — D35" | R3 registry text | no (but rule text: Ming confirms at the gate) |

Not touched (explicit): `app/modules/audit/diff.py` (`USER` stays `("status",)`), `app/modules/tokens/service.py`
(issuance guard stays as defence in depth), `app/core/cors.py` (POST already allowed), `app/modules/auth/**`
(only `AccountRead` and `DeviceCookieDoc` are imported), `app/modules/bug_reports/service.py`.

Size: about 330 changed production/doc lines + 450 test lines. See Split proposal.

## Data model / migration
- Table `users`, column `display_name varchar(100) NULL` (unchanged type).
- New constraint `ck_users_display_name_length`: `CHECK (display_name IS NULL OR char_length(display_name) BETWEEN 1 AND 100)`.
- Migration `20261125_<rev>_usr33_users_display_name_length.py`, `down_revision = "c91e4b7a3d58"`:
  - `upgrade()`:
    1. `op.execute("UPDATE users SET display_name = NULL WHERE display_name IS NOT NULL AND char_length(btrim(display_name)) = 0")`
       (data step; production has no writer so every row is NULL, this is a safety net; `> 100` cannot exist under
       varchar(100)). Note: this does not bump `updated_at`, on purpose (no visible change for USR-29 readers of a
       value that was never valid).
    2. `op.create_check_constraint("display_name_length", "users", "display_name IS NULL OR char_length(display_name) BETWEEN 1 AND 100")`
       — the naming convention expands it to `ck_users_display_name_length`. Plain `ADD CONSTRAINT` (small table,
       brief lock; db rec 1).
  - `downgrade()`: `op.drop_constraint("ck_users_display_name_length", "users", type_="check")`. The data step is
    not reversed (blank names carried no information); the docstring says so.
- No index (column never filtered or sorted; db rec 4). No new column, no `version` column (Q7).
- `updated_at`: every real name write sets `updated_at = bumped_updated_at(User.updated_at)` (`app/db/base.py:17-23`)
  in the same UPDATE, so USR-29 `updated_since` (`rbac/repository.py:691`, `User.updated_at > …`) sees it. A same-value
  write does not run the UPDATE.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/profile` | `DisplayNameSet` `{account_id: uuid, display_name: UserDisplayName \| null}`, `extra="forbid"`, both fields required (`{}` or missing `display_name` → 422) | 200 `AccountRead` `{account_id, email, email_verified, display_name}`; `Cache-Control: no-store`; rotated `Set-Cookie` when the device rotates | 200; 401 `session_invalid` (no/invalid cookie, account not active on this device, user disabled); 403 `app_header_required` / `origin_not_allowed` / `email_unverified`; 422 `validation_error` (never echoes the value); 429 `rate_limited` | Cookie class (R3 2): `mm_device` + `Origin` + `X-MM-App` (any active app incl. `admin`); no `require_permission` |
| PATCH | `/api/v1/users/{user_id}` (existing) | `UserUpdate` `{status?: "active"\|"disabled", display_name?: UserDisplayName \| null}`, `extra="forbid"`; at least one field present; explicit `status: null` → 422 (USR-01b contract kept); explicit `display_name: null` clears | 200 `UserRead` (unchanged) | 200; 401; 403 (`admin:users:write` missing; `admin_wildcard_required` when the target holds a privileged assignment, for either field; `self_status_change_forbidden` for status only); 404 unknown user; 409 last super-admin (status only); 422 | `admin:users:write`; `requires_super="conditional"` and `super_when` text unchanged ("Target user holds a privileged role assignment") |

`UserDisplayName` (in `app/core/text.py`), applied in this order:
1. Before: `nfc_if_str`.
2. After: `str.strip()`; then `collapse_spaces`: every run of characters with category `Zs` (U+0020, U+00A0, U+3000, …)
   becomes one U+0020. Cc characters (tab, newline) are **not** collapsed, so they still fail step 4.
3. After: `trimmed(100)` → length 1–100 code points (after collapsing), else `display_text` "must be 1-100 characters".
4. After: `reject_disallowed_categories` (Cc, Cf, Zl, Zp, Co, Cs, Cn; existing).
5. After: `reject_blank_lookalikes`: any of U+115F, U+1160, U+3164, U+FFA0, U+2800 → `display_text` "contains a
   disallowed character".
6. After: `cap_combining_marks`: more than 2 consecutive `Mn`/`Me` code points → `display_text` "contains too many
   combining marks".
All errors are `PydanticCustomError("display_text", <fixed message>)`; the 422 handler drops `input`/`ctx`
(`app/core/errors.py:215-217`). `DisplayName` (usage metrics) keeps its current chain (NFC, trim 1–100, categories).

Audit row (both routes): action `user.display_name_changed`, target `(AuditTarget.USER, user.id)`,
`changes = {"display_name": {"set": <new value is not None>}}`. Self-service: `actor=EndUser(user.id)`,
`app_key=app.key`. Staff: `actor=principal`. G5 refusal: `record_denied(USER_DISPLAY_NAME_CHANGED, …)` as `set_status`
does (`users/service.py:73-77`). Log line (both): `display_name_changed user_id=%s cleared=%s via=%s` (`self`/`staff`).

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_text.py` › `test_user_display_name_accepts` (parametrized: `"Ming"`, `"Nguyễn Thị Ánh"`, decomposed `"Nguyễn"` → `"Nguyễn"`, `"a"*100`, `"x̀́"`) | Valid names, Vietnamese after NFC, 2-mark limit |
| `tests/test_text.py` › `test_user_display_name_normalises` | Trim, `Zs` runs → one space, NFC; length counted after collapsing (`"a"*98 + "   " + "b"` → 100 chars, accepted; `"a"*99 + " " + "b"` → 101, 422) |
| `tests/test_text.py` › `test_user_display_name_rejects` (parametrized: `""`, `"   "`, `"a"*101`, `"Ada‮"`, `"Ada​"`, `"Ada\u0007"`, `"Ada\tB"`, `"ㅤ"`, `"aㅤb"`, `"ᅟ"`, `"ᅠ"`, `"ﾠ"`, `"⠀"`, `"x̀́̂"`, `"a"`) | Every rejection path; the error never contains the input |
| `tests/test_text.py` › `test_display_name_usage_unchanged` | Moved `DisplayName` still accepts `"x̀́̂"` and NBSP inside (no behaviour change for usage metrics) |
| `tests/test_users_model.py` › `test_display_name_check_rejects_empty` | DB CHECK: `''` → `IntegrityError`; `NULL` and `"a"` accepted |
| `tests/test_audit_coverage.py` › `test_user_diff_tuple_excludes_display_name` (or in `test_users_admin.py`) | `"display_name" not in app.modules.audit.diff.USER` |
| `tests/test_auth_profile.py` › `test_set_name_returns_account_read` | 200, body = `AccountRead` with new name, `Cache-Control: no-store`; `GET /auth/accounts` shows it |
| › `test_null_clears_name` | `null` → DB NULL, response `display_name: null` |
| › `test_same_value_is_noop` | Same value twice: second call 200, `updated_at` unchanged, one audit row total |
| › `test_set_name_bumps_updated_at` | `updated_at` strictly increases on a change |
| › `test_name_stored_normalised` | `"  Ming   Le "` stored as `"Ming Le"` |
| › `test_invalid_name_422_no_echo` (parametrized like the unit rejects, each wrapped with a `zzbad…` marker where possible, plus `{"account_id": …}` without `display_name`, plus an extra field) | 422 `validation_error`, marker not in `resp.text`, DB unchanged |
| › `test_multi_account_device_targets_body_account` | Device with accounts A and B: body `account_id` = B renames B only |
| › `test_other_device_account_id_401_swap` | Cookie of device 1 + `account_id` from device 2 (another user) → 401 `session_invalid`; both users' names unchanged; no audit row |
| › `test_no_cookie_401`, `test_logged_out_account_401`, `test_disabled_user_401` | 401 `session_invalid` |
| › `test_missing_app_header_403`, `test_wrong_origin_403`, `test_unverified_email_403` | 403 codes; DB unchanged |
| › `test_admin_app_header_allowed` | `X-MM-App: admin` with the admin origin → 200 (staff rename themselves here) |
| › `test_ip_rate_limit_429` (`profile_rate_limit=1`), `test_per_user_rate_limit_429` (`profile_user_rate_limit=1`, two IPs) | 429 `rate_limited` with `Retry-After` |
| › `test_next_token_carries_new_name` | Set → `POST /auth/token` → `name` = new value; clear → no `name` key |
| › `test_member_read_shows_new_name` | USR-29 `GET /apps/{key}/orgs/{org_id}/members?updated_since=<before>` (org-members:read key) lists the user with the new `display_name` |
| › `test_audit_row_has_no_value` | One row: action, actor = user, target = user, `app_key`, `changes == {"display_name": {"set": True}}` (then `False` on clear); marker not in the row's JSON |
| › `test_logs_never_contain_name` | Loggers re-enabled (`monkeypatch.setattr(logging.getLogger("app.modules.users.profile_service"), "disabled", False)` and the same for `app.modules.users.service`); success: exactly one `display_name_changed` record containing the user id, marker absent from `caplog.text`; 422 request: marker absent |
| `tests/test_users_admin.py` › `test_patch_sets_display_name`, `test_patch_null_clears_display_name` | Staff set/clear; `UserRead` shows it; `USER_READ_KEYS` unchanged |
| › `test_patch_status_and_name_together` | Both fields: two audit rows (`user.disabled`, `user.display_name_changed`), one transaction |
| › `test_patch_name_same_value_noop` | No audit row, `updated_at` unchanged |
| › `test_patch_invalid_display_name_422_no_echo` | `""`, `"a"*101`, U+202E, U+3164, 3 combining marks → 422, no echo; existing `test_patch_rejects_invalid_bodies` cases unchanged (`{}`, `{"status": None}`, …) |
| › `test_patch_name_requires_users_write` | `admin:users:read` only → 403 |
| › `test_patch_name_privileged_target_needs_wildcard` | `admin:users:write` on a target with an admin-app role → 403 `admin_wildcard_required` + denied audit row; `admin:*` → 200 |
| › `test_patch_name_unknown_user_404` | 404 |
| › `test_patch_name_audit_row_has_no_value`, `test_patch_name_logs_no_value` | Audit `changes` and logs without the value (logger re-enabled, one `display_name_changed` record) |
| `tests/test_audit_coverage.py` | `_id_profile` scenario in `IDENTITY_AUDITED`; `test_every_mutating_identity_route_is_classified` passes; `AUDITED` count stays 34 |
| `tests/test_route_auth_classes.py` | New `COOKIE` entry exercised by `test_cookie_routes_not_bearer_guarded`, `test_deployer_key_on_cookie_routes_unauthenticated` and the CORS/classification tests |

## Approach
1. **Validation (pure logic, `app/core/text.py`).** Move `_trimmed` (as public `trimmed`) and `DisplayName` from
   `usage/schemas.py`; update usage imports (no behaviour change). Add the three validators and `UserDisplayName`
   in the order above. Unit tests first.
2. **Migration + model.** Add the CHECK to `User.__table_args__`; write the migration by hand (data step, then CHECK;
   downgrade drops it). `uv run alembic check` clean; upgrade/downgrade/upgrade on the test DB. Fix
   `tests/test_auth_token.py:938` (drop `""`).
3. **Repository.** `UserRepository.set_display_name(user, value)`: `update(User).where(User.id == user.id).values(
   display_name=value, updated_at=bumped_updated_at(User.updated_at))`, then `refresh(user, ["display_name",
   "updated_at"])`. Explicit `updated_at` overrides `onupdate`.
4. **Staff service + route (R1, R4, G5).** `UserUpdate` with a `model_validator(mode="after")`: at least one field in
   `model_fields_set`; `status` in `model_fields_set` with `None` → error. `UserService.update`: if `status` set →
   `set_status` (unchanged lock order admin app → user row); if `display_name` set → `set_display_name(user_id,
   value, actor, ctx)`: `get_for_update` (already held when status ran), 404 if missing, same value → return; G5:
   `if await rbac.user_has_privileged_assignment(user.id): require_super(actor)` inside the `DENIED_ERRORS` /
   `record_denied` block; write; audit; log. Staff may rename themselves unless they hold a privileged assignment
   without `admin:*` (then they use the self-service route). Disabled users can be renamed.
5. **Self-service service + route.** `users/profile_service.py`: `get_active_account(device.device_id, account_id,
   created_before=device.accounts_created_before)` → `None` → `SessionInvalidError`; `UserRepository.get_for_update`
   → missing/not active → `SessionInvalidError`; unverified → `EmailUnverifiedError` (same checks as
   `resolve_reporter`, `bug_reports/service.py:67-82`, but with a `FOR NO KEY UPDATE` lock because we write: reusing
   `resolve_reporter` would take `FOR SHARE` and then upgrade, which deadlocks two concurrent saves). Then
   `limiter.hit("profile_user", str(user.id), user_limit)` → 429; same value → return; write; audit (`EndUser`,
   `app_key`); log. Router `users/auth_router.py` (prefix `/auth`, tag `auth`): `rate_limit("profile")`,
   `AppContextDep` before `DeviceDep` (403 before any device lookup), `DeviceCookieDoc`, `AuditContextDep`; build
   `AccountRead(account_id=body.account_id, email, email_verified, display_name)`; `_finish` (rotated cookie,
   `no-store`). Register in `app/api/router.py`.
6. **Config.** 4 settings + `.env.example` keys + `"profile"` scope.
7. **Registries.** Route-class `COOKIE`, audit-coverage maps.
8. **Docs.** Spec §5.8 line (Q10 text), identity-architecture §3/§4/D40, ADR-0005 amendment scope bullet, consumers.md
   subsection (request/response, errors, "untrusted text, render as text, key on `sub`", "tokens pick it up on the
   next issue, ≤ TTL; `GET /auth/accounts` and the USR-29 member read show it at once"), CLAUDE.md R3 (2).
9. `make check`; `uv run alembic check`.

Rules: R1 (router → service → repository; services raise `AppError` subclasses), R2 (hand-reviewed migration, working
downgrade, `alembic check`), R3 (one class per route; registry), R4 (audit in the same transaction), R5 (settings
from env, keys in `.env.example`), R6 (422 envelope, no echo), R8 (happy/404/422/401/403 per route), R9 (staff PATCH
stays a partial update on `/users/{id}`; `/auth/*` POST like the other cookie routes), R11 (no value in logs or errors;
verified email required; Origin + `X-MM-App`).

D40 text (identity-architecture §8):
`| D40 | **Display-name write path (owner, 2026-11-25, USR-33):** the signed-in account sets or clears its own name
with `POST /api/v1/auth/profile` `{account_id, display_name}` (cookie class, verified email, per-IP + per-account
limits); staff with `PATCH /api/v1/users/{id}` `display_name` (`admin:users:write`; `admin:*` when the target holds a
privileged assignment). Validation: NFC, trim, whitespace runs → one space, 1-100, no disallowed or blank-looking
characters, at most 2 combining marks in a row; `null` clears, `""` → 422. DB CHECK length 1-100. `updated_at` bumps
only on a real change; last write wins. Audit `user.display_name_changed` with `{"set": bool}`, never the value.
Sign-up takes no name; no uniqueness or reserved-name list. See ADR-0005 amendment. |`

## Specialist input used
- **identity-access-engineer:** adopted cookie class with body `account_id`, verified email, rotated cookie,
  `user.display_name_changed` with `{"set": bool}`, never in `USER` (rec 1, 3); G5 for privileged targets (rec 2);
  D40 without a new ADR (rec 6); rate limits (rec 5). Rejected `""` clears (rec 4) in favour of api/security: `""` →
  422, only `null` clears. Rejected a separate `PATCH /users/{id}/profile` (rec 2): extending the existing PATCH keeps
  R9 without a new exception, keeps the route count and the `require_permission` metadata (same permission, same
  conditional-super text), and is an additive v1 change. Did not extract `resolve_reporter` into `auth/` (R7; and the
  lock mode differs, see Approach 5).
- **api-platform-engineer:** adopted POST (CORS allows only GET/POST for cookie routes, `cors.py:36`), `AccountRead`
  response, required field with explicit `null`, extend PATCH with `UserUpdate` (rec 2), move `DisplayName` (rec 3),
  `""` → 422 (rec 4), no sign-up name (rec 6), registry/docs (rec 8). Rejected the path shape
  `/auth/accounts/{account_id}/profile` (every existing cookie POST takes `account_id` in the body: logout, resend,
  join, member remove, bug reports); rejected 404 for a foreign `account_id` (existing convention is 401
  `session_invalid`); rejected "no verified email" (R11 spirit; identity + security agree; the name lands in tokens
  that already require verification). Rejected "super check only when status present" (G5 rationale from identity:
  renaming a super admin enables impersonation in staff UIs). If-Match skipped (rec 5, Q7). Rejected "whitelist
  `display_name` in the audit diff" — no diff field at all, a custom `{"set": bool}`.
- **security-architect:** adopted shared type (rec 1), combining cap 2 / whitespace collapse / blank-lookalike list
  (rec 2), no impersonation block list (rec 3), cookie class + verified email + limits (rec 4), audit without value +
  non-vacuous log test (rec 5), CHECK with data step (rec 6; data step narrowed to "blank after btrim", since > 100
  cannot exist in varchar(100)). Audit shape `{"set": bool}` instead of `fields`+`cleared` (one key, same information).
- **database-optimizer:** adopted CHECK named `display_name_length`, plain add, downgrade by full name, no index,
  explicit `bumped_updated_at` only on change, last write wins, one transaction with the audit insert (recs 1–5).
- **USR-32 security review 💭s:** 💭1 (combining marks, Zs inside, trimming) addressed by `UserDisplayName`; 💭2
  (101-char API case) and 💭3 (logger re-enable) are USR-32 test nits, not this ticket's write path — follow-ups.

## Assumptions
- **Lock order / residual deadlock:** `DeviceDep` may lock the device row (touch/rotate) before the service locks the
  user row. Login takes the user `FOR SHARE` before the device row (`users/repository.py:41-47`). A login and a name
  save for the **same user on the same browser at the same instant** can deadlock; Postgres aborts one (500, client
  retries). Accepted as very rare; the same shape exists for resend. Staff path: admin app → user row (status), or
  user row only (name) — no cycle.
- The per-account limit counts every resolved attempt (including no-ops), like `org_member_remove_user`.
- `AccountRead` is imported from `app.modules.auth.schemas`; nothing in `app/modules/auth/` changes.
- `X-MM-App: admin` is accepted by `get_app_context` (only `MailLinkAppContextDep` refuses it), so admin-portal can
  use the self-service route.
- The token issuance guard (`tokens/service.py:54-68`) stays; the stricter write-time rules make it defence in depth.
  Names already in tokens are unaffected.
- USR-29 member read nulls the name for disabled users (`organizations/app_service.py:101`); unchanged.
- **Hotspots:** ADM-043 (PLANNING, usage reads) may touch `app/modules/usage/schemas.py`; ADM-045 (PLANNING,
  usage retention, db) may add a migration on head `c91e4b7a3d58` → expect a rebase / `down_revision` fix for
  whichever merges second. `tests/test_route_auth_classes.py` and `tests/test_audit_coverage.py` are shared
  registries; rebase conflicts there are additive.
- Follow-ups (not in this diff): extract the four copies of "resolve account on device" (`bug_reports`,
  `invitations`, `organizations` ×2) into one helper; USR-32 💭2/💭3 test nits; staff UIs must show email/id next to
  the name (admin-portal ticket).

## Blocking questions
Each has a default; the plan is written for the defaults.
1. **Who may set the name?** (a) self-service + staff; (b) self-service only; (c) staff only. **Default: (a).**
2. **Self-service route shape:** (a) `POST /api/v1/auth/profile` with `{account_id, display_name}` in the body (like
   logout/resend/member remove); (b) `POST /api/v1/auth/accounts/{account_id}/profile` (api consult). PATCH is not
   possible (cookie CORS = GET/POST). **Default: (a).**
3. **Verified email required for self-service?** **Default: yes** (identity, security; R11 spirit); api consult said no.
4. **Staff route:** (a) extend `PATCH /api/v1/users/{id}` with optional `display_name`; (b) separate
   `PATCH /api/v1/users/{id}/profile`. **Default: (a).** Privileged targets need `admin:*` (G5): **default yes.**
   Staff may clear a name (e.g. abusive): **default yes, audited.**
5. **Validation:** shared type in `app/core/text.py`; `null` clears, `""` → 422; > 2 combining marks in a row → 422;
   `Zs` runs collapsed to one space; reject U+3164, U+115F, U+1160, U+FFA0, U+2800. **Default: yes to all.**
6. **DB CHECK** `display_name IS NULL OR char_length(display_name) BETWEEN 1 AND 100` in a migration now, with the
   blank-to-NULL data step. **Default: yes** (db, security, identity). If no: drop the migration, model change and
   `test_auth_token.py` edit; ticket leaves the High migration path (still High for `app/core/`).
7. **Concurrency:** last write wins, no `If-Match`; `updated_at` bumps only on a real change. **Default: yes.**
8. **Rate limits:** per IP 20/60 s (`profile`), per account 10/hour (`profile_user`). **Default: yes.**
9. **Impersonation:** no reserved-name/block list; staff UIs show email/id next to the name; sign-up takes no name.
   **Default: yes to all.**
10. **Spec §5.8 line** (after line 88):
    `- **Display name (2026-11-25, USR-33, D40):** the signed-in account sets or clears its own display name (cookie class, verified email); staff set or clear it (\`admin:users:write\`; \`admin:*\` for privileged users). 1–100 characters after NFC, trim and whitespace collapsing; no control, bidi, zero-width or blank-looking characters; at most 2 combining marks in a row. Not unique, untrusted text; audited without the value; sign-up takes no name.`
    plus "set or clear a user's display name" in the §5.8 Admin bullet, and "display name — D40" in CLAUDE.md R3 (2).
    **Default: yes;** D40 only, no ADR-0029.

## Split proposal
None by default: about 330 production/doc lines plus about 450 test lines, one migration, one review cycle (Size M).
If Ming prefers smaller PRs: USR-33a = validation type + migration + staff PATCH; USR-33b = self-service route,
rate limits, consumers.md. This plan would then cover 33a first.
