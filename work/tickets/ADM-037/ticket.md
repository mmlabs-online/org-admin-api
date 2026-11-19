# ADM-037: Release log — guide for app repos and the deployment repo

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P3 |
| Size | S |
| Areas | api |
| Gate | none |
| Depends on | ADM-034, ADM-036 |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-19 |
| Claimed by | — |
| PR | — |

## Goal
App teams and the deployment repo can add the release-log calls the first time, without ever blocking a release or deploy.

## Scope reference
`docs/spec-v1.md` §5.13 cross-repo follow-ups; ADR-0018.

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

## Handoff
