# ADM-027: Google Drive attachment client for bug reports

| Field | Value |
|---|---|
| State | BLOCKED |
| Priority | P2 |
| Size | M |
| Areas | architecture, security |
| Gate | plan-approval |
| Depends on | — |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-19 |
| Claimed by | — |
| PR | — |

## Goal
This API can store and read bug-report attachments in Google Drive, the way TuDoCuaTui stores photos, so later tickets can upload and serve images without local disk.

## Scope reference
`docs/spec-v1.md` §5.12 "Attachment storage — Google Drive" (owner, 2026-11-19, OQ23, OQ34, OQ35). Reference implementation: `mmlabs-online/TuDoCuaTui` `server/drive.js`, `server/README.md` (Google Drive, Drive layout, Photo sweep), its D27/D28.

## Scope
- Drive v3 REST client over `httpx` (no new library), OAuth scope `drive.file`: create (multipart, ≤ 5 MB), read (stream), delete, find/create folder by `appProperties`.
- Config (R5): four all-or-nothing env keys for this API's own OAuth client id/secret, refresh token and root folder id (names set at PLAN); none set = attachments off; partial or malformed = startup error naming variables only; `.env.example` updated.
- Layout `<root>/<app_key>/…` (per-report split decided at PLAN); files tagged with `appProperties`.
- Errors are fixed codes only (no Google text, URLs, ids or tokens in logs or error details, R5/R11); creates never retried; other calls bounded retries.
- Dry-run-by-default sweep CLI that deletes Drive files no DB row references after a grace period (plugged in once ADM-030 creates rows).

## Out of scope
- Bug-report tables and endpoints (ADM-028..030). Video / resumable upload (§5.7 Later).

## Acceptance criteria
- [ ] With no Drive env keys the app starts and the client reports "not configured"; a partial or malformed set fails startup naming only the variables.
- [ ] Create/read/delete/folder lookup work against a fake Drive in tests; the 5 MB cap is enforced on the stream.
- [ ] No Google response text, Drive id, token or URL appears in logs or error details (test).
- [ ] Sweep CLI: dry-run lists, `--apply` deletes only unreferenced files older than the grace period.
- [ ] `docs/deploy.md` documents the four secrets (deployment-repo secrets, unprefixed in the env file, like TuDo D28) and the owner's refresh-token rotation step.

## Technical notes
- Files: new `app/modules/attachments/` (or similar), `app/core/config.py`, `.env.example`, `docs/deploy.md`, CLI entry.
- Same Google account as TuDoCuaTui, separate OAuth client and root folder (owner, 2026-11-19); Drive quota is shared with TuDo.
- Production enablement needs the owner to create the OAuth client, refresh token and folder (work/ming-todo.md M-1); tests use a fake and do not wait on it.

## Test plan
- Unit/integration: fake Drive (httpx MockTransport); config matrix; error-code mapping; sweep dry-run/apply.
- Manual: smoke script against the real Drive after M-1.

## Notes / answers from Ming

Waiting for Ming: plan approval (High tier) — batch 2026-11-19-2, blocking questions 1–6.

## Handoff
### 2026-11-19 — claude — BLOCKED (waiting plan approval, batch 2026-11-19-2)
- Agent calls: 3, tokens ~238k (software-architect×1 63k, security-architect×1 66k, planner×1 opus 109k)
- Done: consult (2), plan.md READY, predicted tier high (`app/core/config.py`, `.env.example`).
- In progress / next: Ming answers batch questions 1–6 + plan approval → `/run-batch --resume`.
- Gotchas: TuDoCuaTui reference repo not reachable in this session. Shares `app/core/config.py`, `.env.example`, `app/main.py`, `docs/deploy.md` with ADM-028; ADR number collision (both plans say 0018) — ADM-027 keeps 0018, ADM-028 renumbers to 0019 when built second.
