# Review — USR-32

## Attempt 1 — 2026-11-24
Verdict: PASS

Reviewed commit 2283210 (branch `claude/festive-volta-rikup7`) against `origin/main`. Plan approved by Ming with all
defaults (claim `name`, app tokens only, absent when unset, issuance guard, no migration, ADR-0005 amendment + D39).

### Checks (run by reviewer)
- `uv run ruff check .` — 13 errors, all in `.github/scripts/risk_tier.py` (kit file, unchanged vs `origin/main`:
  `git diff origin/main...HEAD -- .github/scripts/risk_tier.py` empty). Changed files
  (`app/modules/tokens/service.py`, `tests/test_auth_token.py`, `tests/test_tokens_name_claim.py`): All checks passed.
- `uv run ruff format --check .` — 1 file would be reformatted: `.github/scripts/risk_tier.py` only (same kit file);
  the 3 changed files are formatted.
- `uv run mypy app` (Makefile `typecheck`) — Success: no issues found in 209 source files.
- `uv run pytest -q` (full suite) — 4091 passed, 2 warnings in 631 s. Matches impl-log (4091 passed).
- `uv run alembic check` — No new upgrade operations detected (no migration; no model change).

### Acceptance criteria
- [x] 1 App token carries `name`; absent (not `null`) when unset — `app/modules/tokens/service.py:54-68` (`_name_claim`),
  `:144-145` (`elif … is not None: claims["name"] = name`); tests `tests/test_auth_token.py:913`
  (`test_token_name_claim_present`, exact claim set + `verify_reference`), `:930`, `:939`; unit tests
  `tests/test_tokens_name_claim.py:16-47`.
- [x] 1b `aud=admin` never carries `name` — the claim is added in the `elif` branch of `if app.key == ADMIN_APP_KEY`
  (`service.py:142-145`), so it is structurally unreachable for admin; `tests/test_auth_token.py:949`
  (`test_token_admin_has_no_name_claim`, admin user with a name set, exact claim set = today's admin set with `sid`).
- [x] 1c Issuance guard, fail closed, token still issued, only `user_id`/`aud` logged — `service.py:58-67`
  (NFC, ≤100, `reject_disallowed_categories`, warning `name_out_of_contract user_id=%s aud=%s`);
  `tests/test_auth_token.py:988` (U+202E, U+200B, U+0007 → 200, no `name`);
  `tests/test_tokens_name_claim.py:40` (100 ok / 101 rejected), `:46` (U+202E, U+200B, U+0007, U+2028, U+E000).
- [x] 2 Change shows up in the next token; old token unchanged — `tests/test_auth_token.py:998`
  (`test_token_name_reflects_update_on_next_mint`); lag documented in ADR-0005 amendment (Risk 2), consumers.md item 11.
- [x] 3 Existing claims unchanged, with and without `org_id` — the diff to `tests/test_auth_token.py` is append-only
  (no existing test edited); `test_token_happy_path`, `test_token_admin_audience_issued`, `test_token_org_entitled_has_ent`
  green in the full run; `tests/test_auth_token.py:966` (`test_token_name_claim_with_org`, exact set incl.
  `org`, `org_roles`, `ent`, `name`, verified via `verify_reference`). `REQUIRED_CLAIMS` unchanged
  (`app/core/access_token.py:13`; `git diff origin/main...HEAD -- app/core` empty).
- [x] 4 No name/email in logs; non-vacuous — `tests/test_auth_token.py:1012-1040`: loggers re-enabled, asserts the
  minted token **does** carry `name == "zznamemarker"` (:1026), marker absent from `caplog.text` (:1027), exactly one
  `token_issued` record; second case asserts exactly one `name_out_of_contract` record containing `user_id` (proves
  capture works) and the marker absent (:1040). `token_issued` log line untouched.
- [x] 5 Docs — spec §5.8 claim list + dated USR-32 line (`docs/spec-v1.md`); `docs/identity-architecture.md` token
  claims, D7 `name?`, new D39 row; `docs/consumers.md` claim list, item 11 (display-name change), new item 15
  (untrusted text); `docs/adr/0005-access-token-signing.md` "Amendment: `name` claim (USR-32, 2026-11-24)".
- [x] 6 `make check` green for this diff (only the known, unchanged kit file fails lint); `alembic check` clean.

### Issues
None.

### Rules (CLAUDE.md)
No violations found. R1: change confined to the service; no router/ORM change. R2: no schema change. R3: no auth
class change. R5/R11: the warning logs `user_id` and `aud` only; value never logged. R7: files match the plan's
default file list (service, 2 test files, 4 docs). R9/R10: additive claim, `REQUIRED_CLAIMS` unchanged. R4: nothing
mutating.

### Blockers from review agents
None.

### Nits (do not block)
- Plan AC1c listed "101 chars after NFC" in the API-level parametrisation; it is covered only by the unit test
  (`tests/test_tokens_name_claim.py:40`), which is correct since `varchar(100)` cannot store 101 characters.
- `_name_claim` uses `raise ValueError("length")` inside `try` for control flow; an explicit early `if` with the same
  log would read slightly cleaner.

### Follow-ups (out of scope)
- Display-name write path (USR-33, referenced in the ADR amendment); until then every production token omits `name`.

### Review agents (attempt 1)
- appsec-engineer: `Verdict: PASS` — no 🔴, no 🟡; 3 💭 notes (see `security-review.md`). No blockers from review agents.
