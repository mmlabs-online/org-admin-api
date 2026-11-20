# Security review — ADM-034

## Attempt 1
Verdict: PASS

I found no blockers. This was a static review of commits 75e5277 and a6fc042, diffed against origin/main, using git, grep and file reads only. I did not run pytest or `make check`.

**What I checked against the focus list**
- **Auth order and uniform 401s.** The rate limit is a route-level dependency, so FastAPI resolves it first. The credential dependency runs before path validation and before any body read. `HTTPBearer(auto_error=False)` gives one 401 for missing, malformed, unknown, wrong-secret and revoked keys (`app/modules/app_credentials/service.py` `authenticate`). An `mmdk_` key or an admin JWT fails `parse_credential` and gets 401.
- **Fixed 403 order.** The order is `app_disabled` (not audited), then mismatch, then scope, matching the identity consult default. Mismatch and scope denies go through `_denied(denied_action, …)`, and `record_failure` rolls back and commits the row on its own. `changes` holds only `{"reason": code}`, never the secret. Releases denies are recorded as `release.created`, so they are no longer mislabelled `app.manifest_synced`.
- **Deferred lock.** `lock=False` on `ReleasesCredentialDep` (`app/modules/app_credentials/deps.py:130-135`). `confirm()` runs as the first statement of `ReleaseAppWriteService.put` (`app/modules/releases/app_write.py:706`). Its order is `lock_app`, then a status re-check (403 `app_disabled`), then `touch_last_used`, which re-checks revocation (401). The lock order is app, credential, release row. Staff DELETE locks only the release row, so there is no lock cycle.
- **Raw body.** No Pydantic body parameter (`app/modules/releases/app_router.py:612-629`). The order is auth, path grammar, `Content-Length` (411 if missing or non-ASCII-digit, 413 if over the cap), Content-Type (415), then a streamed read capped at the declared length and the cap, under `asyncio.timeout`. A disconnect or short body gives 422 `incomplete`. A `ValidationError` is re-raised as `RequestValidationError` built with `include_input=False` and `include_context=False`, and the global handler (`app/core/errors.py:208-215`) keeps only type, loc and msg.
- **Strict input.** Every input model has `extra="forbid", strict=True`. All patterns use `re.fullmatch`, and the path version also does (`write_schemas.py:1163-1171`), so `v1.2.3\n` and `%0A` are rejected. The Python version patterns come from `*_RE.strip("^$")` and use `[0-9]`, not `\d`, so Unicode digits are rejected. Every custom error is a `PydanticCustomError` with a fixed message.
  - The Python validation is a subset of every DB CHECK on `app_releases`: version grammar, `git_sha`, staging sha prefix, `deployment_pr` (`[1-9][0-9]{0,9}` is inside `[0-9]+`), and the bounds on images and changes. So `INSERT … ON CONFLICT DO NOTHING` (named unique constraint) cannot raise an IntegrityError that becomes a 500.
  - NUL cannot reach JSONB: titles drop `Cc`, and every other string field is matched against an ASCII regex.
- **Title cleaning.** NFC, then the characters in `DISALLOWED_CATEGORIES` are removed (Cc, Cf, Zl, Zp, Co, Cs, Cn, which covers bidi overrides and zero-width characters), then trim. The 1–200 length check runs after cleaning (`write_schemas.py:1086-1092,1117`).
- **Upsert and enrichment.** Insert-if-absent, then `SELECT … FOR UPDATE`, with one retry if the row was deleted concurrently. `rules.decide_app_write` does what the plan specifies:
  - "Empty" means `None` or `[]`, and `has_migrations=false` counts as a value.
  - A conflict writes nothing and logs a warning with ids and field names only.
  - A no-op writes no audit row.
  - Only `FIELDS` are ever written; `status`, `deployed_at`, `deploy_run_url` and `created_via` cannot be set from the PUT.
- **Audit content.** Audit `changes` hold only environment, version, status, counts and the names of changed fields. There are no titles, URLs, digests or sha (`app_write.py:680-687`).
- **Migration.** `downgrade()` deletes non-`manifest` keys before dropping the column, so a releases key cannot silently become a manifest key. It restores the old index and drops the CHECK and the column. The CHECK is named `ck_app_credentials_scope_valid` through the naming convention, which the round-trip test asserts.
- **Issuance.** The `admin` refusal is an allow-list (only `releases` is allowed). The active-key limit is per (app, scope) and counted under `lock_app`. `scope` is in the issued audit row.
- **Raw keys.** They are hidden from `repr` in `IssuedCredential`, `AppCredentialIssued` and `IssuedDeployerCredential`.
- **CORS.** The release route is not under `/auth/` and `BearerRouteMatcher` does not classify it as Bearer. The registry pins both PUT routes to their scope (`tests/test_route_auth_classes.py:64-69,289`), and `test_no_cors` covers the route.

**Findings**

🟡 None.

💭 **A pool connection stays open while the body is read.** `app/modules/releases/app_router.py:624-629` with `app/modules/app_credentials/service.py` (`authenticate`).
- **Risk:** the dependency's `get_with_app` SELECT autobegins a transaction on the request session. That connection stays checked out, idle in transaction, for up to `app_release_read_timeout_seconds` (10 s) while the body streams. Only callers with a valid key can do this, and each IP is limited to 30 requests a minute. Still, a leaked releases key used from many runner IPs could tie up pool connections.
- **Fix (hardening, optional):** release the connection after auth with `await session.rollback()` (or `commit()`) in the route before `read_json_body`. `AuthenticatedApp` is a plain dataclass, so nothing is lost; `confirm()` then re-locks and re-checks in a new transaction. Alternatively, track it as a follow-up.

💭 **Cleaned titles may not be NFC.** `app/modules/releases/write_schemas.py:1090-1092`.
- **Risk:** NFC runs before the Cf/Cn characters are removed. Removing something like a ZWJ between a base character and a combining mark can leave a string that is not NFC. The result is deterministic, so retries compare equal and this is not a security issue, only a display and dedup inconsistency.
- **Fix:** normalize again after the strip (`unicodedata.normalize("NFC", text).strip()`), or remove the characters first and normalize once.

💭 **Attacker-chosen field names appear in 422 responses.** The `extra_forbidden` errors put the unknown field names into `loc`. These are names, not values, and the handler drops `input`, so this is acceptable. Noted only for completeness.

💭 **The upgrade migration uses the short constraint name.** `migrations/versions/20261120_58a494f0449e_adm034_app_credential_scope.py:35,67` uses the short name `"scope_valid"` and relies on the naming convention. The precedent (`20261118_c7a3e91f5d28…py:24-46`) uses `op.f("ck_<table>_<name>")`. ADM-042 will drop `ck_app_credentials_scope_valid` by its full name. This works as written (the test asserts the name), but `op.f()` with the full name would be clearer.

**Follow-ups (out of scope, R7)**
1. **Per-key rate-limit bucket** after auth for `releases` keys, and a retention or size bound on `app_releases`. The per-IP limit does not cap a leaked key spread across GitHub runner IPs (risk 6 in the security-architect consult). The plan sets this to "no" by default for now; it needs Ming's decision.
2. **ADM-036 (deployer report):** records the deployer creates must store `images` and `changes` with the same keys as the app write (`ticket_id` and `risk` present even when null). `rules.decide_app_write` compares dicts by equality, so a different key set would cause spurious 409 `release_not_pending` on later enrichment.
3. **Ops (already in the plan, Q4):** register `admin-pan` as its own app row before any releases key is issued for `admin`, so the two repos do not share one key and blast radius. Store each key in a GitHub Environment restricted to release branches.
4. **Staff UI:** the OpenAPI text already says "untrusted CI-reported data: render it as text". admin-pan should also re-check `pr_url` and `deployment_pr` against the same allow-list before rendering links, use `rel="noopener noreferrer"`, and label digests as unverified.
