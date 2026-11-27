# Security review — ADM-026 (Attempt 1, appsec-engineer, 2026-11-27)

Saved by the orchestrator (the appsec agent is read-only at REVIEW; code read only — the reviewer ran the suite).

**Verdict: PASS** — no 🔴; one 🟡 that needs no code change (deploy prerequisite, already Ming todo M-10).

Scope: `git diff b54e59f 856d348` (`organizations/{repository,router}.py`, `rbac/{repository,router,schemas}.py`, tests).

## Findings
1. 🟢 **No SQL injection:** `sort` is a closed `Literal` (`organizations/schemas.py:12`, `rbac/schemas.py:30-39`) — 422 before the repository; after stripping `-` it selects a code-owned expression (`organizations/repository.py:19-27`, `rbac/repository.py:92-96`, `:231-240`); collation is a `Final` constant via `.collate()` (`rbac/repository.py:80-89`), compile test in `tests/test_name_sort.py`; `test_apps_sort_allow_list_matches_repository_mapping` keeps Literal and dict in sync; 422 cases for `bogus`, `id`, `KEY` kept.
2. 🟢 **Member-count predicate unchanged:** `member_count_select()` (`rbac/repository.py:99-116`) is the old `member_counts` body moved as-is (distinct `user_id`, active users, unexpired assignments, grouped by `app_id`); `member_counts` (:1135) reuses it; LEFT JOIN + `coalesce(…, 0)`; at most one row per app (no duplication; `total` counted on `App` alone); `test_list_apps_sort_member_count_excludes_expired_and_inactive`.
3. 🟢 **Authorization unchanged:** `list_apps` `require_permission("admin:apps:read")` (`rbac/router.py:76`); `list_organizations` `require_permission(READ)` (`organizations/router.py:58`); router diffs are description text only.
4. 🟢 **No new information:** schemas unchanged; `member_count` already on `AppRead`.
5. 🟡 **Missing ICU → 500:** without `und-x-icu` Postgres raises `collation … does not exist`; not caught (`app/core/errors.py:258-260`); Starlette answers plain-text 500 without traceback (`app_debug` unused, `config.py:41`) — no leak, availability only (`sort=name`/`-name` fail; other sorts work). Mitigations: `test_icu_collation_available` fails loudly in CI (`postgres:17-alpine` has ICU); prod check before merge (M-10: `SELECT 1 FROM pg_collation WHERE collname='und-x-icu'`, `SHOW server_encoding`). No code fix needed.
6. 🟢 **DB cost:** one grouped aggregate over `role_assignments` per `member_count` request, admin-only, `limit` ≤ 100 — acceptable.

## Follow-ups (out of scope)
- 💭 Unhandled exceptions answer plain-text 500 instead of the R6 envelope (pre-existing) — catch-all `Exception` handler in `app/core/errors.py` (High, own ticket).
- 💭 `app_debug` config unused — remove or wire with a production guard.
