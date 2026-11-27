# Consult — api-platform-engineer — USR-28 (2026-11-27)

**Findings**
- Filter `app/modules/users/repository.py:165-166`: `User.email.ilike('%escaped%')`; `router.py:45` `email` 1–254 chars, no normalisation. Sign-up `normalize_email` = `strip().lower()` (`auth/service.py:39-40`), reused by password_reset, invitations, member_service; `app/cli.py:205` has a separate EmailStr variant.
- `GET /users` (router.py:27-109): `Page[UserWithAccessRead] | Page[UserRead]`, params `app`, `role_id`, `include=access`; `admin:users:read`. `UserService.list(email, status, app_key, role_id, limit, offset)`.
- Access logs: Dockerfile CMD (line 18) plain uvicorn `--no-proxy-headers`, no `--no-access-log` / log-config → default access log writes the full request line incl. query string to stdout. `app/main.py` (68-80) has no logging middleware. `?email=` reaches our logs and any proxy/Cloudflare logs.
- Route registry `tests/test_route_auth_classes.py:115` lists `GET /users`; a new POST must be added.
- Whether `users.email` has a unique index on `lower(email)` or the plain column — not verified; check before choosing the exact query.

**Recommendations**
1. `email_match=exact|contains` (default `contains`); `email_match` without `email` → 422; exact: `User.email == normalize_email(email)` (no `ilike`, no `lower()` on the column unless the index is on `lower(email)`) — index-friendly, no wildcard risk.
2. Add `POST /api/v1/users/search` (log-dropping covers only our uvicorn logs, is easily undone, not proxy logs). Body `{email?, email_match?, status?, app?, role_id?, include?: ["access"], limit?, offset?}`, same validators/defaults, `extra="forbid"`.
3. Response identical to the GET (`Page[...]`), **200** (read-only, like D41); one service call and one shared response-building helper (incl. `include=access` branching).
4. R9: verb sub-route `search` — additive, needs a new R9 exception entry + D-number + ADR line; pagination in the body.
5. Auth class 1 (Bearer) `require_permission("admin:users:read")`; add to the registry; read-only — state no audit.
6. Tests: exact hit, case-insensitive with whitespace, no partial in exact mode, `contains` unchanged, 403, 401, 422 bad body, POST/GET parity, no email in logs; OpenAPI asserts the enum and POST schema.
7. Keep GET `email`, description says body search preferred; no deprecation headers until admin-pan PAN-015c migrates.
8. Query-string scrubbing in our logs only if cheap (uvicorn log filter); else follow-up (R7).

**Risks**
- Callers can still put emails in the GET URL until admin-pan migrates.
- Duplicated response schema may drift without a shared helper.
- Log-scrubbing alternative needs a custom log config (app/core — High).
- If request-body logging is ever added, the POST would leak emails.

**Open questions for Ming (defaults)**
1. `email_match=exact|contains`, default `contains`.
2. POST search; log scrubbing as a separate follow-up.
3. POST accepts the full filter set incl. `include=access` — yes.
4. GET `email` deprecation notice now — no, doc note only.
