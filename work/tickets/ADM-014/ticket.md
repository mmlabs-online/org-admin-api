# ADM-014: Docs — admin UI hostname and forgot-password credentials

| Field | Value |
|---|---|
| State | REVIEW |
| Priority | P2 |
| Areas | security |
| Attempts | 0 |
| Created | 2026-11-16 |
| PR | — |

## Goal
The runbooks and the integration guide match the owner's decisions: the admin UI lives at `admin-portal.mmlabs.online`, and forgot-password needs no credentials.

## Spec reference
§6.1 Deployment; docs only. Related: ADR-0008 (admin origins amendment), ADM-002c.

## Acceptance criteria
- [ ] `docs/deploy.md`: "Host policy", every `set-admin-origins` example and the admin-token runbook use `https://admin-portal.mmlabs.online`. The doc states that `admin.mmlabs.online` must have no DNS record or tunnel route.
- [ ] ADR-0008 amendment and any other doc that names `admin.mmlabs.online` as the admin origin are updated. A grep test or a reviewer check shows no remaining reference except the "must not exist" note.
- [ ] `docs/consumers.md`: forgot-password (and verify-email / reset, which are public) works with `credentials: 'omit'`. The guide no longer recommends `include` for those calls.
- [ ] No code change. Runbook blocks still pass `sh -n`.

## Dependencies
ADM-002e (it edits the same runbook block; merge after it).

## Notes / answers from owner
- 2026-11-16 (owner): admin UI hostname is `admin-portal.mmlabs.online`; `admin.mmlabs.online` is not used.
