# ADM-036: Release log — deploy result report (deployer credential)

| Field | Value |
|---|---|
| State | IN_PROGRESS |
| Priority | P2 |
| Size | M |
| Areas | api, auth, security |
| Gate | plan-approval |
| Depends on | ADM-033, ADM-035 |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-19 |
| Claimed by | claude@2026-11-20T15:52Z |
| PR | — |

## Goal
After the health check, the deploy workflow records whether a release deployed or failed, even if the app's own write never arrived.

## Scope reference
`docs/spec-v1.md` §5.13 (OQ38, OQ39, OQ40); ADR-0021 §4–§6.

## Scope
- `…/apps/{key}/releases/{environment}/{version}/deployment` (PUT or PATCH — PLAN decides; upsert semantics), deployer key only, any app incl. disabled (unknown app → 404).
- Transitions: pending → deployed|failed; failed → deployed (redeploy); failed → failed updates the run URL; deployed → deployed with the same run URL = no-op 200, with a different run URL = redeploy/rollback (update `deployed_at` and URL, audited); deployed → failed → 409 `release_already_deployed` (or the code PLAN fixes).
- Missing record → create it from the report (status, `deployed_at`, `deploy_run_url`; `created_via='deployer'`), 201.
- `deployed_at` rules (required with deployed; ≤ 5 min in the future, ≤ 30 days in the past); body cap; per-IP rate limit; no CORS; audit per real change (SystemActor `deployer:<key_id>`).

## Out of scope
- App write (ADM-034), staff reads (ADM-033).

## Acceptance criteria
- [ ] Every transition in the table is tested, including retries (same response, no duplicate audit).
- [ ] Report-first creates a thin record that ADM-034's PUT can later enrich.
- [ ] Wrong key kind / admin JWT → 401; 422 on invalid fields; 429 on the limit.
- [ ] After a rollback report, the live-version read (ADM-033) shows the rolled-back-to version.

## Technical notes
- Files: `app/modules/releases/`, route-class registry, tests. Run after ADM-034 if both touch the route-class test file at the same time.

## Test plan
- API tests per transition; concurrency test for report vs app write on the same key.

## Notes / answers from Ming
- Plan approved (2026-11-20, batch 2026-11-20-2) — Ming: "câu 1–28 theo mặc định /run-batch --resume" (all batch questions answered with defaults; resume to BUILD taken as plan approval).

## Handoff
