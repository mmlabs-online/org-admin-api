# Ming's to-do (things only Ming can do)

Agents read this before `/run`. A ticket waits on an item only if it lists the item in `Needs Ming`.
Tick (`- [x]`) and date items when done. `/run` adds plan-approval and escalation items here.
Never write a secret value here: name the secret and where Ming puts it.

## Always
- Review and merge the PRs that `/run` opens (`/status` lists them). Agents never merge.

## Open
- [ ] M-1 (2026-11-19, for ADM-027/ADM-030 in production): in the Google account TuDoCuaTui uses, create a separate OAuth client for org-admin-api (scope `drive.file`), obtain its refresh token, and create a new root Drive folder for bug-report attachments. Put the four values as repository secrets in `mmlabs-online/deployment` (names set by ADM-027's plan). Never paste values here.
