# ADM-007: Release and deploy pipeline to api-admin.mmlabs.online

| Field | Value |
|---|---|
| State | IN_PROGRESS |
| Priority | P1 |
| Areas | architecture, security |
| Attempts | 0 |
| Created | 2026-11-10 |
| PR | — |

## Goal
Ship org-admin-api to `api-admin.mmlabs.online` with the same release/deploy model as stoktok: a release branch builds and publishes a versioned image, and a PR on `mmlabs-online/deployment` deploys it once the owner merges.

## Spec reference
§6.1 Deployment, §8 Q5

## Acceptance criteria
- [ ] `.github/workflows/release.yml`: a push to `release/vX.Y.Z` (commit already on `main`, version new and higher than every tag) creates the tag, builds the image, pushes it to Docker Hub, and opens a PR on `mmlabs-online/deployment` that pins the image by digest
- [ ] Drafts for the deployment repo under `deploy/deployment-repo/` (compose file, `deploy-org-admin` workflow with `main`-only guard, health wait on `/readyz`, rollback on failure), secrets prefixed `ORG_ADMIN_` and written to an env file outside git
- [ ] Database migrations run once per deploy before the new container serves traffic; a failed migration stops the deploy and keeps the old version running
- [ ] Postgres data survives every redeploy: data lives in a named volume with a fixed name (`external: true` or explicit `name:`), the deploy only recreates the API container (never `docker compose down -v`, never removes or renames the volume), and Postgres is not restarted on a normal release
- [ ] Before migrations, the deploy takes a `pg_dump` backup to a dated file on the host (kept N days, outside git); a failed migration leaves the DB and the old container untouched, and the runbook has a tested restore command
- [ ] `docs/deploy.md` runbook: flow, required repo/environment secrets, one-time setup, Cloudflare Tunnel for `api-admin.mmlabs.online`, rollback steps
- [ ] CI stays green; the release workflow is validated (YAML parses, version guard tested on sample tags)

## Notes / answers from owner
- 2026-11-10: implemented directly (outside `/run`) at the owner's request: `release.yml`, `Dockerfile` (no auto-migrate), `docs/deploy.md` here, and a registration PR on `mmlabs-online/deployment`. No plan/review files. Divergences from the criteria above, following the deployment repo's conventions:
  - release is triggered by a **tag `vX.Y.Z` on main** (the shared `docs/app-release.yml` template), not a `release/vX.Y.Z` branch;
  - Postgres is the deployment repo's **shared Postgres 17** (database/role `org_admin`, external volume `pgdata`, nightly + offsite backups), not a separate Postgres service — still compose on the Mac runner;
  - the pre-migration backup uses the shared `pg-backup` sidecar (`/backup.sh`).
- Remaining before DONE: owner one-time setup in `docs/deploy.md`, first tag `v0.1.0`, and a healthy `/healthz` on api-admin.mmlabs.online.
- 2026-11-10: hostname is `api-admin.mmlabs.online`.
- 2026-11-10: deploy to the same Mac self-hosted runner (`macbook-runner`) as stoktok, Docker Compose, Cloudflare Tunnel. Postgres 16 runs as a service in the same compose stack on the Mac (persistent named volume, see acceptance criteria). Follow stoktok's `docs/deploy.md` and `deploy/deployment-repo/` as the reference.
- 2026-11-10: no Cloudflare Access gate needed before ADM-002 (spec §6.1); the endpoints are public until auth ships — accepted by owner.
