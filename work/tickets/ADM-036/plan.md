Status: BLOCKED

> BLOCKED only on owner choices Q1–Q11 below. Each has a default, and the plan is written for those defaults. When
> Ming approves the plan (High-path gate: `app/core/**`, `.env.example`) with the defaults, or with answers that do
> not change the file list, it becomes READY with no re-plan. A different answer to Q1 (PATCH) or Q9 (take
> `lock_app`) changes only the steps named in that question.

# Plan — ADM-036: Release log — deploy result report (deployer credential)

## Scope summary
Sources: spec §5.13 (lines 226, 236–238, 243–247), §8 OQ38–OQ40, OQ45–OQ47, OQ49 (lines 396–407); ADR-0021 §2–§8, §10–§12. ADM-034's plan (`work/tickets/ADM-034/plan.md`) is the base this plan extends.

- **Spec §5.13 line 226:** *"Deploy report (writer b): `deployed | failed`, `deployed_at`, `deploy_run_url`. Transitions `pending → deployed | failed`, `failed → deployed` (redeploy); `deployed → deployed` with the **same** `deploy_run_url` is a **no-op 200**, with a **different** one it is a real redeploy or rollback … If the release record does not exist yet … it is **created from the report** … **PUT vs PATCH is PLAN's choice**."*
- **OQ38 (line 236):** *"The deployer sets only `status`, `deployed_at`, `deploy_run_url` (plus app, environment and version from the path, validated by the same grammar). Everything else stays empty … `deployed_at` is required with `deployed`, refused with `failed`, timezone-aware, at most 5 min in the future and at most 30 days in the past. Reports are accepted for a **disabled** app …; unknown app → 404. Reports for the `admin` app follow OQ48."*
- **OQ39 (line 237):** *"`failed → failed` = 200, updates `deploy_run_url` … `deployed → failed` = **409 `release_already_deployed`**, record unchanged."*
- **OQ40 (line 238):** *"`deployed → deployed` with a **different** `deploy_run_url` … 200, updates `deployed_at` and `deploy_run_url`, audited `release.deployment_reported`. With the **same** `deploy_run_url` it is a no-op 200 (a retried report; no audit row). So "live now" … = the record with the latest `deployed_at` among `status = deployed`."*
- **OQ45 (line 243):** *"`deploy_run_url`: required on a report, exactly `https://github.com/mmlabs-online/deployment/actions/runs/<N>` with an optional `/attempts/<N>`. Unknown fields → 422."*
- **OQ46 (line 244):** *"per IP, before authentication …: deploy report 30/min. Request body cap … 4 KiB (report), **checked inside the routes** … final values PLAN's."*
- **OQ47 (line 245):** *"One audit row per write that changes something, **ids and status only** … No-ops … write no row … Actors … `deployer:{key_id}`."*
- **ADR-0021 §2:** *"Accepted on exactly one route: `…/apps/{key}/releases/{environment}/{version}/deployment` (method `PUT` upsert or `PATCH`, chosen at PLAN), for any registered app, including disabled ones … Unknown app → 404. Reports for the `admin` app are accepted."*
- **ADR-0021 §3:** *"an `mmak_` key on the report route, or an admin JWT … gives the single 401 … `realm="deployer"` … No CORS … Listed by exact (method, path) in `tests/test_route_auth_classes.py` (… new `DEPLOYER_CREDENTIAL` set)."*
- **ADR-0021 §4:** *"Report (`…/deployment`). Body `{status: deployed|failed, deployed_at, deploy_run_url}`. 201 when it creates the record (§6), 200 otherwise. Races … never a 500."*
- **ADR-0021 §6:** *"`git_sha`, `deployment_pr` and `has_migrations` are null; `images` and `changes` are empty, and a late app PUT may fill them (§4). The audit row records that the record was created from a report."*
- **ADR-0021 §10/§12:** *"`…/deployment` is a singular sub-document"*; R3 4(b) *"only `…/apps/{key}/releases/{environment}/{version}/deployment` (method fixed at PLAN), for any registered app."*
- Ticket out of scope: the app write (ADM-034), staff reads (ADM-033).

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | Every transition in the table is tested, including retries (same response, no duplicate audit) | `tests/test_release_deployment_rules.py` (pure: every cell of ADR-0021 §5 plus the stale guard). `tests/test_releases_deployment_api.py`: `test_create_deployed_201`, `test_create_failed_201`, `test_pending_to_deployed_200`, `test_pending_to_failed_200`, `test_failed_to_deployed_200`, `test_failed_to_failed_new_url_200_audited`, `test_failed_to_failed_same_url_noop`, `test_deployed_same_url_noop`, `test_deployed_new_url_redeploy_200_audited`, `test_deployed_new_url_older_deployed_at_noop` (Q6), `test_deployed_to_failed_409_unchanged_no_audit`, `test_retry_of_create_200_same_body_one_audit_row`. Each asserts status code, stored row and exact audit row count. |
| 2 | Report-first creates a thin record that ADM-034's PUT can later enrich | `test_releases_deployment_api.py › test_report_first_then_app_put_enriches` (201 thin, then ADM-034 PUT 200 fills `git_sha`, `images`, `changes`, `deployment_pr`, `has_migrations`; status, `deployed_at`, `deploy_run_url`, `created_via='deployer'` unchanged); `test_report_on_pending_then_identical_app_put_noop`. Race: `tests/test_releases_deployment_concurrency.py › test_report_then_app_put_concurrent`, `test_app_put_then_report_concurrent`, `test_parallel_identical_reports_one_create`. |
| 3 | Wrong key kind / admin JWT → 401; 422 on invalid fields; 429 on the limit | `test_releases_deployment_api.py › test_401_uniform` (parametrised: missing, malformed, unknown key_id, wrong secret, revoked, `mmak_` scope `manifest`, `mmak_` scope `releases`, admin JWT, cookie only; identical error JSON, `WWW-Authenticate: Bearer realm="deployer"`, no details), `test_invalid_body_422` (parametrised), `test_bad_path_422`, `test_deployed_at_window_422`, `test_rate_limit_429_before_auth`, `test_body_cap_413`, `test_missing_length_411`, `test_wrong_content_type_415`; `test_route_auth_classes.py` registry and OpenAPI tests. |
| 4 | After a rollback report, the live-version read (ADM-033) shows the rolled-back-to version | `test_releases_deployment_api.py › test_rollback_report_moves_live` (v1 deployed, v2 deployed later, then v1 reported with a new URL and a later `deployed_at` → `GET /api/v1/releases/live?app=…&environment=production` returns v1); `test_stale_report_does_not_move_live` (Q6). |

## Ming verification after merge
- Ops step (orchestrator adds to `work/ming-todo.md` on main): issue the first deployer key (`POST /api/v1/deployer-credentials`, `docs/deploy.md` "Deployer credential") and store it as `MMLABS_DEPLOYER_CREDENTIAL` in `mmlabs-online/deployment`.
- After the deployment-repo step exists (cross-repo follow-up, spec line 254): one real staging deploy sends `PUT …/deployment`; the record appears in `GET /api/v1/releases` with `status=deployed`, and `last_used_at` of the key moves.

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|
| `app/core/rate_limit.py` | `"release_deployment"` in `Scope` (after `"app_release"` from ADM-034) and `_LIMITS` | per-IP bucket, own route bucket (OQ46) | **High** |
| `app/core/config.py` | `release_deployment_rate_limit=30` (`ge=1`), `release_deployment_rate_window_seconds=60` (`ge=1`), `release_deployment_max_bytes=4096` (`ge=512`), `release_deployment_read_timeout_seconds=10` (`ge=1`), next to ADM-034's `app_release_*` (after line 105 + ADM-034's block) | R5, OQ46 | **High** |
| `.env.example` | the 4 keys without values, after ADM-034's `APP_RELEASE_*` keys (after line 68 + ADM-034's block) | R5 | **High** |
| `app/modules/audit/actions.py` | `RELEASE_DEPLOYMENT_REPORTED = "release.deployment_reported"` next to line 91 | ADR-0021 §8 | no |
| `app/modules/releases/write_schemas.py` (ADM-034) | add `ReleaseDeploymentReport` + `DEPLOY_RUN_URL_FULL` constant | OQ38/OQ45 | no |
| `app/modules/releases/rules.py` (ADM-034) | add pure `decide_report(stored, incoming) -> ReportDecision` and `deployed_at_window_error(deployed_at, now) -> str \| None` | rule 6 (pure logic first) | no |
| `app/modules/releases/errors.py` (ADM-034) | add `ReleaseAlreadyDeployedError(ConflictError)`, code `release_already_deployed` | OQ39 | no |
| `app/modules/releases/repository.py` | add `app_id_by_key(key) -> uuid.UUID \| None` (any status) and `db_now() -> datetime` (`SELECT now()`); reuse ADM-034's `insert_if_absent` / `get_for_update` unchanged | 404, DB clock | no |
| `app/modules/releases/deployment.py` | new: `ReleaseDeploymentService.report(caller, key, environment, version, body, *, ctx) -> tuple[ReleaseView, bool]` | R1 service | no |
| `app/modules/releases/deployment_router.py` | new: `PUT /apps/{key}/releases/{environment}/{version}/deployment` | the route | no |
| `app/api/router.py` | include `releases_deployment_router` (next to ADM-034's `releases_app_router`) | mount | no |
| `app/modules/deployer_credentials/deps.py` | docstring lines 5–6: "Used only by `PUT /api/v1/apps/{key}/releases/{environment}/{version}/deployment` (ADM-036); its `rate_limit("release_deployment")` dependency runs first." | stale text | no |
| `CLAUDE.md` | R3 4(b): exact method + path; R9: append the `/deployment` sub-document (ADM-034 adds the parent path) | ADR-0021 §10/§12 | no (docs) |
| `docs/identity-architecture.md` | new route row after line 171 (+ ADM-034's release-write row); line 171: drop "no route accepts the key until ADM-036"; D30 row (ADM-034): append "deploy report `PUT …/deployment` (ADM-036)" | API table, decision log | no |
| `docs/adr/0021-release-log-and-deployer-credential.md` | §2 / §5: one "Decided at ADM-036 PLAN" line each (method `PUT`; stale-report guard per Q6; report-first audit action per Q7) | records PLAN decisions the ADR left open | no |
| `docs/deploy.md` | lines 592–593: replace "No route accepts the key until ADM-036 ships, so issue the first one only then." with the route, method, body and "warning-only on 4xx/429 in CI" | ops doc accuracy | no |
| `tests/test_release_deployment_schemas.py` | new, pure | rule 6 | no |
| `tests/test_release_deployment_rules.py` | new, pure | rule 6 | no |
| `tests/test_releases_deployment_api.py` | new, API | AC1–AC4 | no |
| `tests/test_releases_deployment_concurrency.py` | new, committed two-session races | AC2, ADR-0021 §4 races | no |
| `tests/test_route_auth_classes.py` | `DEPLOYER_CREDENTIAL` = the exact route; line 278 → exact equality; OpenAPI `deployerCredential` scheme + branch; CORS loop uses `_fill`; no-CORS + admin-JWT tests for the route | R3 registry | no |
| `tests/test_audit_coverage.py` | `DEPLOYER_CREDENTIAL_AUDITED` map + classified test + audited test (incl. no-op and 409 → zero rows) | R4 | no |

Tier: **High** (`app/core/**`, `.env.example`). Never auto-merged; `appsec-engineer` review is triggered (areas auth, security). No migration.

## Data model / migration
None. ADM-033's `app_releases` has every column and CHECK needed (`app/modules/releases/models.py:57-134`): unique `uq_app_releases_app_id_environment_version` (line 96), `deployed_iff_deployed_at` (line 76), `deploy_run_url_format` (line 92), `version_matches_environment` (line 65), `created_via_valid` incl. `'deployer'` (line 64). The write validation below is at least as strict as every CHECK, so a report can never raise `IntegrityError` (→ 500):
- `deployed_iff_deployed_at`: the schema enforces "`deployed` ⇔ `deployed_at` present" before any write, and the service always writes `status` and `deployed_at` together.
- `deploy_run_url_format` / `version_matches_environment`: `re.fullmatch` (never `$` with `match`), so a trailing `\n` is a 422, not a DB error.

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
| PUT | `/api/v1/apps/{key}/releases/{environment}/{version}/deployment` | `ReleaseDeploymentReport` JSON, raw body ≤ 4096 bytes, `application/json`. `environment` ∈ `production`\|`staging`; `version` per OQ36 (ADM-034 `check_release_path`, `Path(max_length=32)`) | `ReleaseSummary` (existing read schema; no `images`/`changes`) | 201 record created from the report; 200 every other success (update, no-op, stale no-op); 401 `unauthorized` (`WWW-Authenticate: Bearer realm="deployer"`, no details); 404 `not_found` (unknown app, after auth); 409 `release_already_deployed` (`deployed → failed`), 409 `conflict` (`details=[{"field":"version","reason":"retry"}]`, staff-delete race twice, Q10); 411; 413; 415; 422 `validation_error` (body/path) or `unprocessable` (`deployed_at` window: `details=[{"field":"deployed_at","reason":"too_far_in_future"\|"too_old"}]`); 429 (`Retry-After`) | none: R3 class 4(b), `mmdk_` deployer key, any registered app (disabled and `admin` included); no CORS. No 403 path. |

`ReleaseDeploymentReport` (`ConfigDict(extra="forbid", strict=True)`):
- `status: Literal["deployed", "failed"]` (`pending` or anything else → 422).
- `deploy_run_url: str`, required for both statuses (spec OQ45; Q2), `max_length=255`, after-validator `re.fullmatch(r"https://github\.com/mmlabs-online/deployment/actions/runs/[1-9][0-9]{0,19}(/attempts/[1-9][0-9]{0,2})?", v)`; fixed message, never echoes the value.
- `deployed_at: AwareDatetime | None = Field(default=None, validate_default=True)` with a field validator (runs after `status`, reads `info.data.get("status")`): `deployed` and `None` → error `deployed_at_required`; `failed` and not `None` → error `deployed_at_not_allowed` (Q5); else normalise to UTC (`astimezone(UTC)`). Error loc is `("body","deployed_at")`. Naive datetimes and numbers are refused by `AwareDatetime` in strict mode.
- The window (≤ now + 5 min, ≥ now − 30 days) is checked in the service against the **database clock** (codebase convention: `auth/repository.py:32`, `bug_reports/repository.py:18`), via the pure `deployed_at_window_error`.

OpenAPI: `openapi_extra={"requestBody": {"required": True, "content": {"application/json": {"schema": inline_schema(ReleaseDeploymentReport)}}}}` (ADM-034 `body.py`); description: "Deploy result from the deployment repo; server-to-server only, no CORS; `deploy_run_url` is untrusted and only format-checked". `responses` lists 201/401/404/409/411/413/415/422/429 (all error entries reference the envelope like ADM-034's route). Security comes from `Security(deployer_credential_scheme)` in `current_deployer`: `[{"deployerCredential": []}]`.

## Tests to write
| Test (file › name) | Covers |
|---|---|
| `test_release_deployment_schemas.py › test_valid_deployed_and_failed` | happy parse; `deployed_at` normalised to UTC (`+07:00` input) |
| `… › test_status_values` (`pending`, `DEPLOYED`, `""`, missing → error) | OQ38 |
| `… › test_deployed_at_required_with_deployed`, `test_deployed_at_refused_with_failed` (loc `deployed_at`) | OQ38, Q5, CHECK pre-validation |
| `… › test_deployed_at_strict` (naive ISO, epoch int, `"yesterday"`, `null` with deployed → error) | strict datetime |
| `… › test_run_url_allow_list` (ok: `…/runs/123`, `…/runs/123/attempts/2`; refused: other org, other repo, `http:`, `runs/0`, 21-digit id, `attempts/0`, `attempts/1000`, trailing `/`, query, fragment, trailing `\n`, leading space, 256 chars) | OQ45, `$` hazard |
| `… › test_run_url_required_for_both_statuses`, `test_extra_field_forbidden` (`created_via`, `git_sha`, `images`) | OQ45 mass assignment |
| `… › test_errors_never_echo_input` (error `msg`/`ctx` contain no submitted value) | ADR-0021 §7 |
| `test_release_deployment_rules.py › test_none_stored_is_create` (both statuses) | §6 |
| `… › test_pending_to_deployed_fields`, `test_pending_to_failed_fields` | §5 row pending |
| `… › test_failed_to_deployed`, `test_failed_to_failed_new_url_update_url_only`, `test_failed_to_failed_same_url_noop` | §5 row failed, OQ39 |
| `… › test_deployed_same_url_noop_even_if_deployed_at_differs` | OQ40 |
| `… › test_deployed_new_url_later_or_equal_deployed_at_update`, `test_deployed_new_url_older_deployed_at_stale_noop` | OQ40 + Q6 |
| `… › test_deployed_to_failed_conflict` | OQ39 |
| `… › test_window_boundaries` (now+5 min ok, now+5 min+1 µs → `too_far_in_future`; now−30 d ok; now−30 d−1 µs → `too_old`; fixed `now`) | OQ38 window |
| `test_releases_deployment_api.py › test_create_deployed_201`, `test_create_failed_201` (thin: `git_sha`/`deployment_pr`/`has_migrations` null, `images`/`changes` empty, `created_via='deployer'`; one `release.created` row, actor `deployer:<key_id>`, `changes.created_via == "deployer"`; key `last_used_at` set) | §6, AC1, Q7 |
| `… › test_pending_to_deployed_200`, `test_pending_to_failed_200` (record from `make_release`, ADM-034 body fields untouched; one `release.deployment_reported` row) | AC1 |
| `… › test_failed_to_deployed_200`, `test_failed_to_failed_new_url_200_audited`, `test_failed_to_failed_same_url_noop` (no row, `updated_at` unchanged) | AC1 |
| `… › test_deployed_same_url_noop`, `test_deployed_new_url_redeploy_200_audited`, `test_deployed_new_url_older_deployed_at_noop` | AC1, Q6 |
| `… › test_deployed_to_failed_409_unchanged_no_audit` (code `release_already_deployed`; row unchanged; zero audit rows; caplog warning with ids + `key_id` only, no URL) | AC1, Q8 |
| `… › test_retry_of_create_200_same_body_one_audit_row` (second identical call: 200, JSON body equal to the 201 body, still one audit row) | AC1 retry |
| `… › test_report_first_then_app_put_enriches`, `test_report_on_pending_then_identical_app_put_noop` | AC2 |
| `… › test_rollback_report_moves_live`, `test_stale_report_does_not_move_live` | AC4 |
| `… › test_disabled_app_accepted`, `test_admin_app_accepted`, `test_unknown_app_404_after_auth` (valid key → 404; no key → 401) | OQ38, §2 |
| `… › test_401_uniform` (parametrised list in AC3; asserts identical `resp.json()`, header, nothing written) | AC3 |
| `… › test_401_before_body_read` (no key + 10 KiB body → 401, not 413; no key + malformed JSON → 401) | order |
| `… › test_rate_limit_429_before_auth` (`make_client(release_deployment_rate_limit=1)`: 1st no key → 401, 2nd → 429 with `Retry-After`; separate bucket: `app_release` not consumed) | AC3, OQ46 |
| `… › test_body_cap_413` (Content-Length 4097 → 413; 4096-byte valid padded JSON accepted), `test_missing_length_411`, `test_wrong_content_type_415`, `test_malformed_json_422` | OQ46, helper reuse |
| `… › test_invalid_body_422` (parametrised: each schema rule above, incl. URL with trailing `\n`), `test_422_details_have_no_values` | AC3 |
| `… › test_bad_path_422` (`prod` env, `v01.2.3`, `v1.2.3%0A`, `sha-ABCDEF0`, `sha-abcdef0%0A`, production grammar on staging and vice versa, 33-char version) | OQ36 path grammar |
| `… › test_deployed_at_window_422` (now + 10 min, now − 31 d; `details` field `deployed_at`) | OQ38 |
| `… › test_audit_changes_ids_and_status_only` (changes keys ⊆ `{environment, version, status, created_via, fields}`; no `https://` substring anywhere in the row) | OQ47 |
| `… › test_staff_delete_race_retries_then_creates`, `test_staff_delete_race_twice_409` (monkeypatch `get_for_update` → `None` once / twice) | Q10, never 500 |
| `… › test_no_cors` (admin origin and app origin, PUT and preflight: no `access-control-allow-origin`) | ADR-0021 §3 |
| `test_releases_deployment_concurrency.py › test_report_then_app_put_concurrent` (session A report inserts thin `deployed` and waits; session B ADM-034 `put` blocks on the unique index; A commits; B → enrich 200; final row `deployed`, `git_sha` filled, 2 audit calls: `release.created`, `release.updated`) | AC2 race, order 1 |
| `… › test_app_put_then_report_concurrent` (B inserts `pending` first and waits; A blocks; B commits; A → update; final row `deployed` with B's body intact; audit `release.created`, `release.deployment_reported`) | AC2 race, order 2 |
| `… › test_parallel_identical_reports_one_create` (one `created=True`, one no-op, 1 audit call, no exception) | ADR-0021 §4 races |
| `test_route_auth_classes.py › test_deployer_credential_allow_list_is_exact` (exact set; `current_deployer` only on it) | R3 registry |
| `… › test_openapi_bearer_scheme` (+ `deployerCredential == {"type":"http","scheme":"bearer","bearerFormat":"mmdk"}`; branch: `security == [{"deployerCredential": []}]`, `"401"` in responses, `"403"` not in responses, no `x-permission`) | OpenAPI |
| `… › test_deployer_route_no_cors`, `test_admin_jwt_on_deployer_route_401` (`realm="deployer"`), `test_app_credential_on_deployer_route_401` | R3 4(b) |
| `test_audit_coverage.py › test_deployer_credential_routes_are_classified`, `test_deployer_credential_routes_are_audited` (invalid body → 0 rows; create → `["release.created"]`; identical retry → 0; new URL on `failed` → `["release.deployment_reported"]`; 409 → 0) | R4 |

## Approach
1. **Base.** Rebase on main after ADM-034 merges. Reuse, do not copy: `releases/body.py` (`read_json_body`, `inline_schema`), `write_schemas.check_release_path`, `repository.insert_if_absent` / `get_for_update`, `schemas.release_summary`, `errors.py`, `AuditAction.RELEASE_CREATED`, ADM-034's `_fill` (`environment`/`version`) and test factories (`make_credential(scope=…)`).
2. **Config and rate limit** (High). `release_deployment_*` settings; `"release_deployment"` scope mirroring `app_manifest` (`rate_limit.py:85`, `:116-118`); `.env.example` keys.
3. **Audit action and error.** `RELEASE_DEPLOYMENT_REPORTED`; `ReleaseAlreadyDeployedError(ConflictError)` with `code = "release_already_deployed"`, message "Release is already deployed; a failed report cannot replace it", no details.
4. **Schema** `ReleaseDeploymentReport` (see API contract) in `write_schemas.py`.
5. **Pure rules** in `rules.py`:
   - `StoredDeployment(status, deployed_at, deploy_run_url)`, `IncomingReport(status, deployed_at, deploy_run_url)`.
   - `decide_report(stored: StoredDeployment | None, incoming) -> ReportDecision(kind: "create"|"update"|"noop"|"conflict", fields: tuple[str, ...])`:
     - `None` → `create`.
     - incoming `failed`: stored `deployed` → `conflict`; stored `pending` → `update(status, deploy_run_url)`; stored `failed` → same URL `noop`, else `update(deploy_run_url)`.
     - incoming `deployed`: stored `pending`/`failed` → `update(status, deployed_at, deploy_run_url)`; stored `deployed` → same URL `noop`; different URL and `incoming.deployed_at < stored.deployed_at` → `noop` (stale, Q6); else `update` with `deploy_run_url` plus `deployed_at` when it differs.
     - `fields` only lists names whose value actually changes.
   - `deployed_at_window_error(deployed_at, now) -> "too_far_in_future" | "too_old" | None` (`> now + 5 min`, `< now − 30 d`; constants `MAX_FUTURE = timedelta(minutes=5)`, `MAX_AGE = timedelta(days=30)`).
6. **Repository additions:** `app_id_by_key(key)` = `select(App.id).where(App.key == key)` (no status filter: disabled and `admin` allowed); `db_now()` = `select(func.now())`.
7. **Service `ReleaseDeploymentService.report(caller, key, environment, version, body, *, ctx)`** (`deployment.py`; HTTP-free, R1):
   1. `app_id = repo.app_id_by_key(key)` → `None` → `NotFoundError("App not found")`. No `lock_app` (Q9): the insert's FK takes `KEY SHARE` on `apps`, which does not conflict with `lock_app`'s `FOR NO KEY UPDATE`; locks taken are deployer-credential row (auth touch) → release row, the app write takes apps → app-credential row → release row; they meet only on the release row, so no cycle.
   2. If `deployed`: `reason = deployed_at_window_error(body.deployed_at, await repo.db_now())` → `UnprocessableError("deployed_at is outside the accepted window", details=[{"field": "deployed_at", "reason": reason}])`.
   3. Up to 2 attempts: `row = insert_if_absent(app_id=…, environment, version, created_via="deployer", status=body.status, deployed_at=body.deployed_at, deploy_run_url=body.deploy_run_url, images=[], changes=[])`. Row returned → audit `release.created` → return `(view, True)`. Else `row = get_for_update(...)`; `None` (staff delete in between) → next attempt; after 2 misses → `ConflictError("Release changed concurrently; retry", details=[{"field": "version", "reason": "retry"}])` (Q10). 201 vs 200 is decided only by the insert result.
   4. `decide_report(StoredDeployment(row…), IncomingReport(body…))`:
      - `noop` → return `repo.get_view(row.id)`, no write, no audit.
      - `conflict` → `logger.warning("release_already_deployed app=%s env=%s version=%s release_id=%s key_id=%s", …)` → raise `ReleaseAlreadyDeployedError` (no audit row, Q8; the transaction rolls back, so `last_used_at` does not move on a refused report).
      - `update` → `setattr` the decided fields on the locked ORM row (status and `deployed_at` in the same flush, so `deployed_iff_deployed_at` holds), `flush`; audit `release.deployment_reported`.
   5. Audit (`AuditLogger.record`, same transaction, R4): actor `caller.actor` (`SystemActor("deployer:<key_id>")`), target `(AuditTarget.RELEASE, id)`, `app_key=key`. `changes`:
      - create: `diff.diff(None, {"environment", "version", "status", "created_via": "deployer"})`.
      - update: `{"status": diff.change(before, after)}` (only when it changed), plus `"environment"`, `"version"` as unchanged ids the same way `ReleaseService.delete` does (`service.py:50-58`), plus `"fields": diff.change(None, sorted(field names))`.
      - Never the URL or the timestamp value (ADR-0021 §8).
   6. No I/O between `get_for_update` and the commit other than the flush, audit insert and the final `get_view`.
8. **Router `deployment_router.py`:**
   - `router = APIRouter(prefix="/apps", tags=["release-deployments"])`.
   - `@router.put("/{key}/releases/{environment}/{version}/deployment", response_model=ReleaseSummary, dependencies=[rate_limit("release_deployment")], responses=…, openapi_extra=…, summary="Report a deploy result (deployer credential)")`.
   - Parameters: `request: Request`, `response: Response`, `session: SessionDep`, `caller: DeployerDep`, `ctx: AuditContextDep`, `key: str`, `environment: ReleaseEnvironment`, `version: Annotated[str, Path(max_length=32)]`. No Pydantic body parameter (FastAPI would parse it before the dependencies, so a malformed body would beat 429/401).
   - Order: rate limit (decorator dep) → `current_deployer` (401) → path validation (422) → handler: `check_release_path(environment, version)` → `read_json_body(request, ReleaseDeploymentReport, cap=settings.release_deployment_max_bytes, timeout_s=settings.release_deployment_read_timeout_seconds)` (411/413/415/422) → service (404, 422 window, 409) → `response.status_code = 201 if created else 200` → `release_summary(view)`.
   - Settings from `request.app.state.settings` (as `rate_limit.py:137`).
   - Mount in `app/api/router.py`.
9. **Registry and coverage tests** (`test_route_auth_classes.py`):
   - Line 65–66 → comment "Deployer credential (R3 class 4 (b), ADR-0021): exactly one route (ADM-036)." and `DEPLOYER_CREDENTIAL = {("PUT", f"{V1}/apps/{{key}}/releases/{{environment}}/{{version}}/deployment")}`.
   - Line 278 → `assert {("PUT", …/deployment)} == DEPLOYER_CREDENTIAL`.
   - `test_openapi_bearer_scheme`: scheme assertion + `elif route in DEPLOYER_CREDENTIAL` branch.
   - Line 326: `_fill(path)` instead of `path.replace("{key}", "stoktok")` (only if ADM-034 has not already done it).
   - `test_audit_coverage.py`: import `DEPLOYER_CREDENTIAL`; add the map and two tests (see Tests).
10. **Docs:**
    - **CLAUDE.md R3 4(b)** — replace `` — only the release deployment report (ADM-036), for any registered app. `` with `` — only `PUT /api/v1/apps/{key}/releases/{environment}/{version}/deployment` (the deploy report, ADM-036), for any registered app (disabled and `admin` included; unknown app → 404). ``
    - **CLAUDE.md R9** — after ADM-034's appended `` `PUT /api/v1/apps/{key}/releases/{environment}/{version}` (natural key, not a UUID; D30) `` append `` and its `/deployment` sub-document (singular, a single document; D30) ``.
    - `docs/identity-architecture.md`: new row `| \`PUT /api/v1/apps/{key}/releases/{environment}/{version}/deployment\` \`{status, deployed_at?, deploy_run_url}\` | deployer credential (ADR-0021, D30), per-IP limit, no CORS | deploy result: create (201) or transition (200), 409 \`release_already_deployed\` | ADM-036 |`; line 171 and D30 edits as in the file list.
    - ADR-0021 lines and `docs/deploy.md` as in the file list.
11. `make check` (no `alembic check` needed: no model change; run it anyway since the reviewer may).

Rules: **R1** router = HTTP (body, status code), service = rules and audit, repository = SQL, `rules.py` pure. **R2** no schema change. **R3** class 4(b), exact registry entry, no other route accepts `mmdk_`. **R4** audit in the request transaction; no-ops and 409 write nothing. **R5** four settings + `.env.example`. **R6** existing classes; one new code `release_already_deployed` (409 already listed). **R7** no per-key bucket, no change to `DeployerCredentialService`, no `lock_app`. **R8** tests above. **R9** documented exception. **R11** no key, URL or body in logs or errors.

## Specialist input used
- **API platform (adopted):** PUT (their Q1); separate router like `manifest_router.py`; status codes 201 only from the insert result; `extra="forbid"`; `deploy_run_url` required; cross-field rule; window with an injected clock (here: DB clock passed into a pure function; boundaries tested in pure tests, API tests use wide margins); insert-ON-CONFLICT + FOR UPDATE shared with ADM-034; report writes only the three fields; enrichment and pending-then-identical-PUT tests; rollback test (ties already resolved by greatest id, `repository.py:163-168`); 30/min configurable (their Q4).
  - **Changed:** body cap — their default "no 411" is replaced by reusing ADM-034's helper unchanged (411 on missing length), so both release routes behave the same (Q3).
  - **Changed:** audit action for report-first create — `release.created` with `created_via` rather than `release.deployment_reported` with `created=true` (Q7): ADM-034 already uses `release.created` for every creation, and staff filtering creations then sees all of them.
  - **Changed:** order of 404 vs 422 — path/body validation before the app 404 (same order as ADM-034's route); both come after auth, so nothing is revealed to an unauthenticated caller.
- **Identity (adopted):** raw body, no Pydantic body param, order 429 → 401 → 411/413/415/422 → 404; separate bucket; 404 after auth, disabled and `admin` accepted; full 401 uniformity matrix; no deployer advisory lock; `last_used_at` moves only on committed reports (documented); exact-equality registry, `deployerCredential` OpenAPI assertion, `403` absent, `_fill` in the CORS loop, no-CORS preflight test; 409 not audited (their Q3); R3 4(b) text split with ADM-034 (their Q5).
  - **Changed:** scope name `release_deployment` (not `deployer_report`) to match ADM-034's `app_release`.
  - Their Q4 (admin-pan) is decided by ADM-034 Q4; nothing to add here.
- **Security (adopted):** decide every transition against a `FOR UPDATE` row, never a blind UPDATE; same-URL no-ops; stale-report guard (Q6); fullmatch + bounded digits for run id and attempts; trailing-`\n` tests on URL and version; strict aware datetime normalised to UTC; schema enforces `deployed_iff_deployed_at` before the write; staff-delete race retry once then 409; no `lock_app` (lock order apps → release if Q9 changes); two-session tests in both orders; logs with ids and `key_id` only.
  - **Deferred to Ming:** per-key bucket (Q4, default no — consistent with ADM-034 Q2); runner-IP allow-list (their Q3, default no — out of scope, follow-up); production report-first alert (their Q5, default no).
  - **Agreed with security over API platform** on the audit action name (Q7).
- **Unresolved disagreement → Ming:** none beyond Q1–Q11.

## Assumptions
- **ADM-034 is built and merged first** in this batch; this plan rebases on it and reuses `releases/body.py`, `write_schemas.check_release_path`, `rules.py`, `errors.py`, `repository.insert_if_absent` / `get_for_update`, `schemas.release_summary`, `AuditAction.RELEASE_CREATED`, the `app_release` rate-limit pattern, `_fill` with `environment`/`version`, `APP_CREDENTIAL` as a dict, and `make_credential(scope=…)`. If ADM-034 names differ at merge time, the implementer uses the merged names and records it in impl-log. If ADM-034 is not merged, ADM-036 does not start.
- **Retry response:** a retried create answers **200** with a body identical to the first 201 (ADR-0021 §4: "201 when it creates the record, 200 otherwise"). "Same response" in AC1 is read as same body and same stored state.
- **`deployed → deployed` with a different URL and an equal `deployed_at`** is an update (URL only); only a strictly older `deployed_at` is stale (Q6).
- **Window clock** is the database clock (`SELECT now()`, transaction start), like every other expiry check in this codebase.
- **Auth row lock while the body is read:** `current_deployer` touches the credential row before the body is read, so the row lock is held for at most `release_deployment_read_timeout_seconds` (10 s) on a slow client; it only serialises reports using the same key and a staff revoke. Not changed (R7, ADM-035's service).
- **Numbers:** 30/min/IP, 60 s window, 4096-byte cap, 10 s read deadline (OQ46 "final values PLAN's").
- **Hotspots (expect rebases):** ADM-034 (merged first): `app/modules/releases/*`, `app/core/rate_limit.py`, `app/core/config.py`, `.env.example`, `app/api/router.py`, `app/modules/audit/actions.py`, `CLAUDE.md` (R3, R9), `docs/identity-architecture.md`, `docs/adr/0021-…`, `tests/test_route_auth_classes.py`, `tests/test_audit_coverage.py`. ADM-040 (PLANNING): `docs/identity-architecture.md`, possibly `app/core/config.py`.
- No ADR-0026 / D35: every decision here is one ADR-0021 left to PLAN, recorded as a dated line in ADR-0021.
- `tests/test_deployer_credentials_auth.py` keeps its test-only probe route (its docstring stays true for that file).

## Blocking questions
Each has a default; the plan is written for the defaults.
1. **Method: PUT or PATCH?** **Default: PUT** (upsert that may create → 201; the body is the full desired deployment state; idempotent; matches ADM-034's PUT). PATCH would change only the decorator, docs and registry entry.
2. **`deploy_run_url` required for both statuses?** Spec OQ45 already says "required on a report". **Default: yes** (for `failed` too).
3. **Body cap behaviour.** **Default: 4096 bytes, reuse ADM-034's `read_json_body` unchanged:** missing `Content-Length` → 411, declared or actually read bytes over the cap → 413, non-JSON content type → 415. (API-platform alternative: no 411, cap the bytes read only — would need a flag in the shared helper.)
4. **Rate limit.** **Default: per-IP 30/min, configurable (`RELEASE_DEPLOYMENT_RATE_LIMIT`), no per-key bucket** (OQ46 fixes per-IP only; same as ADM-034 Q2; follow-up if a key leaks). Note the runner shares one bucket across all apps' deploys; raise the env value if bulk deploys hit 429.
5. **`failed` report with `deployed_at` present.** **Default: 422** (spec "refused with `failed`").
6. **Stale redeploy:** `deployed → deployed` with a different URL but an **older** `deployed_at` than stored. The ADR table literally says "update". **Default: 200 no-op, no audit row, record unchanged** (a late retry of an older run must not rewrite "live now"). Recorded as a dated line in ADR-0021 §5.
7. **Audit for a report-first create.** **Default: one row `release.created`** with `changes.created_via = "deployer"` (no second `release.deployment_reported` row). Alternative: one `release.deployment_reported` row with `created=true`.
8. **409 `release_already_deployed`:** **Default: no audit row**, a warning log line with ids and `key_id` only (ADR-0021 §8 lists `denied` rows only for wrong-app / wrong-scope keys).
9. **`lock_app` on the report?** **Default: no** (no RBAC data touched; FK `KEY SHARE` does not conflict with `lock_app`; no lock cycle). If yes: take it in the service after the body is read, before the insert, order apps → release row.
10. **Staff delete between the insert and `SELECT … FOR UPDATE`.** **Default: retry the insert once (→ 201 recreated, accepted by OQ42), then 409 `conflict` with reason `retry`; never 500.**
11. **App resolution.** **Default (spec OQ38 / ADR-0021 §2):** unknown app → 404, only after auth (an unauthenticated caller always gets 401/429); disabled apps and the `admin` app are accepted.

## Split proposal
None. Estimate: about 300 production lines (schema 40, rules 60, service 90, router 50, config/rate-limit/actions/errors 30, docs 30) and about 750 test lines. Production code is under the ~400-line guide because ADM-034 provides the helper, path validation and repository pair.
