# ADM-037: Release log — guide for app repos and the deployment repo

| Field | Value |
|---|---|
| State | DONE |
| Priority | P3 |
| Size | S |
| Areas | api |
| Gate | none |
| Depends on | ADM-034, ADM-036 |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-19 |
| Claimed by | — |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/96 |

## Goal
App teams and the deployment repo can add the release-log calls the first time, without ever blocking a release or deploy.

## Scope reference
`docs/spec-v1.md` §5.13 cross-repo follow-ups; ADR-0021.

## Scope
- A guide (e.g. `docs/release-log.md`): request contracts for the app write and the deploy report, warning-only CI steps (`curl -fsS --retry 3 --max-time 15 … || echo "::warning::…"`, `continue-on-error`), secret names (`MMLABS_RELEASE_CREDENTIAL` per app repo in a release-branch Environment secret; `MMLABS_DEPLOYER_CREDENTIAL` in the deployment repo), how to build `changes` from merged PRs, PII rule.
- Owner runbook steps in `docs/deploy.md`: issuing and rotating both key kinds.

## Acceptance criteria
- [ ] Paths, fields and codes in the guide match OpenAPI.
- [ ] Includes a copy-paste step for an app release workflow and for `deploy-<app>.yml`.
- [ ] States that any error must be a warning only.

## Technical notes
- Docs only.

## Test plan
- Manual: run the curl steps against a local server.

## Notes / answers from Ming
- 2026-11-22 (batch 2026-11-22-1): Gate none; Ming answered the batch round "duyệt hết; câu 1–22 theo mặc định".

## Handoff

### 2026-11-24 — claude — DONE (waiting for merge)
- Agent calls: 3, tokens ~142k total (planner×1 sonnet 42k, implementer×1 sonnet 41k, reviewer×1 opus 59k); fast lane, no consults, no review agents (docs only).
- Done: docs/release-log.md guide + docs/deploy.md; review PASS attempt 1; the reviewer's should-fix (temp credential file cleanup) applied by the orchestrator as a docs-only commit. PR https://github.com/mmlabs-online/org-admin-api/pull/96 (tier low; no --auto-merge, so Ming merges).
- Next: Ming merges #96; after merge, one real call per the guide (criterion 4).
