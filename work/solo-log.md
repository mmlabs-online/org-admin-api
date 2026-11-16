# Solo log

| Date | Summary | Files | Checks |
|---|---|---|---|
| 2026-11-10 | `/healthz` returns `version` from `APP_VERSION` setting ("dev" when unset); release build should set it (ADM-007) | app/core/config.py, app/modules/health/router.py, tests/test_health.py, .env.example | make check pass: ruff, format, mypy, pytest 9/9 |
| 2026-11-11 | Swagger UI `/docs` (and `/openapi.json`) public in production too — owner decision, temporary; spec §6.1 note | app/main.py, tests/test_health.py, docs/spec-v1.md | make check pass: ruff, format, mypy, pytest 243/243 |
| 2026-11-12 | docs/deploy.md: production runtime config (TRUSTED_PROXY_CIDRS, IDENTITY_HMAC_KEY, JWT_SIGNING_KEY, JWT_ISSUER) is now written by the deployment workflow (deployment #52/#53); emergency rotation via host key file + flush consumer JWKS caches (USR-03c security S1) | docs/deploy.md | make check pass: ruff, format, mypy, pytest 718/718 |
| 2026-11-16 | Password minimum length 6 (was 12), owner decision; floor `ge=6`. Also exclude `work/` from ruff (ADM-002e item 5): verbatim ticket files made `make check` red on main | app/core/config.py, pyproject.toml, tests/test_auth_signup.py, tests/test_cli_create_account.py, tests/test_password_reset_api.py, work/tickets/ADM-002e/ticket.md | make check pass: ruff, format, mypy, pytest 1622/1622 |
