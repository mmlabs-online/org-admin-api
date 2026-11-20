# Ming's to-do (things only Ming can do)

Agents read this before `/run`. A ticket waits on an item only if it lists the item in `Needs Ming`.
Tick (`- [x]`) and date items when done. `/run` adds plan-approval and escalation items here.
Never write a secret value here: name the secret and where Ming puts it.

## Always
- Review and merge the PRs that `/run` opens (`/status` lists them). Agents never merge.

## Open
- [ ] M-1 (2026-11-19, for ADM-027/ADM-030 in production): follow `docs/deploy.md` → "Attachments — Google Drive (ADM-027)". In the Google account TuDoCuaTui uses, create a **new Google Cloud project** (not TuDoCuaTui's) with its own OAuth client (scope `drive.file`) and **publish the consent screen** ("In production"; in "Testing" the refresh token expires after 7 days). Mint a refresh token, run `uv run python -m app.cli attachments-init-root` locally to create the root folder (it prints the folder id). Add the four values as repository secrets in `mmlabs-online/deployment`: `ORG_ADMIN_GOOGLE_DRIVE_CLIENT_ID`, `ORG_ADMIN_GOOGLE_DRIVE_CLIENT_SECRET`, `ORG_ADMIN_GOOGLE_DRIVE_REFRESH_TOKEN`, `ORG_ADMIN_GOOGLE_DRIVE_ROOT_FOLDER_ID` (the envfile step writes them unprefixed, all four or none). After deploy check the log `attachment_store=drive` and run `attachments-sweep` (dry run). Never paste values here.
- [ ] M-2 (2026-11-20, for ADM-034 after merge + deploy): register an app row `admin-pan` via `POST /api/v1/apps` (no roles, no manifest key) — `admin` now means org-admin-api only. Then issue a `releases` app credential (`POST /api/v1/apps/{key}/credentials` with body `{"scope":"releases"}`) for `admin` and one for `admin-pan`; store each as `MMLABS_RELEASE_CREDENTIAL` in a GitHub Environment limited to release branches / `main` of its own repo. Never paste values here.
