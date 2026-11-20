# ADR-0021: Release log, scoped app credentials and the deployer credential

Status: Accepted (2026-11-19)
Amended by ADR-0022 (§1 scope set and auth path, §3 rate limits, §12 R3 text)

> Originally numbered ADR-0018 with decision-log row D28; renumbered to ADR-0021 / D30 on 2026-11-20 (collision with `0018-attachment-storage-google-drive.md` and with D28 bug-report submit). Old PR and commit references to "ADR-0018 release log" mean this record.
Date: 2026-11-19

## Context
Spec §5.13 (owner, 2026-11-19): every production release and every staging build of every mmlabs
app is recorded in this API and read by `admin:*` staff in admin-pan. Two machines write, no staff
login:

- (a) the app's release workflow (GitHub Actions in the app repo), right after it opens the
  deployment PR;
- (b) the deploy workflow in `mmlabs-online/deployment` (self-hosted runner), after the health
  check. Staging deploys also go through this repo, so (b) reports staging too (owner
  confirmation, 2026-11-19).

CI treats every error from these calls as a warning, so a write must never block a release or a
deploy, and every write must be idempotent and safe to retry.

R3 class 4 (D25, ADR-0016) allows the app credential on exactly one route,
`PUT /api/v1/apps/{key}/manifest`. ADR-0017 §2 kept that unchanged and rejected widening it for
entitlement pulls. The owner now decides to widen it, **for releases only** (2026-11-19). No
existing class fits writer (b), which reports for any app: it is neither an app nor a person.

The open points (spec §8 OQ36–OQ49) were answered by the owner on 2026-11-19 (translated):
*"Agree with everything; the 4 points follow the software architect's recommendation."* All
proposed defaults are accepted except OQ37, OQ40, OQ44 and OQ48, which take the software
architect's recommendation; those four are marked *(architect)* below.

## Decision

### 1. App credential scopes: manifest or its own app's releases (amends ADR-0016 §1, §3, §5) *(architect, OQ44)*
App credentials gain a **`scope`**: `manifest` | `releases`. Each key has **exactly one** scope.

- A `manifest` key is accepted only on `PUT /api/v1/apps/{key}/manifest` (unchanged, ADR-0016).
- A `releases` key is accepted only on `PUT /api/v1/apps/{key}/releases/{environment}/{version}`
  (new).
- A valid key of the **wrong scope** on either route → **403 `app_credential_scope`**, audited
  `denied`. Another app's key → 403 `app_credential_mismatch`, audited `denied`; a disabled app →
  403 `app_disabled` (ADR-0016 §4 unchanged). The order of the scope and app checks is PLAN's.
- **Same `mmak_` prefix and format**; the scope is read from the database row, not encoded in the
  key.
- **Migration:** existing keys become `manifest`.
- **Limit:** at most **2 active keys per (app, scope)** (rotation overlap per scope; replaces the
  per-app limit of ADR-0016 §3).
- **Issuance** takes an optional `scope`, default `manifest`, so current clients keep working; list
  returns the scope. Revocation unchanged.
- The `admin_app` refusal of ADR-0016 §5 applies to the **`manifest` scope only**: `releases` keys
  may be issued for the `admin` app (§11).
- App-credential authentication keeps taking `lock_app` (as today) before touching `last_used_at`,
  for both scopes.
- **Secrets:** each app repo holds its `releases` key as **`MMLABS_RELEASE_CREDENTIAL`**, a GitHub
  Environment secret limited to release branches / `main`. `manifest` keys stay in the deployment
  repo.

ADR-0017 §2 ("R3 class 4 unchanged") stays true for entitlements: no pull endpoint is added.

### 2. Deployer credential (new key type)
- **Format** `mmdk_<key_id>_<secret>`, same shape as `mmak_`: `key_id` 16 hex (lookup), secret
  256 bits; only SHA-256(secret) stored, compared with `hmac.compare_digest`; no pepper (ADR-0016
  §3 reasoning) *(OQ43)*.
- **Not bound to an app.** Its own table (`app_credentials.app_id` stays NOT NULL), at most
  **2 active** for rotation overlap, no expiry, `last_used_at`, immediate revoke (every request
  reads the database) *(OQ43)*.
- **Issued, listed and revoked by `admin:*` only** (Bearer, `aud=admin`), each write audited
  (`deployer_credential.issued`, `deployer_credential.revoked`). The value is shown once with
  `Cache-Control: no-store`; list returns metadata only. No CLI bootstrap: the first key is issued
  through the admin API, like app credentials (`docs/deploy.md`) *(OQ43)*.
- **Accepted on exactly one route:** `…/apps/{key}/releases/{environment}/{version}/deployment`
  (method `PUT` upsert or `PATCH`, chosen at PLAN; architect), for any registered app, including
  disabled ones since the deploy happened *(OQ38)*. Unknown app → 404. Reports for the `admin`
  app are accepted, since its releases are recorded (§11).
- Stored as `MMLABS_DEPLOYER_CREDENTIAL` in the deployment repo.

### 3. Machine-route rules (both credentials)
- `Authorization: Bearer <key>` only. Each route accepts **only its own key type**: an `mmdk_` key
  on an app route, an `mmak_` key on the report route, or an admin JWT on either gives the single
  401 answer of ADR-0016 §4 (`unauthorized`, `WWW-Authenticate: Bearer realm="app"` or
  `realm="deployer"`, no details). An admin JWT is never accepted on these routes and machine keys
  are never accepted on any other route.
- **No CORS** (server-to-server; outside `/api/v1/auth/`).
- **Per-IP rate limit before authentication**, in-process (ADR-0002), one bucket per route:
  30/min app write, 30/min report *(OQ46)*. A 429 is just a warning in CI.
- **Body size caps checked inside the routes** (architect): 256 KiB app write, 4 KiB report
  *(OQ46)*; numbers in `app/core/config.py`, final values PLAN's.
- Listed by exact (method, path) in `tests/test_route_auth_classes.py` (`APP_CREDENTIAL` gains the
  release route; new `DEPLOYER_CREDENTIAL` set).

### 4. Idempotency, retry and late enrichment
- **App write (`PUT`).** Creates the record as `pending` (201) or, while `pending`, replaces the
  whole document (200). An identical retry returns 200 with the stored record and writes no audit
  row. The app writer never sets `status`; the PUT **never changes status**.
- **Late PUT after `deployed` / `failed`** *(architect, OQ37)*: fields that are still NULL/empty
  (a record created from a report, §6) are filled in from the PUT (200, audited
  `release.updated`). If **any** non-null field differs from the PUT → **409
  `release_not_pending`**, record unchanged. If all non-null fields are identical and nothing is
  left to fill → 200 no-op, no audit row.
- **Report (`…/deployment`).** Body `{status: deployed|failed, deployed_at, deploy_run_url}`.
  201 when it creates the record (§6), 200 otherwise.
- **Races.** Unique (app, environment, version); concurrent first writes resolve through the
  unique constraint (insert-or-update, or catch the conflict and retry once as an update), never a
  500. Exact mechanism is PLAN's.
- A staff-deleted record may be recreated by a later CI retry; accepted *(OQ42)*.

### 5. Status transitions (report route)
| From \ report | `deployed` | `failed` |
|---|---|---|
| (no record) | create as `deployed` (201) | create as `failed` (201) |
| `pending` | `deployed`, sets `deployed_at`, `deploy_run_url` | `failed`, sets `deploy_run_url` |
| `failed` | `deployed` (redeploy; owner) | 200, updates `deploy_run_url` *(OQ39)* |
| `deployed` | same `deploy_run_url`: **no-op 200**; different `deploy_run_url`: **redeploy/rollback**, 200, updates `deployed_at` and `deploy_run_url`, audited *(architect, OQ40)* | **409 `release_already_deployed`**, unchanged *(OQ39)* |

`deployed_at` is required with `deployed` and refused with `failed`; timezone-aware, at most 5 min
in the future and at most 30 days in the past *(OQ38)*. There is no `rolled_back` / `superseded`
state in v1. Because a redeploy of an older version moves its `deployed_at` forward, **"live now"
per app + environment = the record with the latest `deployed_at` among `status = deployed`**, and
it stays correct after a rollback *(architect, OQ40)*. v1 exposes a read for it; its shape (list
`sort=deployed_at` plus a filter, or a small dedicated endpoint) is PLAN's. Only the latest deploy
of each release is kept (no deployment-event history in v1).

### 6. Report before the release record
If writer (a) failed, the report creates the record with what is known: app, environment and
version from the path (same grammar as the app write: production strict `vX.Y.Z`, staging
`sha-<7 hex>`, OQ36), `status`, `deployed_at`, `deploy_run_url`. The production version is the git
tag `vX.Y.Z`, not the branch name: the release branch is `release/vX.Y.Z` today and
`release/X.Y.Z` in spaceM project-kit 2.0; the API grammar stays `vX.Y.Z`. `git_sha`, `deployment_pr` and
`has_migrations` are null; `images` and `changes` are empty, and a late app PUT may fill them
(§4). The audit row records that the record was created from a report.

### 7. Validation and PII
- Formats and limits are those of spec §5.13 / OQ45 (digest `sha256:<64 hex>`; `deployment_pr`
  only `https://github.com/mmlabs-online/deployment/pull/<N>`; `deploy_run_url` only
  `https://github.com/mmlabs-online/deployment/actions/runs/<N>[/attempts/<N>]`; `pr_url` only on
  `github.com/mmlabs-online/*`; `changes` ≤ 200, titles ≤ 200 chars with control characters
  stripped; unknown fields → 422).
- **No PII:** no author emails, names or GitHub logins are accepted or stored. Titles are free
  text the API cannot screen; the release template sends PR titles only. 422 details name fields
  and never echo values; keys never appear in logs or errors (R5, R11).

### 8. Audit
One audit row per write that changes something, in the same transaction (R4), ids and status only
(app key, environment, version, release id, status, change count), never titles or URLs:
`release.created`, `release.updated`, `release.deployment_reported`, `release.deleted`. No-ops
write no row; a wrong-app or wrong-scope key writes a `denied` row. Actors are `SystemActor`
values, `SystemActor("app:{key}/{key_id}")` and `SystemActor("deployer:{key_id}")`,
`actor_kind = system` (no audit-schema migration) *(OQ47)*.

### 9. Staff routes and permission
`admin:*` only — no new leaf permission, no `admin:releases:*` (owner, reconfirmed 2026-11-19).
`GET /api/v1/releases` (`Page[T]`, filters app, environment, status, date range,
`sort=created_at|deployed_at`, newest first), `GET /api/v1/releases/{id}`,
`DELETE /api/v1/releases/{id}` (204, audited) *(OQ42, OQ49)*, the live-version read (§5);
`/api/v1/deployer-credentials` (POST, GET, DELETE `/{id}`). App-credential issuance gains the
optional `scope` (§1).

### 10. R9 exception
`/api/v1/apps/{key}/releases/{environment}/{version}` addresses a release by its natural key
(environment + version) instead of a UUID, and `…/deployment` is a singular sub-document (like
D18/D25). Staff routes follow R9 normally.

### 11. Releases of this API and admin-pan *(architect, OQ48)*
Recorded in v1: `releases`-scoped keys may be issued for the `admin` app, and deploy reports for
`admin` are accepted. **Open for PLAN (not blocking):** org-admin-api and admin-pan both map to
the `admin` app and could collide on `(app, environment, version)`. Proposed resolution: give
admin-pan its own app row or key; decided at PLAN of the releases ticket.
Decided at ADM-034 PLAN: `admin` = org-admin-api; admin-pan is registered as its own app row (ops step), no code special case.

### 12. R3 class 4 text for `CLAUDE.md` (applied by the first implementing ticket)
> (4) **machine credential** (D25, D30; ADR-0016, ADR-0021) — opaque keys issued by staff
> (`admin:*`), stored only as a SHA-256 hash, sent as `Authorization: Bearer …`, no CORS, per-IP
> rate-limited, never accepted on any other route; a key of the wrong type → 401:
> (a) **app credential** `mmak_<key_id>_<secret>` with one scope — `manifest`: only
> `PUT /api/v1/apps/{key}/manifest`; `releases`: only
> `PUT /api/v1/apps/{key}/releases/{environment}/{version}`; accepted only for its own app
> (another app → 403 `app_credential_mismatch`, wrong scope → 403 `app_credential_scope`);
> (b) **deployer credential** `mmdk_<key_id>_<secret>` — only
> `…/apps/{key}/releases/{environment}/{version}/deployment` (method fixed at PLAN), for any
> registered app.

and in R9's exception list: "`/api/v1/apps/{key}/releases/{environment}/{version}` and its
`/deployment` sub-document (natural key, singular; D30)". A decision-log row D30 in
`docs/identity-architecture.md` records this ADR.

## Rejected alternatives
- **This API pulls GitHub releases** (polling or GitHub API): needs a GitHub token and a scheduler
  in this API, staging builds have no GitHub release, and deploy results live in the deployment
  repo anyway. More moving parts, less exact data.
- **Deployment repo as the single writer** (it sends release data and the deploy result): the
  deployment repo does not know the app's PR list, risk tiers or migrations without parsing every
  app repo; it would also need write access for all apps' data, and a record would only exist after
  the deploy ran (a pending release would be invisible).
- **The app credential also reports deploys**: the deploy runs in the deployment repo, which would
  then hold every app's `mmak_` key, each able to rewrite its app's manifest — a much larger blast
  radius than a report-only key.
- **One unscoped `mmak_` key for both manifest and releases** (the earlier proposed default): the
  app repo would hold a key that can also rewrite the app's roles manifest; scoping keeps a leaked
  release key from touching RBAC (owner chose the architect's recommendation, 2026-11-19).
- **Deployer as an app credential of a pseudo-app**: `mmak_` keys are bound to one app; "any app"
  would need a special case in the class-4 check, and the pseudo-app would show in the app list
  and role catalog.
- **`deployed → deployed` always a no-op** (the owner's first wording): a rollback to an older,
  already-deployed version would be invisible and "live now" wrong; replaced by §5.
- **No backfill after `pending`** (the earlier proposed default): a record created from a report
  would stay thin forever when the app write merely arrived late; replaced by §4 enrichment.
- **OAuth client credentials**: rejected for the same reasons as ADR-0016 §2 (token endpoint, new
  audience, toward the R10 non-goal).
- **Webhooks from GitHub to this API**: needs a public webhook secret per repo and mapping events
  to environments; still lacks the health-check result.

## Consequences
- **Blast radius grows a little:** a leaked `releases` key can forge or overwrite *pending*
  release records of its own app, or fill empty fields of its deployed/failed records (misleading
  the owner, not changing any system); it cannot touch the manifest. A leaked `mmdk_` key can mark
  any app's release deployed or failed, move `deployed_at` of a deployed release (fake
  redeploy/rollback), or create thin records for any (app, environment, version); it cannot touch
  manifests, roles, users or orgs. Mitigations: `admin:*` to issue, at most 2 keys per (app, scope)
  / 2 deployer keys, `last_used_at`, full audit, immediate revoke, per-IP limit, GitHub Environment
  restriction on `MMLABS_RELEASE_CREDENTIAL`.
- `app_credentials` gains a `scope` column (migration, existing rows `manifest`) and the active-key
  limit becomes per (app, scope); issuance stays backward compatible.
- The deployment repo gains `MMLABS_DEPLOYER_CREDENTIAL`; each app repo gains
  `MMLABS_RELEASE_CREDENTIAL` (a `releases` key, Environment-restricted). It replaces the earlier
  `MMLABS_APP_CREDENTIAL` proposal; `manifest` keys stay in the deployment repo.
- The API is now on the release path of every app, but only as a warning-only side call: an API
  outage loses log entries, never releases. The deployer's per-IP bucket is shared by all apps'
  deploys (production and staging) from the one runner IP.
- Only the latest deploy of each release is kept; a full deployment-event history is a later
  follow-up if needed.
- Releases of this API and admin-pan are recorded under the `admin` app; the version collision
  between the two is settled at PLAN (§11).
- `CLAUDE.md` R3 and R9, `docs/identity-architecture.md` (D30) and `tests/test_route_auth_classes.py`
  change in the first implementing ticket; ADR-0016 §1, §3 and §5 are amended by §1 above.
- Bug reports (spec §5.12) link to the release the reporter ran — report app version first, else
  the release live at report time — exposed as `release_match` = `reported` | `inferred` | `none`
  (owner, 2026-11-19). Because only the latest deploy is kept, an `inferred` link can drift after
  a later redeploy/rollback; compute-at-read vs store-at-create is PLAN's.
- Follow-ups: credential `expires_at`/`label` (shared with ADR-0016), deployment-event history,
  staging retention purge (~1 GB or ~100k rows, OQ41).
