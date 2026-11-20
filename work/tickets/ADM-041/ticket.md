# ADM-041: ADR-0018 amendment: usage key scopes

| Field | Value |
|---|---|
| State | BACKLOG |
| Priority | P2 |
| Size | S |
| Areas | architecture, auth, security |
| Gate | plan-approval |
| Depends on | — |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-20 |
| Claimed by | — |
| PR | — |

## Goal
Record how app backends call this API for usage: new scopes on the scoped app-credential mechanism, before any usage route exists.

## Scope reference
`docs/spec-v1.md` §5.14 Usage limits and credits per user and app (owner answers 2026-11-18 and 2026-11-20; OQ51–OQ69). OQ56, OQ61, OQ66; ADR-0018 (scoped app credentials).

## Scope
- ADR-0018 amendment (or ADR-0021): scopes `usage:read`, `usage:write`, `limits:write`; `on_behalf_of` rules for app-admin calls (`<app>:usage:manage`, app-scope assignment only); idempotency keys; fail-closed default and opt-in fail-open grace; per-key rate limit; why token issuance stays ungated.
- R3 class 4 wording in `CLAUDE.md`; D-log entry in `docs/identity-architecture.md`.

## Acceptance criteria
- [ ] ADR accepted by Ming at the plan gate; CLAUDE.md R3 and the D-log updated in the same PR.
- [ ] No code change in this ticket (docs only).

## Notes / answers from Ming

## Handoff
