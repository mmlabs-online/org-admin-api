# ADR-0003: RBAC keys, permission matching and catalog integrity

Status: Accepted (USR-02 plan, slice USR-02a, 2026-11-11)
Amended 2026-11-11 (USR-02b): §1, §3, §4, §7, §8, new §9.
Amended 2026-11-11 (USR-02d): §1, §7, §9, new §10.
Amended 2026-11-11 (USR-02e): new §11.
Amended 2026-11-11 (ADM-011): §11.
Date: 2026-11-11

## Context
ADR-0001 §3–§4 defines namespaced permissions, app-scoped roles and the five RBAC tables;
identity-architecture.md §3 and D9 add `join_policy`, `org_mode`, `origins`, `default_role_id` and
`permissions.is_admin`. Several details were left open and must be identical everywhere a permission
is checked: this service (USR-03 token `roles`, ADM-002 `require_permission`) and the `mmlabs-auth`
library apps use (USR-03b).

## Decision
1. **Key grammar** (enforced by database CHECKs; the USR-02b endpoints add 422s and the
   "prefix == owning app" check, which a CHECK cannot express):
   - app key: `^[a-z][a-z0-9-]{1,31}$` (no `:`; immutable once created: the apps API has no way
     to change it, `PATCH` refuses `key` with 422);
   - permission key: `<app>:<resource>:<action>` or `<app>:*`, each segment `[a-z][a-z0-9-]{0,47}`.
     Rejected: bare `*`, uppercase, `_`, extra segments, and `<app>:<resource>:*`. The ADM-002 ticket
     example `admin:apps:*` is therefore not allowed; use `admin:apps:read` / `admin:apps:write`;
   - role key: namespaced `<app>:<name>` (e.g. `stoktok:member`, `admin:super-admin`).
   - "prefix == owning app key" is enforced in the service (422 `unprocessable`,
     `reason: prefix_mismatch`) by the permission/role endpoints (USR-02d); the read path drops
     foreign prefixes (§4).
2. **Wildcard.** `<app>:*` is a stored permission row and must have `is_admin = true` (CHECK).
   Through the API, creating `<app>:*` with `is_admin` absent or null stores `true`; `is_admin =
   false` on create or PATCH is a 422 `unprocessable` (`reason: wildcard_must_be_admin`).
   It is returned unexpanded by `effective_permissions` and covers every `<app>:…` key, including keys
   registered later.
3. **One matcher**, `permits(granted, required)`: true when `required` is in `granted` or
   `<app>:*` of the same app is. No global `*`, no `<app>:<resource>:*`, no deny rules. A required key
   without an `<app>:` prefix never matches. `mmlabs-auth` copies it exactly.
   `granted` must be a set or sequence of keys; a bare `str` raises `TypeError` (`in` on a str
   would match substrings). Pinned edge cases:

   | granted | required | result |
   |---|---|---|
   | `{"adm:*"}` | `admin:x:y` | false |
   | `{"admin:*"}` | `adm:x:y` | false |
   | `{"a:*"}` | `a:` | false |
   | `{"a:*"}` | `:x:y` | false |
   | `{"A:*"}` | `a:x:y` | false (case-sensitive) |
   | `["a:x:read"]` | `a:x:read` | true (list accepted) |
   | `"a:x:read-all"` | `a:x:read` | `TypeError` |
4. **Same-app integrity in the database.** `roles` and `permissions` have `UNIQUE (id, app_id)`;
   `role_permissions` carries `app_id` with composite FKs to both, and
   `apps (default_role_id, id) → roles (id, app_id)`, so a role can neither hold another app's
   permission nor be another app's default role. `effective_permissions` and `effective_roles` also
   drop keys whose prefix is not the requested app (second check).
5. **Assignments.** `UNIQUE NULLS NOT DISTINCT (user_id, role_id, org_id)`: `org_id NULL` = app scope
   and is unique per user and role. Expiry is compared with the database `now()`.
6. **`effective_*` is RBAC only.** It does not check user status, email verification, or app
   status/join policy; callers check those and return their own errors.
7. **Default role is data, rebuilt only when asked.** `build_default_role(app)` makes `<app>:member`
   hold exactly the app's non-`is_admin` permissions, sets `default_role_id` only when it is NULL, is
   idempotent and returns the diff. It never runs automatically on permission changes, so a staff
   tightening is not silently undone. `default_role_id` is set by a conditional
   `UPDATE … WHERE default_role_id IS NULL` (a concurrent change is never overwritten); the
   `role_permissions` insert is idempotent (`ON CONFLICT DO NOTHING`); a concurrent creation of the
   member role, or any racing insert/update/delete in the apps API, is a 409 (savepoint, the request
   session stays usable). The builder and the role PUT (USR-02d) share one diff helper
   (`_set_role_permissions`): no write when nothing changes, the diff applied in one savepoint, a
   race (e.g. a permission deleted meanwhile) a 409. A manual PUT on `<app>:member` is allowed
   (content is data) and is overwritten by the next explicit rebuild.
8. **The `admin` app has no member role.** It is `closed`; `default_role_id` stays NULL and roles
   come only from grants. Seed: `admin`, `admin:*` (`is_admin`), `admin:super-admin` (`is_system`).
   `build_default_role("admin")` is refused (409). Through the API the `admin` app cannot be
   deleted, and its `join_policy` (≠ `closed`), `default_role_id` (≠ NULL) and `status`
   (≠ `active`) cannot be changed (409); a same-value PATCH is a no-op.
9. **A default role never holds an `is_admin` permission.** Setting `apps.default_role_id` to a role
   that holds any `is_admin` permission is refused (422 `unprocessable`), as is a role of another
   app or an unknown role. **Origins** (`apps.origins`, the future CORS echo) are stored in one
   normal form: lowercase `scheme://host[:port]`, no path, query, fragment, userinfo or trailing
   dot; default ports (`:443`, `:80`) dropped. Only mmlabs origins (D19): `https://mmlabs.online`
   and `https://<sub>.mmlabs.online` (ASCII only; register the punycode form) on the default port,
   never `https://api.mmlabs.online` (the API itself); loopback (`localhost`, `127.0.0.1`,
   `[::1]`) on `http` or `https`, any port; no other hosts or IP literals; at most 20 entries of
   at most 255 characters, deduplicated. Invalid entries are a 422 that does not echo the value.
   The "default role never holds `is_admin`" invariant is enforced on every path that can break
   it (app PATCH of `default_role_id`, role PUT, permission PATCH of `is_admin`), each under the
   per-app lock (§10).
10. **Writers lock the app row first (USR-02d).** Every RBAC mutation (permission and role
    endpoints, app PATCH/DELETE, `build_default_role`) starts with
    `SELECT … FROM apps WHERE key = … FOR NO KEY UPDATE` and decides every guard after it. Roles,
    permissions, `role_permissions` and `apps.default_role_id` are bound to one app by the
    composite FKs, so one lock serializes them; the order is always app → rows (no deadlocks);
    FK checks take `KEY SHARE`, which this lock does not block. Guard catalogue:
    - 409 `conflict`: delete `admin:*`; delete a permission held by any role; delete a system
      role, the app's default role, or a role with assignments; PUT leaving `admin:super-admin`
      without `admin:*`; PUT with any `is_admin` permission on the app's default role; PATCH
      `is_admin = true` on a permission the default role holds; any racing write
      (`IntegrityError`).
    - 422 `unprocessable`: key prefix ≠ app key; `<app>:*` with `is_admin = false`; PUT with
      unknown or other-app keys (every one listed in `details` with its `key`: format-valid
      catalog keys, never free-form values).
    - System rows: `admin:*` cannot be deleted; `admin:super-admin` keeps `admin:*`; `is_system`
      roles accept PUT and name changes but cannot be deleted; `key` and `is_system` are never
      input.
11. **Public role catalog (USR-02e, D17).** `GET /api/v1/apps/{key}/roles` is public (R3 class 3),
    always mounted, rate-limited per IP (scope `role_catalog`, `ROLE_CATALOG_RATE_LIMIT` per
    `ROLE_CATALOG_RATE_WINDOW_SECONDS`, default 120/60 s; 304 and 404 responses count; over the
    limit → 429 `rate_limited` with `Retry-After`).
    - Document: `{"app": <key>, "roles": [{"key", "name", "is_system", "permissions": [<key>]}]}`,
      compact UTF-8 JSON (non-ASCII not escaped), roles sorted by key and permission keys sorted
      (code-point order, done in the service, never by DB collation). Nothing else (no ids,
      `is_admin`, descriptions, app fields or timestamps). Fields are append-only.
    - `ETag`: strong, `"` + first 32 hex chars (128 bits) of SHA-256 of the exact response bytes +
      `"`; `Cache-Control: no-cache` on 200 and 304.
    - `If-None-Match` (weak comparison, RFC 9110): exact tag, `W/` tag, a list containing the tag,
      or `*` → 304 with an empty body, the same `ETag` and `Cache-Control`. Malformed entries are
      ignored; `*` inside a list and headers over 2048 characters never match (→ 200).
    - 404 `not_found` with one message and no details for unknown, disabled and `admin` apps and
      for keys that fail the app key grammar (no DB query).
    - Display text (app names, role names, permission descriptions; input only) is
      NFC-normalized, then length-checked, then refused if any code point has Unicode category
      Cc, Cf, Zl, Zp, Co, Cs or Cn (Python's Unicode database: 15.0 on Python 3.12, so newly
      assigned characters are refused until Python is upgraded). This is a superset of the
      USR-02e list. ZWJ (U+200D) is refused, so emoji ZWJ sequences are not allowed; variation
      selectors are allowed. 422 `validation_error`, error type `display_text`, value not echoed.
      Stored values are NFC; read paths return stored values unchanged. Consumers render names
      as plain text and authorize from token claims; the catalog is for display and role →
      permission mapping only.

## Consequences
- USR-03 uses `effective_roles` for the token `roles` claim; ADM-002 uses `permits`.
- Widening the grammar later (e.g. allowing `_`) is additive; narrowing it is not.
- A new permission reaches `<app>:member` only after an explicit rebuild (USR-02b app creation,
  USR-05 manifest) or a staff edit.
- The `org_id → organizations` FK is added in USR-06.
