# Security review — USR-37 (Attempt 1, appsec-engineer, 2026-11-27)

Saved by the orchestrator (the appsec agent is read-only at REVIEW).

**Verdict: PASS** — no 🔴, no 🟡.

Scope: commit c010b6e (diff `86d8936..c010b6e`). Tests: `pytest -q tests/test_rbac_assignments_api.py tests/test_openapi_contract.py` → 69 passed (local `org_admin_test`).

## Findings (all 🟢)
- **No new leak in 409 details** (`rbac/service.py:1479-1484`, `:1719`, `:1726-1728`, `:1742-1744`): `_assignment_conflict` adds only `details=[{"reason": <constant>}]` to the existing messages; no ids, emails, tokens, timestamps; the `IntegrityError` stays as `__cause__`. Check order unchanged (409 after `_guard_privileged` and the unknown-user 404) — no new probe (R5, R11).
- **403 override is docs only** (`rbac/assignments_router.py:49-73`): `require_permission` (`admin_auth/deps.py:116-150`) builds a fresh `responses` dict per call, so `_ASSIGN_KWARGS["responses"][403] = …` changes only this route's OpenAPI metadata; `RequirePermission("admin:roles:assign")`, `requires_super="conditional"`, `super_when=_PRIVILEGED` and `current_admin` unchanged; `require_permission` itself unmodified.
- **Documentation text** lists only codes and reasons the endpoint already returns; no internal detail.
- **Other `_grant_locked` callers** (`auth/provisioning.py:80`, `invitations/service.py:319,336`, `join_requests/service.py:113`, `membership/service.py:146`, `organizations/auth_service.py:129`) don't catch `ConflictError` or read `.details`; on their cookie routes a 409 may now carry `details[0].reason` about the caller's own assignment (or one staff already see) — additive, no other user's state revealed.

## Follow-ups (out of scope)
- 💭 `rbac/service.py:1741`: the `IntegrityError` branch also covers "user deleted meanwhile" → `assignment_exists` (misleading, no leak; pre-existing). A later ticket could tell FK from unique violations.
- 💭 Prefer a `forbidden=` / `errors_403=` parameter on `require_permission` over per-route dict patches.
