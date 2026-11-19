# BUG-xxx: <short description of the wrong behaviour>

| Field | Value |
|---|---|
| Type | bug |
| State | BACKLOG |
| Severity | S1 / S2 / S3 |
| Priority | P0 |
| Size | XS |
| Areas | <from loop.config.md Consult / Review triggers> |
| Gate | none |
| Found in | vX.Y.Z (production / local) / unknown |
| Introduced in | vX.Y.Z (<ticket or commit>) / unknown |
| Depends on | — |
| Needs Ming | — |
| Attempts | 0 |
| Created | YYYY-MM-DD |
| Claimed by | — |
| PR | — |

## Report
<What Ming saw, in Ming's words (quote), date. Screenshot: describe it; do not commit images that show emails or personal data.>

## Environment
- Version / mode · Browser and device (UI bugs) · Other relevant setup

## Steps to reproduce
1. …

## Expected
- …

## Actual
- …

## Root cause
<file:line and why, once known; "unknown — planner investigates" otherwise. Mark it **confirmed** (reproduced) or **suspected**.>

## Scope
- Fix the cause above; nothing else.

## Out of scope
- …

## Acceptance criteria
- [ ] A regression test reproduces the bug: it fails on `main` before the fix and passes after (name the test).
- [ ] The expected behaviour above holds.
- [ ] All checks pass.

## Technical notes
- Files: …
- Test level: the bug test level in `.claude/loop.config.md`; a one-off scripted check recorded in impl-log when that level cannot see the bug.

## Notes / answers from Ming

## Handoff
