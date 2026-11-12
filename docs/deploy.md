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
- **`IDENTITY_HMAC_KEY`** (USR-03): secret key for the per-email login failure counters
  (`login_failures.email_hmac` = HMAC-SHA256 of the normalized email; ADR-0002 §5). At least 32
  random characters, e.g. `python -c "import secrets; print(secrets.token_urlsafe(48))"`. In
  staging/production **both `migrate` and the API refuse to start** without it (or with a shorter
  value), so set it before deploying the release that contains USR-03.
  - It is a secret: put it in the env file written by the deploy (with `DATABASE_URL`, from the
    deployment repo's secrets), never in `compose.yml`, the image or logs.
  - Never rotate it casually: a new key orphans every stored counter, which resets login backoff
    and the Turnstile-after-3 requirement for every email (old rows become dead weight).

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

## Bootstrap the first super-admin

The first `admin:super-admin` is granted from the command line (USR-02f), never over HTTP. The CLI
creates no users, takes no password and never marks an email verified. Re-running it is safe.

**Prerequisites:** migrations are at head (the seeded `admin` app and role exist), and
`TRUSTED_PROXY_CIDRS` is set in `apps/org-admin-api/compose.yml` → `x-app.environment` (see
"Required runtime config"). Without it settings refuse to load in production and the CLI exits 1
(`error: settings could not be loaded`), like `migrate` and the API.

1. **Sign up yourself** through the public sign-up, then **log in once** (after USR-03) to prove you
   control the password. Never grant to an account someone else could have registered in your
   name (pre-registration hijack).
2. **Run on the Mac**, from the deployment repo checkout the deploy uses. Start the line with a
   space so zsh keeps it out of history (`setopt HIST_IGNORE_SPACE`), or run `fc -p` first:

   ```bash
    docker compose -p org-admin-api -f apps/org-admin-api/compose.yml --env-file apps/org-admin-api/release.env \
      run --rm --no-deps migrate /app/.venv/bin/python -m app.cli grant-super-admin --email you@example.com
   ```

   This reuses the one-shot `migrate` service (only on the `db` network) with its command
   replaced. `--rm` removes the container (the email is visible in `ps`/`docker inspect` only while
   it runs); `--no-deps` leaves Postgres untouched. Required env is the same as `migrate`:
   `DATABASE_URL` (env file written by the deploy), `APP_ENV=production`, `TRUSTED_PROXY_CIDRS`.
   **Never set `DB_ECHO`** (the CLI refuses it). `ADMIN_RBAC_API_ENABLED` is not needed.
3. **Check the output** (the email is masked):

   ```
   granted admin:super-admin            (or: already held admin:super-admin)
   user_id: <uuid>
   email: y***@e***.com
   created_at: <timestamp>
   email_verified: no
   check: created_at must match your own sign-up; if not, revoke this grant (docs/deploy.md)
   ```

   `created_at` must match the time of your own sign-up; `email_verified` stays `no` until USR-04.
   If `created_at` is earlier than your sign-up, the account is not yours: revoke the grant with
   the one-off `psql` from "Operations" (`DELETE FROM role_assignments WHERE id = …`) and
   investigate.

| Exit | Meaning |
|---|---|
| 0 | granted, or already held |
| 1 | any other failure (settings, `DB_ECHO`, missing seed, database); one fixed line, no details |
| 2 | no account with this email: sign up first |
| 3 | the account is disabled; nothing granted |
| 64 | usage error (bad arguments or invalid email) |

**After USR-04 ships:** verify the email, then reset the password (this revokes every device session).

Locally: `make grant-super-admin email=you@example.com`.
