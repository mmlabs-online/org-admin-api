# ADM-007: Release and deploy pipeline to api-admin.mmlabs.online

| Field | Value |
|---|---|
| State | BACKLOG |
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
- [ ] `docs/deploy.md` runbook: flow, required repo/environment secrets, one-time setup, Cloudflare Tunnel + Access for `api-admin.mmlabs.online`, rollback steps
- [ ] CI stays green; the release workflow is validated (YAML parses, version guard tested on sample tags)

## Notes / answers from owner
- 2026-11-10: hostname is `api-admin.mmlabs.online`.
- Open (spec §8 Q5): same Mac self-hosted runner + Cloudflare Tunnel as stoktok? Postgres inside the compose stack on that host, or an existing/managed Postgres? The planner will BLOCK on these if they change the design.
- Until ADM-002 lands, the hostname must be behind Cloudflare Access (spec §6.1).
