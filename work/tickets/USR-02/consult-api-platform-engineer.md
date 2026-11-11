# consult-api-platform-engineer — USR-02

## Findings
1. **R9 vs `{key}` in paths.** R9 asks for UUID ids, but the spec/ADR already fix `GET /api/v1/apps/{key}/roles` as the catalog path; clients (`mmlabs-auth`) will hard-code it → frozen for v1.
2. **One path, two auth classes.** `GET /api/v1/apps/{key}/roles` is the public catalog, and the staff UI would want a role list there too. R3 gives each endpoint (method + path) exactly one class.
3. **Assignments are a privilege-escalation hole until ADM-002.** Unauthenticated `POST …/role-assignments` lets anyone grant themselves `admin:super-admin`, and the grant survives ADM-002. The users API is already behind `ADMIN_USERS_API_ENABLED` for this reason (off by default, refused in staging/production — `app/core/config.py:70`, `app/main.py:37`).
4. **No domain 422 in `errors.py`.** Only 404/409/403/429 exist; the AC needs 422 for a cross-app permission or another app's default role.
5. **Permission declarations.** `users/router.py` uses `openapi_extra=permission(...)` and `tests/test_users_admin.py:312` asserts the full map. Organizations routes declare none (follow-up, not this ticket).
6. **NULL `org_id` uniqueness.** `unique (user_id, role_id, org_id)` doesn't stop NULL-org duplicates; the duplicate app-scope 409 needs `NULLS NOT DISTINCT` (PG17). DB consultant's call, but the API contract depends on it.

## Recommendations
**Identifiers (R9 — flag as a conflict).** Address apps by `key`, recorded as an R9 exception: `key` is unique and immutable (PATCH rejects it, 422). Permissions, roles and assignments by UUID (keeps R9, avoids `:` in path segments). Patterns: app `^[a-z][a-z0-9-]{1,31}$` (no colon); permission `^{app}:(\*|[a-z0-9-]+:[a-z0-9-]+)$`; role `^{app}:[a-z0-9-]+$`; wrong app prefix → 422.

**Staff endpoints (Bearer, R3; behind the users-API gate — reuse the flag or add `ADMIN_RBAC_API_ENABLED` with the same prod validator):**

| Endpoint | Status codes | Permission |
|---|---|---|
| `GET/POST /api/v1/apps` | `Page[AppRead]` / 201 | `admin:apps:read` / `admin:apps:write` |
| `GET/PATCH/DELETE /api/v1/apps/{key}` | 404, 204 | `admin:apps:read` / `admin:apps:write` |
| `GET/POST /api/v1/apps/{key}/permissions` | `Page`, 201 | `admin:apps:read` / `admin:roles:manage` |
| `PATCH/DELETE /api/v1/apps/{key}/permissions/{permission_id}` | — | `admin:roles:manage` |
| `POST /api/v1/apps/{key}/roles` | 201 | `admin:roles:manage` |
| `PATCH/DELETE /api/v1/apps/{key}/roles/{role_id}` | — | `admin:roles:manage` |
| `PUT /api/v1/apps/{key}/roles/{role_id}/permissions` | — | `admin:roles:manage` |
| `GET /api/v1/users/{user_id}/role-assignments?app=<key>` | `Page` | `admin:users:read` |
| `POST /api/v1/users/{user_id}/role-assignments` | 201 | `admin:roles:assign` |
| `DELETE /api/v1/users/{user_id}/role-assignments/{assignment_id}` | 204 | `admin:roles:assign` |

- Apps: `DELETE` of `admin` → 409; `DELETE` of an app with assignments → 409.
- Permissions: `DELETE` cascades to `role_permissions` (changes the catalog ETag).
- Role permissions `PUT`: body `{permission_keys:[…]}` replaces the set (retry-safe); response = role with its permissions; 422 `invalid_permission` with `details` listing unknown/cross-app keys.
- Roles: an `is_system` role can't be deleted or re-keyed (409).
- Assignments: body `{role_id, org_id?, expires_at?}`; resource name `assignment` (has its own id).
- Seed these permission keys plus the existing `admin:users:read/write` as `admin` permissions; extend the OpenAPI x-permission test to every new route.

**Status rules (R6):** unknown id in the **path** → 404; unknown id in the **body** (role, org, `default_role_id`) → 422; duplicate key/assignment → 409 (also map `IntegrityError` → `ConflictError` for races); cross-app permission or `default_role_id` → 422; `expires_at` must be future and tz-aware → 422; `org_id` on an `org_mode = none` app → 422 (proposal; confirm in plan). Add `UnprocessableError(AppError)` (422, `unprocessable` or specific codes) — touches core → appsec at review.

**PATCH semantics:** follow `UserStatusUpdate` with `model_fields_set`; `origins` replaces the list; explicit `null` allowed only for `default_role_id` (clears it); other non-nullable fields reject `null` (422).

**Origins validation:** parse as `scheme://host[:port]`; no path (a lone `/` stripped), query, fragment, userinfo or wildcards; lowercase, dedupe, ≤ 20; `https` only, `http` only for `localhost`, `127.0.0.1`, `[::1]`. Return the normalized values so USR-03's CORS echo compares exact strings.

**Catalog `GET /api/v1/apps/{key}/roles` (public, decided now):** keep it public — moving it behind an app credential later would break v1 consumers (R9), and the ticket already calls it "not a secret by design".
- Shape: a document, not `Page[T]` (documented R9 exception): `{"app": key, "roles": [{"key","name","is_system","permissions":[keys]}]}`, roles and permissions sorted by key.
- Unknown app → 404; disabled apps also 404.
- Layering (R1): service returns the DTO; router serializes it and handles ETag/304.
- ETag: strong, `"` + first 32 hex of sha256 over the exact serialized body + `"` (safe: no GZip middleware; switch to `W/` if compression is added).
- `If-None-Match`: weak comparison (RFC 9110), accept lists, `W/` values and `*`; match → 304, empty body, same `ETag` and `Cache-Control`.
- `Cache-Control: public, max-age=60` (role changes visible within one access-token TTL; `no-cache` if the owner wants immediacy).
- Light per-IP rate limit with the existing `rate_limiter`.

**Staff role list:** the staff UI reads the same catalog; no second GET on that path.

## Risks
- Unauthenticated RBAC writes, especially assignments → escalation surviving ADM-002 unless gated as above (R3, R11).
- Missing `NULLS NOT DISTINCT` → duplicate app-scope grants, broken 409 test.
- The key-in-path R9 exception not written down → reviewer flags it.
- `http://localhost` origins in production data slightly widen CORS/CSRF once USR-03 echoes them.

## Open questions
1. Does the owner accept an R9 exception for `{key}` on apps and a non-paginated catalog document?
2. Gating: reuse `ADMIN_USERS_API_ENABLED` (rename to `ADMIN_API_ENABLED`?) or add a new flag?
3. Catalog: public now with `max-age=60` vs `no-cache`?
4. Staff "rebuild member role" endpoint, or is the seed/service helper enough (the AC needs only the helper)?
5. `org_id` on an `org_mode = none` app: 422 or allowed?
6. Include `is_admin` per permission in the catalog? (Useful to clients; adding later is backward-compatible.)
