# Implementation log — USR-04c

## Attempt 1 — 2026-11-13

### Files touched
| File | Required because |
|---|---|
| `app/modules/auth/deps.py` | Deleted `get_optional_link_origin`; `get_signup_hook` now depends on `get_link_origin` (requires `AppContextDep`) — AC1, AC2 |
| `app/modules/auth/hooks.py` | `link_origin: str`; removed the `None` → "no mail" branch and its log line — AC2 |
| `app/modules/auth/router.py` | Signup docstring: class 3 public + required app context (403), link uses the validated Origin |
| `app/core/config.py` | Removed `email_link_base_url`, its validation and gate entry; dropped the unused `normalize_origin`/`is_loopback_origin` import; updated email comment — AC3 |
| `.env.example` | Removed `EMAIL_LINK_BASE_URL=` and its comment — AC3, R5 |
| `tests/conftest.py` | Removed `TEST_EMAIL_LINK_BASE_URL` and its env line — AC3 |
| `tests/test_config.py` | SMTP gate parametrize without the link key; deleted the two link-base rule tests; added the stray-env test — AC3 |
| `tests/test_email_verification_api.py` | `stoktok` fixture autouse; `_signup` defaults to `auth_headers()`; replaced the link-base test and the old bad-header test with the extended 403 test; host/forwarded-host test sends the header on both calls — AC1, AC2, AC5 |
| `tests/test_auth_signup.py` | Autouse `stoktok` fixture; every `post(URL, …)` sends `auth_headers()` — AC5 |
| `docs/consumers.md` | Sign-up paragraph rewritten (required header + Origin, `credentials: "include"`, every registered origin serves `/verify-email`), v1 contract change note — AC6 |
| `docs/deploy.md` | Removed the key row; start-up gate names SMTP only; prerequisite step 4 says the compose line can be dropped — AC6 |
| `docs/adr/0007-email-verification-and-delivery.md` | §2 "Amended by USR-04c" note — AC6 |
| `docs/identity-architecture.md` | §4 signup row Auth column — AC6 |

### Tests added / changed
- `tests/test_email_verification_api.py › test_signup_bad_app_context_403_no_side_effects` (new, 8 cases: no headers, Origin without key, `Bad Key!` → `app_header_required`; unknown app, disabled app, no Origin, `Origin: null`, unregistered origin → `origin_not_allowed`; asserts 0 users, no Turnstile call, no mail, empty send log). Replaces `test_signup_without_app_header_uses_link_base` and `test_signup_bad_app_header_403_no_user`.
- `tests/test_email_verification_api.py › test_link_ignores_host_and_forwarded_host` (adjusted: both calls send `auth_headers()`, link is always `ORIGIN`)
- `tests/test_config.py › test_stray_email_link_base_url_ignored` (new, staging + production)
- `tests/test_config.py › test_smtp_required_in_production`, `test_smtp_optional_locally` (adjusted); `test_email_link_base_url_rules`, `test_email_link_base_url_loopback_only_locally` (deleted)
- `tests/test_auth_signup.py` — all signup calls now send `auth_headers()` (autouse `stoktok` app)

### Checks (actual output summary)
Local Postgres was already running (`pg_isready` → accepting connections). Command: `env -u APP_VERSION -u DATABASE_URL -u APP_ENV make check`, run twice:
- ruff: `All checks passed!`; format: `299 files already formatted`; mypy: `Success: no issues found in 77 source files`; pytest: `855 passed` (both runs)
- alembic check: n/a (no schema change)
- `grep -rn EMAIL_LINK app tests .env.example` → only the stray-env test's `setenv`

### Out-of-plan files
None

### Follow-ups (noticed, not done)
- The `mmlabs-online/deployment` compose line `EMAIL_LINK_BASE_URL=…` can be dropped by the owner (harmless meanwhile, A3).

### Scope self-check
- Could the diff be smaller? no
- Diff size: +109 / -129 lines (13 files)
