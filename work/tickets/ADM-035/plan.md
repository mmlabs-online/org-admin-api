Status: READY

# Plan — ADM-035: Deployer credential — data model and staff issue, list, revoke

> **ADR numbering.** The ticket says "ADR-0018 §2–§3". Two files in `docs/adr/` are numbered 0018. This batch
> renumbers `docs/adr/0018-release-log-and-deployer-credential.md` to **ADR-0021** (D30) in a separate solo change.
> This plan, and every docstring or doc line written for this ticket, calls it **ADR-0021**. The file is still at its
> old path until the solo change lands.

## Scope summary
- `docs/spec-v1.md` §5.13, line 227: *"The **deployer credential** is a new opaque key type (own prefix, SHA-256
  hash), **issued, listed and revoked by `admin:*` only** (Bearer endpoints, audited)."*
- `docs/spec-v1.md` §5.13, line 241 (OQ43): *"Format `mmdk_<key_id>_<secret>` (same shape and hashing as `mmak_`),
  its own table, **at most 2 active** (rotation overlap), no expiry, `last_used_at`, immediate revoke. Issue (value
  shown once, `no-store`), list (metadata only), revoke: Bearer `admin:*`, audited. **No CLI bootstrap**: the first
  key is issued through the admin API like app credentials (`docs/deploy.md`)."*
- ADR-0021 §2: *"`key_id` 16 hex (lookup), secret 256 bits; only SHA-256(secret) stored, compared with
  `hmac.compare_digest`; no pepper … Not bound to an app. Its own table (`app_credentials.app_id` stays NOT NULL), at
  most 2 active … immediate revoke (every request reads the database) … Issued, listed and revoked by `admin:*` only
  (Bearer, `aud=admin`), each write audited (`deployer_credential.issued`, `deployer_credential.revoked`). The value
  is shown once with `Cache-Control: no-store`; list returns metadata only."*
- ADR-0021 §3: *"Each route accepts **only its own key type**: an `mmdk_` key on an app route, an `mmak_` key on the
  report route, or an admin JWT on either gives the single 401 answer of ADR-0016 §4 (`unauthorized`,
  `WWW-Authenticate: Bearer realm="app"` or `realm="deployer"`, no details). An admin JWT is never accepted on these
  routes and machine keys are never accepted on any other route."*
- ADR-0021 §8: *"Actors are `SystemActor` values, … `SystemActor("deployer:{key_id}")`, `actor_kind = system` (no
  audit-schema migration)."*
- ADR-0021 §9: *"`/api/v1/deployer-credentials` (POST, GET, DELETE `/{id}`)."*
- Ticket scope: table `deployer_credentials` (key_id, secret SHA-256 hash, created_by, created_at, last_used_at,
  revoked_at), at most 2 active; codec `mmdk_` that rejects a wrong prefix before any DB read; the three Bearer
  `admin:*` routes; a `current_deployer` dependency for ADM-036, used by no production route.
- Out of scope: the deploy-report route, its rate limit, its body cap, and the `deployerCredential` OpenAPI scheme
  (ADM-036). The R3 class-4 text in `CLAUDE.md` and D28 in `docs/identity-architecture.md` are also out (ADM-034
  owns the first edit; ADM-036 adds the first route that accepts `mmdk_`).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Issue, list and revoke work and need `admin:*` (401 without a token, 403 with a non-`admin:*` admin) | `tests/test_deployer_credentials_api.py` › `test_issue_list_revoke_happy_path`, `test_routes_require_admin_wildcard` (each route with `admin:apps:write` + `admin:apps:read` → 403 `permission_denied`, `details.required == "admin:*"`); `tests/test_route_auth_classes.py` › `test_every_bearer_route_401_without_token` (the three new `BEARER` entries) |
| 2 | Issue and revoke are audited (one row on success, none on failure) | `tests/test_audit_coverage.py` › `test_success_writes_one_row` / `test_failure_writes_no_row` for `("POST", /deployer-credentials)` and `("DELETE", /deployer-credentials/{credential_id})`; `test_deployer_credentials_api.py` › `test_audit_rows_hold_key_id_only` |
| 3 | A 3rd active key → 409 `conflict` with `details[0].reason == "credential_limit"`; revoke-then-issue works | `test_deployer_credentials_api.py` › `test_third_active_key_409`, `test_revoke_then_issue`; `tests/test_deployer_credentials_concurrency.py` › `test_parallel_issue_at_limit_one_wins` (real connections: 1 active key + 2 parallel POSTs → one 201, one 409, exactly 2 active) |
| 4 | Only the hash is stored; the value is returned once (`Cache-Control: no-store`) and never appears in logs, audit rows or the list | `test_deployer_credentials_api.py` › `test_issue_returns_value_once_no_store` (header; `secret_hash` row == SHA-256 of the secret; GET list has no `credential` and no hash field), `test_value_never_logged_or_audited` (caplog + audit rows have neither the raw value nor the secret); `tests/test_audit_coverage.py` › `test_no_secret_or_email_in_any_row` (now covers both routes); `test_deployer_credentials_migration.py` › `test_table_and_constraints` (hash length CHECK) |
| 5 | A revoked key is refused at once; `last_used_at` updates on use | `tests/test_deployer_credentials_auth.py` › `test_revoked_key_401`, `test_revoked_between_lookup_and_touch_401` (touch returns no row → 401), `test_valid_key_touches_last_used`; `test_deployer_credentials_api.py` › `test_list_shows_last_used_at` |
| 6 | `current_deployer` refuses `mmak_` keys and admin JWTs with the single 401 (`unauthorized`, `WWW-Authenticate: Bearer realm="deployer"`, no details) | `test_deployer_credentials_auth.py` › `test_probe_rejections_identical` (parametrised: no header, non-Bearer scheme, garbage, `mmak_` valid key, admin JWT, `mmdk_` unknown key_id, wrong secret, revoked; same status, body and header each time); `test_wrong_prefix_no_db_read` |
| 7 | `mmdk_` keys are refused by every other auth class | `tests/test_route_auth_classes.py` › `test_deployer_key_on_bearer_routes_401` (every `BEARER` + `SELF_SCOPED` route → 401, `realm="admin"`), `test_deployer_key_on_manifest_401` (`realm="app"`), `test_deployer_key_on_cookie_routes_unauthenticated` (every `COOKIE` route + `mmdk_` Bearer → 401 `session_invalid`); `tests/test_app_credentials_codec.py` is unchanged; the new codec test asserts the `mmak_` parser refuses a valid `mmdk_` value |
| 8 | No production route uses `current_deployer` yet | `tests/test_route_auth_classes.py` › `test_deployer_credential_allow_list_is_exact` (`DEPLOYER_CREDENTIAL == set()` and no route's dependant calls `current_deployer`) |
| 9 | Migration is reversible and `alembic check` is clean | `tests/test_deployer_credentials_migration.py` › `test_downgrade_drops_table`; `uv run alembic check` in `make check` / CI |
| 10 | `make check` is green | `make check` |

## Ming verification after merge
- After deploy, the migration step creates `deployer_credentials`, and `GET https://api.mmlabs.online/api/v1/deployer-credentials`
  with an `admin:*` token returns `{"items": [], "total": 0, …}`.
- Wait for ADM-036 before you issue the first production key and store it as `MMLABS_DEPLOYER_CREDENTIAL` in
  `mmlabs-online/deployment`. Until then no route accepts the key, so an early key would only count toward the limit
  of 2. Follow the new `docs/deploy.md` section when you do.

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/modules/deployer_credentials/__init__.py` | create (empty) | new module | no |
| `app/modules/deployer_credentials/codec.py` | create: `PREFIX = "mmdk_"`, `_MAX_LENGTH = 128`, `_RE = ^mmdk_([0-9a-f]{16})_([A-Za-z0-9_-]{43})\Z`, `new_credential()`, `parse_credential()` (length cap, then `startswith(PREFIX)`, then regex); re-exports `secret_hash` from `app.modules.app_credentials.codec` | own prefix-aware codec. Does not touch `app/core/` or the `mmak_` codec (ADM-034 edits that module) | no |
| `app/modules/deployer_credentials/models.py` | create `DeployerCredential` | the table | no (registered in a High file, below) |
| `app/modules/deployer_credentials/schemas.py` | create `DeployerCredentialRead`, `DeployerCredentialIssued` | API contract | no |
| `app/modules/deployer_credentials/repository.py` | create: `lock()`, `add`, `count_active`, `page`, `get`, `revoke`, `get_by_key_id`, `touch_last_used` | R1 (SQLAlchemy only here) | no |
| `app/modules/deployer_credentials/service.py` | create `DeployerCredentialService` (`issue`, `list`, `revoke`, `authenticate`), `AuthenticatedDeployer`, `DeployerCredentialView`, `IssuedDeployerCredential`, `_reject` | business rules, R1, R4, R11 | no |
| `app/modules/deployer_credentials/deps.py` | create `deployer_credential_scheme` (`HTTPBearer(scheme_name="deployerCredential", bearerFormat="mmdk", auto_error=False)`), `current_deployer`, `DeployerDep`; the docstring says a route that uses it must put its per-IP rate limit first (ADM-036) | ticket: dependency for ADM-036 | no |
| `app/modules/deployer_credentials/router.py` | create the three Bearer routes, router-level `Depends(current_admin)`, `require_permission("admin:*", …)` | API | no |
| `app/api/router.py` | include `deployer_credentials` router (after `stats_router`, line 16) | mount | no |
| `app/db/models.py` | import + `__all__` entry `DeployerCredential` | R2 (Alembic metadata) | **yes** (`app/db/**`) |
| `migrations/versions/20261120_<rev>_adm035_deployer_credentials.py` | create table, `downgrade()` drops it | R2 | **yes** (`migrations/**`) |
| `app/modules/audit/actions.py` | add `AuditTarget.DEPLOYER_CREDENTIAL = "deployer_credential"` (after line 27), `AuditAction.DEPLOYER_CREDENTIAL_ISSUED = "deployer_credential.issued"`, `DEPLOYER_CREDENTIAL_REVOKED = "deployer_credential.revoked"` (after line 56) | R4. The DB has only the `ck_audit_log_action_format` regex, no enum, so no audit migration | no |
| `tests/deployer_credential_factories.py` | create `make_deployer_credential(session, *, revoked=False, created_by=None) -> (row, raw)` (mirror of `tests/app_credential_factories.py`) | test setup | no |
| `tests/test_deployer_credentials_codec.py` | create | pure-logic unit tests | no |
| `tests/test_deployer_credentials_api.py` | create | R8 endpoint tests | no |
| `tests/test_deployer_credentials_auth.py` | create (throwaway probe app) | `current_deployer` tests | no |
| `tests/test_deployer_credentials_concurrency.py` | create | limit race | no |
| `tests/test_deployer_credentials_migration.py` | create (mirror of `tests/test_app_credentials_migration.py`) | R2 round-trip + constraints | no |
| `tests/test_route_auth_classes.py` | modify: 3 `BEARER` entries (`"admin:*"`), `DEPLOYER_CREDENTIAL: set[tuple[str, str]] = set()`, exact-set test, `mmdk_` rejection tests | R3 classification, AC 7–8 | no |
| `tests/test_audit_coverage.py` | modify: 2 `AUDITED` entries + scenarios; `len(AUDITED) == 27` → `29` (line 508) | `test_every_mutating_bearer_route_is_mapped` fails otherwise | no |
| `tests/test_openapi_contract.py` | modify `_EXPECTED_ERRORS` (lines 307–343): `("post", "/deployer-credentials"): {"409"}`, `("get", "/deployer-credentials"): set()`, `("delete", "/deployer-credentials/{credential_id}"): {"404"}` | declared error responses stay pinned | no |
| `docs/deploy.md` | new section `## Deployer credential (ADM-035)` after "App credentials" (after line 586) | ticket technical notes: issuing and rotating | no |
| `docs/identity-architecture.md` | one row in the API table after line 169: `Admin: POST\|GET /api/v1/deployer-credentials, DELETE /api/v1/deployer-credentials/{id}` · Bearer `aud=admin` + `admin:*` · staff issue (shown once), list and revoke deployer credentials · ADM-035 | keeps the API list complete; no D-log row (D28 belongs to ADM-034) | no |

No other file. In particular there is no change to `CLAUDE.md`, `app/core/**`, `app/modules/app_credentials/**`,
`app/main.py`, `.env.example` or any ADR.

## Data model / migration
Table **`deployer_credentials`**. The model is `DeployerCredential(UUIDPkMixin, Base)`, in the same shape as
`app/modules/app_credentials/models.py:16-47` minus `app_id`.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | `uuid` | no | Python `uuid4` (UUIDPkMixin) | PK `pk_deployer_credentials` |
| `key_id` | `text` | no | — | UNIQUE `uq_deployer_credentials_key_id`; CHECK `ck_deployer_credentials_key_id_format`: `key_id ~ '^[0-9a-f]{16}$'` |
| `secret_hash` | `bytea` | no | — | CHECK `ck_deployer_credentials_secret_hash_length`: `octet_length(secret_hash) = 32` |
| `created_by` | `uuid` | yes | — | FK `fk_deployer_credentials_created_by_users` → `users.id` `ON DELETE SET NULL` |
| `created_at` | `timestamptz` | no | `now()` | |
| `last_used_at` | `timestamptz` | yes | — | |
| `revoked_at` | `timestamptz` | yes | — | active = `revoked_at IS NULL` |

- The model's `CheckConstraint` names are `key_id_format` and `secret_hash_length` (the naming convention adds the
  `ck_deployer_credentials_` prefix). The migration uses `op.f("ck_deployer_credentials_…")` with the **identical
  SQL text**. Alembic does not compare CHECKs, so the model docstring repeats the warning from
  `app_credentials/models.py:3-4`.
- `__repr__` returns `<DeployerCredential id=… key_id=…>` and never the hash.
- **No extra index.** The table holds a handful of rows for its whole life, so the PK and the unique `key_id` index
  are enough (`get_by_key_id` uses the unique index; list and count read a few rows).
- **Limit of 2 active:** enforced in the service under a transaction-scoped advisory lock (see Approach), not in the
  schema.
- Migration file `migrations/versions/20261120_<rev>_adm035_deployer_credentials.py`. Create it with
  `make migration m="adm035 deployer credentials"`, then hand-edit it. `down_revision` = the head at implement time
  (today `c4e8a2f7b916`, ADM-029). The docstring says: "ADM-035: expand-only. New table `deployer_credentials`
  (ADR-0021 §2). `downgrade()` drops the table and every issued deployer key."
- `downgrade()`: `op.drop_table("deployer_credentials")`.

## API contract
All three routes are R3 class 1 (Bearer `aud=admin`), with router-level `dependencies=[Depends(current_admin)]`,
`prefix="/deployer-credentials"` and `tags=["deployer-credentials"]`. They have no CORS change beyond the existing
admin matcher.

| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| POST | `/api/v1/deployer-credentials` | no body | `DeployerCredentialIssued` {`id`, `key_id`, `created_by`, `created_at`, `last_used_at` (null), `revoked_at` (null), `active` (true), `credential` (`mmdk_…`, shown once)}; header `Cache-Control: no-store` | 201; 401; 403; 409 `conflict` `details=[{"field": null, "reason": "credential_limit"}]` | `admin:*` (`require_permission("admin:*", errors={409: "Credential limit (details[0].reason)"})`) |
| GET | `/api/v1/deployer-credentials?limit=&offset=` | `PageDep` (`limit` ≤ 100) | `Page[DeployerCredentialRead]` (same fields without `credential`), newest first (`created_at DESC, id`), revoked rows included with `active: false` | 200; 401; 403; 422 (bad page params) | `admin:*` |
| DELETE | `/api/v1/deployer-credentials/{credential_id}` | path UUID | empty | 204 (also when already revoked: no-op, no audit row); 401; 403; 404 `not_found` (unknown id); 422 (non-UUID) | `admin:*` (`errors={404: "Credential not found"}`) |

- `requires_super` is not passed: `require_permission` raises on `requires_super` with `admin:*`
  (`app/modules/admin_auth/deps.py:132-134`). The precedent for a plain `admin:*` route is
  `app/modules/users/router.py:86`.
- **`current_deployer` (not on any route):** `Authorization: Bearer mmdk_<key_id>_<secret>`. Every failure returns
  401 `unauthorized`, message "Authentication required", `details: null`, `WWW-Authenticate: Bearer realm="deployer"`.
  It returns `AuthenticatedDeployer(credential_id, key_id)` with
  `.actor = SystemActor(f"deployer:{key_id}", privileged=False)`. It takes no path parameter, because the key works
  for any app.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `tests/test_deployer_credentials_codec.py` › `test_round_trip` | `new_credential` → `parse_credential` gives the same `key_id`; the value starts with `mmdk_`; the total length is 64 |
| › `test_rejects_malformed` (parametrised) | `""`, `"mmdk_"`, a valid `mmak_` value, a JWT-shaped `a.b.c`, uppercase hex `key_id`, 15-hex `key_id`, 42-char secret, trailing `"\n"`, 129+ chars → `None` |
| › `test_app_codec_rejects_deployer_key` | `app_credentials.codec.parse_credential(<valid mmdk_>) is None` (reverse direction) |
| › `test_secret_hash_is_sha256_32_bytes` | the hash rule is shared with `mmak_` |
| `tests/test_deployer_credentials_migration.py` › `test_downgrade_drops_table` | `to_regclass('deployer_credentials')` is NULL after downgrade to the previous revision and back after upgrade; ends at head |
| › `test_table_and_constraints` | key_id CHECK (uppercase, 15 chars), hash length CHECK, unique `key_id`, `created_by` SET NULL on user delete |
| `tests/test_deployer_credentials_api.py` › `test_issue_list_revoke_happy_path` | 201 body shape, GET lists it (`active` true, no `credential`), DELETE 204, GET shows `active` false + `revoked_at` |
| › `test_issue_returns_value_once_no_store` | `Cache-Control: no-store`; the stored `secret_hash` equals `secret_hash(parsed secret)`; no GET item has a `credential` or any `*hash*` key; `repr(row)` has no hash |
| › `test_third_active_key_409` | 2 active (factory) → 409, `details[0].reason == "credential_limit"`, no new row |
| › `test_revoked_keys_do_not_count` | 2 keys, 1 revoked → issue 201 |
| › `test_revoke_then_issue` | at the limit, revoke one → issue 201 |
| › `test_revoke_twice_is_noop` | second DELETE 204, still one `deployer_credential.revoked` row |
| › `test_revoke_unknown_404` / `test_revoke_non_uuid_422` | 404 / 422 |
| › `test_revoke_last_key_allowed` | revoking the only active key → 204 (incident path) |
| › `test_list_pagination_and_order` | newest first, `limit` > 100 → 422, `offset` |
| › `test_routes_require_admin_wildcard` (parametrised over the 3 routes) | a staff principal with `admin:apps:write`, `admin:apps:read` → 403 `permission_denied`, `details.required == "admin:*"` |
| › `test_routes_401_without_token` (parametrised) | 401 `realm="admin"` (also swept by the route-class test) |
| › `test_audit_rows_hold_key_id_only` | `issued`: `changes == {"key_id": {"from": None, "to": key_id}}`, target `("deployer_credential", id)`, `app_key` NULL, actor = the staff user; `revoked`: `changes` only `revoked_at` |
| › `test_value_never_logged_or_audited` | `caplog` (DEBUG) during issue + a probe authentication, and all audit rows: neither the raw value nor the secret part appears |
| › `test_list_shows_last_used_at` | after `authenticate`, the GET item has `last_used_at` set |
| `tests/test_deployer_credentials_auth.py` › `test_valid_key_authenticates` | probe route returns `credential_id`, `key_id`, actor label `deployer:<key_id>`, `privileged` False |
| › `test_valid_key_touches_last_used` | `last_used_at` NULL → set |
| › `test_probe_rejections_identical` (parametrised) | missing header, `Basic …`, garbage, valid `mmak_` (real `make_credential`), admin JWT (`mint(make_staff(…, "admin:*"))`), `mmdk_` unknown key_id, right key_id + wrong secret, revoked → each 401, body `{"error": {"code": "unauthorized", "message": "Authentication required", "details": null}}`, `WWW-Authenticate: Bearer realm="deployer"` |
| › `test_revoked_between_lookup_and_touch_401` | monkeypatch `get_by_key_id` to return the row, then revoke it before the touch → 401 (guarded UPDATE) |
| › `test_wrong_prefix_no_db_read` | monkeypatch `DeployerCredentialRepository.get_by_key_id` to fail the test if called; `mmak_` value / JWT / garbage → 401 |
| › `test_rejection_logs_reason_and_key_id_only` | caplog shows `deployer_credential_denied reason=… key_id=…` and not the raw value |
| `tests/test_deployer_credentials_concurrency.py` › `test_parallel_issue_at_limit_one_wins` | committed setup (1 active key + one staff `User` for `created_by`), `count_active` held behind an `asyncio.Barrier(2)` with a 0.5 s timeout (pattern of `tests/test_bug_reports_concurrency.py:50-62`), `AuditLogger.record` stubbed, `current_admin` overridden → `[201, 409]`, exactly 2 active; `finally` deletes every `deployer_credentials` row it created and the user (`_cleanup` style) |
| `tests/test_route_auth_classes.py` › `test_deployer_credential_allow_list_is_exact` | `DEPLOYER_CREDENTIAL == set()` and `{r for r, d in routes if current_deployer in dependant_calls(d)} == DEPLOYER_CREDENTIAL` |
| › `test_deployer_key_on_bearer_routes_401` (parametrised `BEARER` + `SELF_SCOPED`) | `mmdk_` Bearer → 401 `unauthorized`, `realm="admin"` |
| › `test_deployer_key_on_manifest_401` | `PUT /apps/stoktok/manifest` with `mmdk_` → 401 `realm="app"` |
| › `test_deployer_key_on_cookie_routes_unauthenticated` | every `COOKIE` route with `auth_headers()` + `mmdk_` Bearer → 401 `session_invalid`, no `www-authenticate` |
| `tests/test_audit_coverage.py` › existing parametrised tests | 2 new `AUDITED` entries: POST success `_deployer_issue` (no setup), failure `_deployer_issue_at_limit` (2 active → 409); DELETE success `_deployer_revoke`, failure `_deployer_revoke_unknown` (404) |

## Approach
1. **Codec** (`codec.py`). Pure functions with no ORM. Order: `len(raw) > 128` → None; `not raw.startswith("mmdk_")` →
   None; anchored regex with `\Z`. Do not change the `mmak_` regex. Import `secret_hash` from
   `app.modules.app_credentials.codec` (import only, no edit), so both key types share one hash rule.
2. **Model + migration** as in *Data model*. Register the model in `app/db/models.py`. Run `uv run alembic upgrade head`
   and `uv run alembic check` (clean), then a downgrade/upgrade round-trip locally. (R2)
3. **Audit enums** in `app/modules/audit/actions.py`. There is no DB migration, because `audit_log.action` only has the
   `ck_audit_log_action_format` regex.
4. **Repository** (SQLAlchemy only, R1):
   - `lock()`: `select(func.pg_advisory_xact_lock(func.hashtextextended(literal("deployer_credentials"), 0)))`. This is
     the pattern of `app/modules/bug_reports/repository.py:27-35`.
   - `add`: flush + refresh.
   - `count_active()`.
   - `page(limit, offset)`: total + rows, `created_at DESC, id`.
   - `get(id)`: with `populate_existing`.
   - `revoke(id) -> datetime | None`: guarded `UPDATE … WHERE id=:id AND revoked_at IS NULL SET revoked_at=now()
     RETURNING revoked_at`.
   - `get_by_key_id(key_id)`.
   - `touch_last_used(id) -> bool`: guarded `UPDATE … WHERE revoked_at IS NULL RETURNING id`.

   Copy these from `app/modules/app_credentials/repository.py:15-82`, without `app_id` and without the join.
5. **Service** (HTTP-free; raises `AppError` subclasses only, R1):
   - `MAX_ACTIVE = 2`.
   - `_reject(reason, key_id=None)` logs `deployer_credential_denied reason=%s key_id=%s` at info and returns
     `UnauthorizedError("Authentication required", headers={"WWW-Authenticate": 'Bearer realm="deployer"'})`. It never
     logs the raw value (R5, R11). This mirrors `app_credentials/service.py:35-41`.
   - `issue(actor, ctx)`:
     1. `repo.lock()`.
     2. If `count_active() >= 2` → `ConflictError("At most 2 active deployer credentials; revoke one first",
        details=[{"field": None, "reason": "credential_limit"}])`.
     3. `new_credential()`, then build the row with `secret_hash(secret)` and `created_by=actor_user_id(actor)`, then
        `add`.
     4. `audit.record(DEPLOYER_CREDENTIAL_ISSUED, target=(DEPLOYER_CREDENTIAL, row.id),
        changes={"key_id": diff.change(None, key_id)})`, with no `app_key`.
     5. Return `IssuedDeployerCredential(view…, credential=raw)`.

     The record is written in the same transaction as the insert (R4).
   - `list(limit, offset)` → views.
   - `revoke(id, actor, ctx)`:
     1. `repo.lock()`.
     2. `get` → if missing, `NotFoundError("Credential not found")`.
     3. If already revoked, return (no audit row).
     4. `repo.revoke`. If it returns None, return.
     5. Otherwise `audit.record(DEPLOYER_CREDENTIAL_REVOKED, changes={"revoked_at": diff.change(None, revoked_at)})`.
   - `authenticate(raw)`, in a fixed order:
     1. missing → `_reject("missing")`
     2. parse → `_reject("malformed")` (no DB read)
     3. `get_by_key_id` → `_reject("unknown", key_id)`
     4. `hmac.compare_digest(row.secret_hash, secret_hash(secret))` → `_reject("secret", key_id)`
     5. `row.revoked_at` → `_reject("revoked", key_id)`
     6. `touch_last_used(row.id)` returns False → `_reject("revoked", key_id)`
     7. return `AuthenticatedDeployer`

     It takes **no** advisory lock. The guarded UPDATE row-locks against a concurrent revoke, so a revoke that
     committed first is seen at once. A deploy report is never queued behind a staff write. Failed authentications
     write **no** audit row, so unauthenticated callers cannot flood the audit log (same as app credentials).
6. **Deps** (`deps.py`): `current_deployer(session: SessionDep, credentials: Security(deployer_credential_scheme))`
   passes `credentials.credentials` or None to `authenticate`. A non-Bearer `Authorization` gives None with
   `auto_error=False`, which leads to the "missing" 401. `DeployerDep = Annotated[AuthenticatedDeployer,
   Depends(current_deployer)]`. The docstring says the route class is R3 class 4 (b), that only the ADM-036 route may
   use it, and that its rate-limit dependency must come first.
7. **Router** (HTTP only, R1): POST sets `response.headers["Cache-Control"] = "no-store"` and returns
   `DeployerCredentialIssued.model_validate(issued)`. This is the pattern of `app_credentials/router.py:33-38`.
8. **Mount** in `app/api/router.py`.
9. **Tests** per the table. The probe app in `test_deployer_credentials_auth.py`:
   - Build it from `create_app()` plus a test-local `APIRouter` with `GET /__probe` depending on `DeployerDep`, and
     `dependency_overrides[get_session]` → `db_session`.
   - Mount it only inside the test module, so `test_every_route_is_classified` (which builds the production app)
     never sees it.
   - The admin JWT case needs real verification. Do not override `current_admin`; the probe does not use it anyway.
10. **Route-class test.** Add `DEPLOYER_CREDENTIAL` next to `APP_CREDENTIAL` in every expression that excludes or
    allows `APP_CREDENTIAL`:
    - `test_every_route_is_classified` line 172: an analogous `(route in DEPLOYER_CREDENTIAL and (has_admin, keys) ==
      (False, []))`.
    - The exclusions at lines 185, 236, 242 and 294.

    The set is empty today, so ADM-036 only adds its route to the set. Do **not** assert a `deployerCredential`
    security scheme in `test_openapi_bearer_scheme`: FastAPI publishes a scheme only when a route uses it. ADM-036
    adds that assertion.
11. **Docs.** `docs/deploy.md` gets a new section in the style of lines 541–586:
    - issue with the same safe `mktemp` / `python3` snippet, against `$api/deployer-credentials`;
    - store as the GitHub Environment secret `MMLABS_DEPLOYER_CREDENTIAL` in `mmlabs-online/deployment`;
    - list;
    - **rotation**: issue the 2nd key → update the secret → run one deploy → confirm `last_used_at` moved on the new
      `key_id` → revoke the old one;
    - **leak**: revoke first (immediate), then issue;
    - at most 2 active, and a lost value still counts until revoked;
    - `admin:*` for all three calls;
    - audit actions; no secret or hash in the audit log;
    - `last_used_at` rolls back with a request that later fails;
    - no route accepts the key until ADM-036.

    `docs/identity-architecture.md` gets the one API-table row.
12. `make check` green (R8).

Rules: R1 (steps 4–7), R2 (step 2), R3 class 1 for the three routes, with class-4 (b) prepared but unused (steps 6, 10),
R4 (step 5, same transaction), R5/R11 (hash only, `compare_digest`, nothing secret logged, value once with
`no-store`), R6 (envelope codes 201/204/401/403/404/409/422), R7 (file list above), R8 (tests), R9
(`/api/v1/deployer-credentials`, `Page[T]`, UUID ids, `timestamptz`; no new R9 exception).

## Specialist input used
- **Adopted, all three consults:** advisory lock `pg_advisory_xact_lock(hashtextextended('deployer_credentials', 0))`
  first in issue and revoke. No lock in authenticate; the guarded `touch_last_used` UPDATE does that job.
- **Rejected (database-optimizer #2 alternative):** the `slot` column with a partial UNIQUE. It adds a column and
  slot-picking logic for no gain over the lock.
- **Adopted (all):** auth check order, a single 401 with `realm="deployer"`, `HTTPBearer(scheme_name="deployerCredential",
  bearerFormat="mmdk", auto_error=False)`, `AuthenticatedDeployer` with `SystemActor("deployer:{key_id}",
  privileged=False)`, and no audit row on failed authentication.
- **Adopted (identity-access R4, security open Q2 option B):** the deployer key's codec is its own module. Rejected:
  security-architect R1's shared `KeyCodec` in `app/core/`, because it is a High path, would conflict with ADM-034's
  edits to the `mmak_` codec, and the caller asked to avoid `app/core`. Only `secret_hash` is imported from
  `app_credentials.codec`.
- **Adopted (identity-access #3, security #4/#7):** `require_permission("admin:*")` on all three routes, including
  revoke. This differs on purpose from app-credential revoke (`admin:apps:write`), per the ticket, spec and ADR-0021
  §2.
- **Adopted (identity-access R7):** idempotent revoke (204, no second audit row), unknown → 404, and revoking the last
  key is allowed.
- **Adopted (identity-access R5, security tests):** empty `DEPLOYER_CREDENTIAL` set with an exact-match assertion;
  `mmdk_` rejection on Bearer, manifest and cookie routes; probe route only in a test-built app; no
  `deployerCredential` scheme assertion until ADM-036.
- **Adopted (all):** CHECK texts identical in model and migration, `__repr__` without the hash, audit `changes` with
  `key_id` / `revoked_at` only, `last_used_at` in the list, and a concurrency test on real connections.
- **Rejected (db-optimizer #1, identity-access R6):** the partial index on `revoked_at IS NULL` and the `created_at`
  index. The table holds a handful of rows, and fewer `postgresql_where` texts means less model/migration drift.
- **Rejected (security R5):** `Pragma: no-cache`. ADR-0021 §2 asks for `Cache-Control: no-store`, which matches app
  credentials, and HTTP/1.0 caches are not in the path.
- **Answered (identity-access open Q2):** a non-`admin:*` caller is refused by the `RequirePermission` dependency with
  no `denied` audit row. That is the behaviour of every other plain `admin:*` route (e.g. user sessions). The service
  has no `require_super` step to audit, unlike `AppCredentialService.issue`, which needs one because its route
  permission is `admin:apps:write`.
- **Answered (identity-access open Q3):** list is `Page[T]`, with no new R9 exception.
- **Deferred (all, the R3 / D28 question):** the `CLAUDE.md` R3 class-4 text and D28 are left to ADM-034, which lists
  them in its scope, and ADM-036, which adds the first route that accepts `mmdk_`. ADM-035 adds no route that accepts
  a machine key, so the current R3 text stays true. There is no strong reason to move it here.
- **Deferred to ADM-036 (db-optimizer open Q4, identity-access risks):** the per-IP rate limit and body cap.

## Assumptions
- **ADR numbering.** The batch's solo change renumbers `0018-release-log-and-deployer-credential.md` to ADR-0021 (D30)
  before or with this PR. If it has not landed when the implementer starts, docstrings still say "ADR-0021". The
  reviewer must not flag that, and the orchestrator should make sure the solo change merges first.
- **Hotspots** (other open tickets in this batch or in progress, so expect a rebase):
  - `migrations/versions/`: ADM-033, ADM-030 and ADM-039 probably add migrations too. Set `down_revision` to the real
    head at implement time and re-check `alembic heads` == 1 after the rebase.
  - `app/db/models.py`: ADM-033, ADM-039.
  - `app/modules/audit/actions.py`: ADM-033 (`release.*`), ADM-039.
  - `tests/test_route_auth_classes.py`: ADM-033 and ADM-039 add Bearer routes, ADM-034 the app-credential scope
    registry, ADM-036 the `DEPLOYER_CREDENTIAL` route.
  - `tests/test_audit_coverage.py`: the `len(AUDITED)` count of 29 assumes no other new mutating route merged first.
    Recount on rebase.
  - `tests/test_openapi_contract.py`.
  - `app/api/router.py`.
  - `docs/deploy.md`: ADM-007 is `IN_PROGRESS`, and ADM-030 may also edit it.
  - `docs/identity-architecture.md`: ADM-033, ADM-034, ADM-041.
- The deployer table is global (no parent row), so any committed leftover row would break `count_active` in other
  tests. The concurrency test must delete every row it created in `finally`. The other tests use the rolled-back
  `db_session` only.
- `created_by` is the staff user id from `actor_user_id(principal)`. In `test_audit_coverage.py`, `admin_user=actor` is
  already a real `User`, so the FK holds.
- No new env/config keys, so `.env.example` and `app/core/config.py` are untouched.
- Changed-line estimate: about 330 production lines (module ~250, migration ~55, enums/registry/mount ~10, docs ~45)
  plus ~450 test lines. It mirrors an existing module closely, so one implement/review cycle is realistic.

## Blocking questions
None. ADR-0021 §2–§3, §8 and §9 and spec §5.13 / OQ43 settle every product decision. The remaining choices are
technical and are recorded above (advisory lock, separate codec module, no denied row on the dependency-level 403, R3
text left to ADM-034/036).

## Split proposal
None.
