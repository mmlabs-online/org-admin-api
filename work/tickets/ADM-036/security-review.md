# Security review — ADM-036

## Attempt 1
Verdict: PASS

Static review of commit 3592976 against origin/main. I read the code and tests and used git and grep only. I did not run pytest or make check. I found no 🔴 blockers and no 🟡 should-fix items. Every point in the requested focus checks out.

**Auth and order of checks**
- The route's `dependencies=[rate_limit("release_deployment")]` puts the per-IP limit before `DeployerDep`, and the bucket is separate from `app_release`. Tested in `tests/test_releases_deployment_api.py:454`.
- `DeployerCredentialService.authenticate` gives every failure the same 401 with `WWW-Authenticate: Bearer realm="deployer"` and no details (`app/modules/deployer_credentials/service.py:31-34`). That covers missing, malformed, unknown key_id, wrong secret, revoked, both `mmak_` scopes (manifest, releases) and an admin JWT. `tests/test_releases_deployment_api.py:411` checks all of them give one identical body, and `tests/test_route_auth_classes.py` adds the same checks for admin JWT and app keys.
- The secret is compared with `hmac.compare_digest`, and `touch_last_used` re-checks revocation with a guarded UPDATE.
- The registry `DEPLOYER_CREDENTIAL` contains exactly this one route, and the OpenAPI security scheme is `deployerCredential` with no 403.

**Body handling**
- `read_json_body` (`app/modules/releases/body.py:62-83`) runs only after auth. Order: Content-Length (411/413), then media type (415), then a capped read with a deadline (413/422), then `model_validate_json`.
- Errors are rebuilt with `include_input=False`, so submitted values are never echoed. Tested at `tests/test_releases_deployment_api.py:444`, `:470`, `:483`, `:495`.

**Validation**
- `deploy_run_url` uses `re.fullmatch` against the mmlabs-online/deployment Actions-run allow-list (`app/modules/releases/write_schemas.py:43-46`, `:59-67`). It is stricter than the DB CHECK, so a value that passes validation cannot cause a CHECK-violation 500.
- `deployed_at` is checked as follows:
  - It must carry a timezone (`AwareDatetime`).
  - It is required with `deployed` and refused with `failed`.
  - It must fall within +5 min / −30 days of the DB clock (`db_now()`), checked in `app/modules/releases/rules.py:64-70`.
- `extra="forbid"` and `strict=True` apply, and error messages are fixed text.
- Path version is checked with fullmatch (`check_release_path`) plus `max_length=32`.
- The DB constraint `deployed_iff_deployed_at` (`app/modules/releases/models.py:75-77`) guarantees the `assert` at `rules.py:113` holds.

**Transition table** (`rules.py:95-117`)
- `deployed → failed` returns 409 `release_already_deployed`, writes nothing and adds no audit row. Tested at `:257`.
- An older `deployed_at` with a different URL is a no-op, and a same-URL retry is a no-op. Neither writes an audit row.
- `pending|failed → deployed|failed` is handled correctly.

**Concurrency**
- Insert uses `INSERT … ON CONFLICT DO NOTHING`. If the row exists, it is read `FOR UPDATE`. If the row is deleted concurrently, the service retries once and then returns 409 retry. This avoids IntegrityError 500s and lost updates.
- Lock order is safe:
  - The deployer takes only the release row lock.
  - The app PUT takes the app lock (`confirm`) and then the release row, so there is no cycle.
  - Apps cannot be deleted (FK is `ondelete=RESTRICT` and there is no delete path), so `app_id_by_key` followed by insert cannot hit an FK violation.
- Report-first-then-app-PUT enrichment is tested at `:302` and `:329`. The staff-delete race is tested at `:596` and `:621`.

**Audit**
- `release.created` records environment, version, status and created_via.
- `release.deployment_reported` records environment, version, changed field names and the status diff. It never records the URL or the timestamp. Tested at `:581`.
- The actor is `SystemActor("deployer:<key_id>", privileged=False)`.

**`insert_if_absent`** (`app/modules/releases/repository.py:127`)
- `{"status": PENDING, **fields}` behaves the same as before for ADM-034. `ReleasePut` is `extra="forbid"` with no `status` field, and `app_write._doc` sends only `FIELDS`, so status stays `pending`.

**CORS and logs**
- `BearerRouteMatcher` covers only Bearer dependants, and the identity CORS prefix is `/auth/`, so this route gets no CORS headers. Tested in `tests/test_route_auth_classes.py` and at `:636`.
- The conflict log (`app/modules/releases/deployment.py:105-112`) contains only the app key (already confirmed to exist), env, version, release_id and key_id. It never contains the URL, the timestamp or the secret.

**💭 Notes (non-blocking)**
- 💭 `app/modules/releases/rules.py:103-105`: on `failed → failed` with a different URL, the URL is always replaced. `failed` carries no timestamp, so a late retry from an older failed run can overwrite a newer failed run's URL. The only impact is that the record points at the wrong run, not the release state. A possible fix is to keep the run with the higher `runs/<id>` number (parse the id from the URL already validated by fullmatch), or to accept this and document it in ADR-0021 §5.
- 💭 `app/modules/releases/repository.py:127`: the merge now lets a caller's `fields` override `status`. Before, passing `status` raised a duplicate-keyword TypeError, which acted as a guard against the app write ever setting status. Today the app write is safe because of `extra="forbid"` and `FIELDS`. To make the protection explicit, add an `assert "status" not in incoming` in `app_write.py`, or give `insert_if_absent` an explicit `status: str = ReleaseStatus.PENDING.value` keyword argument instead of the dict merge.
- 💭 `app/modules/releases/deployment_router.py:65`: the `key` path parameter has no `max_length`. It is only used in a parameterised lookup after auth, so this is harmless. Add `Path(max_length=…)` to match the apps key grammar for consistency with `version`.

**Follow-ups (out of scope)**
- Cross-repo: the deployment repo step that sends the report must keep the `mmdk_` key in GitHub Actions secrets only and must never print it in logs. Ming still has to issue the first deployer key (already listed in impl-log).
