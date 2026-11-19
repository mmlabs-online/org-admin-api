# ADR-0018: Release log, app credential widening and the deployer credential

Status: Proposed (2026-11-19)
Date: 2026-11-19

## Context
Spec §5.13 (owner, 2026-11-19): every production release and every staging build of every mmlabs
app is recorded in this API and read by `admin:*` staff in admin-pan. Two machines write, no staff
login:

- (a) the app's release workflow (GitHub Actions in the app repo), right after it opens the
  deployment PR;
- (b) the deploy workflow in `mmlabs-online/deployment` (self-hosted runner), after the health
  check.

CI treats every error from these calls as a warning, so a write must never block a release or a
deploy, and every write must be idempotent and safe to retry.

R3 class 4 (D25, ADR-0016) allows the app credential on exactly one route,
`PUT /api/v1/apps/{key}/manifest`. ADR-0017 §2 kept that unchanged and rejected widening it for
entitlement pulls. The owner now decides to widen it, **for releases only** (2026-11-19). No
existing class fits writer (b), which reports for any app: it is neither an app nor a person.

Items marked *(proposed default)* are open in spec §8 OQ36–OQ49; this ADR becomes Accepted when
the owner accepts them (or the answers are written back here).

## Decision

### 1. App credential: manifest + its own app's releases (amends ADR-0016 §1)
The `mmak_` app credential is accepted on exactly two routes:

- `PUT /api/v1/apps/{key}/manifest` (unchanged, ADR-0016);
- `PUT /api/v1/apps/{key}/releases/{environment}/{version}` (new).

Only for its own app: a valid key of another app gives 403 `app_credential_mismatch`, audited
`denied`; a disabled app gives 403 `app_disabled` (ADR-0016 §4 unchanged). One key covers both
routes; there are **no per-key scopes** in v1 *(proposed default, OQ44)*. Issuance, the 2-key
limit and revocation (ADR-0016 §3, §5) are unchanged. ADR-0017 §2 ("R3 class 4 unchanged") stays
true for entitlements: no pull endpoint is added.

### 2. Deployer credential (new key type)
- **Format** `mmdk_<key_id>_<secret>`, same shape as `mmak_`: `key_id` 16 hex (lookup), secret
  256 bits; only SHA-256(secret) stored, compared with `hmac.compare_digest`; no pepper (ADR-0016
  §3 reasoning) *(prefix: proposed default, OQ43)*.
- **Not bound to an app.** Its own table (`app_credentials.app_id` stays NOT NULL), at most
  **2 active** for rotation overlap, no expiry, `last_used_at`, immediate revoke (every request
  reads the database) *(proposed default, OQ43)*.
- **Issued, listed and revoked by `admin:*` only** (Bearer, `aud=admin`), each write audited
  (`deployer_credential.issued`, `deployer_credential.revoked`). The value is shown once with
  `Cache-Control: no-store`; list returns metadata only. No CLI bootstrap: the first key is issued
  through the admin API, like app credentials (`docs/deploy.md`) *(proposed default, OQ43)*.
- **Accepted on exactly one route:**
  `PATCH /api/v1/apps/{key}/releases/{environment}/{version}/deployment`, for any registered app
  (including disabled ones, since the deploy happened), except the `admin` app (404, as for the
  role catalog) *(proposed default, OQ38, OQ48)*.

### 3. Machine-route rules (both credentials)
- `Authorization: Bearer <key>` only. Each route accepts **only its own key type**: an `mmdk_` key
  on an app route, an `mmak_` key on the report route, or an admin JWT on either gives the single
  401 answer of ADR-0016 §4 (`unauthorized`, `WWW-Authenticate: Bearer realm="app"` or
  `realm="deployer"`, no details). An admin JWT is never accepted on these routes and machine keys
  are never accepted on any other route.
- **No CORS** (server-to-server; outside `/api/v1/auth/`).
- **Per-IP rate limit before authentication**, in-process (ADR-0002), one bucket per route:
  30/min app write, 30/min report; body caps 256 KiB and 4 KiB *(proposed defaults, OQ46)*.
  A 429 is just a warning in CI.
- Listed by exact (method, path) in `tests/test_route_auth_classes.py` (`APP_CREDENTIAL` gains the
  release route; new `DEPLOYER_CREDENTIAL` set).

### 4. Idempotency and retry
- **App write (`PUT`).** Creates the record as `pending` (201) or, while `pending`, replaces the
  whole document (200). An identical retry returns 200 with the stored record and writes no audit
  row. Once `deployed` or `failed`: 409 `release_not_pending`, including for a record created
  from a report (no backfill) *(proposed default, OQ37)*. The app writer never sets `status`.
- **Report (`PATCH …/deployment`).** Body `{status: deployed|failed, deployed_at, deploy_run_url}`.
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
| `failed` | `deployed` (redeploy; owner) | 200, updates `deploy_run_url` *(proposed, OQ39)* |
| `deployed` | **no-op 200** (owner) | **409 `release_already_deployed`**, unchanged *(proposed, OQ39)* |

`deployed_at` is required with `deployed` and refused with `failed`; timezone-aware, at most 5 min
in the future and at most 30 days in the past *(proposed default, OQ38)*. There is no
`rolled_back` / `superseded` state in v1: a rollback to an older, already-deployed version is not
visible (consequence of the `deployed → deployed` no-op); "live now" is read as the latest
`deployed_at` per app and environment *(proposed default, OQ40)*.

### 6. Report before the release record
If writer (a) failed, the report creates the record with what is known: app, environment and
version from the path (same grammar as the app write: production strict `vX.Y.Z`, staging
`sha-<7 hex>`, OQ36), `status`, `deployed_at`, `deploy_run_url`. `git_sha`, `deployment_pr` and
`has_migrations` are null; `images` and `changes` are empty. The audit row records that the
record was created from a report.

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
`release.created`, `release.updated`, `release.deployment_reported`, `release.deleted`. Actors
`SystemActor("app:{key}/{key_id}")` and `SystemActor("deployer:{key_id}")`, `actor_kind = system`
(no audit-schema migration) *(proposed default, OQ47)*.

### 9. Staff routes and permission
`admin:*` only — no new leaf permission (owner). `GET /api/v1/releases` (`Page[T]`, filters app,
environment, status, date range, newest first), `GET /api/v1/releases/{id}`,
`DELETE /api/v1/releases/{id}` (204, audited) *(OQ42, OQ49)*; `/api/v1/deployer-credentials`
(POST, GET, DELETE `/{id}`).

### 10. R9 exception
`/api/v1/apps/{key}/releases/{environment}/{version}` addresses a release by its natural key
(environment + version) instead of a UUID, and `…/deployment` is a singular sub-document (like
D18/D25). Staff routes follow R9 normally.

### 11. Proposed R3 class 4 text for `CLAUDE.md` (applied by the implementing ticket after the owner accepts)
> (4) **machine credential** (D25, D28; ADR-0016, ADR-0018) — opaque keys issued by staff
> (`admin:*`), stored only as a SHA-256 hash, sent as `Authorization: Bearer …`, no CORS, per-IP
> rate-limited, never accepted on any other route; a key of the wrong type → 401:
> (a) **app credential** `mmak_<key_id>_<secret>` — only `PUT /api/v1/apps/{key}/manifest` and
> `PUT /api/v1/apps/{key}/releases/{environment}/{version}`, accepted only for its own app
> (another app → 403);
> (b) **deployer credential** `mmdk_<key_id>_<secret>` — only
> `PATCH /api/v1/apps/{key}/releases/{environment}/{version}/deployment`, for any registered app
> except `admin`.

and in R9's exception list: "`/api/v1/apps/{key}/releases/{environment}/{version}` and its
`/deployment` sub-document (natural key, singular; D28)". A decision-log row D28 in
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
- **Deployer as an app credential of a pseudo-app**: `mmak_` keys are bound to one app; "any app"
  would need a special case in the class-4 check, and the pseudo-app would show in the app list
  and role catalog.
- **Per-key scopes on `mmak_`** (manifest vs. releases): finer, but a schema change and staff UI
  for a small gain while one CI secret serves both steps; revisit if a release-only key is needed.
- **OAuth client credentials**: rejected for the same reasons as ADR-0016 §2 (token endpoint, new
  audience, toward the R10 non-goal).
- **Webhooks from GitHub to this API**: needs a public webhook secret per repo and mapping events
  to environments; still lacks the health-check result.

## Consequences
- **Blast radius grows a little:** a leaked `mmak_` key can now also forge or overwrite *pending*
  release records of its own app (misleading the owner, not changing any system). A leaked
  `mmdk_` key can mark any app's release deployed or failed, or create thin records for any
  (app, environment, version); it cannot touch manifests, roles, users or orgs. Mitigations:
  `admin:*` to issue, at most 2 keys, `last_used_at`, full audit, immediate revoke, per-IP limit.
- The deployment repo gains one secret (`MMLABS_DEPLOYER_CREDENTIAL`); each app repo needs its
  `mmak_` key as `MMLABS_APP_CREDENTIAL` (apps that push a manifest already hold one).
- The API is now on the release path of every app, but only as a warning-only side call: an API
  outage loses log entries, never releases. The deployer's per-IP bucket is shared by all apps'
  deploys from the one runner IP.
- Rollbacks to an already-deployed version are not visible in v1 (§5); a `last_deployed_at`
  column or deployment-event table is the follow-up if needed.
- Releases of this API and admin-pan are not recorded in v1 (the `admin` app has no app
  credential).
- `CLAUDE.md` R3 and R9, `docs/identity-architecture.md` (D28) and `tests/test_route_auth_classes.py`
  change in the implementing tickets; ADR-0016 §1 is amended by §1 above.
- Follow-ups: per-key scopes, credential `expires_at`/`label` (shared with ADR-0016), rollback
  tracking, staging retention purge, release records for the `admin` app.
