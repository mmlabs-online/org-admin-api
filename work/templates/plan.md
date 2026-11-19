Status: READY | BLOCKED

# Plan — <ID>: <title>

## Scope summary
<Quote the exact lines of the scope sources (and work/decisions.md) this ticket implements.>

## Acceptance criteria → verification
| # | Criterion | How the reviewer verifies it |
|---|---|---|
| 1 | | <test name / command / observable behaviour in a local run> |

## Ming verification after merge
<Criteria only Ming can check: real device, hosted service, production dashboard. "None" otherwise.>

## Files to create / modify
| File | Change | Required because | High path? |
|---|---|---|---|

## Data model / migration
<Tables, columns (type, null, default), constraints, indexes, migration + downgrade. "None" if no schema change.>

## API contract
| Method | Path | Request | Response | Codes | Permission |
|---|---|---|---|---|---|
<"None" if no endpoint change.>

## Tests to write
| Test (file › name) | Covers |
|---|---|

## Approach
<Short, ordered steps. Note which CLAUDE.md rules apply and how they are met.>

## Specialist input used
<consult-*.md items adopted or rejected, with one-line reason. "None" if no consults.>

## Assumptions
<Non-blocking assumptions, including hotspot files shared with other open tickets.>

## Blocking questions
<Only if Status: BLOCKED, each with a proposed default. Otherwise "None".>

## Split proposal
<If the ticket is too large: the slices, and which one this plan covers. Otherwise "None".>
