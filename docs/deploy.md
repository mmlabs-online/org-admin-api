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

**In production the deploy writes all of these itself** (`mmlabs-online/deployment`,
`deploy-org-admin-api.yml` → step `envfile`, on every run): `TRUSTED_PROXY_CIDRS` from
`docker network inspect web`; `IDENTITY_HMAC_KEY` and `JWT_SIGNING_KEY` generated **on the Mac** the
first time and kept in `~/.config/org-admin-api/secrets/` (`identity_hmac_key`,
`jwt_signing_key.b64`, mode 0600), never in git, GitHub secrets or logs; `JWT_ISSUER` in
`apps/org-admin-api/compose.yml`. Nothing to set by hand; the details below are for other
environments and for rotation.

- **`TRUSTED_PROXY_CIDRS`** (ADM-011): the CIDR(s) of the Docker network cloudflared reaches the
  API on (`web`). Without it every client shares one rate-limit bucket, so in staging/production
  **both `migrate` and the API refuse to start** when it is empty or unsafe (deploy step 5 fails,
  the old API keeps running).
  - Production: written into the env file by the deploy from the `web` network's subnet(s). Not a
    secret.
  - Format: a **JSON list**, e.g. `TRUSTED_PROXY_CIDRS=["172.18.0.0/16"]` in an env file (quote it,
    `'["…"]'`, in YAML). A bare `172.18.0.0/16` fails to parse.
  - Rules: private networks only, no `/0`, IPv4 prefix ≥ /8, IPv6 prefix ≥ /48.
  - Find the subnet: `docker network inspect web -f '{{(index .IPAM.Config 0).Subnet}}'`. Better:
    pin the `web` subnet, give cloudflared a fixed IP and trust that `/32`.
- **`IDENTITY_HMAC_KEY`** (USR-03): secret key for the per-email login failure counters
  (`login_failures.email_hmac` = HMAC-SHA256 of the normalized email; ADR-0002 §5). At least 32
  random characters, e.g. `python -c "import secrets; print(secrets.token_urlsafe(48))"`. In
  staging/production **both `migrate` and the API refuse to start** without it (or with a shorter
  value), so set it before deploying the release that contains USR-03.
  - It is a secret. Production: generated on the host by the deploy (64 hex chars) and written to
    the env file; never in `compose.yml`, the image or logs.
  - Never rotate it casually: a new key orphans every stored counter, which resets login backoff
    and the Turnstile-after-3 requirement for every email (old rows become dead weight).
- **`JWT_SIGNING_KEY`** (USR-03c, ADR-0005): the access-token signing key, **base64 of a PEM
  P-256 private key on one line**. Secret: put it in the env file written by the deploy (like
  `IDENTITY_HMAC_KEY`), never in `compose.yml`, the image or logs.
  - Production: generated on the host by the deploy the first time. Elsewhere generate with
    `openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 | base64 | tr -d '\n'`.
    Use a different key per environment.
  - In staging/production **both `migrate` and the API refuse to start** without a valid key, so
    the deployment repo must provide it **before** the release PR that contains USR-03c is merged.
    Otherwise deploy step 5 fails and the old API keeps running.
  - The `kid` is derived from the key (RFC 7638 thumbprint); the API logs it at startup
    (`jwt_keys_loaded signing_kid=…`), never the key.
- **`JWT_ISSUER`** (USR-03c): the token `iss`, `https://api.mmlabs.online` in production. Not a
  secret: set it in `apps/org-admin-api/compose.yml` → `x-app.environment`. Required in
  staging/production, `https://<host>` with no path, port or trailing slash. **Never change it**:
  every consumer pins it.
- **`JWT_PUBLIC_KEYS_EXTRA`** (USR-03c): JSON list (in a quoted string, like
  `TRUSTED_PROXY_CIDRS`) of base64 public PEMs that the JWKS publishes next to the signing key.
  Not a secret (`compose.yml`). Empty except during a rotation (below).

Done in `mmlabs-online/deployment` (PRs #52, #53, 2026-11-12) before the first release with
USR-03c.

## Signing key rotation

Tokens live 900 s; the JWKS is cached for `max-age=300` by clients and at most 1 h by consumers
(`docs/consumers.md`).

**Planned rotation:**

1. Generate the next key (`NEXT`, as above) and derive its public key:
   `echo "$NEXT" | base64 -d | openssl pkey -pubout | base64 | tr -d '\n'`. Add that value to
   `JWT_PUBLIC_KEYS_EXTRA` and redeploy. Check that
   `https://api.mmlabs.online/.well-known/jwks.json` lists two kids. Wait at least the JWKS
   `max-age` (300 s) plus the longest consumer cache (≤ 1 h).
2. Make the next key the signer (`JWT_SIGNING_KEY` = `NEXT`), replace the extra entry with the
   **old** public key, and redeploy.
3. After the TTL (900 s) + leeway (60 s) + `max-age` (300 s), about 25 minutes, remove the old
   public key from `JWT_PUBLIC_KEYS_EXTRA` and redeploy.

**Emergency rotation** (key leaked): delete `~/.config/org-admin-api/secrets/jwt_signing_key.b64`
on the host (or, without host access, change the deploy to regenerate it) and redeploy with no
extra key; the deploy generates a new one. Then restart or flush the JWKS cache of every consumer
(ricotdin, …): until a verifier refetches, it still trusts the leaked key. After that, live tokens
fail at verifiers; clients get a 401 from their backend and call `/token` again,
which the cookie still allows, and verifiers refetch the JWKS on the unknown `kid`. Expect about one
failed request per active client. Never keep a leaked public key in the JWKS.

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
`TRUSTED_PROXY_CIDRS` is in the env file (written by the deploy, see "Required runtime
config"). Without it settings refuse to load in production and the CLI exits 1
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
