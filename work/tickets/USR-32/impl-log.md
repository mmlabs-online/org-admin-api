# Impl log — USR-32

## Attempt 1
Plan defaults approved by Ming (Q1a, Q2 defer: no migration, Q3-Q5 yes; D39 after D38).

Files touched:
- `app/modules/tokens/service.py` — `_name_claim` + `NAME_CLAIM_MAX_LEN`; `name` added in `issue()` for non-admin apps (AC1, 1b, 1c, 2).
- `tests/test_tokens_name_claim.py` (new) — unit tests for `_name_claim`.
- `tests/test_auth_token.py` — new API tests appended; existing tests unchanged.
- `docs/adr/0005-access-token-signing.md`, `docs/identity-architecture.md` (token section, D7, D39), `docs/consumers.md` (claims, item 11, item 15), `docs/spec-v1.md` (claim list + dated line) — AC5.

Tests added: all listed in the plan (unit + API, including non-vacuous `test_token_logs_no_name`).

Checks:
- `ruff format --check app tests`: 393 files already formatted; `ruff check app tests`: All checks passed.
- `mypy app`: Success, no issues in 209 source files (`mypy app tests` has 143 pre-existing errors in tests; unchanged by this ticket).
- Full `pytest -q` (after last change): 4091 passed.
- `alembic check`: No new upgrade operations detected.
- Known kit lint failure in `.github/scripts/risk_tier.py` not touched.

Out-of-plan files: none.
Follow-ups: USR-33 display-name write path (+ DB CHECK); expose `name` in client libraries.
Scope self-check: no migration, no endpoint, no other claims, no log of name/email.
