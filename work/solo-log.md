# Solo log

| Date | Summary | Files | Checks |
|---|---|---|---|
| 2026-11-10 | `/healthz` returns `version` from `APP_VERSION` setting ("dev" when unset); release build should set it (ADM-007) | app/core/config.py, app/modules/health/router.py, tests/test_health.py, .env.example | make check pass: ruff, format, mypy, pytest 9/9 |
| 2026-11-11 | Swagger UI `/docs` (and `/openapi.json`) public in production too — owner decision, temporary; spec §6.1 note | app/main.py, tests/test_health.py, docs/spec-v1.md | make check pass: ruff, format, mypy, pytest 243/243 |
