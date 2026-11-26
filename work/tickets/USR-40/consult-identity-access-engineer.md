# Consult — identity-access-engineer — USR-40 (2026-11-26)

**Findings**
- `organizations/auth_service.py:167-189`: `list_mine` checks `org_mode` and `_user` (account active, user active, verified, 67-79); no membership or privilege check. App active and Origin enforced earlier by `get_app_context` (`auth/deps.py:67-78`).
- `organizations/repository.py:104-153`: member list has no org-status filter (122-133) — members see suspended orgs; the member mint refuses them (`rbac/repository.py:463-483` filters `status == ACTIVE`; `tokens/service.py:129-134` → `org_access_denied`). Picker and mint already disagree for members.
- `tokens/service.py:132`: mint access = `org_role_keys` (unexpired roles in exactly that org); the picker's `member` label must use the same rule.
- No existing helper for an `admin:super-admin` holder; pattern: `users/service.py:110`, `rbac/service.py:69` (`SUPER_ADMIN_ROLE`).

**Recommendations**
1. One holder helper shared with USR-39, e.g. `AuthzService.super_admin_org_access(user_id, app) -> bool`: true only if `org_mode == required`, `key != admin`, `super_admin_role_id` set; user holds it at app scope (`org_id IS NULL`, unexpired by DB clock); user holds active app-scope `admin:super-admin`, read live. Plain reads, never the `admin` lock (ADR-0003 §10).
2. One "org of the app" predicate (repository SQL `EXISTS` per OQ89) + `status = active`, used as list filter and as mint gate. A test over the same fixtures: every `super_admin` item mints with `org_access`, every super-admin-mintable org is listed.
3. One membership rule: `member` exactly when the mint takes the member path (unexpired org-exact roles, same `expires_at > now()`); expired member roles → `super_admin`.
4. Suspended orgs hidden from `super_admin` items only; member items unchanged (AC2). Reword AC1: "suspended orgs are not listed *as super_admin*".
5. No leaks: branch chosen server-side by the helper only; no `?all=`; non-holder SQL, `total`, errors unchanged; `super_admin` items `roles: []`; holder is always the user behind `account_id`.
6. Fixed query count on the holder path: helper (1–2), count, page (`LEFT JOIN`/`EXISTS` for `access`, `ORDER BY name, id`), existing roles batch; assert equal at limit 1 and 100.
7. Live re-check, no caching; disabled user already 401 `session_invalid`.
8. Log `orgs_listed access=super_admin user_id= app= total=` (no email); no audit row (spec §5.16).
9. Tests: non-holder with a role in another app's org sees no extras; holder sees nothing of other apps; revoked `admin:super-admin` or expired role → member-only list; `admin` app → 422 `org_not_supported`.

**Risks**
- Separate "org of the app" computations drift (listed-not-openable or mintable-hidden) — 1–2 prevent it.
- OQ89 (b): an org whose last assignment expired disappears; an org belongs to the app only because someone joined — document.
- Member items in suspended orgs still cannot be minted (existing mismatch; R7, not fixed).
- Revealing every org of the app is intended; bounded by live `admin:super-admin`.

**Open questions for Ming (defaults)**
- OQ89 (b): unexpired org-scope role assignment of the app or active entitlement (same predicate as `get_active_for`).
- Suspended orgs hidden for `super_admin` items; member items unchanged.
- OQ82 picker requires live `admin:super-admin` on every list — yes.
- OQ84 `access: "member" | "super_admin"`, always present, member wins.
- Suspended org where the holder is a member listed as `member` with `status: suspended` — yes, as today.
- `docs/consumers.md` note: `super_admin` items have `roles: []`, opened via the token's `org_access` claim — yes.
