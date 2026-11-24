# USR-32: Display name claim in app access tokens

| Field | Value |
|---|---|
| State | DONE |
| Priority | P2 |
| Size | S |
| Areas | auth, security |
| Gate | plan-approval |
| Depends on | — |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-24 |
| Claimed by | — |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/98 |

## Goal
Apps can show the signed-in user's display name straight from the access token, without a server call (simap request; complements the USR-29 member read, which covers people who have not opened the app yet).

## Scope reference
- Spec §5.8 "Verification by apps" lists the access-token claims (`sub`, `email`, `email_verified`, `aud`, `roles`, `org`, `org_roles`, `ent`, `iat`, `exp`); ADR-0005 (access-token signing); `docs/identity-architecture.md` token section.
- ⚠️ No spec line adds a name claim yet; this is an additive claim change to spec §5.8. The plan adds a dated spec line and states whether an ADR or a D-log row is needed; Ming confirms at the plan gate.
- Raised by simap with USR-29 (2026-11-20) as "a smaller first step for names only"; deferred to this ticket.

## Scope
- Add an optional `name` claim (the account's `display_name`) to access tokens issued for apps (`aud=<app>`). PLAN decides: claim name (`name`, as OIDC), absent vs `null` when unset, whether staff (`aud=admin`) tokens get it too, and any length/normalisation rule.
- Additive within v1: existing verifiers that ignore unknown claims keep working; `mmlabs-auth` / `@mmlabs/auth-client` (separate repo, USR-08) may expose it later — follow-up there.
- Document the staleness: a display-name change shows up on the next token (up to the token TTL, 12 h).
- Docs: spec §5.8 claim list, `docs/identity-architecture.md` token claims, `docs/consumers.md`.

## Out of scope
- Changing display names from apps; adding other profile claims (avatar, locale).
- Updating the client libraries (separate repo).

## Acceptance criteria
- [ ] An app access token carries the user's display name in the agreed claim; when the user has no display name the claim follows the agreed absent/null rule (tested).
- [ ] A display-name change is reflected in the next token issued; tokens already issued are unchanged (documented lag).
- [ ] Existing claims and token verification are unchanged (regression tests on `/auth/token`, with and without `org_id`).
- [ ] No email or name is written to logs by token issuance (existing log tests stay green).
- [ ] Spec §5.8 claim list, `docs/identity-architecture.md` and `docs/consumers.md` updated.

## Technical notes
- Files: `app/modules/tokens/` (claim building), possibly `app/modules/auth/`, tests for `/auth/token`, docs.
- Risk: display name is user-controlled text; apps must treat it as untrusted text (document). Token size grows slightly.

## Test plan
- Token issuance tests per criterion; JWT decode in tests asserting the claim.

## Notes / answers from Ming
- Plan approved (2026-11-24, batch 2026-11-24-1) — Ming: "duyệt hết; câu 1–13 theo mặc định" (all batch questions answered with the defaults).
- 2026-11-24: created at Ming's request ("tạo USR-31, USR-32") as the alternative/complement raised by simap with USR-29.

## Handoff

### 2026-11-24 — claude — DONE (waiting for merge)
- Agent calls: 6, tokens ~260k total (identity-access-engineer×1 33k, security-architect×1 32k, planner×1 opus 57k, implementer×1 sonnet 53k, reviewer×1 opus 39k, appsec-engineer×1 46k)
- Done: `name` claim for app tokens with issuance guard, ADR-0005 amendment + D39, spec/docs. Review PASS attempt 1 (4091 passed); appsec PASS (3 💭). PR https://github.com/mmlabs-online/org-admin-api/pull/98 (tier high).
- Next: Ming merges #98; USR-33 (display-name write path) makes the claim useful.
