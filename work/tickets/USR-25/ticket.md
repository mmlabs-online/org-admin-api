# USR-25: Scope of `self_assignment_forbidden` beyond the admin app

| Field | Value |
|---|---|
| State | DONE |
| Priority | P2 |
| Areas | auth, security |
| Attempts | 1 |
| Claimed by | — |
| Created | 2026-11-19 |
| PR | https://github.com/mmlabs-online/org-admin-api/pull/110 |

## Goal
Close (or deliberately accept) a separation-of-duties gap: today `self_assignment_forbidden` applies only to admin-app roles, so a staff member with `admin:roles:assign` can grant themselves any non-admin product role, and with `admin:*` also privileged product roles. admin-pan's security review flagged it.

## Spec reference
§5.3 Authorization; ADR-0001, ADR-0008 (guards G1–G5).

## Acceptance criteria
- [ ] The owner picks one: (a) keep as is and document it in ADR-0001/0008; (b) forbid self-grant and self-revoke of privileged roles (any role holding an `is_admin` permission) in every app; (c) forbid all self-assignment in every app.
- [ ] Whatever is chosen: the 403 code stays `self_assignment_forbidden`; refused attempts are audited as `denied` (existing pattern); ADR-0008 records the rule.
- [ ] The guard applies to every path that grants or revokes on the caller (assignment create/revoke, join-request approval if it can grant to self).
- [ ] Tests per app type (admin app, non-admin app with and without `is_admin` permissions, org-scoped role).

## Dependencies
USR-02c, ADM-002b, USR-03g. Requested by admin-pan (batch 2026-11-19).

## Notes / answers from owner
- 2026-11-19 (owner): **option (b)** — forbid self-grant and self-revoke of privileged roles (any role holding an `is_admin` permission, plus every admin-app role as today) in every app; non-privileged roles (e.g. `<app>:member`) may still be self-assigned.
- 2026-11-27 (Ming, chat): "duyệt hết; câu 1–24 theo mặc định; cho merge USR-28".
- Plan approved (2026-11-27, batch 2026-11-27-1); all blocking questions answered with the plan defaults.

## Handoff

### 2026-11-27 — claude — DONE (batch 2026-11-27-1, attempt 1)
- Agent calls: 7, tokens ~437k total (identity-access-engineer 53k, security-architect 52k, planner opus 111k, implementer sonnet 85k, reviewer opus 73k, appsec-engineer 63k).
- Done: option (b) — one privileged predicate (`_role_is_privileged`: admin app, any `is_admin` permission at either scope, designated role) computed once per request and passed to `_forbid_self(privileged, user_id, actor)`; self grant, renew and revoke of privileged roles refused in every app (`self_assignment_forbidden`, "Cannot change your own privileged roles"); check order unchanged; batch per-item failure + denied row; docs (ADR-0008/0029/0030, identity-architecture, spec) and the read-only report query in docs/deploy.md (M-9). Review PASS (4514 passed; ruff/mypy/alembic clean; tier high); appsec PASS.
- Follow-ups: stale docstring `rbac/assignments_router.py:3-4`; denied-audit handler on join approval if default roles could ever hold `is_admin`; optional self-grant history query over the audit log.
- PR: https://github.com/mmlabs-online/org-admin-api/pull/110 (tier high). Next: Ming merges #110 (or "merge cho tôi #110"); then `/run-batch --resume` builds USR-26.
