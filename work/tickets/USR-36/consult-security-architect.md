# Consult — security-architect — USR-36 (2026-11-26)

**Findings**
- `app/modules/rbac/service.py:1584-1616`: the single `assign` already answers 404 `User not found` (1588) — a per-item `not_found` reveals nothing new to staff (Bearer + `admin:roles:assign`), only faster (100 ids/call). Acceptable.
- `service.py:1733-1750` `_guard_privileged`: with admin-app and designated roles refused at request level (ADR-0029 §3, ADR-0030 §4), no per-user `DENIED_ERRORS` path remains today; an `is_admin` role is a request-level `admin_wildcard_required`. Per-user denied rows only once USR-25 widens the self-ban.
- `audit/service.py:156-196`: `record_failure` rollback + commit — mid-batch it would wipe earlier grants (breaks R4).
- `service.py:1600-1611`: the single POST's denied row targets `(USER, user_id)` with `{role_key}` — "identical" = one row per user.
- `app/core/errors.py:102`: `DENIED_ERRORS` is the shared list; branch on it.
- No global body size limit (413 only for attachments, `errors.py:140`); admin Bearer routes have no rate limit (`main.py:82` limiter only on public and machine routes).

**Recommendations**
1. Request-level checks once under `lock_app` before per-user work (app, role, admin-app, super-admin, `role_has_admin_permission` + `require_super`, org, expiry). On `admin_wildcard_required` nothing is written yet, so `record_denied` is safe: one denied row per requested user (same content as the single POST) in that one commit (R4, AC2).
2. Per-user denials: roll back the user's savepoint, then add the denied row with a `record`-style insert in the outer transaction (`outcome=denied`, same `_build` content) via a small helper (`record_denied_inline`); a 5xx loses them with the batch — document.
3. No per-user `error.message` varying with user state (disabled, unverified, email): codes with fixed messages; no email in results, logs, audit (R11).
4. Keep `not_found` per item; no "exists but disabled" hint.
5. `user_ids: list[UUID]` 1–100, unique (422), `extra="forbid"`; Content-Length cap for this route (e.g. 16 KiB → 413 `payload_too_large`, 411 when missing); 100 UUIDs ≈ 4 KB.
6. `SET LOCAL lock_timeout` (5 s) and `= ANY(:ids)` preloads — otherwise a 100-user batch is a lock-holding DoS lever against single assigns and manifest PUTs.
7. Tests: non-super caller + `is_admin` role → 403, N denied rows, zero grants; duplicates → 422; 101 ids → 422; unknown ids mixed with valid → same 404 info as single POST; self id (after USR-25) → `failed` + denied row while other grants survive.

**Risks**
- Amplified mistakes: wrong role/org → 100 grants; no bulk revoke, no audit filter by `batch_id` (OQ79).
- `admin:roles:assign` holder can spread a non-privileged org-power role to 100 users per call — same privilege, faster.
- No rate limit: a compromised staff token runs ~100× more grants per minute.

**Open questions for Ming (defaults)**
1. Request-level `admin_wildcard_required`: one denied row per user (matches single POST, AC4).
2. Content-Length cap on this route — yes, 16 KiB, 411/413.
3. Per-staff batch rate limit — no for v1; follow-up with OQ79.
4. OQ79 (`request_id` filter) and bulk revoke as undo path — follow-up ticket, P2.
