# USR-13: Allow 12-hour access tokens

| Field | Value |
|---|---|
| State | IN_PROGRESS |
| Priority | P1 |
| Areas | architecture, auth, security |
| Attempts | 0 |
| Created | 2026-11-17 |
| PR | — |

## Goal
Access tokens can live 12 hours (`ACCESS_TOKEN_TTL_SECONDS=43200`), so developers and apps do not have to re-mint a
token every 15 minutes (owner request, 2026-11-17).

## Spec reference
ADR-0005 (access-token signing), point 6 "Lifetime: 900, bounds 60–3600, no refresh token"; `docs/consumers.md`
(token age rule for consumers); `docs/deploy.md` (key rotation timing). Requested from TuDoCuaTui decision D46.

## Scope
- Raise the upper bound of `access_token_ttl_seconds` (`app/core/config.py:70`, today `le=3600`) to at least 43200.
- Amend ADR-0005 point 6 (new lifetime, why, and the consequences below); keep the default at 900 unless the owner
  says otherwise in the plan.
- Update `docs/consumers.md` (consumers may accept tokens up to 12 h old; ban / role removal latency) and
  `docs/deploy.md` (key rotation: wait TTL + leeway + JWKS `max-age` before removing an old key, now about 12.5 h).
- The production value `ACCESS_TOKEN_TTL_SECONDS=43200` is set in the deployment repo: an owner step after merge.

## Acceptance criteria
- [ ] `ACCESS_TOKEN_TTL_SECONDS=43200` is accepted at startup; values above the new bound are rejected (test).
- [ ] An issued token has `exp - iat = 43200` when configured so, and `expires_in` matches (test).
- [ ] ADR-0005, `docs/consumers.md` and `docs/deploy.md` state the new lifetime and its consequences.
- [ ] Checks green.

## Consequences accepted by the owner (2026-11-17)
- The setting is global: every consuming app (admin-pan, ricotdin, TuDoCuaTui, …), including the admin app, gets
  12-hour tokens.
- A banned user or a removed role keeps access until the token expires: up to 12 hours instead of 15 minutes.
- A leaked token is usable for up to 12 hours on every app that accepts its audience.
- Signing-key rotation must wait about 12.5 hours before the old key is removed.

## Notes / answers from owner
- 2026-11-17 (Ming, in the TuDoCuaTui session): "token 12 hours", then "chọn C" after being told that the bound is
  3600 in code, the TTL is global for all apps including admin, and ban latency becomes 12 h. The softer options
  (TuDo dev script auto-refresh; 1 h via config only) were declined.

## Handoff
