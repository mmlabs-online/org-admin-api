# consult-identity-access-engineer — USR-02

## Findings
1. **NULL `org_id` uniqueness gap is real.** ADR-0001's `unique (user_id, role_id, org_id)` with NULL = app-wide; Postgres treats NULLs as distinct, so duplicates of app-scope assignments are allowed. PG17: `UNIQUE NULLS NOT DISTINCT`, or a partial unique index `(user_id, role_id) WHERE org_id IS NULL`.
2. **ADM-002 ticket conflicts with the key grammar.** ADM-002 lists `admin:apps:*` as an example permission; ADR-0001 §3 and the USR-02 AC allow only `<app>:<resource>:<action>` or `<app>:*`.
3. **Role key form is underspecified.** ADR-0001 shows `stoktok:member` as a role key, but the roles table already has `app_id`, so the stored key could be `member` or `stoktok:member`.
4. **`app/core/permissions.py` only declares, never enforces.** The users router declares `admin:users:read|write`; apps/roles endpoints should follow the same pattern.
5. **USR-03 already owns status checks** ("disabled users rejected", "app disabled → 403"). `UserService.set_status` has no last-super-admin guard today.

## Recommendations
- **Key grammar (AC2) — suggested:** app key `^[a-z][a-z0-9-]{1,31}$`; segment `[a-z][a-z0-9_-]{0,47}`; permission `^<app>:<seg>:<seg>$` or exactly `^<app>:\*$`, ≤ 128 chars. Reject (don't normalize) uppercase, dots, extra segments, bare `*`, `<app>:<resource>:*`, and any prefix ≠ the app key → 422. Resource-level wildcards can be added later (additive). Flag the ADM-002 example as a spec conflict.
- **Role keys (AC2):** store namespaced (`<app>:<name>`, same prefix check) so token `roles` are globally unambiguous.
- **Wildcard (AC6):** `<app>:*` is an ordinary permission row with `is_admin = true` forced. `effective_permissions` returns concrete keys plus the wildcard marker. One matcher `has_permission(perms, key)`: `<app>:*` matches any `<app>:…` key, including keys not registered yet (ADM-002 needs this for "`admin:*` grants everything"). Never matches across apps; no global `*`. Share the matcher with `mmlabs-auth` so semantics stay identical.
- **`effective_roles(user_id, app_key, org_id=None)` (AC6):** app-scope roles (`org_id IS NULL`) plus roles with `org_id = :org_id`; with `org_id=None`, app scope only — never every org's roles. Filter `expires_at IS NULL OR expires_at > now()` (DB time). Deduplicated, sorted. Keep it pure RBAC — no user/app status filtering: USR-03 needs distinct errors (401 disabled user, 403 disabled app) and folding status in would hide them. Document in the docstring.
- **Assignments:** reject past `expires_at` (422); reject an org-scoped assignment when `app.org_mode = none` (422); re-assigning an active assignment → 409; re-assigning an expired one → replace in place.
- **Token `roles` claim (D7, for USR-03):** namespaced role keys, never expanded permissions; apps map roles → permissions via the ETag catalog; the catalog returns `admin:*` literally, unexpanded.
- **Default role (AC3):** an explicit, idempotent `build_default_role(app)` service helper (called by the seed) setting `<app>:member` = all non-`is_admin`, non-wildcard permissions, returning the diff. No automatic rebuild on permission create or `is_admin` flip (silent rebuilds widen every member's access); USR-05's manifest push is the automated trigger. Optional staff action later (`admin:roles:manage`).
- **Admin app (D9, AC4):** seed `join_policy = closed`, `default_role_id = NULL`, no `admin:member` role — D9's "every app gets `<app>:member`" doesn't apply to a closed app (flag this reading of D9). `admin:super-admin` is `is_system`. The admin app and system roles can't be deleted, and `admin:*` can't be removed from `admin:super-admin` — 409 in the service, starting now.
- **Bootstrap (AC4):** CLI only (e.g. `python -m app.cli grant-super-admin --email …` via `docker exec`). No env-var auto-grant at startup: with open signup (D2) and no email verification until USR-04 (D16), anyone could register the configured email first and become super-admin. User must exist. Same user already holds it → no-op, exit 0; a different active super-admin exists → refuse unless `--additional`; otherwise `granted_by = NULL`, `expires_at = NULL`. Log the user id only, not the email; audit entry with actor `system:cli` once ADM-004 lands.
- **Escalation guards (R3/R4) — design now:** give service methods an `actor` parameter now (unused until ADM-002). Once ADM-002 lands: granting a role or editing a role's permissions requires the actor to hold every conferred permission ("grant only what you hold"); any change to the `admin` app's roles/permissions or any grant of `admin:super-admin` requires `admin:*`. Implement the **last-super-admin guard** now (no auth needed): revoking, shortening the expiry of, or role-stripping the last active non-expired super-admin → 409. Follow-up: apply the same guard to `PATCH /users/{id}` status=disabled.

## Risks
- Anyone reaching the API can call role/assignment endpoints until ADM-002 — including granting themselves `admin:super-admin`. Widens the spec §6.1 accepted risk; keep ADM-002 P1.
- `role_assignments.org_id` has no FK until USR-06 → dangling/arbitrary org ids possible; validate on write where possible, backfill-check before USR-06 adds the FK.
- Members keep stale permissions after an `is_admin` flip until an explicit rebuild.
- A public `GET /apps/{key}/roles` reveals the admin permission map — low sensitivity, but deliberate.

## Open questions
1. Role keys namespaced (`stoktok:member`) or short (`member`)?
2. Allow `<app>:<resource>:*`? (ADM-002 vs ADR-0001.)
3. Admin app really has no member role and `default_role_id = NULL`?
4. CLI: refuse when a super-admin exists, or allow with `--additional`?
5. Catalog for a disabled app: 404 or the catalog?
