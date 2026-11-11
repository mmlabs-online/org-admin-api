# consult-security-architect — USR-02e

## Findings
1. **Ticket conflicts with D17.** Title "(flag-gated)", Goal "without making it public" and the test "flag off → 404" vs AC1 + D17 "always mounted and public". The spec/decision log wins → the plan should drop those (or replace the test with "catalog reachable when `ADMIN_RBAC_API_ENABLED` is off"). The router must be mounted outside the `admin_rbac_api_enabled` branch in `app/main.py:41`.
2. **Disclosure surface.** Keys are ASCII-only by regex (`models.py:33-35`); role `name` is the only free-text field going public. App-key existence is weakly sensitive (keys appear in tokens `aud`, origins, app UIs), but one identical 404 for unknown/disabled/`admin` is cheap and hides the `admin` RBAC model and disabled apps.
3. **No size caps.** Nothing limits roles/permissions per app, so catalog size and ETag hashing cost are bounded only by what staff create (staff trusted today, but RBAC routes are unauthenticated in local/test). Hashing (SHA-256 over a few KB) is far cheaper than the DB query.
4. **Rate-limit bucket (F9 still pending).** `trusted_proxy_cidrs` defaults to `[]` (`config.py:38`) → in production every request comes from cloudflared's IP and shares **one** bucket; anyone could exhaust the catalog limit and starve legitimate app backends. The limiter is in-process, per worker.
5. **`_NO_CONTROL`** (`schemas.py:76`) still allows C1, bidi and line/paragraph separators; app `name` has no filter at all (NUL → 500).

## Recommendations
- **AC1 / R3:** separate `"role_catalog"` scope in `_limit_for` with its own `ROLE_CATALOG_RATE_LIMIT` / `_WINDOW_SECONDS` settings (R5, in `.env.example`); suggested 60 per 60 s per IP (/64 for IPv6); 429 with `Retry-After`; count 304s too.
- **Release prerequisite:** set `TRUSTED_PROXY_CIDRS` in the deployment repo (F9) before this ships; consider refusing to start in production with an empty list.
- **D17:** a dedicated response model built field by field (never `from_attributes` over ORM rows): `{app: key, roles: [{key, name, is_system, permissions: [keys]}]}`. Never ids, `is_admin`, descriptions, origins, `join_policy`, `org_mode`, `default_role_id`, timestamps. Test asserting the exact JSON key set.
- **AC3:** one service-level `NotFoundError` with the same message for unknown, disabled and `admin` (exact key check), no `details`; test the three bodies are byte-identical.
- **AC2:** keep `Cache-Control: no-cache` (+ `Vary: Accept-Encoding` if compression is on); no Cloudflare edge caching — a role change must appear on the next revalidation. Hash canonical bytes (sorted, `separators=(",",":")`, `ensure_ascii=False`, UTF-8) with SHA-256 truncated to ~128 bits. Parse `If-None-Match` defensively (cap header length, ignore malformed entries), weak comparison; 304 with no body and no Content-Length mismatch.
- **Carry-over (before role names go public):** pattern excluding C0, DEL, C1 (`\x80-\x9f`), ALM (`؜`), zero-width and LRM/RLM (`​-‏`), LS/PS (`  `), bidi embeddings/overrides (`‪-‮`), word joiner / invisible operators / bidi isolates (`⁠-⁩`), BOM (`﻿`). Apply to `RoleName`, `Description`, `AppCreate.name`, `AppUpdate.name`. Reject tests: `"a‮b"`, `"a\x85b"`, `"a b"`, NUL in an app name (422, not 500). **NFC:** normalize role and app names to NFC in a validator before storing (don't reject non-NFC input) — stable ETags across clients, fewer lookalike duplicates. Not NFKC (too lossy for Vietnamese).
- **R8 tests:** a 429 test for the catalog scope; a test that the route is listed and reachable with the RBAC flag off.

## Risks
- Shipping before F9 → one shared global bucket; the catalog can be denied to everyone.
- Homoglyph/mixed-script spoofing in role names remains possible even with the filter; consumers must render names as escaped plain text, never HTML.
- A client caching the catalog without revalidation could grant from stale role→permission maps beyond the 15-min token TTL; docs should say apps authorize from token claims, the catalog is only for display/mapping.
- Swagger `/docs` will list this route in production — acceptable (public by design).

## Open questions
1. Hard caps per app (e.g. ≤ 200 roles, ≤ 500 permissions, enforced in USR-02d's create paths) as a follow-up?
2. Include `default_role` (key only)? Useful to apps but reveals the join outcome; recommend leaving it out unless D17 is amended.
3. Empty `TRUSTED_PROXY_CIDRS` in production: startup error (R5 config validation change) or deploy-checklist item?
