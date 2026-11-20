# Consult — security-architect — ADM-034 (2026-11-20)

### Findings
1. **The body is parsed before the rate limit and auth run.** FastAPI reads and JSON-decodes a typed body parameter before it resolves dependencies. The manifest route works this way (`app/modules/rbac/manifest_router.py:21,36`). If the release route copies it, the OQ46 "cap checked inside the route" comes after a full parse, and malformed JSON returns 422 without ever reaching the limiter. The streaming pattern with a byte cap already exists in `app/modules/bug_reports/auth_router.py:96-104,149` and `bug_reports/upload.py`.
2. **`authenticate` is manifest-specific.** The denied audit action is hard-coded to `APP_MANIFEST_SYNCED` (`app/modules/app_credentials/service.py:168-175`). The `admin_app` refusal runs before any scope is known (`service.py:115-116`). `count_active` counts per app only (`repository.py:20-28`), and the partial index `ix_app_credentials_app_id_active` has no `scope` column (`models.py:24-28`).
3. **Python `$` accepts a trailing newline.** The shared regex constants use `^…$` (`app/modules/releases/models.py:45-51`). With Python `re.match`, `"v1.2.3\n"` passes. Pydantic `pattern=` (Rust regex) and Postgres `~` do not have this problem.
4. **The read schemas are lax and must not be reused for input.** `ReleaseImage` and `ReleaseChange` (`releases/schemas.py:35-47`) lack `extra="forbid"` and strict types. Also, `router.py:39-40` builds `ReleaseChange(**c)` from stored JSON, so anything the write accepts that the read schema rejects becomes a 500 for staff.
5. **Calling `model_validate_json` by hand gives a 500.** A raw `pydantic.ValidationError` from a manual parse does not reach the handler that strips `input`/`ctx` (`app/core/errors.py:208-215`).
6. **Threat model for a leaked `releases` key** (held by CI; anything that runs in the release workflow, including a compromised action or dependency, can read it):
   - **Forge pending records:** any `vX.Y.Z` or `sha-xxxxxxx` with fake images, digests and changes. The real CI write later replaces the whole pending document, so this alone does little harm.
   - **Main risk — first writer wins on enrichment:** an attacker fills the empty fields of a record the deployer created (fake digests or `has_migrations=false`). The real CI write then gets 409 `release_not_pending`, CI treats that as a warning, and the forged data stays on a deployed record for good.
   - **Storage growth:** about 30 writes/min per IP × 256 KiB, with no retention limit, and GitHub runners have many IPs.
   - **Audit spam:** wrong-app or wrong-scope `denied` rows, bounded only by the per-IP limit.
   - The key cannot touch the manifest, roles or users, and the actor is `privileged=False` (`service.py:55`).
7. **The `admin` app collision is also a security issue.** If org-admin-api and admin-pan share one `admin` app with one releases key, a leak in either repo can overwrite the other's records.

### Recommendations
- **AC "422 cases", "429", body cap:**
  - Do not declare a Pydantic body parameter. Order: `rate_limit("app_release")` dependency (new `Scope` in `app/core/rate_limit.py:71-127`; numbers in config, R5) → app-credential auth (header only) → in the handler: `Content-Length` check (411/413) → stream-read with the 256 KiB cap → require `application/json` (415) → `ReleasePut.model_validate_json(raw)`.
  - Re-raise `ValidationError` as `RequestValidationError(exc.errors())`. Publish the schema through `openapi_extra`.
- **Input models:** separate classes with `ConfigDict(extra="forbid", strict=True)` at every level.
  - Use Pydantic `pattern=` or `re.fullmatch`, never `$`; add tests with a trailing `\n` and `%0A` in the path version.
  - URLs: plain `str` with exact regexes, not `HttpUrl`, which normalizes the value.
  - `pr_url`: `^https://github\.com/mmlabs-online/[A-Za-z0-9_.-]{1,100}/pull/[1-9][0-9]{0,9}$`, rejecting `.` and `..` as the repo name.
  - Digest: lowercase only. Image name: Docker reference grammar with no `:tag` or `@`.
- **Titles:** NFC-normalize, then strip `DISALLOWED_CATEGORIES` (`app/core/text.py:9`), not just `Cc`. That also removes bidi overrides and zero-width characters (Trojan-Source-style spoofing in the staff UI). Then trim and enforce 1–200 characters. Strip rather than reject, so one bad title does not lose the whole release. Never echo values in 422 responses.
- **AC "scope 403":**
  - Give `authenticate(raw, key, *, scope, denied_action)` a fixed order: secret → revoked → scope (403, audited denied) → app disabled → mismatch (audited with `denied_action`).
  - Call `touch_last_used` only after every check passes (`service.py:178-180`).
  - Apply the `admin_app` refusal only when `scope == manifest`.
  - Count active keys per (app, scope), add `scope` to the active index, and add a CHECK on `scope`. Migration: NOT NULL with server default `manifest`.
- **AC "every PUT outcome", "audit only real changes":**
  - Use `INSERT … ON CONFLICT DO NOTHING RETURNING`, then `SELECT … FOR UPDATE`, then apply the rules, all in one transaction.
  - Enrichment: "empty" means NULL for scalars and `[]` for `images`/`changes`. `has_migrations=false` is non-null.
  - Compare normalized values; list order counts.
  - If any non-null field differs, return 409 and write nothing (no partial write).
  - Never write `status`, `deployed_at`, `deploy_run_url` or `created_via` from the PUT.
  - Write an audit row only when the change set is non-empty: ids, status and counts only.
- **AC "no PII":** `forbid` blocks author fields. Titles may still contain emails; document that the API cannot screen them.
- **Route registry:** turn `APP_CREDENTIAL` (`tests/test_route_auth_classes.py:64,270-274`) into a route → scope map and assert that each route's dependency requires that scope. Add the release route to `APP_CREDENTIAL_AUDITED` (`tests/test_audit_coverage.py:920`).
- **Staff UI contract** (put it in the OpenAPI descriptions):
  - Every stored string is untrusted, CI-reported data. Render it as text only: no Markdown, HTML or auto-linking of URLs inside titles.
  - Re-check `href` values against the same allow-list in the client, and use `rel="noopener noreferrer"`.
  - Label digests and changes as "reported by CI, not verified".
  - Guard any CSV export against formula injection.

### Risks
- **Forged enrichment persists after a 409** and nothing is alerted. Mitigation: log a warning line on 409 (ids and key_id only, no audit row).
- **Per-IP limits do not bound a leaked key** spread across runner IPs, and records are never purged.
- Shared `admin` app → shared blast radius across two repos.

### Open questions for Ming (suggested defaults)
1. **Unknown path app with a valid key:** 404 (ticket AC) or 403 mismatch? Default: 404 checked before the mismatch, with no audit row.
2. **Per-key bucket after auth** (ADR-0022 pattern), e.g. 120/hour per key? Default: yes.
3. **Image registry allow-list** (e.g. only `ghcr.io/mmlabs-online/…`)? Default: yes.
4. **Strip or reject disallowed characters in titles?** Default: strip `Cc`/`Cf`/`Zl`/`Zp`/`Co`/`Cs`/`Cn`.
5. **`admin` collision:** default is a separate app row for admin-pan (no roles, no manifest key), so each repo gets its own releases key.
6. **Log 409 enrichment conflicts** as a forgery signal? Default: yes, a log line only.
