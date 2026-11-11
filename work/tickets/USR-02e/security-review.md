# Security review — USR-02e

## Attempt 1 — 2026-11-11

**Verdict: no 🔴 findings.** `make check` green (ruff, format, mypy, 406 tests). One hard condition is a **release gate** (F1): do not deploy until `TRUSTED_PROXY_CIDRS` is set in the deployment repo.

**Checked:**
- **Catalog contents:** only `app` and `roles[{key, name, is_system, permissions}]`, built field by field (`service.py:89-109`); no ids, `is_admin`, descriptions, origins, `join_policy`, timestamps. Foreign-prefix permissions dropped (`service.py:101`).
- **Role keys:** app-key prefix enforced on create (`service.py:366`); DB keeps roles/permissions in the same app (ADR §4).
- **404s:** unknown, disabled, `admin`, `UPPER` and 5000-char keys all return the same body (`not_found`, "App not found", `details: null`, no ETag); a malformed key fails `re.fullmatch` before any query; the rate-limit dependency runs before the handler.
- **Route clash:** the staff router has no GET on `/{key}/roles`, so the catalog shadows nothing when the flag is on.
- **304:** no `Content-Length`, empty body, same `ETag` and `Cache-Control: no-cache`.
- **`If-None-Match` probes:** match — exact, `W/`, list containing the tag, tag split across headers, `' * '`; no match — `"x",*`, `*` across two headers, `w/` (lowercase), `W/ "…"`, `W/W/`, `""`, unterminated quote; headers > 2048 chars never match. Comma splitting can only cause a miss (200), never a false 304 (our tags are hex).
- **`_NO_CONTROL`:** raw string on pydantic's Rust regex engine → no ReDoS. Rejects `‮`, `\x85`, ` `, NUL, `﻿`, `\n`, lone surrogates (NUL → 422, not 500); Vietnamese accepted. Applied to `AppName`, `RoleName`, `Description`.

### Findings

🟡 **F1 — per-IP limit is one global bucket in production until `TRUSTED_PROXY_CIDRS` is set** — `app/core/config.py:38`, `app/core/client_ip.py`, `app/modules/rbac/catalog_router.py:19`. **Risk:** first always-mounted public RBAC endpoint. Behind cloudflared every request has one peer IP, so all callers share one 120 req / 60 s bucket — any single client can 429 every app backend (and signup, same problem); 404s count, so a stream of junk keys exhausts it. **Fix:** release prerequisite (plan A7), not a code change here: the PR body must state it as a deploy blocker — set `TRUSTED_PROXY_CIDRS` to cloudflared's peer address in `mmlabs-online/deployment` before the image containing USR-02e is deployed, then confirm `CF-Connecting-IP` is honoured. Prioritise FU3 (refuse to start in staging/production with an empty list).

💭 **F2 — display-text filter is a fixed code-point list, not Unicode categories** — `app/modules/rbac/schemas.py:19`. Matches ADR-0003 §11 exactly but still accepts (verified) U+00AD soft hyphen, U+180E, variation selectors (U+FE0F, U+E0100–E01EF) and tag characters U+E0000–E007F (used for hidden-text / prompt smuggling). **Risk:** low — names are written only by trusted staff, the admin API can't be enabled in staging/production before ADM-002 — but role names are now publicly readable and may be shown in app UIs or passed to LLMs. **Fix (follow-up):** an `AfterValidator` rejecting any character whose `unicodedata.category` is in `{Cc, Cf, Zl, Zp, Co, Cs, Cn}`; reject tests for `"a\U000E0041b"` and `"a­b"`; amend ADR-0003 §11.

💭 **F3 — framework routing responses aren't the R6 envelope.** `POST /api/v1/apps/{key}/roles` with the flag off → 405 `{"detail":"Method Not Allowed"}` with `Allow: GET`; a key containing `%2F` → framework 404 `{"detail":"Not Found"}`. **Risk:** the 405 reveals only that a public, documented route exists; the flag state can't leak in production (validator forbids the flag there); the `%2F` 404 doesn't reveal app existence. No security property broken, but not R6. **Fix (follow-up, existing behaviour):** a `StarletteHTTPException` handler mapping framework 404/405 into the `{"error": …}` envelope.

💭 **F4 — the foreign-prefix filter has no test** — `service.py:101`. Defence in depth already made unreachable via the API by the same-app FKs; a refactor could drop it silently. **Fix (optional):** a service-level unit test feeding `catalog_rows` a row with `other:x:y`.

💭 **F5 — two DB queries per request (304s included); catalog size unbounded.** Distributed traffic bounded only per IP; staff-created role/permission counts uncapped (consult F3). Acceptable at current scale. **Fix:** FU4 (per-app caps), optional short-TTL in-process cache keyed on app id.

### Follow-ups
1. FU3: refuse to start in staging/production with empty `TRUSTED_PROXY_CIDRS` — high priority now that an always-on public endpoint exists.
2. Limiter: per-scope key caps or separate eviction — catalog traffic from many IPv6 /64s can evict signup buckets from the shared 10,000-key store (`app/core/rate_limit.py:30`) (plan FU2).
3. Category-based display-text validator (F2) + FU5 NFC normalization.
4. Global framework 404/405 handler returning the R6 envelope (F3).
5. Per-app role/permission caps (FU4), possibly catalog caching (F5).
6. Integrator docs: render role names as escaped plain text; authorize from token claims, never from the catalog.
