# USR-23: Staff read of an organization's members and invitations (admin Bearer)

| Field | Value |
|---|---|
| State | MERGED |
| Priority | P1 |
| Areas | api, auth, security, db |
| Attempts | 1 |
| Created | 2026-11-19 |
| Gate | plan-approval |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/72 |

## Goal
Staff can see, in an organization's detail, who its members are (with roles and joined date) and its invitations with their status (admin-pan PAN-018). Today the only membership/invitation routes are the cookie routes under `/api/v1/auth/orgs/*` and `/api/v1/auth/invitations/accept`.

## Spec reference
§5.1 Organizations; §5.3 Authorization (org-scoped role assignments; ADR-0013); invitations (USR-06b, ADR-0014); §8 Q6 (PII).

## Acceptance criteria
- [ ] `GET /api/v1/organizations/{org_id}/members` (Bearer, `admin:users:read`, `Cache-Control: no-store`) → `Page[T]` (`limit` ≤ 100, `offset`), one row per org-scoped `role_assignments` row: `assignment_id`, `user_id`, `email`, `display_name`, `user_status`, `app`, `role_id`, `role_key`, `role_name`, `granted_by`, `created_at` (joined), `expires_at`, `active` (not expired). Optional filters `app` and `active`. No N+1.
- [ ] `GET /api/v1/organizations/{org_id}/invitations` (Bearer, `admin:organizations:read`) → `Page[T]`: `id`, `app`, `role_id`, `role_key`, `invited_by`, `created_at`, `expires_at`, `accepted_at`, `accepted_by`, `revoked_at` and derived `status` (`pending` | `accepted` | `revoked` | `expired`, database clock). Optional filter `status`. Never the token, token hash or email HMAC.
- [ ] The invitee email is not shown (it is stored only as an HMAC; owner decided 2026-11-19 not to store a masked hint). No new column for this.
- [ ] Optional if cheap (planner decides): `DELETE /api/v1/organizations/{org_id}/invitations/{id}` → 204, revokes, audited in the same transaction (R4), `admin:organizations:write`.
- [ ] Tests: 401, 403, 404 unknown org, paging, a user with two roles in one org (two rows), expired assignment `active=false`, each invitation status, no token fields in the body.

## Dependencies
USR-06, USR-06b (invitations), ADM-002b, ADM-012. Consumer: admin-pan PAN-018 (blocked on this). Requested by admin-pan (batch 2026-11-19).

## Notes / answers from owner
- 2026-11-19 (owner): **no masked email hint** — the invitations list shows no invitee email; no new column. The list carries app, role, inviter, timestamps and status only.
- 2026-11-19 (owner): Plan approved (2026-11-19, batch 2026-11-19-1); questions 1–5 (batch numbering) per the defaults in plan.md — "duyệt hết; câu 1–9 theo mặc định".

## Handoff
### 2026-11-19 — claude — BLOCKED (waiting plan approval, batch 2026-11-19-1)
- Agent calls: 5, tokens ~266k total (api-platform-engineer×1 41k, identity-access-engineer×1 53k, security-architect×1 51k, database-optimizer×1 38k, planner×1 opus 83k)
- Done: consult (4), plan.md READY, predicted tier high (app/modules/rbac/**).
- In progress / next: Ming answers batch questions 1–5 + plan approval → `/run-batch --resume`.
- Gotchas: shares rbac repository/service/schemas with USR-24 — expect a rebase for whichever lands second. Session-mandated branch `claude/festive-volta-rikup7` used instead of `ticket/USR-23`.
### 2026-11-19 — claude — DONE (PR #72, waiting for Ming's merge)
- Agent calls (build): 6, tokens ~287k (implementer×2 sonnet 88k+26k, reviewer×2 opus 63k+30k, appsec-engineer×2 52k + sonnet 28k). Batch total for USR-23 incl. plan: 11 calls, ~553k.
- Done: implemented members / invitations lists + staff revoke; review attempt 1 REWORK (audit-coverage registry missing the DELETE), attempt 2 PASS; appsec PASS both attempts. PR https://github.com/mmlabs-online/org-admin-api/pull/72.
- In progress / next: Ming merges #72 (High tier). Then USR-24 builds on the same session branch.
- Gotchas: CI `make lint` is red on main because of kit file `.github/scripts/risk_tier.py` (PR #71) — not this ticket. Local env: no Docker, `service postgresql start`.
