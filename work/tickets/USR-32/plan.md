Status: BLOCKED

# Plan — USR-32: Display name claim in app access tokens

> BLOCKED only on Ming's answers to the questions in "Blocking questions" (Q1 and Q2 change the file list).
> Every question has a proposed default. If Ming accepts all defaults, this plan is READY as written.
> Files marked "(Q2 = yes only)" are left out under the default answer.

## Scope summary
- Ticket USR-32, Scope: "Add an optional `name` claim (the account's `display_name`) to access tokens issued for
  apps (`aud=<app>`). PLAN decides: claim name (`name`, as OIDC), absent vs `null` when unset, whether staff
  (`aud=admin`) tokens get it too, and any length/normalisation rule." / "Additive within v1: existing verifiers
  that ignore unknown claims keep working" / "Document the staleness: a display-name change shows up on the next
  token (up to the token TTL, 12 h)." / "Docs: spec §5.8 claim list, `docs/identity-architecture.md` token
  claims, `docs/consumers.md`."
- Spec §5.8 (`docs/spec-v1.md:83`), "Verification by apps": "Claims: `sub`, `email`, `email_verified`, `aud`,
  `roles` …, `org` (if scoped), `org_roles` (if scoped …), `ent` (if scoped …; ADR-0017), `iat`, `exp`."
  **There is no name claim in the spec yet.** This ticket adds one with a dated spec line. That is a change to the
  spec's claim list, so **Ming must confirm it at the plan gate** (Q5).
- ADR-0005, amendments for `sid` (:102-110) and `ent` (:115-121): "Adding a claim is not breaking; verifiers must
  not require it (`REQUIRED_CLAIMS` is unchanged)." Risk 2 (:82-85) lists the claims frozen until `exp`.
- `docs/identity-architecture.md:294` (D7) lists the token claims; :210-213 is the token section.
- `docs/consumers.md:539-542` lists the claims; :525-526 (item 11) covers revocation lag.
- `work/decisions.md`: nothing on display names. D38 is reserved by USR-31 (its consult), so this ticket uses **D39**.

### Key finding (both consults; confirmed)
`users.display_name` is `String(100)` and nullable, with no CHECK (`app/modules/users/models.py:31`). **Nothing in
the app writes it.** There is no endpoint, no schema field and no validation. The only setter is a test
(`tests/test_auth_accounts.py:73`). In production every `display_name` is NULL. As specified, the claim would
therefore be absent from every real token until some ticket adds a write path. USR-29's member read `display_name`
is always `null` today for the same reason. This is blocking question Q1.

Note: `String(100)` is `varchar(100)`, so Postgres already refuses more than 100 characters. A
`CHECK char_length(display_name) BETWEEN 1 AND 100` would add only the lower bound (no empty string). This affects
Q2.

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | An app access token carries the display name in the agreed claim (`name`). When the user has no display name, the claim is absent (not `null`). | `tests/test_auth_token.py` › `test_token_name_claim_present`, `test_token_name_claim_absent_when_unset`, `test_token_name_claim_absent_when_empty_or_blank`; unit tests in `tests/test_tokens_name_claim.py` |
| 1b | (Plan decision) `aud=admin` tokens never carry `name`. | `test_token_admin_has_no_name_claim` (admin user with a name set; exact claim set equals today's admin set) |
| 1c | (Plan decision, defence in depth) A stored name that fails NFC + category check + length ≤ 100 is left out of the token (fail closed). The token is still issued, and only `user_id`/`aud` are logged. | `test_token_name_out_of_contract_omitted` (parametrised: U+202E, U+200B, U+0007, 101 chars after NFC) + unit tests |
| 2 | A display-name change appears in the next token issued. Tokens already issued are unchanged (lag documented). | `test_token_name_reflects_update_on_next_mint` (mint, update the row, mint again; the first token still decodes to the old name). Docs: ADR-0005 Risk 2 amendment, consumers.md item 11, spec line |
| 3 | Existing claims and verification are unchanged, with and without `org_id`. | Existing `test_token_happy_path`, `test_token_admin_audience_issued`, `test_token_org_entitled_has_ent` stay green **unmodified** (exact claim sets, user without name). New `test_token_name_claim_with_org` asserts the exact claim set `{…, org, org_roles, ent, name, …}` and verifies through `verify_reference` |
| 4 | Token issuance never writes the email or the name to logs. | Existing `test_token_logs_no_secrets` / `test_token_logs_no_entitlement_values` green. New `test_token_logs_no_name`, which is non-vacuous: it asserts the minted token **does** carry `name == "zznamemarker…"`, then that the marker is absent from `caplog.text` and exactly one `token_issued` record exists. Second case: an out-of-contract marker name produces exactly one `name_out_of_contract` record containing `user_id`, and the marker is not in the logs |
| 5 | Spec §5.8, `docs/identity-architecture.md` and `docs/consumers.md` are updated. | Reviewer reads the diff: spec line 83 claim list + a dated line; identity-architecture :213 and D7 :294 + D39 row; consumers.md claim list :539-542, item 11 (lag), a new item "`name` is untrusted text"; ADR-0005 amendment |
| 6 | `make check` is green. If Q2 = yes, `uv run alembic check` is also clean. | Commands |

## Ming verification after merge
- None for the default answers. If Q2 = yes: **before deploy**, Ming runs on production
  `SELECT count(*) FROM users WHERE display_name IS NOT NULL AND char_length(display_name) < 1;`. The expected
  result is 0. Otherwise the migration fails at the deploy step.
- After a write path exists (follow-up ticket): simap confirms that `name` shows up in its UI.

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/modules/tokens/service.py` | Add the pure helper `_name_claim(display_name: str \| None, *, user_id, aud) -> str \| None` next to `_ent_claim` (:38-47). In `issue()`, after the `sid` block (:121-122), add `if app.key != ADMIN_APP_KEY and (name := _name_claim(...)) is not None: claims["name"] = name`. Leave the `token_issued` log line (:140-148) unchanged. | AC1, 1b, 1c, 2 | **High** (`app/modules/tokens/**`) |
| `tests/test_tokens_name_claim.py` (new) | Pure unit tests for `_name_claim` (no DB) | Pure logic first; AC1c | no |
| `tests/test_auth_token.py` | New API tests (listed below). Existing tests are not edited. | AC1-4 | no |
| `docs/adr/0005-access-token-signing.md` | New section "Amendment: `name` claim (USR-32, 2026-11-24)": optional, app tokens only, absent when unset, issuance guard, Risk 2 gains `name`, untrusted text | Decision record (no ADR-0030) | no |
| `docs/identity-architecture.md` | Token claims :213 add `name`; D7 :294 add `name?`; new row **D39** after D37 (:334), placed after USR-31's D38 if that merges first | AC5 | no |
| `docs/consumers.md` | Claim list :539-542 add `name`; item 11 (:525) add "a display-name change"; new checklist item: `name` is untrusted user text, render as text only, never for identity/authz, key users on `sub`, may be stale up to TTL, never log decoded claims | AC5 | no |
| `docs/spec-v1.md` | §5.8 line 83: add `name` (optional display name, absent when unset; app tokens only) to the claim list + a dated line `> 2026-11-24 (USR-32): …` | AC5 (**spec change, Ming confirms**, Q5) | no |
| `app/modules/users/models.py` (Q2 = yes only) | `__table_args__` add `CheckConstraint("char_length(display_name) BETWEEN 1 AND 100", name="display_name_length")` (precedent `app/modules/usage/models.py:51`) | Q2 | no |
| `migrations/versions/20261124_<rev>_usr32_users_display_name_check.py` (Q2 = yes only) | `down_revision = "c91e4b7a3d58"`; `op.create_check_constraint("display_name_length", "users", …)`; downgrade `op.drop_constraint(…, type_="check")`. Use the naming-convention name `ck_users_display_name_length`, checked against `app/db/base.py` naming. | Q2 | **High** (`migrations/**`) |
| `tests/test_users_display_name_constraint.py` (new, Q2 = yes only) | `''` and 101 chars → `IntegrityError`; NULL and 100 chars OK | Q2 | no |

Tier: **High** under every answer (`app/modules/tokens/**`). Expected diff: about 220 lines (default) or about 280
(Q2 = yes). This fits size S.

## Data model / migration
- **Default (Q2 = defer): none.** `users.display_name varchar(100) NULL` is unchanged.
- **If Q2 = yes:** the check constraint `ck_users_display_name_length CHECK (char_length(display_name) BETWEEN 1
  AND 100)` on `users` (NULL passes). The migration has no data change. Downgrade drops the constraint.
  down_revision `c91e4b7a3d58`. The model is updated as well, so that `alembic check` reports no drift.

## API contract
No endpoint changes. `POST /api/v1/auth/token` (cookie class, R3) keeps the same request, response body
(`{access_token, token_type, expires_in, audience}`), codes and auth. The only change is in the JWT payload:

| Claim | Type | Present when |
|---|---|---|
| `name` | string, 1-100 Unicode code points, NFC | `aud != admin` AND `users.display_name` is non-NULL, not blank, and passes the issuance guard. Otherwise the key is **absent** (never `null`). |

`REQUIRED_CLAIMS` (`app/core/access_token.py`) is unchanged. Verifiers must not require `name`.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_tokens_name_claim.py` › `test_name_none_returns_none` | unset → absent |
| … › `test_name_empty_or_whitespace_returns_none` (parametrised `""`, `"   "`) | blank → absent, no warning log |
| … › `test_name_plain_returned_unchanged` (ASCII, Vietnamese `"Nguyễn Văn A"`, emoji) | AC1 |
| … › `test_name_nfd_input_returned_nfc` (`"é"` → `"é"`) | NFC |
| … › `test_name_exactly_100_ok_101_rejected` (after NFC) | length |
| … › `test_name_disallowed_category_rejected` (parametrised U+202E bidi, U+200B zero-width, U+0007 Cc, U+2028 Zl, U+E000 Co) | Domain focus: PII/untrusted text; AC1c |
| … › `test_name_rejected_logs_user_id_only` (caplog, marker name) | R11, AC4 |
| `tests/test_auth_token.py` › `test_token_name_claim_present` | AC1: stoktok token, `verify_reference`, `claims["name"] == name`, exact claim set = happy-path set + `name` |
| … › `test_token_name_claim_absent_when_unset` | AC1: `"name" not in claims` (explicit, though the happy path already implies it) |
| … › `test_token_name_claim_absent_when_empty_or_blank` | AC1 (`""`/`"  "` written directly to the row; skip `""` if Q2 = yes) |
| … › `test_token_admin_has_no_name_claim` | AC1b: admin user with a name; exact claim set = the set from `test_token_admin_audience_issued` |
| … › `test_token_name_claim_with_org` | AC3: calechip org token (reuse `_entitled_org`), exact claim set incl. `org`, `org_roles`, `ent`, `name` |
| … › `test_token_name_out_of_contract_omitted` (parametrised) | AC1c: 200, token issued, `name` absent |
| … › `test_token_name_reflects_update_on_next_mint` | AC2 |
| … › `test_token_logs_no_name` | AC4, non-vacuous (see table above); re-enable the loggers as in :737-738 |

Test setup: set `user.display_name = …; await db_session.flush()` after `_member(...)`, as
`tests/test_auth_accounts.py:73` does. `tests/auth_factories.py` is not changed.

## Approach
1. `_name_claim` (pure, module level in `tokens/service.py`, modelled on `_ent_claim`):
   `None` → `None`; `value = unicodedata.normalize("NFC", raw)`; `value.strip() == ""` → `None` (no log);
   `len(value) > 100` or `reject_disallowed_categories(value)` raising `PydanticCustomError` → log
   `logger.warning("name_out_of_contract user_id=%s aud=%s", user_id, aud)` and return `None`; otherwise return
   `value` (the NFC form, not trimmed: the write path owns trimming). Import `reject_disallowed_categories` from
   `app.core.text`; nothing in `app/core/` is edited. Extract a constant `NAME_CLAIM_MAX_LEN = 100` that matches
   `String(100)`.
2. In `issue()`, add the claim only for `app.key != ADMIN_APP_KEY`, after the `sid` block. It goes into `claims`
   and is spread at :134. It must never be added to the `token_issued` log line.
3. Tests as listed. Run `make check`.
4. Docs: the ADR-0005 amendment (claim, absent rule, app tokens only and why: the admin UI reloads the user per
   request (ADR-0008); the issuance guard; Risk 2 gains `name`; disabled users get no new token, but live tokens
   keep the name until `exp` (unlike the USR-29 member read, which nulls it, ADR-0025); untrusted text, confusables
   cannot be filtered; size ≤ ~540 B extra). Then D39, the spec line, and consumers.md.
5. If Q2 = yes: model CHECK + migration with hand-reviewed downgrade, then `uv run alembic check`.

Rules: R1 (service-only change, no ORM in routers); R2 (only if Q2 = yes); R3 (no auth class change); R5/R11 (no
name, email or token in logs; the warning carries `user_id` only); R7 (no write path, no other claims, no client
library); R8 (`make check`); R9/R10 (additive within v1; one registered OIDC claim name ≠ "full OIDC provider").
No audit entry (R4): nothing mutating.

## Specialist input used
- identity-access-engineer: claim `name` (OIDC) **adopted**; absent when unset **adopted**; app tokens only
  **adopted**; ADR-0005 amendment + D-row, no ADR-0030 **adopted** (D39, because USR-31 reserved D38); Risk 2 list
  **adopted**; log marker test pattern (:878-898) **adopted**; the no-write-path finding becomes **Q1**.
- security-architect: F1 → issuance guard (NFC + `reject_disallowed_categories` + ≤ 100, fail closed, log user_id
  only) **adopted**; CHECK constraint → **made Q2** with default *defer*. varchar(100) already caps the length, so
  the CHECK only forbids `""`, which the guard already treats as unset. A High-path migration that could conflict
  with USR-31's head is not worth it here; the write-path ticket owns write validation. R2/R3/R4 (absent, no admin,
  untrusted-text docs) **adopted**; F5 size: no test needed (bounded by 100 chars; `test_token_worst_case_ent_size`
  already exists).

## Assumptions
- Blank/whitespace-only names are treated as unset (absent, no warning). Values are emitted in NFC form, not
  trimmed.
- The claim value is read from the same `users` row already loaded at `service.py:92`. There is no extra query.
- Hotspots: USR-31 (PLANNING) may add a migration on head `c91e4b7a3d58` and edit `docs/identity-architecture.md`
  (D38) and `docs/consumers.md`. If Q2 = yes and USR-31 merges first, rebase `down_revision` onto USR-31's
  revision. Doc conflicts are line-level only.
- Client libraries (`mmlabs-auth`, `@mmlabs/auth-client`, USR-08 repo) do not reject unknown claims (ADR-0005
  amendments assume this). Exposing `name` there is a follow-up in that repo.
- Follow-ups (not in this diff, R7): (F1) display-name write path with validation (see Q1); (F2) expose `name` in
  the client libraries; (F3) if Q2 is deferred, the CHECK constraint moves to F1.

## Blocking questions
1. **No write path for `display_name` exists. In production every name is NULL, so the `name` claim (and USR-29's
   member `display_name`) would be absent/null for every user until one exists.**
   (a) Ship USR-32 now as specified (claim absent when unset, issuance guard included, so it is safe whatever
   writes the column later) and create a follow-up ticket "display-name write path": self-service
   `PATCH /api/v1/auth/accounts/…` (cookie class) and/or staff `PATCH /api/v1/users/{id}` (`admin:users:write`,
   audited), with NFC + trim + `reject_disallowed_categories` + 1-100 validation and the DB CHECK.
   (b) Widen USR-32 to include the write path. That makes it **M** (about 500-700 lines: schema, service, router,
   audit, auth-class registry, 401/403/404/422 tests, docs, likely a migration), which is over one
   implement/review cycle. If (b), I propose a split: USR-32a = this plan (claim), USR-32b = write path, which
   amounts to (a) with a dependency.
   **Default: (a).** Ming also picks self-service, staff, or both for the follow-up (default: both,
   self-service first).
2. **DB CHECK `char_length(display_name) BETWEEN 1 AND 100`?** (a) Add it now (migration on head `c91e4b7a3d58`,
   High path; only if production has no `''` rows, checked by Ming before deploy). (b) Defer it to the write-path
   ticket. **Default: (b) defer.** varchar(100) already enforces ≤ 100, the issuance guard covers the token, and
   this avoids a migration and a head conflict with USR-31. (The caller's suggested default was "yes if rows
   comply"; I changed it to (b) for these reasons. Ming decides.)
3. **Claim shape:** name `name` (OIDC), **absent** when unset (never `null`), **app tokens only** (no `aud=admin`).
   **Default: yes to all three.**
4. **Issuance guard:** NFC + `reject_disallowed_categories` + length ≤ 100; on failure, leave the claim out
   (the token is still issued) and log `user_id` only. **Default: yes.**
5. **Spec §5.8 change:** add `name` to the claim list with the line
   `> 2026-11-24 (USR-32): optional \`name\` claim (display name, untrusted text; absent when unset; app tokens only; stale up to TTL); ADR-0005 amendment, D39.`
   **Default: yes.** Recorded as an ADR-0005 amendment + D39, no ADR-0030.

## Split proposal
None for the default answers (size S, about 220 lines). If Q1 = (b), split as described in Q1 and this plan covers
slice USR-32a.
