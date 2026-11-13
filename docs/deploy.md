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

## Email (USR-04)

Verification links are sent through Gmail SMTP (`smtp.gmail.com:587`, STARTTLS required,
certificate checked) behind `EmailSender` (ADR-0007). Sending happens in-process after the
request commits, with retry; a restart loses queued mail and users press resend. On a clean stop
the API drains queued mail for up to 5 s, then logs `email_dropped reason=shutdown count=N`
(USR-04d); a message cut off mid-send may still have been delivered.

| Key | Secret | Notes |
|---|---|---|
| `SMTP_USERNAME` | no (but keep it out of git) | Gmail address of the shared mmlabs sender account. Required in staging/production. |
| `SMTP_PASSWORD` | **yes** | Gmail **App Password** (16 letters). Required in staging/production. |
| `EMAIL_FROM` | no | Optional; defaults to `SMTP_USERNAME` (Gmail rewrites other From addresses). |
| `EMAIL_DAILY_CAP` | no | Global sends per rolling 24 h, default 300. |
| `SMTP_HOST`, `SMTP_PORT` | no | Defaults `smtp.gmail.com`, `587`. |
| `VERIFY_EMAIL_RATE_LIMIT`, `VERIFY_EMAIL_RATE_WINDOW_SECONDS` | no | Per-IP limit on `POST /auth/verify-email`, default 20 / 60 s. |

In staging/production **both `migrate` and the API refuse to start** without `SMTP_USERNAME` and
`SMTP_PASSWORD`. Error messages name the key, never the value. Email links always use the
requesting app's validated `Origin` (USR-04c); there is no link-base key.

### Deploy prerequisites (owner, separate PR in `mmlabs-online/deployment`)

Merge this **before** the release PR that contains USR-04, otherwise `migrate` refuses to start
and the old API keeps running. A value written by hand on the Mac is lost on the next deploy (the
env file is rewritten every run).

1. On the shared Gmail account (the one the mmlabs site uses, owner note 2026-11-12) create a
   **separate App Password** named for org-admin-api, so it can be revoked without touching the
   site.
2. Add GitHub Actions secrets `ORG_ADMIN_SMTP_USERNAME` and `ORG_ADMIN_SMTP_PASSWORD` in the
   deployment repo, ideally in an Environment that only `main` can deploy.
3. `deploy-org-admin-api.yml`, step `envfile`: pass them through `env:`; fail before `pull` when
   missing; validate (address regex; App Password `^[a-z]{16}$` after removing spaces) so a newline
   cannot inject lines; write with `printf 'SMTP_USERNAME=%s\n'` / `printf 'SMTP_PASSWORD=%s\n'`;
   update the "No GitHub secrets" comment and the README.
4. `apps/org-admin-api/compose.yml` → `x-app.environment`: optional `EMAIL_FROM`,
   `EMAIL_DAILY_CAP`. No link-base key is needed: `EMAIL_LINK_BASE_URL` was removed (USR-04c), a
   leftover value is ignored, so that compose line can be dropped at any time.

### Gmail limits (runbook)

- Gmail allows about **500 messages/day** on a consumer account (about 2,000 on Workspace), counted
  over a rolling 24 h and **shared with the mmlabs site**. `EMAIL_DAILY_CAP` (default 300) keeps
  this service below that.
- Symptoms: log `email_quota_exceeded` (Gmail answered `550 5.4.5`; sending pauses for 1 h and
  queued mail is dropped with `email_dropped reason=paused`), `email_not_sent reason=daily_cap`
  (our own cap), users getting 429 on resend.
- What to do: wait for the rolling window to pass (users can resend later; limits still apply);
  find the traffic source (sign-up spikes, the site's own mail); lower or raise `EMAIL_DAILY_CAP`
  (redeploy); for lasting volume move to Workspace or another provider.
- **Switch provider:** add a new `EmailSender` implementation and a branch in
  `build_email_sender` (`app/modules/email/sender.py`) with its env keys; no API or schema change.
- **App Password rotation:** create a new App Password, update the GitHub secret, redeploy, then
  revoke the old one. A revoked or wrong password logs `email_auth_failed` and pauses sending for
  1 h.
- **Smoke check after deploy:** sign up a test address and look for `email_sent purpose=verify`
  in the API logs (startup logs `email_sender=gmail`). Logs never contain addresses, links or
  tokens.

### Email alerts (runbook)

Check the last day's events (no log aggregation exists today; wiring these patterns into an
alerting tool is a follow-up):

```bash
docker compose -p org-admin-api logs --since 24h org-admin-api | grep -E 'email_(not_sent|quota_exceeded|auth_failed|dropped)'
```

| Event | Meaning | Check | Action |
|---|---|---|---|
| `email_quota_exceeded` | Gmail answered `550 5.4.5`; sending paused 1 h | Gmail account's sent volume, the site's own mail | **Same day.** See "Gmail limits" above |
| `email_auth_failed` | App Password revoked or wrong; sending paused 1 h | GitHub secret and the App Password on the Gmail account | **Same day.** Rotate per "Gmail limits" above |
| `email_not_sent reason=daily_cap` | Our total `EMAIL_DAILY_CAP` is reached | Sign-up spikes, forgot floods (per-IP 429s) | Find the source; consider `EMAIL_DAILY_CAP` |
| `email_not_sent reason=purpose_cap purpose=reset` | Reset used its 50 % share | Per-IP 429s and Turnstile failures on forgot | Repeated: likely forgot abuse; verification keeps its share |
| `email_not_sent reason=purpose_cap purpose=verify` | Verification used its 80 % share | Sign-up volume | Repeated: sign-up spam; reset keeps its share |
| `email_dropped reason=paused\|queue_full\|shutdown` | Mail dropped while paused, with a full queue, or at a stop | The event before it (quota/auth), traffic, deploy time | Users resend; investigate repeated `queue_full` |

Alert rule: any `email_quota_exceeded` or `email_auth_failed` → act the same day;
`daily_cap` / `purpose_cap` more than a few per day → investigate. Logs carry event, purpose and
user id only, never addresses, links or tokens.

## Browser origins (CORS)

No env var (USR-03e, ADR-0006). The API echoes the origins of **active** apps (`apps.origins`,
D19) with credentials, for `/api/v1/auth/*` only. Register or update an app's origins with the
USR-07 CLI, see "Register an app" below (or the admin API later). Changes made through the API process apply at once; changes
from the CLI or manual SQL apply within 60 s. Loopback origins are ignored in staging/production.
A stale `CORS_ORIGINS` in the deployment env file is ignored and can be deleted from
`mmlabs-online/deployment`.

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
6. **Ops workflow environment:** in `mmlabs-online/deployment` create the GitHub Environment
   `org-admin-api-ops` (required reviewer: owner; deployment branches: `main` only). The
   `ops-org-admin-api` workflow (register-app, grant-super-admin, cleanup) waits for that
   approval.

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
2. **Run the ops workflow:** in `mmlabs-online/deployment`, Actions → `ops-org-admin-api` → Run
   workflow (branch `main`) → command `grant-super-admin`, `email` = your address; approve the
   `org-admin-api-ops` environment; read the output in the run summary. The email is masked in the
   log, but the input stays readable in the run's event payload by anyone with read access to the
   deployment repo, which must stay private.

   **Fallback** with a shell on the Mac, from the deployment repo checkout the deploy uses. Start
   the line with a space so zsh keeps it out of history (`setopt HIST_IGNORE_SPACE`), or run
   `fc -p` first:

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

   `created_at` must match the time of your own sign-up; `email_verified` stays `no` until you
   open the verification link (USR-04).
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

**After USR-04 ships:** verify the email via the link in the verification email (resend from a
signed-in app if it expired). If you don't know the password, use "forgot password" in an app,
then reset it from the emailed link (USR-04b): this ends every signed-in session of the account
(its account sessions on every device) and also marks the email verified. Access tokens already
issued stay valid for at most 15 minutes (`ACCESS_TOKEN_TTL_SECONDS`).

Locally: `make grant-super-admin email=you@example.com`.

## Register an app (USR-07)

A consuming app (key, name, browser origins, join policy, org mode, `<key>:member` role) is
registered from the CLI, without the flag-gated RBAC admin API. Rules live in
`AppService.register`; the CLI only parses and prints.

**Prerequisites:** a release containing USR-07 is deployed; the `org-admin-api-ops` environment
exists (One-time setup, step 6).

1. In `mmlabs-online/deployment`: Actions → `ops-org-admin-api` → Run workflow (branch `main`) →
   command `register-app`, inputs `key`, `name`, `origins` (comma- or space-separated), optional
   `permissions`, `join_policy` / `org_mode` (`keep` = not passed), `dry_run` (default on).
2. Approve the `org-admin-api-ops` environment; read the output in the run summary.
3. Always run with `dry_run` on first and check the `+` / `-` lines (a `- origin` line removes an
   origin), then run again with `dry_run` off.

**Worked example: ricotdin** (owner, 2026-11-12: separate app keys, so staging tokens are never
valid on the prod backend; both `open`, `none`; no permissions yet):

| Input | ricotdin | ricotdin-staging |
|---|---|---|
| `key` | `ricotdin` | `ricotdin-staging` |
| `name` | `Ricotdin` | `Ricotdin Staging` |
| `origins` | `https://ricotdin.mmlabs.online` | `https://ricotdin-staging.mmlabs.online` |
| `join_policy` / `org_mode` | `open` / `none` | `open` / `none` |

Expected output (first real run; a dry run adds the `dry-run: …` line, a re-run prints
`unchanged app …` and no change lines):

```
created app ricotdin
name: Ricotdin
join_policy: open
org_mode: none
origins: https://ricotdin.mmlabs.online
member role: ricotdin:member = (none)
+ origin https://ricotdin.mmlabs.online
note: the API applies origin changes within 60 s
dry-run: rolled back, nothing was saved      (only with dry_run)
```

The member role starts empty, so ricotdin tokens carry `roles: ["ricotdin:member"]` and no
permissions. Add ricotdin's permission keys later by re-running with `permissions` (e.g.
`ricotdin:app:use`).

**Semantics:**

- Origins **replace** the stored set (at least one); same set in another order is unchanged.
- `join_policy` / `org_mode` change only when passed; `org_mode` of an existing app is never
  changed (exit 1).
- Permissions are only **added**, always `is_admin=false`; `<key>:*`, another app's prefix and an
  existing `is_admin` permission are refused. Adding one rebuilds `<key>:member` from all
  non-admin permissions, which drops permissions removed from it by hand (D9). A re-run without
  a new permission never touches the member role.
- `status` and the default role never change; a **disabled** app is refused (exit 3). The `admin`
  app is refused.
- An origin belongs to one app (any status, `admin` included); loopback origins are refused in
  staging/production.
- The API applies origin changes within 60 s (CORS cache). Separate app keys mean separate token
  audiences.
- Until ADM-004 the workflow run (actor, time, inputs, summary) is the audit trail.

| Exit | Meaning |
|---|---|
| 0 | created, updated or unchanged (also a dry run) |
| 1 | settings, `DB_ECHO`, origin held by another app, `org_mode` change, `is_admin` permission, concurrent change, database; one fixed line, no details |
| 3 | the app is disabled; nothing changed |
| 64 | usage or validation error (bad key, name, origin or permission; `admin`; loopback in staging/production) |

**Fallback** with a shell on the Mac (same command the workflow runs):

```bash
docker compose -p org-admin-api -f apps/org-admin-api/compose.yml --env-file apps/org-admin-api/release.env \
  run --rm --no-deps migrate /app/.venv/bin/python -m app.cli register-app \
  --key ricotdin --name Ricotdin --origin https://ricotdin.mmlabs.online --join-policy open --org-mode none --dry-run
```

Locally: `make register-app ARGS='--key ricotdin --name Ricotdin --origin https://ricotdin.mmlabs.online'`.

## Email retention cleanup (USR-04d)

`python -m app.cli cleanup [--dry-run]` deletes:

- `email_send_log` rows older than **48 h** (the per-email window only reads the last 24 h);
- `email_tokens` rows older than **7 d** that are **used or expired** (the global daily cap counts
  the tokens of the last 24 h).

Both ages have a 24 h floor in code, so a change can never break the limits. Deletes run in
batches of 1000 (`FOR UPDATE SKIP LOCKED`, `lock_timeout` 2 s, one commit per batch); the job
takes no lock on `users` and never waits on live requests. Recommended cadence: **weekly**
(volume is bounded by `EMAIL_DAILY_CAP`).

1. In `mmlabs-online/deployment`: Actions → `ops-org-admin-api` → Run workflow (branch `main`) →
   command `cleanup`, with `dry_run` on first (available once the deployment PR that adds the
   `cleanup` choice is merged).
2. Approve the `org-admin-api-ops` environment; read the counts in the run summary, then run
   again with `dry_run` off.

Output (counts only; a dry run says `would delete` and adds `dry-run: nothing was deleted`):

```
email_send_log: deleted <n> (older than 48 h)
email_tokens: deleted <n> (used or expired, older than 7 d)
```

| Exit | Meaning |
|---|---|
| 0 | done (also a dry run) |
| 1 | settings, `DB_ECHO`, database; one fixed line. Batches already committed stay deleted; running again is safe |
| 64 | usage error (only `--dry-run` is accepted) |

**Fallback** with a shell on the Mac (same command the workflow runs):

```bash
docker compose -p org-admin-api -f apps/org-admin-api/compose.yml --env-file apps/org-admin-api/release.env \
  run --rm --no-deps migrate /app/.venv/bin/python -m app.cli cleanup --dry-run
```

Locally: `make cleanup ARGS=--dry-run`.

A scheduled run (a separate `schedule:` workflow that can run only `cleanup`, in its own
environment without a required reviewer, since the ops approval gate would block a cron run) is
an open owner decision.
