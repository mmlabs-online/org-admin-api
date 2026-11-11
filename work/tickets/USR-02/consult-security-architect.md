# consult-security-architect — USR-02

## Findings
1. **Every staff endpoint here is a privilege-escalation path with persistent effects.** An unauthenticated caller could assign themselves `admin:super-admin` before ADM-002 ships, and that row survives into the authenticated era. The §6.1 accepted risk ("anyone can call admin endpoints") does not cover grants that persist. Same for `origins`: anything written now becomes credentialed CORS once USR-03 reads `apps.origins`.
2. `app/api/router.py` mounts `api_router` ungated (organizations, ADM-001); the USR-02 routers must not be added there.
3. **NULL-distinct uniqueness:** `unique (user_id, role_id, org_id)` does not block duplicate app-scope (`org_id NULL`) assignments, and a bootstrap `ON CONFLICT` would silently insert duplicates.
4. **Namespace spoofing:** if permission keys are only regex-checked, app `stoktok` could own a permission keyed `admin:*`; any prefix-based resolution would then grant admin.
5. **The `GET /apps/{key}/roles` catalog is low-sensitivity** (permission names, nothing per user), but R3 enumerates the public endpoints exhaustively and this one isn't listed → making it public is a scope/rule change.

## Recommendations
- **R3/R7 exposure gate:** a **separate** default-off flag `ADMIN_RBAC_API_ENABLED` (don't reuse `ADMIN_USERS_API_ENABLED` — enabling PII reads shouldn't enable privilege writes). Copy the USR-01b pattern: validator refusing staging/production, conditional `include_router` in `create_app`, test that the default app returns 404 for `/api/v1/apps`.
- **Close the USR-01b `APP_ENV` caveat for both flags (cheap):** when either flag is true, require `app_env` to have been set explicitly (`"app_env" in self.model_fields_set`), else refuse to start. Fails closed.
- **Data integrity (AC2) — enforce in DB and service:**
  - Permission key prefix must equal the owning app's key; regex `^<app>:([a-z0-9-]+:[a-z0-9-]+|\*)$`; reject `*`, `*:*`, mid-key wildcards. App key `^[a-z][a-z0-9-]{1,31}$`, immutable after create.
  - Cross-app role permissions: `role_permissions` gets `app_id` with composite FKs `(role_id, app_id) → roles(id, app_id)` and `(permission_id, app_id) → permissions(id, app_id)`; service returns 422 before the DB error.
  - `default_role_id`: composite FK `(id, default_role_id) → roles(app_id, id)` (`use_alter`/DEFERRABLE for the apps↔roles cycle).
  - `role_assignments`: `UNIQUE NULLS NOT DISTINCT (user_id, role_id, org_id)` (PG 17) or a partial unique index `WHERE org_id IS NULL`.
  - `effective_permissions` resolves through `app_id` joins, never string prefixes; `<app>:*` expands only to that app's own permissions.
  - Reject past `expires_at` (422); compare expiry against DB `now()`.
- **Deletes and system rows:** FKs from assignments / role permissions / `default_role_id` use `ON DELETE RESTRICT` → 409. Prefer `status=disabled` over app deletion. The `admin` app, `admin:super-admin` and `is_system` rows are not deletable/editable via the API. Revoking the last active `admin:*` holder → 409. `is_system`/`is_admin` not mass-assignable through update schemas (`extra="forbid"`).
- **Origins validation (needed for S4/D12 in USR-03):** exact serialized origin `scheme://host[:port]` only (no path/query/fragment/userinfo, no `*`, no `null`); canonical storage (lowercase, IDNA → punycode, default port dropped); `https` only, `http` only for `localhost`/`127.0.0.1` when `app_env` is local/test; ≤ ~10 origins per app, each ≤ 255 chars, deduplicated; optionally a configured suffix allowlist (e.g. `.mmlabs.online`, per the §7 same-site risk). USR-03 matches by exact string equality.
- **Role catalog (AC7):** return only role key, role name and permission keys; the same 404 for unknown and disabled apps, and 404 for `admin` too (only this service needs that catalog). Strong ETag from a hash of canonical JSON, `Cache-Control: public, max-age=60`, no credentials, per-IP rate limit. Until the owner amends R3, keep it behind the same gate or defer the public mount to USR-05.
- **Bootstrap super-admin (AC4):** CLI only (`python -m app.cli grant-super-admin --email …`, via `docker compose exec`/`run` on the runner) — never HTTP. No auto-grant from an env var at startup (keeps re-granting after a revoke; readable by anyone with deploy-env access). Idempotent (insert on conflict do nothing, relying on NULLS NOT DISTINCT) with exit codes for created / already-present / user-not-found / user-disabled. `granted_by` NULL. Output: user id + masked email only (R11). Seed the `admin` app + role in a migration or idempotent seed; never seed a user grant.
- **R4:** leave a hook for audit entries on assign/revoke and on `origins`/`join_policy` changes (S8 events once ADM-004 lands).

## Risks
- Any staging/local instance with the flag on and reachable through the tunnel = full escalation path.
- ADM-002 release checklist: review/purge `role_assignments` for the `admin` app and review `apps.origins` before mounting the gated routers; remove the flag.
- No FK on `role_assignments.org_id` until USR-06 → arbitrary UUIDs storable; validate as UUID only and clean up before adding the FK.
- Changing `is_admin` later doesn't rebuild `<app>:member` — by design (data change), but staff may assume otherwise.
- Until USR-04 (D16), a bootstrapped super-admin has an unverified email and can't get a token. Do not add a "mark verified" path in the CLI.

## Open questions
1. Is the public catalog read approved as an R3 class-3 addition, or does it wait for USR-05's app credential? (Owner decision; flag the R3 conflict.)
2. Restrict `origins` to `*.mmlabs.online` (§7 same-site) or allow any HTTPS origin?
3. Separate `ADMIN_RBAC_API_ENABLED` or one combined `ADMIN_STAFF_API_ENABLED`? (Recommend separate.)
4. Bootstrap CLI: require `status=active` only, or also refuse while another super-admin exists (single-use)? Recommend active-only + idempotent, logging the result to stdout.
