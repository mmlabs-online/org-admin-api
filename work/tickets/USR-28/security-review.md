# Security review — USR-28 (Attempt 1, appsec-engineer, 2026-11-27)

Saved by the orchestrator (the appsec agent is read-only at REVIEW; code read only — the reviewer ran the suite).

**Verdict: PASS** — no 🔴, no 🟡.

Scope: commit d820596 (diff `efe6ddb..d820596`).

## Findings (all 🟢)
- **Auth class and permission:** `POST /api/v1/users/search` (`app/modules/users/router.py:180`) is Bearer via router-level `current_admin` + `require_permission("admin:users:read")` (`_search_kwargs()`), same as `GET /users`; registered at `tests/test_route_auth_classes.py:116`; 401/403 decided before the body is read (tests `tests/test_users_search.py:296`, `:306`).
- **No CORS/CSRF change:** `app/core/cors.py`, `app/main.py` untouched; Bearer, not cookie (R11 Origin/`X-MM-App` n/a).
- **Body cap / content-type:** shared `read_json_body` (`app/modules/releases/body.py:61`): 411 missing/non-digit Content-Length, 413 > 4 KiB before reading, 415 non-JSON, 10 s read limit → 422 `incomplete`; `UserSearch` `extra="forbid"` (`users/schemas.py:36`); tests `tests/test_users_search.py:238-269`.
- **No email echoed:** validation errors built with `include_input=False, include_context=False` (`body.py:79`); global handler strips `input`/`ctx` (`app/core/errors.py:215`); `email_required` 422 fixed message (`users/service.py:93`); no new logging; regression test `tests/test_users_search.py:275`.
- **Exact match not abusable:** bound-parameter equality on CITEXT (`users/repository.py:166`), no LIKE — `%`/`_` literal; `normalize_email` as at sign-up (`service.py:96-98`); whitespace-only → `""` → no match; contains path keeps `escape_like`.
- **Route ordering safe:** no `POST /users/{user_id}`; `/search` declared first; `GET /users/search` → `GET /{user_id}` 422 bad UUID, no leak.
- **Audit exclusion narrow:** `tests/test_audit_coverage.py:686` exempts exactly `("POST", "/api/v1/users/search")`; `len(AUDITED) == 35` still holds; no-audit test `tests/test_users_search.py:288`.
- **No extra information:** same `UserService.list`, shared `_users_page`, same models, `include=access` gate, `limit` ≤ 100; parity test `tests/test_users_search.py:148`.
- 💭 `GET /users?email=…&email_match=exact` still puts a full email in the URL (owner-accepted gap; field description points to POST).

## Follow-ups (out of scope)
- Deferred (owner decision): strip query strings / emails from access logs.
- Admin UI switches every email search to `POST /users/search`.
