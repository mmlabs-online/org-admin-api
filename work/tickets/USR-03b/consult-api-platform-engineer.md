# consult-api-platform-engineer — USR-03b

**Findings**
- Cookie-class pattern exists in `app/modules/auth/router.py`: `AppContextDep` (X-MM-App + Origin → 403 `app_header_required`/`origin_not_allowed`), then `DeviceDep` (401 `session_invalid`, clears the cookie, may rotate it). Each handler then re-sets a rotated cookie and sends `Cache-Control: no-store`. `/token` already returns 403 `not_member` with `details.join_available`, plus `email_unverified` and `audience_mismatch`.
- `_ERR` responses carry no schema, so openapi-fetch types error bodies as `unknown`.
- No `join_requests` table and no join code exist yet. `_grant_locked` raises 409 when the role is already assigned, so join cannot reuse it as it is if join must be idempotent.
- There is no cap on account sessions per device, so `/accounts` is unbounded. `users.display_name` exists and is nullable.
- Staff endpoints follow the pattern in `assignments_router.py`: `openapi_extra=permission(...)`, `Page[T]`, and mounting only when `admin_rbac_api_enabled` is on.

**Recommendations**
- `GET /auth/accounts`: cookie class, including `AppContextDep` (R3/R11). Response is `{"items":[{account_id, user_id, email, email_verified, display_name|null}]}`, ordered by created_at. It is a single document, not `Page[T]`. That needs a named R9 exception like D18, or add a per-device cap so the list stays bounded. Filter with `accounts_created_before` (N1). Send `no-store` and re-set a rotated cookie. Add a new rate-limit scope `accounts`.
- `POST /auth/apps/{key}/join {account_id}`:
  - Order of checks: Origin/X-MM-App, then path `{key}` == X-MM-App, else 403 `app_mismatch` (also covers `admin`). Then 401 `session_invalid`, then 403 `email_unverified`, then the policy.
  - `open`: 201 with `{status:"joined", roles:[...]}` the first time, 200 with the same shape when already a member. Any active app-scope role counts as membership, so the call is idempotent with no 409.
  - `approval`: 202 `{status:"pending", request_id}`, also idempotent while the request is pending.
  - `closed`: 403 `join_closed`.
  - A NULL `default_role_id` should answer 403 `join_unavailable` (or 409), never 500.
  - Use `lock_app`; F4/USR-02d also apply. Add a rate-limit scope `join`.
- Staff endpoints, flag-gated: `GET /api/v1/apps/{key}/join-requests?status=` returns `Page[JoinRequestRead]` (`admin:apps:read`). Approve and reject are `POST …/join-requests/{id}/approve` and `/reject`, return 200 with the row and need `admin:roles:assign`. A request that is no longer pending returns 409 `join_request_not_pending`. An unknown request returns 404.
- Add a shared `ErrorEnvelope` model in `responses=` and use stable `operation_id`s, so openapi-fetch gets typed errors. Response models should be discriminated by `status` (Literal).
- Fill in the join section of `docs/consumers.md`: the "Continue as" flow, the `/token` → `not_member` → join → `/token` sequence, and an error table.
- **Split:** the API part comes to about 400–550 lines plus a migration. I recommend USR-03b = accounts list + join (with `join_requests` written for `approval`), and USR-03f = staff list/approve/reject.

**Risks**
- An unbounded account list gives an unbounded response.
- `register-app` can reset `default_role_id` silently (S1 2026-11-13).
- If the staff endpoints are unauthenticated before ADM-002, approving a request grants roles. The flag must stay off in staging and production.

**Open questions**
- What does approve grant: the default role at approval time, or the one captured on the request?
- Should a rejected user be able to re-request, and is there a cooldown?
- Does `/token` `join_available` stay true while a request is pending?
