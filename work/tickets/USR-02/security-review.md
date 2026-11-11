# Security review — USR-02

## Attempt 1 — 2026-11-11

`make check` passes (ruff, format, mypy, 142 tests). This slice adds no HTTP route, CLI, router, `main.py` change, dependency, env key, Docker or CI change; the only reference to rbac outside the module is the model import in `app/db/models.py`. R3/R5 not affected; R11 not touched (no credentials, tokens or logging).

**Verified correct:**
- **App scoping uses joins:** queries join `roles → apps` and `permissions` on `app_id = apps.id`, then filter `App.key == app_key` — no string-prefix reliance.
- **Org scope:** `org_id=None` → app-scope roles only; with an `org_id` → app-scope roles plus roles in that org.
- **Expiry** compared with `func.now()` (DB clock).
- **Same-app integrity in the DB:** `UNIQUE (id, app_id)` on `roles` and `permissions`; composite FKs from `role_permissions`, and `apps(default_role_id, id) → roles(id, app_id)` NO ACTION; `NULLS NOT DISTINCT` on the assignments unique; key-format CHECK regexes identical in model and migration; `wildcard_is_admin` forces `is_admin` on `<app>:*`.
- **Member-role builder** uses only `is_admin=False` permissions (`<app>:*` can never be included) and also removes any `is_admin` permission added to the role by hand.
- **Seed rows:** `admin` `closed` with `default_role_id` NULL; `admin:*` `is_admin`; `admin:super-admin` `is_system` holding exactly `admin:*`; no user grant.
- **Downgrade** drops the circular FK first, then indexes and tables in dependency order; seed rows go with the tables (expected).
- **`permits()` edge cases** (`app/modules/rbac/authz.py`): `admin:` and `nocolon` → False; no global `*`; `adm:*` does not cover `admin:x:y` (exact `f"{app}:*"` match); no case folding (`Admin:x:y` only looks for `Admin:*`, impossible under the CHECK); `admin:*x` → True only for `admin:*` holders (acceptable — they already cover all of `admin`).

### Findings

🟡 **`permits()` accepts a `str` as `granted`, silently becoming substring matching** — `app/modules/rbac/authz.py:11-20`. **Risk:** `str` is a `Collection[str]`, so mypy accepts `permits("admin:users:read-all", "admin:users:read")`, which returns **True** (`in` on a string is a substring check). JWT permission claims are often space-delimited strings (OAuth `scope` style), and ADR-0003 says ADM-002 and `mmlabs-auth` (USR-03b) copy this function "as is" — the footgun would spread to every app: escalation through any granted key containing the required key (`…:read-all` matching `…:read`, or `admin:*`-like text inside another key). **Fix:**
```python
def permits(granted: AbstractSet[str] | Sequence[str], required: str) -> bool:
    if isinstance(granted, str):
        raise TypeError("granted must be a collection of keys, not a str")
    ...
```
Test: `pytest.raises(TypeError)` on `permits("a:x:read-all", "a:x:read")`; document in ADR-0003 §3.

🟡 **`effective_roles` has no prefix check, unlike `effective_permissions`** — `app/modules/rbac/authz.py:33-37` (compare 44-46). **Risk:** the DB cannot enforce "role key prefix == owning app" (deferred to USR-02b per ADR-0003 §1). Until then a role keyed `admin:super-admin` registered under app `stoktok` would be returned by `effective_roles(user, "stoktok")`; USR-03 puts this list in the token `roles` claim, so a stoktok token would carry `admin:super-admin`, confusing any consumer that matches role names without checking `aud`. Permissions already get this second check; roles don't. **Fix:**
```python
keys = await self.repo.effective_role_keys(user_id, app_key, org_id)
prefix = f"{app_key}:"
return [k for k in keys if k.startswith(prefix)]
```
Test `test_effective_roles_drop_foreign_prefix` (a `stoktok` role keyed `admin:x` excluded for both `stoktok` and `admin`).

🟡 **`build_default_role("admin")` is not refused** — `app/modules/rbac/service.py:68-105`. **Risk:** ADR-0003 §8 decides "the `admin` app has no member role", but nothing enforces it. A future caller (USR-05 manifest sync, USR-02b app-creation path) could create `admin:member` holding every non-`is_admin` `admin:` permission (e.g. `admin:users:read`) and set it as `admin.default_role_id` (the column is NULL). If `join_policy` later moved away from `closed`, every joining user would get read access to admin data. The builder is the one function in this slice that turns data into grants, so it should fail closed. **Fix:** right after the app lookup: `if app.key == "admin": raise ConflictError("The admin app has no member role")` (or refuse every app with `join_policy == closed`), plus a test.

💭 **`permits()` silently accepts malformed required keys** — `authz.py:17-20`. A typo like `require_permission("admin:users")` (two segments) is granted to `admin:*` holders and refused to everyone else, so it hides during super-admin testing. Optional: validate `required` against the non-wildcard part of `PERMISSION_KEY_RE` — raise `ValueError` at declaration time in ADM-002 rather than inside `permits`, so `permits` stays pure.

💭 **`func.now()` is transaction start time** — `app/modules/rbac/repository.py:179`. An assignment expiring during a long transaction is honoured until commit. Negligible for request-scoped sessions; use `clock_timestamp()` only if long-lived sessions ever evaluate authorization.

💭 **`test_permits` misses edge cases** — `tests/test_rbac_authz.py:146-158`. Add `({"adm:*"}, "admin:x:y", False)`, `({"admin:*"}, "adm:x:y", False)`, `({"a:*"}, "a:", False)`, `({"a:*"}, ":x:y", False)`, `({"A:*"}, "a:x:y", False)`, plus the `str`-granted case — this pins the behaviour `mmlabs-auth` must copy exactly.

### Follow-ups
1. **USR-02b:** enforce "key prefix == owning app's key" for permissions and roles in the service (422). Consider a DB rule: `roles.app_key`/`permissions.app_key` with a composite FK to `apps(id, key)` + `CHECK (split_part(key, ':', 1) = app_key)`, removing reliance on the second-check filters. Also make app keys immutable (all prefixes depend on them).
2. **USR-02b/02c:** escalation guards on grant and role-edit paths — granting `<app>:*` or any `is_admin` permission, or assigning `admin:super-admin`, requires `admin:*`; last-super-admin guard; audit entries (R4, once ADM-004 lands).
3. **USR-06:** add the FK `role_assignments.org_id → organizations` (CASCADE or RESTRICT to decide). Until then nothing may write org-scoped assignments in staging/production (as the plan states).
4. **USR-03:** the token issuer must check user status, email verification, app status and join policy before calling `effective_*` (ADR-0003 §6: `effective_*` is RBAC only).
5. **USR-03b (`mmlabs-auth`):** copy `permits` together with the `str` guard and the edge-case tests above; tell consumers to always check `aud` and never match bare role names across apps.
