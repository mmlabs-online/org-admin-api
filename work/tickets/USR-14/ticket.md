# USR-14: Admin tokens end on logout, password reset and staff session revoke

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P2 |
| Areas | architecture, auth, security, api |
| Attempts | 0 |
| Created | 2026-11-17 |
| PR | — |

## Goal
With 12 h access tokens (USR-13), logout, password reset, staff session revoke (USR-10) and disable-then-re-enable must end a live admin token on its next request, not at `exp`.

## Spec reference
§5.8, §5.10; ADR-0005 amendment risk 4; ADR-0008 §3; USR-13 and USR-10 follow-ups

## Acceptance criteria
- [ ] Design chosen in the plan and recorded in an ADR-0005/0008 amendment: an additive `sid` claim (account session id) checked in the per-request admin reload, and/or a per-user `tokens_valid_after` cutoff compared with `iat`
- [ ] For `aud=admin`: after logout, password reset, staff revoke of that session/all sessions, or disable→re-enable, the previously issued token gets 401 on the next admin request
- [ ] Consumer-app tokens unchanged (still valid until `exp`); docs (consumers.md, deploy.md runbook, ADR) updated to the new admin behaviour
- [ ] Additive claim only (no breaking change for mmlabs-auth verifiers); migration with working downgrade if a column is added
- [ ] Tests for each trigger above, plus a token issued after the cutoff still works

## Dependencies
USR-13, USR-10.

## Notes / answers from owner
- 2026-11-17 (owner): create from the USR-13/USR-10 follow-ups (most valuable with 12 h tokens).
