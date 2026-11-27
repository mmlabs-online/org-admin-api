# Security review — USR-26 (Attempt 1, appsec-engineer, 2026-11-27)

Saved by the orchestrator (the appsec agent is read-only at REVIEW; code read only — the reviewer ran the suite).

**Verdict: PASS** — no 🔴, no 🟡.

Scope: commit 2438687 (diff `f101d92..2438687`). The change adds two read fields and stable `details[].reason` on existing 409s; authorization and guards unchanged; no secrets in details.

## Verified
- **Permissions unchanged:** role-definitions list/detail `require_permission("admin:apps:read")` (`rbac/router.py:298`, `:313`); DELETE and PUT permissions `admin:roles:manage` with `requires_super="conditional"` / `super_when=_ADMIN_APP` (`router.py:405-412`, `:425-435`); router diff only changes 409 description strings. `RoleDefinitionRead` only on these staff Bearer routes, not the public role catalog.
- **`is_org_owner`** (`service.py:1210`) already visible to the same caller as `AppRead.org_owner_role_id` under `admin:apps:read` (`schemas.py:107`).
- **`assignment_count`** (`repository.py:303-312`, `service.py:1201/1213`): one parameterized GROUP BY over the app's loaded role ids; bare integer, no identities; `admin:apps:read` already exposes `AppRead.member_count` (`schemas.py:114`).
- **Guards unchanged:** `delete` (`service.py:1351-1372`) and `replace_permissions` (`service.py:1445-1486`) keep order, conditions and exception types (system/default/org-owner guards, designated super-admin guards, `role_has_assignments`, IntegrityError race, G3 `_role_has_assignments` with code `role_has_assignments`, `field: permission_keys`); 409 paths still write no audit (R4); USR-25 rule untouched.
- **Details content (R5/R6):** `{"reason": <constant>}` only (`service.py:1512-1545`); no names, keys, user data, counts or exception text; `IntegrityError` chained with `from exc`, not serialized.
- **Shared helper:** `_set_role_permissions` (`service.py:215`) race 409 gains `details`; status, code, message unchanged.

## Findings
- 🟢 `service.py:1213` / `schemas.py:269`: `assignment_count` gives `admin:apps:read` callers a per-role holder count (incl. expired rows and, on the `admin` app, the number of `admin:super-admin` holders) — previously only via `GET …/role-definitions/{role_id}/assignments` (`admin:users:read`, `router.py:333`). Aggregate, no identities, `member_count` precedent — not blocking; owner decision if "how many super-admins exist" is sensitive (accept, or gate the field behind `admin:users:read`).
- 🟢 `repository.py:303` docstring and `test_assignment_count_expired_only_blocks_delete` tie the count to the delete guard.

## Follow-ups (out of scope)
- Owner decision on the first 🟢 note.
