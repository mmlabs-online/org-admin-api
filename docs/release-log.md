# Release log: guide for app repos and the deployment repo (ADM-037)

How CI reports releases to org-admin-api (spec §5.13, [ADR-0021](adr/0021-release-log-and-deployer-credential.md)).
Two calls, two credentials, both server-to-server only (never from a browser or mobile app; no CORS).

> **Rule: a failed report is a warning, never a failed job.** Every call below must end in
> `|| echo "::warning::…"` (and the step in `continue-on-error: true`). A 4xx, 5xx, 429 or timeout
> must never block a release or a deploy. The log can be repaired later by repeating the call.

## Who sends what

| Sender | When | Call | Credential (secret name) |
|---|---|---|---|
| App repo | At release, after the image is pushed | `PUT /api/v1/apps/<app-key>/releases/<environment>/<version>` | app credential, scope `releases` (`MMLABS_RELEASE_CREDENTIAL`) |
| `mmlabs-online/deployment` | After the deploy finishes (success or failure) | `PUT /api/v1/apps/<app-key>/releases/<environment>/<version>/deployment` | deployer credential (`MMLABS_DEPLOYER_CREDENTIAL`) |

The two calls may arrive in either order: a deploy report for an unknown release creates the
record, and a late app write enriches it (see "Idempotency").

## Keys and secrets

- App credential `mmak_<key_id>_<secret>`, scope `releases`, for **one app**. Store it as the secret
  `MMLABS_RELEASE_CREDENTIAL` in a GitHub **Environment** of the app repo that is limited to the
  release branch(es). It works only on the route above and only for its own app.
- Deployer credential `mmdk_<key_id>_<secret>`, not bound to an app. Store it as the secret
  `MMLABS_DEPLOYER_CREDENTIAL` in `mmlabs-online/deployment`. It works only on the `/deployment` route.
- Both are sent as `Authorization: Bearer <credential>`. A key of the other kind, an admin JWT or an
  unknown/revoked key gives 401.
- The owner issues and rotates both: [deploy.md](deploy.md), "App credentials" and "Deployer credential".
  Never put a key in a file in the repo, a log, an argument list or an issue.
- `admin` is org-admin-api itself (it may only hold `releases` keys). admin-pan is registered as its
  **own** app row; use its own key, not `admin`.

## App write: `PUT /api/v1/apps/{key}/releases/{environment}/{version}`

- `environment`: `production` or `staging`.
- `version` (max 32 chars): production `vX.Y.Z` (no leading zeros, e.g. `v1.4.0`); staging
  `sha-<7 lowercase hex>`, and it must equal `sha-` plus the first 7 characters of `git_sha`.
- Headers: `Authorization: Bearer …`, `Content-Type: application/json`, and a `Content-Length`
  (a plain `curl --data`/`-d @file` sets it; do not use chunked uploads). Body at most 256 KiB.
- Body (unknown fields are refused; types are strict):

| Field | Type | Required | Rule |
|---|---|---|---|
| `git_sha` | string | yes | 40 lowercase hex |
| `images` | array | yes | 1 to 10 items, unique `name` |
| `images[].name` | string | yes | Docker repository name, no tag or digest, max 255 |
| `images[].digest` | string | yes | `sha256:` + 64 lowercase hex |
| `changes` | array | no (default `[]`) | at most 200 items, unique `pr_url` |
| `changes[].pr_url` | string | yes | `https://github.com/mmlabs-online/<repo>/pull/<n>` |
| `changes[].title` | string | yes | 1 to 200 chars after cleanup (control characters are stripped) |
| `changes[].ticket_id` | string or null | no | like `ADM-123` |
| `changes[].risk` | `low`, `medium`, `high` or null | no | |
| `deployment_pr` | string or null | no | `https://github.com/mmlabs-online/deployment/pull/<n>` |
| `has_migrations` | boolean or null | no | |

Do not send `status`, `deployed_at`, `deploy_run_url` or authors: they are refused (422).

Responses: `201` created as `pending`; `200` replaced, enriched or unchanged. The body is the release
record. Errors: see the table below.

### Building `changes` from merged PRs

List the PRs merged since the previous release and send one item per PR: `pr_url`, `title` and, when
you have them, `ticket_id` (from the PR title/branch) and `risk`. Example item:

```json
{"pr_url": "https://github.com/mmlabs-online/<repo>/pull/123", "title": "ADM-123: add release log", "ticket_id": "ADM-123", "risk": "low"}
```

With `gh`: `gh pr list --state merged --search "merged:>=<previous release date>" --json url,title`
then map `url` to `pr_url`. Keep to 200 items; send the PR **title only**.

### PII rule

The record is shown to staff and its content is not verified. Send only public facts: PR URLs, PR
titles, ticket ids, SHAs, image names and digests. Never put emails, names of end users, customer
data, tokens, secrets or log excerpts in a title or anywhere else. Titles are rendered as text.

## Deploy report: `PUT /api/v1/apps/{key}/releases/{environment}/{version}/deployment`

Same path rules for `environment` and `version`; here `{key}` can be any registered app (an unknown
app is 404). Body at most 4 KiB, same header rules.

| Field | Type | Required | Rule |
|---|---|---|---|
| `status` | `deployed` or `failed` | yes | |
| `deploy_run_url` | string | yes | `https://github.com/mmlabs-online/deployment/actions/runs/<id>` (optional `/attempts/<n>`), max 255 |
| `deployed_at` | RFC 3339 timestamp with offset | with `deployed`: required; with `failed`: refused | at most 5 minutes in the future and 30 days in the past |

Responses: `201` the record was created from the report; `200` updated or unchanged (the body is the
release summary). `pending` or `failed` can move to `deployed` or `failed`; a redeploy is allowed.

## Idempotency and retry

- Both calls are safe to repeat. Use `--retry 3 --max-time 15`.
- App write: a `pending` release is replaced. Once `deployed` or `failed`, only fields that are still
  empty are filled (late enrichment) and the same data is a no-op 200; a **different** non-empty value
  is 409 `release_not_pending`. It never changes `status`.
- Deploy report: the same `deploy_run_url` again is a no-op 200. `failed` for a `deployed` release is
  409 `release_already_deployed`.
- Rate limit: 30 requests per minute per IP on each route (before authentication).

## Errors (all are warnings in CI)

| Code | Meaning | What to do |
|---|---|---|
| 401 | Missing, wrong-kind, revoked or unknown credential | Warn; check the secret and ask the owner to rotate |
| 403 `app_credential_mismatch` | Key belongs to another app | Warn; use the key of this app |
| 403 `app_credential_scope` | Key has another scope | Warn; issue a `releases` key |
| 403 `app_disabled` | The app is disabled | Warn |
| 404 | Unknown app (deploy report) | Warn; register the app |
| 409 `release_not_pending` | Release already final and the data differs | Warn; nothing to fix in CI |
| 409 `release_already_deployed` | `failed` after `deployed` | Warn |
| 411 | No `Content-Length` | Send the body with `-d @file` |
| 413 | Body over the cap | Send fewer `changes` |
| 415 | `Content-Type` is not `application/json` | Add the header |
| 422 | Invalid body or version (details name fields, never values) | Warn; fix the payload |
| 429 | Rate limited | Warn; retry later |
| 5xx, timeout | Server or network | Warn |

## Copy-paste: app release workflow step

In the release job (the job must use the Environment that holds the secret). Replace `<app-key>`;
`VERSION`, `GIT_SHA`, `IMAGE_NAME` and `IMAGE_DIGEST` come from your build step, and `changes.json`
from the recipe above.

```yaml
      - name: Record release in the release log (warning only)
        continue-on-error: true
        env:
          MMLABS_RELEASE_CREDENTIAL: ${{ secrets.MMLABS_RELEASE_CREDENTIAL }}
          VERSION: ${{ steps.build.outputs.version }}
          GIT_SHA: ${{ github.sha }}
          IMAGE_NAME: ${{ steps.build.outputs.image_name }}
          IMAGE_DIGEST: ${{ steps.build.outputs.image_digest }}
        run: |
          set -u
          hdr=$(mktemp); body=$(mktemp)
          printf 'Authorization: Bearer %s\n' "$MMLABS_RELEASE_CREDENTIAL" > "$hdr"
          python3 - "$body" <<'PY' || { echo "::warning::release log: could not build the body"; exit 0; }
          import json, os, sys
          changes = json.load(open("changes.json")) if os.path.exists("changes.json") else []
          body = {
              "git_sha": os.environ["GIT_SHA"],
              "images": [{"name": os.environ["IMAGE_NAME"], "digest": os.environ["IMAGE_DIGEST"]}],
              "changes": changes,
          }
          json.dump(body, open(sys.argv[1], "w"))
          PY
          curl -fsS --retry 3 --max-time 15 -X PUT \
            -H @"$hdr" -H "Content-Type: application/json" --data @"$body" \
            "https://api.mmlabs.online/api/v1/apps/<app-key>/releases/production/${VERSION}" \
            || echo "::warning::release log write failed (release is not affected)"
          rm -f -- "$hdr" "$body"
```

For staging use `staging` and `sha-<first 7 of GIT_SHA>` as the version.

## Copy-paste: `deploy-<app>.yml` step in the deployment repo

After the deploy step, with `if: always()` so a failed deploy is reported too. `DEPLOY_STATUS` is
`deployed` or `failed`.

```yaml
      - name: Report deploy result to the release log (warning only)
        if: always()
        continue-on-error: true
        env:
          MMLABS_DEPLOYER_CREDENTIAL: ${{ secrets.MMLABS_DEPLOYER_CREDENTIAL }}
          VERSION: ${{ inputs.version }}
          DEPLOY_STATUS: ${{ job.status == 'success' && 'deployed' || 'failed' }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}/attempts/${{ github.run_attempt }}
        run: |
          set -u
          hdr=$(mktemp); body=$(mktemp)
          printf 'Authorization: Bearer %s\n' "$MMLABS_DEPLOYER_CREDENTIAL" > "$hdr"
          if [ "$DEPLOY_STATUS" = deployed ]; then
            printf '{"status":"deployed","deploy_run_url":"%s","deployed_at":"%s"}' \
              "$RUN_URL" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$body"
          else
            printf '{"status":"failed","deploy_run_url":"%s"}' "$RUN_URL" > "$body"
          fi
          curl -fsS --retry 3 --max-time 15 -X PUT \
            -H @"$hdr" -H "Content-Type: application/json" --data @"$body" \
            "https://api.mmlabs.online/api/v1/apps/<app-key>/releases/production/${VERSION}/deployment" \
            || echo "::warning::release log deploy report failed (deploy is not affected)"
          rm -f -- "$hdr" "$body"
```

## Bug reports and the release version

Bug reports link to a release by `app_version` (ADM-038). Apps must send the **exact release version
string** from this log as `app_version` (for example `v1.4.0` or `sha-1a2b3c4`), so a report matches
its release.
