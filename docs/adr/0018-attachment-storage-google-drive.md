# ADR-0018: Attachment storage in Google Drive

Status: Accepted on merge (owner approved the plan 2026-11-19)
Date: 2026-11-19

## Context
Spec §5.12 (OQ23, OQ34, OQ35) stores bug-report screenshots in Google Drive, in an account the
owner holds. ADM-027 adds the client; ADM-030 adds the route and the attachment table.

## Decision
1. **Client.** Drive v3 over plain `httpx`, no Google SDK. OAuth scope `drive.file`. Hosts are
   constants in code, so the bearer token cannot be sent elsewhere; redirects are not followed.
2. **Config (R5).** `GOOGLE_DRIVE_CLIENT_ID`, `GOOGLE_DRIVE_CLIENT_SECRET`,
   `GOOGLE_DRIVE_REFRESH_TOKEN`, `GOOGLE_DRIVE_ROOT_FOLDER_ID`, all or none. None = attachments
   off (a typed `DriveError(not_configured)`, never a startup failure, a WARNING in
   staging/production). A partial or malformed set fails startup naming the variables only. The
   secret, the token and the folder id are `SecretStr`. Deployment secrets are
   `ORG_ADMIN_GOOGLE_DRIVE_*`, written unprefixed.
3. **Layout.** Flat: `<root>/<app key>/<attachment id>`; `report_id` is in `appProperties`. One
   Drive create per upload, no per-report folders. The root is created by
   `attachments-init-root` because `drive.file` cannot see a folder made by hand.
4. **`appProperties` contract.** Every file carries `mm_service=org-admin-api`,
   `kind=attachment|app_folder|root`, and for attachments `app_key`, `report_id`,
   `attachment_id`. The sweep and folder lookup only trust this marker.
5. **Errors.** `DriveError` carries one code: `not_configured`, `auth_failed`, `quota_exceeded`,
   `rate_limited`, `not_found`, `too_large`, `rejected`, `unavailable`, `invalid_input`. It is not
   an `AppError`; ADM-030 decides the outcome (the report stays accepted as text). Errors are
   raised with no cause or context; Google's text, ids, tokens and URLs are never logged.
6. **Retries.** Creates (files and folders) are never retried, also not after a 401. Reads,
   deletes, listings and the token refresh retry up to 3 times with jittered backoff; a 401
   refreshes the token once. The 5 MiB cap is enforced on the byte stream, not on headers.
7. **Consistency.** The DB row is written after Drive accepts the file; if it fails, the caller
   deletes the Drive file. A failed delete leaves an orphan, which the sweep removes.
8. **Sweep.** `attachments-sweep` is a dry run by default. `--apply` deletes permanently, only
   files with our marker, older than the grace period (default 48 h, floor 24 h), that no
   attachment row references, at most `--max-deletes`. It fails closed: no reference checker,
   a failing checker or too many candidates delete nothing. Deletes are permanent, as spec §5.12
   says.
9. **Output.** The CLI prints our attachment ids and app keys, never Drive ids, with one
   exception: `attachments-init-root` prints the new root folder id once, to the owner's
   terminal, because it must be stored as a secret.
10. **Custody.** The owner holds the Google account and rotates the refresh token (runbook in
    `docs/deploy.md`).

## Risks
- Storage quota is shared with TuDoCuaTui, Gmail and Photos.
- A refresh token issued under a consent screen in "Testing" expires after 7 days; the screen must
  be published.
- `drive.file` access is most likely scoped to the Cloud project, so the OAuth client lives in a
  separate project from TuDoCuaTui's.
