Status: BLOCKED

> BLOCKED only on owner choices Q1–Q6 below. Each has a default, and the plan is written for those defaults. When Ming approves the plan (the High-path gate) with the defaults, or with answers that do not change the file list, it becomes READY with no re-plan. A different answer to Q1 or Q4 changes the steps listed in that question.

# Plan — ADM-034: Release log — scoped app credentials and the app's release write

## Scope summary
Sources: spec §5.13 (lines 210–257), OQ36/37/44/45/46/47/48/49 (spec §8, lines 394–407), ADR-0021 §1, §3, §4, §7, §8, §10, §11, §12, ADR-0022 §1, §3, §10.

- **OQ44 / ADR-0021 §1:** *"App credentials gain a `scope`: `manifest` | `releases`; each key has exactly one scope. Existing keys become `manifest` (migration default). At most 2 active keys per (app, scope) … Issuance takes an optional `scope` (default `manifest`, so current clients keep working); list shows it. A key of the wrong scope on a route → 403 `app_credential_scope`, audited `denied`. Same `mmak_` prefix; the scope is read from the database … the `admin_app` refusal applies to the `manifest` scope only (OQ48)."* Also: *"App-credential authentication keeps taking `lock_app` (as today) before touching `last_used_at`, for both scopes."* and *"The order of the scope and app checks is PLAN's."*
- **OQ37 / ADR-0021 §4:** *"full replacement of the release document while `pending`; an identical retry returns 200 … Once `deployed` or `failed`, the app write may only enrich the record: fields that are still NULL/empty … are filled in from the PUT (200, audited `release.updated`); if any non-null field in the record differs from the PUT → 409 `release_not_pending`, record unchanged; if all non-null fields are identical and nothing is left to fill → 200 no-op (no audit row). The PUT never changes `status`."* Races: *"concurrent first writes resolve through the unique constraint … never a 500. Exact mechanism is PLAN's."*
- **OQ36:** production `vX.Y.Z` strict (no leading zeros, no suffix); staging `sha-` + 7 lowercase hex; a mismatch with the environment → 422; `git_sha` 40 lowercase hex, required on the app write; staging version = `sha-` + first 7 chars of `git_sha` (else 422).
- **OQ45:** `changes` ≤ 200; `pr_url` = `https://github.com/mmlabs-online/<repo>/pull/<N>`, unique per release; `title` 1–200 chars after control characters (Unicode `Cc`) are stripped and whitespace is trimmed; `ticket_id` optional `^[A-Z][A-Z0-9]{1,9}-[0-9]{1,6}[a-z]?$`; `risk` optional `low|medium|high`; `images` 1–10 on the app write, `name` a Docker repository name without tag or digest (≤ 255, unique), `digest` `sha256:<64 lowercase hex>`; `deployment_pr` optional, exactly `https://github.com/mmlabs-online/deployment/pull/<N>`; unknown fields → 422.
- **OQ46 / ADR-0021 §3:** *"per IP, before auth, own bucket per route: 30/min app write … body cap 256 KiB (app write) … checked inside the routes; values in config, final numbers PLAN's."* No CORS. A key of the wrong type or an admin JWT → the single 401.
- **OQ47 / ADR-0021 §8:** one audit row per write that changes something, **ids and status only** (app key, environment, version, release id, status, change count), never titles or URLs; no-ops write no row; another app's key or a wrong-scope key writes a `denied` row; actor `SystemActor("app:{key}/{key_id}")`.
- **OQ48 / ADR-0021 §11:** `releases` keys may be issued for `admin`; *"org-admin-api and admin-pan both map to the `admin` app and could collide … decided at PLAN of the releases ticket."* Decided below (Q4).
- **ADR-0021 §10 / §12, ADR-0022 §10:** R3 class 4 and R9 text in `CLAUDE.md`, decision-log row D30, registry `APP_CREDENTIAL` as a route → scope map. ADR-0022 §10 says ADM-041 already made R3 scope-generic, so §12 is **not** pasted verbatim.
- **ADR-0022 §1/§3/§10:** the CHECK ships only `('manifest','releases')`; ADM-042 widens it. Runtime scopes are refused for `admin`. `lock_app` stays for `manifest` and `releases`.
- Ticket out of scope: deployer credential and report (ADM-035, ADM-036).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Existing manifest keys and manifest pushes behave exactly as before | Unchanged tests stay green: `tests/test_app_credential_auth.py` (all, including `test_wrong_app_unknown_path_app_same_403` and `test_disabled_between_auth_and_lock_403`), `tests/test_app_manifest_api.py`, `tests/test_app_credentials_api.py`. Only the mechanical `authenticate(...)` call-site update at `test_app_credential_auth.py:244` and the index-name assertion at `test_app_credentials_migration.py:96` change. New: `test_app_credential_scope_migration.py › test_existing_rows_become_manifest`; `test_app_credentials_api.py › test_issue_without_body_is_manifest`, `test_issue_empty_object_is_manifest`. |
| 2 | A releases key cannot push a manifest, and a manifest key cannot write a release (403 `app_credential_scope`) | `test_app_credential_auth.py › test_releases_key_on_manifest_403_scope_denied_row`; `test_releases_write_api.py › test_manifest_key_on_release_403_scope_denied_row`. Each asserts one `denied` audit row with the right action and `changes.reason == "app_credential_scope"`, no secret, and nothing written. |
| 3 | Every PUT outcome in the scope list is tested, including retry, enrichment and the race on first insert | `test_releases_write_api.py`: `test_create_201_pending`, `test_pending_replace_200`, `test_identical_retry_200_no_audit`, `test_enrich_deployed_fills_empty_fields_200_audited`, `test_enrich_failed_fills_empty_fields_200_audited`, `test_enrich_conflict_409_writes_nothing`, `test_deployed_identical_200_noop`, `test_put_never_changes_status`, `test_status_field_in_body_422`. `test_release_rules.py` (pure unit tests of every branch). `test_releases_write_concurrency.py › test_parallel_first_insert_one_creates_one_noop`. |
| 4 | 422 cases for each validation rule; 429 on the rate limit; 404 unknown app | `test_releases_write_api.py`: parametrised `test_invalid_body_422` (one case per OQ45 rule), `test_bad_version_422` (each environment, a trailing `\n` and `%0A`), `test_staging_sha_mismatch_422`, `test_unknown_field_422_nested`, `test_rate_limit_429_before_auth`, `test_body_cap_413`, `test_missing_length_411`, `test_wrong_content_type_415`. **Unknown app:** per Q1 default, `test_unknown_path_app_403_mismatch` (a valid key gives 403, not 404) plus `test_app_credentials_api.py › test_issue_releases_unknown_app_404`. Spec §5.13 "404 unknown app" is flagged in Q1. |
| 5 | Audit rows only for real changes; no PII (no author emails) stored | `test_audit_coverage.py › test_app_credential_routes_are_audited` (extended per route); `test_releases_write_api.py › test_audit_changes_ids_and_status_only` (asserts that the `changes` keys are exactly the allowed set and contain no `title` or `pr_url` substring); `test_author_fields_rejected_422` (`author`, `author_email` at top level and inside `changes[]`). |

## Ming verification after merge
- **Ops step (Q4 default), to add to `work/ming-todo.md` (the orchestrator does this, on main):** register an app row `admin-pan` through `POST /api/v1/apps` (no roles, no manifest key). Issue a `releases` key for `admin` (org-admin-api) and one for `admin-pan`. Store each as `MMLABS_RELEASE_CREDENTIAL` in a GitHub Environment limited to release branches / `main` in its own repo.
- After deploy: one real `PUT /api/v1/apps/<app>/releases/staging/sha-…` from a release workflow returns 201, and the record appears in `GET /api/v1/releases`.

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `migrations/versions/20261120_<rev>_adm034_app_credential_scope.py` | new; `down_revision = "d7a3f5b2e801"` | scope column, CHECK, index swap (R2) | **High** |
| `app/modules/app_credentials/models.py` | `AppCredentialScope` StrEnum; `scope` column; CHECK `scope_valid`; index `ix_app_credentials_app_id_scope_active` replaces `ix_app_credentials_app_id_active` (lines 20–28) | model = migration | **High** |
| `app/modules/app_credentials/repository.py` | `count_active(app_id, scope)` (lines 20–28) | per-(app, scope) limit | **High** |
| `app/modules/app_credentials/service.py` | `issue(..., scope)`; `admin` refusal unless `scope == releases` (line 115); `scope` in `CredentialView` and the issued audit `changes`; `authenticate(raw, path_key, *, scope, denied_action, ctx, lock=True)` in the fixed order below; new `confirm(caller)`; `credential: str = field(repr=False)` (line 74) | AC1, AC2 | **High** |
| `app/modules/app_credentials/errors.py` | `AppCredentialScopeError(PermissionDeniedError)`, code `app_credential_scope` | AC2 | **High** |
| `app/modules/app_credentials/deps.py` | callable class `RequireAppCredential(scope, denied_action, *, lock=True)`; keep `AppCredentialDep` as the manifest alias; add `ReleasesCredentialDep` | AC2 | **High** |
| `app/modules/app_credentials/schemas.py` | `scope` on `AppCredentialRead`; new `AppCredentialIssue` (`extra="forbid"`, `scope: AppCredentialScope = manifest`); `credential: str = Field(repr=False)` on `AppCredentialIssued` | issuance | **High** |
| `app/modules/app_credentials/router.py` | `POST /{key}/credentials` takes an optional body, `Body(default=None)`; 409 description mentions the scope | issuance | **High** |
| `app/modules/rbac/manifest_router.py` | 403 description adds `app_credential_scope` (line 24); no code change (it keeps `AppCredentialDep`) | OpenAPI accuracy | **High** |
| `app/modules/deployer_credentials/service.py` | `credential: str = field(repr=False)` (line 65); incidental, **only if Q5 = yes** | same leak class | no |
| `app/core/rate_limit.py` | `"app_release"` in `Scope` and `_LIMITS` | per-IP bucket | **High** |
| `app/core/config.py` | `app_release_rate_limit=30`, `app_release_rate_window_seconds=60`, `app_release_max_bytes=262144` (`ge=1024`), `app_release_read_timeout_seconds=10` (`ge=1`), after line 105 | R5 | **High** |
| `.env.example` | the 4 keys, without values (after line 68) | R5 | **High** |
| `app/modules/audit/actions.py` | `RELEASE_CREATED = "release.created"`, `RELEASE_UPDATED = "release.updated"` | ADR-0021 §8 | no |
| `app/modules/releases/write_schemas.py` | new: strict input models `ReleaseImageIn`, `ReleaseChangeIn`, `ReleasePut`; `check_release_path()`; `check_staging_sha()` | OQ36/OQ45 | no |
| `app/modules/releases/rules.py` | new, pure: `decide_app_write(stored, incoming) -> Decision` | pure logic first | no |
| `app/modules/releases/body.py` | new: `read_json_body(request, model, *, cap, timeout_s)` (411/413/415/422); `inline_schema(model)` for `openapi_extra`; reused by ADM-036 | OQ46 | no |
| `app/modules/releases/errors.py` | new: `ReleaseNotPendingError(ConflictError)`, code `release_not_pending` | 409 | no |
| `app/modules/releases/repository.py` | `insert_if_absent(...)`, `get_for_update(app_id, environment, version)` (the pair ADM-036 reuses) | race-safe upsert | no |
| `app/modules/releases/app_write.py` | new: `ReleaseAppWriteService.put(caller, environment, version, body, ctx) -> (ReleaseView, created)` | R1 service | no |
| `app/modules/releases/app_router.py` | new: `PUT /apps/{key}/releases/{environment}/{version}` (machine route, no `current_admin`) | the route | no |
| `app/modules/releases/schemas.py` | move `_summary`/`_detail` from `router.py:32-41` here as `release_summary()`/`release_detail()`; `pr_url` description "untrusted" | shared by both routers | no |
| `app/modules/releases/router.py` | import the moved helpers (lines 32–41 removed) | — | no |
| `app/api/router.py` | include `releases_app_router` | mount | no |
| `CLAUDE.md` | R3 class 4 header D-list + 4(a) `releases` text; R9 exception | ADR-0021 §10/§12 | no (docs) |
| `docs/identity-architecture.md` | D30 row between D29 (line 323) and D31 (line 324) | ADR-0021 §12 | no |
| `docs/adr/0016-app-credential-auth-class-and-manifest.md` | line after `Status:` (line 3): "Amended by ADR-0021 (§1 scopes, §3 limit per (app, scope), §5 `admin` refusal manifest-only)" | ticket scope | no |
| `docs/adr/0017-entitlement-consumption.md` | line after `Status:` (line 3): "Amended by ADR-0021 (§2: R3 class 4 widened for releases only; still no entitlement pull)" | ticket scope | no |
| `docs/adr/0021-release-log-and-deployer-credential.md` | fix line 6 (text only, see step 12); §11: one line recording the Q4 decision | garbled note; OQ48 | no |
| `tests/app_credential_factories.py` | `make_credential(..., scope="manifest")` | tests | no |
| `tests/test_route_auth_classes.py` | `APP_CREDENTIAL` → `dict[(method, path), scope]`; exact-list test by `isinstance(call, RequireAppCredential)` and `.scope`; `_fill` gains `environment`/`version`; no-CORS / admin-JWT / `mmdk_` 401 tests parametrised over the map | registry | no |
| `tests/test_audit_coverage.py` | `APP_CREDENTIAL_AUDITED` gains the release route `{"release.created", "release.updated"}`; per-route lookup (lines 920–944) | R4 | no |
| `tests/test_app_credential_auth.py` | call site at line 244; new scope tests | AC1/2 | no |
| `tests/test_app_credentials_api.py` | scope issuance tests | AC1 | no |
| `tests/test_app_credentials_migration.py` | line 96: new index name | head changed | no |
| `tests/test_app_credential_scope_migration.py` | new: down/up round trip | R2 | no |
| `tests/test_release_rules.py` | new: pure unit tests | rule 6 | no |
| `tests/test_release_write_schemas.py` | new: pure validation tests | rule 6 | no |
| `tests/test_releases_write_api.py` | new: API tests | AC2–5 | no |
| `tests/test_releases_write_concurrency.py` | new: committed two-session race | AC3 | no |

Tier: **High** (migrations, `app/core/**`, `app/modules/app_credentials/**`, `app/modules/rbac/**`, `.env.example`). It is never auto-merged. `appsec-engineer` review is triggered.

## Data model / migration
`app_credentials` (migration `adm034_app_credential_scope`, one transaction):
1. `ADD COLUMN scope text NOT NULL DEFAULT 'manifest'`. This is metadata-only on PG17, and existing rows read as `manifest`. Keep the server default. The ORM also sets `default=AppCredentialScope.MANIFEST.value`.
2. `CONSTRAINT ck_app_credentials_scope_valid CHECK (scope IN ('manifest', 'releases'))`. The model declares `CheckConstraint("scope IN ('manifest', 'releases')", name="scope_valid")` with byte-identical text (Alembic does not compare CHECKs). The docstring says: *"ADM-042 drops and recreates `ck_app_credentials_scope_valid` to add the ADR-0022 runtime scopes."*
3. `DROP INDEX ix_app_credentials_app_id_active`, then `CREATE INDEX ix_app_credentials_app_id_scope_active ON app_credentials (app_id, scope) WHERE revoked_at IS NULL`. It serves `count_active` and keeps one index on the hot table.

**Downgrade** (docstring states that data is lost, as `f3a8d51c9e27` does):
1. `DELETE FROM app_credentials WHERE scope <> 'manifest'`. Otherwise `releases` keys would silently become `manifest` keys, a privilege escalation that could also break the 2-per-app limit.
2. Drop the new index, recreate `ix_app_credentials_app_id_active ON (app_id) WHERE revoked_at IS NULL`, drop the CHECK, drop the column.

`app_releases`: no schema change. ADM-033's CHECKs stay the backstop. Every CHECK-only rule (version grammar, `git_sha`, `staging_version_matches_sha`, `images_bounded`, `changes_bounded`, `deployment_pr_format`) is pre-validated at least as strictly in `write_schemas.py`, so a write cannot raise `IntegrityError` (→ 500). `deployed_iff_deployed_at` cannot fire because the PUT never writes `status` or `deployed_at`.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| PUT | `/api/v1/apps/{key}/releases/{environment}/{version}` | `ReleasePut` JSON (raw body, ≤ 256 KiB, `application/json`). `environment` ∈ `production`\|`staging`; `version` per OQ36 | `ReleaseDetail` (existing read schema) | 201 created `pending`; 200 replace / no-op / enrich; 401 `unauthorized` (`WWW-Authenticate: Bearer realm="app"`); 403 `app_disabled`, `app_credential_mismatch`, `app_credential_scope`; 409 `release_not_pending` (`details=[{"field","reason":"differs"}]`, names only); 411; 413; 415; 422 `validation_error`; 429 (`Retry-After`) | none: R3 class 4(a), `releases`-scoped `mmak_` key, own app only; no CORS |
| POST | `/api/v1/apps/{key}/credentials` (changed) | optional body `AppCredentialIssue {scope?: "manifest"\|"releases"}`; no body or `{}` = `manifest`; unknown field or scope → 422 | `AppCredentialIssued` + `scope` | 201; 404; 409 `admin_app` (manifest scope only), `app_disabled`, `credential_limit` (per scope); 422 | `admin:apps:write` + super, unchanged |
| GET | `/api/v1/apps/{key}/credentials` (changed) | — | `Page[AppCredentialRead]` + `scope` | unchanged | unchanged |

`ReleasePut`: `ConfigDict(extra="forbid", strict=True)` at every level. Fields:
- `git_sha`: str, required, pattern `GIT_SHA_RE`.
- `deployment_pr`: `str | None = None`, `DEPLOYMENT_PR_RE` tightened to `pull/[1-9][0-9]{0,9}`.
- `has_migrations`: `bool | None = None` (spec does not make it required).
- `images`: `list[ReleaseImageIn]`, 1–10, names unique. `ReleaseImageIn`:
  - `name`: Docker repository grammar `(host(:port)?/)?component(/component)*`, lowercase, ≤ 255, no `:tag`, no `@`.
  - `digest`: `^sha256:[0-9a-f]{64}$`.
- `changes`: `list[ReleaseChangeIn] = []`, ≤ 200, `pr_url` unique. `ReleaseChangeIn`:
  - `pr_url`: after-validator with `re.fullmatch(r"https://github\.com/mmlabs-online/([A-Za-z0-9_.-]{1,100})/pull/[1-9][0-9]{0,9}")`; repo name not `.` or `..`. No lookaround, so the pattern works with Pydantic's Rust regex too.
  - `title`: before-validator NFC → strip code points in `app/core/text.py` `DISALLOWED_CATEGORIES` (Q6) → `strip()`. Then 1–200 chars.
  - `ticket_id`: `str | None`, pattern as in OQ45.
  - `risk`: `ChangeRisk | None`.

Every validator raises `PydanticCustomError` with a fixed message and never interpolates the value. `status`, `deployed_at`, `deploy_run_url`, `created_via` and author fields are rejected by `forbid`. OpenAPI: `openapi_extra={"requestBody": {"required": True, "content": {"application/json": {"schema": inline_schema(ReleasePut)}}}}` (`$defs` inlined, so `app/core/openapi.py` is untouched). The description says: "untrusted CI-reported data; render as text; not verified; server-to-server only, no CORS". `responses` lists 200/201/401/403/409/411/413/415/422/429.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `test_release_write_schemas.py › test_valid_minimal_and_full` | happy parse |
| `… › test_version_grammar` (parametrised: `v1.2.3` ok; `v01.2.3`, `v1.2`, `v1.2.3-rc1`, `1.2.3`, `v1.2.3\n`, staging `sha-ABCDEF0`, `sha-abc`, `sha-abcdef0\n`, production grammar on staging and vice versa) | OQ36, `$` hazard |
| `… › test_staging_sha_cross_check` | OQ36 / CHECK pre-validation |
| `… › test_digest_lowercase_64_hex`, `test_image_name_no_tag_or_digest`, `test_image_name_max_255`, `test_images_1_to_10`, `test_image_names_unique` | OQ45 images |
| `… › test_pr_url_allow_list` (other org, `http:`, `.`/`..` repo, `pull/0`, trailing `/`, query, `\n`), `test_pr_url_unique`, `test_changes_max_200` | OQ45 changes |
| `… › test_title_strip_and_bounds` (C0/C1, bidi override U+202E, zero-width U+200B, NFC, only-controls → 422, 201 chars → 422, 200 ok) | OQ45 titles |
| `… › test_ticket_id_and_risk_formats`, `test_deployment_pr_exact` | OQ45 |
| `… › test_strict_types` (`has_migrations: "true"` → error), `test_extra_forbid_every_level` | mass assignment |
| `… › test_errors_never_echo_input` (error `msg` and `ctx` contain no submitted value) | ADR-0021 §7 |
| `test_release_rules.py › test_none_stored_is_create`, `test_pending_identical_noop`, `test_pending_diff_replace`, `test_deployed_fill_null_scalars`, `test_deployed_fill_empty_lists`, `test_has_migrations_false_is_non_null`, `test_deployed_differing_non_null_conflict` (incl. incoming null vs stored value), `test_deployed_identical_noop`, `test_failed_same_as_deployed`, `test_list_order_matters`, `test_conflict_lists_field_names_only` | OQ37 rule table |
| `test_app_credential_scope_migration.py › test_existing_rows_become_manifest_and_down_up` (seed a manifest and a releases key; downgrade deletes the releases key only; old index back; upgrade again; constraint and index names asserted) | R2, downgrade escalation |
| `test_app_credentials_api.py › test_issue_without_body_is_manifest`, `test_issue_empty_object_is_manifest`, `test_issue_releases_scope`, `test_issue_unknown_scope_422`, `test_issue_extra_field_422`, `test_issue_releases_for_admin_app_201`, `test_issue_manifest_for_admin_app_still_409`, `test_limit_is_per_scope` (2 manifest + 2 releases ok; a 3rd of either → 409), `test_issue_releases_unknown_app_404`, `test_list_shows_scope`, `test_issued_audit_has_scope` | issuance |
| `test_app_credential_auth.py › test_releases_key_on_manifest_403_scope_denied_row`, `test_check_order_mismatch_before_scope` (a releases key of app A on B's manifest → mismatch), `test_disabled_before_scope`, `test_scope_check_after_401s` (revoked releases key on manifest → 401), `test_issued_credential_repr_hides_secret` | AC2, order |
| `test_releases_write_api.py › test_create_201_pending` (`created_via=app`, status `pending`, `last_used_at` set, one `release.created` row, actor `app:{key}/{key_id}`) | AC3 |
| `… › test_pending_replace_200`, `test_identical_retry_200_no_audit` (also `updated_at` unchanged), `test_enrich_deployed_fills_empty_fields_200_audited`, `test_enrich_failed_fills_empty_fields_200_audited`, `test_enrich_conflict_409_writes_nothing` (record and `updated_at` unchanged, no audit row), `test_deployed_identical_200_noop`, `test_put_never_changes_status`, `test_status_field_in_body_422` | AC3 |
| `… › test_manifest_key_on_release_403_scope_denied_row` (action `release.created`, outcome `denied`) | AC2 |
| `… › test_unknown_path_app_403_mismatch` (Q1), `test_other_app_403_mismatch_denied_row`, `test_disabled_app_403_no_row`, `test_disabled_between_auth_and_confirm_403`, `test_revoked_between_auth_and_confirm_401` | auth, deferred lock |
| `… › test_releases_key_for_admin_app_writes_admin_release` | OQ48 |
| `… › test_invalid_body_422` (parametrised per OQ45 rule), `test_bad_version_422` (incl. `%0A`), `test_bad_environment_422`, `test_staging_sha_mismatch_422`, `test_unknown_field_422_nested`, `test_author_fields_rejected_422`, `test_malformed_json_422`, `test_422_details_have_no_values` | AC4 |
| `… › test_missing_length_411`, `test_body_cap_413` (Content-Length over the cap; also a chunked body over the cap), `test_wrong_content_type_415`, `test_schema_invalid_body_without_credential_401` (auth before body) | OQ46 order |
| `… › test_rate_limit_429_before_auth` (`make_client(app_release_rate_limit=1)`, no key) | AC4 |
| `… › test_audit_changes_ids_and_status_only`, `test_conflict_logs_warning_without_values` (caplog: ids and `key_id` only) | AC5 |
| `… › test_no_cors` (allowed admin and app origins: no ACAO, also on preflight), `test_admin_jwt_401`, `test_deployer_key_401` | ADR-0021 §3 |
| `test_releases_write_concurrency.py › test_parallel_first_insert_one_creates_one_noop` (committed data; two `SessionLocal` sessions; `AppCredentialService.confirm` stubbed to bypass `lock_app`; barrier after `insert_if_absent`; `AuditLogger.record` stubbed and counted; asserts one `created=True`, one identical no-op, no exception, 1 audit call; cleanup) | AC3 race |
| `… › test_parallel_first_insert_different_bodies` (second gets replace 200 on pending) | AC3 race |
| `test_route_auth_classes.py`: exact-map test (route set **and** each instance's `.scope`; release route `lock is False`, manifest `lock is True`), `_fill`, parametrised 401 / admin-JWT / `mmdk_` / no-CORS over `APP_CREDENTIAL` | registry |
| `test_audit_coverage.py › test_app_credential_routes_are_audited` (per route: invalid body → no row; success → exactly the route's action) | R4 |

## Approach
1. **Migration and model** (see Data model). `make migration m="adm034 app credential scope"`, then hand-edit the CHECK text and index names; `uv run alembic check` must be clean.
2. **Repository.** `count_active(app_id, scope)`.
3. **Issuance** (`service.issue(key, *, scope, actor, ctx)`), in this order:
   1. `lock_app`
   2. `require_super` (denied audit unchanged)
   3. `if app.key == ADMIN_APP_KEY and scope != AppCredentialScope.RELEASES` → 409 `admin_app`. This is an allow-list, so the future runtime scopes stay refused for `admin` (ADR-0022 §1).
   4. disabled → 409
   5. `count_active(app.id, scope) >= MAX_ACTIVE` → 409 `credential_limit` (message "per app and scope")
   6. insert with `scope`; the audit `changes` gain `"scope": diff.change(None, scope)`.

   The router passes `body.scope if body else MANIFEST`.
4. **`authenticate(raw, path_key, *, scope, denied_action, ctx, lock=True)`.** Fixed order (decision: the identity consult's order, see Specialist input):
   1. every 401, unchanged and identical (missing, malformed, unknown, secret, revoked). An `mmdk_` key or a JWT fails `parse_credential` and gets this 401.
   2. app not active → 403 `app_disabled`, not audited (as today).
   3. `app.key != path_key` → 403 `app_credential_mismatch`, `record_failure(denied_action, …, changes={"reason": "app_credential_mismatch"})`. This replaces the hard-coded `APP_MANIFEST_SYNCED` at line 169.
   4. `row.scope != scope` → 403 `app_credential_scope`, `record_failure(denied_action, …, changes={"reason": "app_credential_scope"})`.
   5. if `lock`: `confirm(caller)`.

   `confirm(caller)`:
   - `app = await rbac.lock_app(caller.app_key)`
   - if `app is None or app.status != active` → `AppCredentialAppDisabledError`. This re-check is new in auth: it closes the disable race for releases. The manifest service keeps its own re-check (R7).
   - `touch_last_used` → `_reject("revoked")` when the key was revoked.

   `AuthenticatedApp` gains `scope`. The lock order is app → credential row → release row, everywhere.
5. **Dependency.** `class RequireAppCredential` with `__init__(scope, denied_action, *, lock=True)` and `async __call__(self, key: str, session: SessionDep, ctx: AuditContextDep, credentials=Security(app_credential_scheme))`. It keeps `Security(app_credential_scheme)`, so the OpenAPI `appCredential` assertion holds.
   - `AppCredentialDep = Annotated[AuthenticatedApp, Depends(RequireAppCredential(MANIFEST, AuditAction.APP_MANIFEST_SYNCED))]`. Same name, so `manifest_router.py` code is unchanged.
   - `ReleasesCredentialDep = …RequireAppCredential(RELEASES, AuditAction.RELEASE_CREATED, lock=False)`.
   - Remove `current_app_credential` and update its only importer (`tests/test_route_auth_classes.py:26`).
6. **Rate limit and config.** Add `"app_release"` (3 edits mirroring `app_manifest`) and the four settings plus their `.env.example` keys.
7. **Input models and the pure rules** (`write_schemas.py`, `rules.py`).
   - `check_release_path(environment, version)` uses `re.fullmatch` with the constants of `releases/models.py:45-46` and raises `RequestValidationError([{"type": "release_version", "loc": ("path", "version"), "msg": "does not match the environment's version grammar"}])`.
   - `check_staging_sha(environment, version, body)` raises loc `("body", "git_sha")`.
   - `rules.decide_app_write(stored: StoredDoc | None, incoming: Doc) -> Decision(kind: create|replace|noop|enrich|conflict, fields: tuple[str, ...])`, where `Doc` = `git_sha`, `deployment_pr`, `has_migrations`, `images`, `changes`, and the `images`/`changes` values are the JSON form (`model_dump(mode="json")`, all keys present, `None` kept).
   - **Empty** = `None` for scalars, `[]` for lists. `False` is non-null. Lists compare by order.
   - pending: identical → `noop`, else `replace`.
   - deployed/failed: any stored non-empty field ≠ incoming → `conflict(fields)`. This includes an incoming empty value against a stored value (spec: "any non-null field in the record differs from the PUT"). Otherwise, if any stored empty field has a non-empty incoming value → `enrich(fields)`, else `noop`.
8. **Raw-body helper** (`releases/body.py`, reusable by ADM-036 with `cap=4096`). `read_json_body(request, model, *, cap, timeout_s)`:
   1. `Content-Length` missing or not ASCII digits → 411. Over `cap` → 413 (before reading).
   2. Content type is not `application/json` (parameters allowed) → 415.
   3. Stream `request.stream()` under `asyncio.timeout(timeout_s)`. More than the declared length or `cap` → 413. Short, stalled, or `ClientDisconnect` → `UnprocessableError("Request body is incomplete", details=[{"field": "body", "reason": "incomplete"}])`.
   4. `model.model_validate_json(raw)`. On `ValidationError` → `raise RequestValidationError([{**e, "loc": ("body", *e["loc"])} for e in exc.errors(include_url=False, include_input=False, include_context=False)])`. The handler at `errors.py:207` then strips values.

   It stays in `app/modules/releases/` (not `app/core/`): both users are release routes, and it avoids a wider High-path change. A follow-up can move it if a third user appears. Declare no Pydantic body parameter.
9. **Repository pair** (ADM-036 reuses it):
   - `insert_if_absent(*, app_id, environment, version, created_via, **fields) -> AppRelease | None`: `session.scalars(pg_insert(AppRelease).values(...).on_conflict_do_nothing(constraint="uq_app_releases_app_id_environment_version").returning(AppRelease))`. Pass explicit `status="pending"`, `images`, `changes`.
   - `get_for_update(app_id, environment, version) -> AppRelease | None`: `select … .with_for_update().execution_options(populate_existing=True)`.

   No `DO UPDATE`: the branch depends on status.
10. **Service `ReleaseAppWriteService.put(caller, environment, version, body, *, ctx)`.**
    1. `await AppCredentialService(session).confirm(caller)` (app lock first).
    2. Up to 2 attempts: `row = insert_if_absent(...)`. If a row came back, audit `release.created` and return `(view, True)`. Else `row = get_for_update(...)`. If that is `None` (deleted concurrently), retry; after 2 misses raise `ConflictError("Release changed concurrently; retry", details=[{"field": "version", "reason": "retry"}])`. This is unreachable in practice.
    3. `decide_app_write`:
       - `noop` → return the stored view, no write, no audit.
       - `replace`/`enrich` → `setattr` only the decided fields on the ORM row, then flush. `TimestampMixin.onupdate` bumps `updated_at` only when there are changes. Audit `release.updated`.
       - `conflict` → `logger.warning("release_not_pending_conflict app=%s env=%s version=%s release_id=%s key_id=%s fields=%s", …)`, then raise `ReleaseNotPendingError(details=[{"field": f, "reason": "differs"} …])`. Nothing is written.
    4. Audit `changes` = `diff.diff(before, after)` over `{"environment", "version", "status", "changes_count", "images_count"}` plus `"fields": diff.change(None, sorted(field names))` on update. Target `(AuditTarget.RELEASE, id)`, `app_key=caller.app_key`, actor `caller.actor`. Never titles, URLs, digests or the sha.
    5. Return `repo.get_view(id)`.
11. **Router `app_router.py`.**
    - `APIRouter(prefix="/apps", tags=["app-releases"])`, `@router.put("/{key}/releases/{environment}/{version}", response_model=ReleaseDetail, dependencies=[rate_limit("app_release")], responses=…, openapi_extra=…)`.
    - Parameters: `request`, `response`, `session`, `ctx`, `caller: ReleasesCredentialDep`, `key: str`, `environment: ReleaseEnvironment`, `version: Annotated[str, Path(max_length=32)]`.
    - The order FastAPI runs is: rate limit → auth dependency (headers only) → path validation. Then the handler: `check_release_path` → `read_json_body(request, ReleasePut, cap=settings.app_release_max_bytes, timeout_s=…)` → `check_staging_sha` → service → `response.status_code = 201 if created else 200` → `release_detail(view)`.
    - The app lock is taken only in the service, after the body is read and validated, so a slow client never holds `lock_app`. Mount it in `app/api/router.py`.
12. **Docs** (same PR):
    - **CLAUDE.md R3 class 4.** Header `(D25, D31; …)` → `(D25, D30, D31; …)`. In (a), replace `` `manifest` — `PUT /api/v1/apps/{key}/manifest` (live; until ADM-034 adds the scope column every key is `manifest`); `releases` — the app's release write (ADM-034); `` with `` `manifest` — `PUT /api/v1/apps/{key}/manifest`; `releases` — `PUT /api/v1/apps/{key}/releases/{environment}/{version}` (the only scope that may be issued for the `admin` app; `admin` = org-admin-api); ``. Leave 4(b) to ADM-036.
    - **CLAUDE.md R9.** Append `` `PUT /api/v1/apps/{key}/releases/{environment}/{version}` (natural key, not a UUID; D30) ``. ADM-036 appends `/deployment`.
    - **D30 row:** `| D30 | **Release log (owner, 2026-11-19; ADM-033–036):** every production release and staging build recorded in `app_releases`, read and managed by `admin:*` only; app credentials carry one `scope` (`manifest` \| `releases`), at most 2 active per (app, scope), `releases` keys allowed for `admin` (= org-admin-api; admin-pan is its own app row); app write `PUT /api/v1/apps/{key}/releases/{environment}/{version}` (pending replace, late enrichment, 409 `release_not_pending`, never changes status); deployer credential `mmdk_` for the deploy report; natural-key R9 exception. See ADR-0021. |`
    - **ADR-0016 / ADR-0017:** the pointer lines.
    - **ADR-0021 line 6:** replace with `> Originally numbered ADR-0018 with decision-log row D28; renumbered to ADR-0021 / D30 on 2026-11-20 (collision with `0018-attachment-storage-google-drive.md` and with D28 bug-report submit). Old PR and commit references to "ADR-0018 release log" mean this record.` The implementer first confirms the original number with `git log -p --follow -- docs/adr/0021-release-log-and-deployer-credential.md | grep -m3 "ADR-0018\|D28"`. If it is not confirmed, write the neutral form "Renumbered to ADR-0021 / D30 on 2026-11-20 to resolve numbering collisions (attachment-storage ADR, bug-report decision row)".
    - **ADR-0021 §11:** append "Decided at ADM-034 PLAN: `admin` = org-admin-api; admin-pan is registered as its own app row (ops step), no code special case."
13. **Incidental (Q5):** `field(repr=False)` on `IssuedDeployerCredential.credential`, recorded in impl-log as incidental.
14. `make check` and `uv run alembic check`.

Rules:
- **R1:** router = HTTP (body reading, status code); service = rules and audit; repository = SQL; `rules.py` is pure.
- **R2:** step 1.
- **R3:** class 4(a), registry map.
- **R4:** audit in the same transaction; denied rows via `record_failure`.
- **R5:** config and `.env.example`.
- **R6:** existing error classes; new codes are only `app_credential_scope` and `release_not_pending`.
- **R7:** the manifest-service re-check and the per-key bucket are left alone.
- **R8:** tests above.
- **R9:** documented exception.
- **R11:** no key or value in logs or errors; `repr=False`.

## Specialist input used
- **Identity (adopted):**
  - check order 401s → disabled → mismatch → scope → lock/touch, which keeps `test_wrong_app_unknown_path_app_same_403`;
  - the callable-class dependency;
  - the `APP_CREDENTIAL` route → scope map;
  - the status re-check under the lock inside auth;
  - the `admin` refusal as an allow-list on `releases`;
  - `scope` in the audit changes and in `AppCredentialRead`;
  - `repr=False`;
  - docs edits that build on ADM-041's scope-generic R3 rather than pasting §12;
  - denied action `release.created` for the release route;
  - the race test that bypasses auth.
- **Security:**
  - **Adopted:** no Pydantic body param, with the order rate limit → auth → 411/413 → 415 → parse → `RequestValidationError`; strict, `forbid` input models separate from the read schemas; fullmatch, no `$`; exact URL regexes, not `HttpUrl`; lowercase digests; the `DISALLOWED_CATEGORIES` strip; `touch_last_used` only after every check; ON CONFLICT + FOR UPDATE; 409 writes nothing; never-writable fields; audit ids only; the 409 warning log (their Q6, a log line only); OpenAPI "untrusted" text.
  - **Rejected:** scope-before-mismatch. A cross-app attempt is the stronger signal, the existing test is kept, and ADR-0021 §1 leaves the order to PLAN.
  - **Rejected by default:** 404 before mismatch (Q1). It would change manifest behaviour (AC1) and reveal app existence to any key holder.
  - **Asked:** the registry allow-list (Q3) and the per-key bucket (Q2).
- **API platform (adopted):**
  - the `ReleasePut` shape and the 201/200 via `response.status_code`;
  - the full `responses` block;
  - optional issuance body via `Body(default=None)`;
  - the `AppCredentialScopeError` location;
  - config names (adapted: `app_release_max_bytes`);
  - a malformed version gives 422 at `("path","version")`;
  - 256 KiB and 30/min as the final numbers (their Q3);
  - `changes` ≤ 200 kept (their Q4).
  - Their `app_credential_for(scope)` factory is replaced by the callable class: same effect, and the registry test can inspect `.scope`.
- **Database (adopted):** the migration shape, CHECK name and index; downgrade deletes non-manifest keys first; ON CONFLICT DO NOTHING + `FOR UPDATE` (no advisory lock, no `DO UPDATE`); CHECK-only rules pre-validated; ORM `setattr` so `onupdate` fires only on a real change; a round-trip test that asserts names; `scope` in the issued audit (their Q4).
- **Added by PLAN, in no consult:** the deferred `lock_app`.
  - The DB consult warns that the lock is held across the PUT, and the security consult requires auth before the body read. Both hold only if auth (dependency) runs without the lock and `confirm()` (lock → re-check → touch) runs at the start of the service, after the body is read.
  - ADR-0021 §1 still holds: lock before `last_used_at`, for both scopes.
  - The manifest route keeps `lock=True`, so it behaves exactly as before.

## Assumptions
- **Numbers (OQ46 "final values PLAN's"):** 256 KiB cap, 30/min/IP, read deadline 10 s (new, mirrors ADM-030).
- **`has_migrations`** is optional on the PUT (spec does not require it). `changes` defaults to `[]`.
- **Unique-name rules:** image names are unique case-sensitively. The pr_url repo segment is case-sensitive as sent.
- **Audit action of a denied write on the release route:** `release.created` (the PUT is an upsert; identity consult default).
- **Hotspots** (open tickets):
  - ADM-036 (PLANNING): `app/modules/releases/*`, `tests/test_route_auth_classes.py`, `app/core/rate_limit.py`, `app/core/config.py`, `.env.example`, `CLAUDE.md` (R3 4(b), R9), `docs/identity-architecture.md`, `tests/test_audit_coverage.py`. ADM-036 should build on `body.py` and the repository pair.
  - ADM-040 (PLANNING): migration head `d7a3f5b2e801` (whichever merges second rebases `down_revision`), `docs/identity-architecture.md`, possibly `app/core/config.py`.
  - Expect rebases.
- **No `docs/deploy.md` / guide changes:** ADM-037 owns the guide for app repos.

## Blocking questions
1. **Unknown app on the release route with a valid key: 404 or 403?** Spec §5.13 "Tests" and the ticket AC say "404 unknown app". With the auth order above, a valid key always belongs to an existing app, so a path naming an unknown app is a mismatch. **Default: 403 `app_credential_mismatch`** (audited denied), the same as the manifest route today (`test_wrong_app_unknown_path_app_same_403`). The "404 unknown app" test is met on issuance (`POST …/credentials` → 404) and belongs to the ADM-036 deployer route (ADR-0021 §2). The alternative (404 before the mismatch, not audited) also changes manifest behaviour and reveals app existence to any key holder; it would add one `get_app_by_key` step in `authenticate` and change one existing test.
2. **Per-key rate-limit bucket after auth** (ADR-0022 pattern, e.g. 120/hour per `key_id`) in addition to the per-IP limit? **Default: no.** OQ46 fixes per-IP only; it goes to follow-ups.
3. **Registry allow-list for `images[].name`** (e.g. only `ghcr.io/mmlabs-online/…`)? **Default: no.** Strict Docker repository grammar only. Today's images are on Docker Hub (`mingming21400/org-admin-api`, `release.yml:29`), so a GHCR list would break this API's own releases. The name is display-only and never pulled by this API.
4. **`admin` collision (org-admin-api vs admin-pan).** **Default:** `admin` = org-admin-api. admin-pan gets its own app row `admin-pan`, registered by staff as an ops step (Ming todo), with its own `releases` key and no code special case. The cost: `admin-pan` appears in the app list and the role catalog (no roles). The alternative, a shared `admin` row, gives two repos one blast radius and colliding versions.
5. **Also apply `field(repr=False)` to `IssuedDeployerCredential.credential`** (`deployer_credentials/service.py:65`)? It is one line, outside the ticket's module, and the same leak class. **Default: yes**, flagged as incidental in impl-log.
6. **Title stripping set.** Spec OQ45 says strip Unicode `Cc`. The plan strips the wider `DISALLOWED_CATEGORIES` (`Cc Cf Zl Zp Co Cs Cn`; bidi overrides, zero-width), as the security consult recommends. It still strips, never rejects, except that a title with nothing left gives 422. **Default: the wider set.** It is stricter than the spec but accepts every title the spec accepts, apart from titles made only of format characters.

## Split proposal
Estimate: about 650 production lines and 900 test lines. This is above the ~400-line guide. The steps are ordered so the work can be cut at a clean boundary:
- **Slice A** = steps 1–6, 12 (R3 header and D30 only), 13: the scope column, issuance, `authenticate`/`confirm`, the dependency and the registry. Independently shippable: `releases` keys can be issued, and no route accepts them yet.
- **Slice B** = steps 7–11 and the rest of 12: the release PUT.

**Default: one PR with two commits (A, then B)**, because R3/R9 and the route should land together and both slices are High-tier anyway. The orchestrator may open two PRs at the commit boundary if review of A needs rework.
