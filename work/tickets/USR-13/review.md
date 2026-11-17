## Attempt 1 — 2026-11-17
Verdict: PASS

### Checks (run by reviewer)
- `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check` (ruff, format, mypy --strict, pytest): exit 0;
  pytest "1997 passed, 1 warning in 161.30s" (the warning is the pre-existing `forkpty` DeprecationWarning in
  `tests/test_cli_create_account.py`). Matches impl-log.md.
- `alembic check`: "No new upgrade operations detected." (no schema change).
- `rg -n "15 min|15-minute|about 15 minutes|900 s|60-3600|60–3600|~15" docs .env.example app`: every remaining hit is
  the default-plus-production wording, login backoff "cap 15 min", the historical ADR-0005 §6 line (marked amended),
  ADR-0008 §7 "default 900 s" (still true), the 900 s freshness example in consumers.md item 13, or the rotation math.

### Acceptance criteria
- [x] `ACCESS_TOKEN_TTL_SECONDS=43200` accepted at startup; values above the bound rejected — evidence:
  `app/core/config.py:87` `Field(default=900, ge=60, le=43200)`; `tests/test_config.py:183`
  `test_access_token_ttl_bounds[59, 43201]`, `:189` `test_access_token_ttl_accepted[60, 3601, 43200]`, `:193`
  `test_access_token_ttl_from_env` (env 43200 loads, env 43201 raises naming the field without echoing the value). All pass.
- [x] Issued token has `exp - iat = 43200` and `expires_in` matches — evidence: `tests/test_auth_token.py:111`
  `test_token_ttl_43200` (real `/token` via `make_client(access_token_ttl_seconds=43200)`, verified with
  `verify_reference` against the served JWKS). Default 900 still asserted in `test_token_happy_path`. Pass.
- [x] (plan 2a/2b) Verifier has no age cap; admin reload still revokes long-lived tokens — evidence:
  `tests/test_access_token.py:53`, `tests/test_admin_auth.py:206` (role revoked -> 403), `:234` (disabled -> 401),
  using the plain `client` fixture so `current_admin` reload runs. Pass.
- [x] ADR-0005, `docs/consumers.md`, `docs/deploy.md` state the new lifetime and consequences — evidence:
  `docs/adr/0005-access-token-signing.md:70` dated amendment (decision, unchanged items, five accepted risks incl. admin
  nuance and rotation wait, follow-ups) plus §6 pointer; consumers.md lines 76, 117-121, 154, 300, 374-385 (checklist
  11-14); deploy.md 224, rotation step 3 (43560 s, wait 12.5 h, counted from last old-key signer), 251 incident
  runbook, 262 owner post-merge step. Arithmetic checked: 43200 + 60 + 300 = 43560 s = 12 h 06 min.
- [x] Checks green — see above.

### Issues
None.

### Scope
Every changed file is in the plan's Files table (code, tests, the three named docs, ADR-0001/0007/0008/0011,
identity-architecture, spec, `.env.example`, `app/cli.py`) or is workflow bookkeeping (`work/backlog.md`,
`work/tickets/USR-13/*`). No change to `tokens/service.py`, `access_token.py`, `auth/router.py` or the forbidden test
files. No unjustified out-of-plan change.

### Rules R1–R11
No violations found. R5: value from env only, bound in `config.py`, `.env.example` key still valueless; the env test
asserts the rejected value is not echoed. R10: no refresh token introduced. R11: keys, `kid` and rotation mechanics
unchanged; only the documented wait changed. Spec conflict (§5.8/§5.10/OQ8, D7 "~15 min") was flagged in the plan and
resolved by owner-tagged edits, per CLAUDE.md.

### Nits (do not block)
- Several edited Markdown lines exceed the surrounding wrap width (e.g. `docs/adr/0005-access-token-signing.md:19`,
  `docs/adr/0007-email-verification-and-delivery.md:102`, `docs/adr/0008-admin-api-authentication.md:40`,
  `docs/adr/0011-admin-cors.md:40`, `docs/consumers.md:154,300,375`, `docs/deploy.md:224,516,587,604`).
- `docs/consumers.md:154,300,375`: nested parentheses "(up to `ACCESS_TOKEN_TTL_SECONDS` (default 15 min; 12 h in
  production))" read awkwardly; a comma or colon would do.
- `docs/deploy.md:604` omits "default 15 min" unlike the plan's standard wording (accurate for production, which is
  the context there).
- `docs/spec-v1.md:66` "up to 12 h; 12 h in production" is slightly redundant.
- OQ8 is ticked as answered by the owner's 2026-11-17 decision; the owner should confirm that reading when merging,
  as the plan states.

### Follow-ups (out of scope)
- As recorded in plan.md Assumptions: per-audience admin TTL (`ADMIN_ACCESS_TOKEN_TTL_SECONDS`); `tokens_valid_after`
  or `sid` check in the ADR-0008 §3 admin reload; cap `exp` at earliest assignment expiry; verify `mmlabs-auth`,
  `@mmlabs/auth-client`, admin-pan, ricotdin and TuDoCuaTui before setting production to 43200.
- Owner post-merge step: set `ACCESS_TOKEN_TTL_SECONDS=43200` in `mmlabs-online/deployment`.
