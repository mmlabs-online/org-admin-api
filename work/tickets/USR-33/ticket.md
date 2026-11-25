# USR-33: Let users and staff set a display name

| Field | Value |
|---|---|
| State | REVIEW |
| Priority | P2 |
| Size | M |
| Areas | api, auth, security, db |
| Gate | plan-approval |
| Depends on | USR-32 |
| Needs Ming | — |
| Attempts | 0 |
| Created | 2026-11-24 |
| Claimed by | claude@2026-11-25T17:05Z |
| PR | — |

## Goal
Give `users.display_name` a real write path so the USR-32 `name` claim and the USR-29 member read's `display_name` carry a value; today nothing can set it and every name is NULL in production.

## Scope reference
- Batch 2026-11-24-1, Q8 default (Ming, 2026-11-24): ship USR-32 as specified and open this follow-up for a display-name write path with validation.
- Spec §5.8 (accounts; claims), `docs/identity-architecture.md` account fields; USR-29 (ADR-0025 §6, member read fields); USR-32 (`name` claim, ADR-0005 amendment).
- ⚠️ No spec section defines who may set the display name; the plan adds a dated spec line for Ming's confirmation at the plan gate.

## Scope
- PLAN decides: self-service (cookie class, the signed-in account sets its own name) and/or staff (Bearer, `admin:users:write`), and whether sign-up accepts an optional name.
- Validation on write: NFC, trim, reject disallowed Unicode categories (`app/core/text.py`), length 1–100; clearing the name sets NULL. Optionally a DB CHECK on length (deferred from USR-32, Q9).
- Audit the change (ids only, never the name value in logs; R11).
- Documented lag: tokens pick the new name up on the next issue (≤ TTL); the USR-29 member read shows it at once.

## Out of scope
- Other profile fields (avatar, locale); name uniqueness.

## Acceptance criteria
- [ ] The chosen actor(s) can set and clear a display name; invalid values (control/bidi/zero-width chars, > 100 chars, blank after trim) → 422 without echoing the value.
- [ ] The next app token carries the new `name`; the USR-29 member read returns it.
- [ ] Each change writes an audit row without the name value; no name appears in logs (non-vacuous log test).
- [ ] 401/403 tests for the new route(s); route-class registry updated; docs updated.

## Technical notes
- Files: `app/modules/users/` and/or `app/modules/auth/` (self-service), `app/core/text.py` reuse, tests, docs; migration only if the CHECK is added.

## Test plan
- API tests per criterion; token claim test after a change.

## Notes / answers from Ming
- Plan approved (2026-11-25, batch 2026-11-25-1) — Ming: "duyệt hết; câu 1–23 theo mặc định" (all batch questions answered with the defaults).
- 2026-11-24: created from batch 2026-11-24-1 Q8 (default a), approved by Ming ("duyệt hết; câu 1–13 theo mặc định").

## Handoff
