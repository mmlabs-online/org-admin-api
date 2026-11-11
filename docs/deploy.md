# Deploy runbook (ADM-007)

org-admin-api runs on the mmlabs home server (Mac, self-hosted runner `macbook-runner`) and is served at
**https://api.mmlabs.online** through the shared Cloudflare Tunnel. How it runs is defined in
`mmlabs-online/deployment` (`apps/org-admin-api/`, `.github/workflows/deploy-org-admin-api.yml`).
Only the owner merges in either repository.

## Flow

```
push branch release/vX.Y.Z (from main) ─► release.yml (GitHub runner)
                                   ├─ check: commit on main, version new and higher than every tag; create tag vX.Y.Z
                                   ├─ build image mingming21400/org-admin-api:vX.Y.Z (linux/amd64), push to Docker Hub
                                   ├─ smoke test: run the pushed image, GET /healthz
                                   └─ open PR release/org-admin-api-vX.Y.Z on mmlabs-online/deployment
                                        (only apps/org-admin-api/release.env: image@digest + version)
owner merges that PR ─────────────► deploy-org-admin-api (Mac runner)
                                   1. check release.env
                                   2. write ~/.config/org-admin-api/org-admin-api.env (DATABASE_URL)
                                   3. pull image
                                   4. pg_dump of all databases (pg-backup sidecar), right before migrating
                                   5. alembic upgrade head (one-shot `migrate` service)   ── fail → stop, old API keeps running
                                   6. replace the API, wait for /readyz                   ── fail → restore previous image, push revert
```

Release a version: create a branch `release/vX.Y.Z` from the current `main` and push it. Either on
GitHub (branch dropdown → type `release/v0.1.0` → "Create branch release/v0.1.0 from main"), or:

```bash
git fetch origin && git push origin origin/main:refs/heads/release/v0.1.0
```

The workflow refuses a commit that is not on main, a version that already exists or is not higher
than the latest tag, and a branch name that is not `release/vX.Y.Z`. It creates the tag itself.
`/healthz` then reports `"version": "v0.1.0"`. Never commit to a release branch directly.

## Database

- The **shared Postgres 17** of the deployment repo (`postgres/`), database and role **`org_admin`**, on the
  internal `db` network. No port is published. Created by `postgres/scripts/add-app-db.sh org_admin`.
- Data lives in the shared external volume `pgdata`. Redeploys only recreate the `org-admin-api`
  container; Postgres is not touched, so **data survives every redeploy**. Never run
  `docker compose down -v` or any `docker volume rm/prune` on the server (the deployment repo's
  `volume-safety` check blocks them in PRs).
- Backups: nightly dumps (7 daily / 4 weekly / 6 monthly) + encrypted offsite copy, plus one dump at
  every deploy before migrations. Restore: `ops/backup/RESTORE.md` in the deployment repo.
- The password is committed in the (private) deployment repo at `postgres/secrets/apps/org_admin.password`,
  as for every app there. The deploy turns it into `DATABASE_URL` on the host; it never appears in logs.

## Required runtime config

- **`TRUSTED_PROXY_CIDRS`** (ADM-011): the CIDR(s) of the Docker network cloudflared reaches the
  API on (`web`). Without it every client shares one rate-limit bucket, so in staging/production
  **both `migrate` and the API refuse to start** when it is empty or unsafe (deploy step 5 fails,
  the old API keeps running).
  - Set it in `apps/org-admin-api/compose.yml` → `x-app.environment` (read by `migrate` and the
    API). It is not a secret. Not in the env file: the deploy rewrites that file on every run.
  - Format: a **JSON list in a quoted string**, e.g. `TRUSTED_PROXY_CIDRS: '["172.18.0.0/16"]'`.
    A bare `172.18.0.0/16` fails to parse.
  - Rules: private networks only, no `/0`, IPv4 prefix ≥ /8, IPv6 prefix ≥ /48.
  - Find the subnet: `docker network inspect web -f '{{(index .IPAM.Config 0).Subnet}}'`. Better:
    pin the `web` subnet, give cloudflared a fixed IP and trust that `/32`.

## Migration rules

A rollback restarts the **previous image on the new schema** (it never migrates down). So every
migration must keep the previous release working:

- Expand first, contract later: add nullable columns / new tables in release N; drop or rename only in
  a later release, after nothing reads the old shape.
- No long-locking operations on large tables without a plan in the ticket.
- If a migration fails half-way, the API is not replaced; restore from the dump taken in step 4 if needed.

## One-time setup (owner)

1. **Merge the deployment-repo PR** that registers the app (adds `apps/org-admin-api/`, the
   `deploy-org-admin-api` workflow and the `org_admin` database). Merging runs `deploy-postgres`,
   which creates the role and database on the running Postgres. `deploy-org-admin-api` does nothing
   until the first release.
2. **Docker Hub:** create the repository `mingming21400/org-admin-api` (private or public).
3. **Secrets in this repo** (Settings → Secrets and variables → Actions):
   - `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` — push to `mingming21400/org-admin-api`.
   - `DEPLOY_REPO_TOKEN` — fine-grained PAT on `mmlabs-online/deployment` only: Contents read/write,
     Pull requests read/write. (The same kind of token stoktok/calechip use.)
4. **Cloudflare:** in the tunnel used by `proxy/` (Zero Trust → Networks → Tunnels → the mmlabs
   tunnel → Public hostnames), add `api.mmlabs.online` → `HTTP` → `org-admin-api:8000`.
   No Cloudflare Access application (owner decision, 2026-11-10 — the API has no auth until ADM-002).
5. Push the branch `release/v0.1.0` from main, merge the release PR it opens, and check
   `https://api.mmlabs.online/healthz`.

## Operations

```bash
# on the Mac
docker compose -p org-admin-api ps
docker compose -p org-admin-api logs -f --tail 100 org-admin-api
# redeploy what main pins (also rewrites the env file): Actions → deploy-org-admin-api → Run workflow (main)
# one-off psql
docker compose -p postgres -f ~/srv/postgres/compose.yml exec postgres psql -U postgres -d org_admin
```

**Roll back by hand:** revert the release PR in the deployment repo and merge the revert. The deploy
runs the previous image (migrations are kept, see the rules above).
